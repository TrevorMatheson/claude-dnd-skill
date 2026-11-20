#!/usr/bin/env python3
"""kokoro_server.py — the local narrator voice.

Runs Kokoro (an 82M-parameter text-to-speech model) on this machine and
answers the display app over localhost. It runs inside its own Python
environment (made by kokoro_setup.py), so Kokoro's large libraries never touch
the display's Python, and a crash here only silences the voice.

    POST /synth   {"text": str, "voice": "bm_lewis", "speed": 1.0}
                  -> raw L16 PCM, 24 kHz mono (same format as Gemini)
    GET  /health  -> {"ok": true, "provider": "cuda"|"cpu", "voices": [...]}

The display app starts this helper itself (see _kokoro_supervise in
dnd-display-app.py) and passes --parent-pid; the helper exits when the display
does, so nothing is left running after the display is stopped.

Uses the NVIDIA GPU when onnxruntime-gpu and its CUDA libraries load, and
falls back to the CPU otherwise without saying anything: the CPU still reads
about 4x faster than real time. DND_KOKORO_DEVICE=cpu forces the CPU.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_CHARS = 2000


def _pick_device() -> str:
    """Prefer CUDA; return the provider kokoro-onnx should be told to use."""
    if os.environ.get("DND_KOKORO_DEVICE", "").lower() == "cpu":
        return "cpu"
    try:
        import onnxruntime as ort
        ort.set_default_logger_severity(3)   # errors only: its warnings flood kokoro.log
        if "CUDAExecutionProvider" not in ort.get_available_providers():
            return "cpu"
        # onnxruntime-gpu ships the CUDA/cuDNN DLLs as pip wheels; they are only
        # found after preload_dlls(). Without it the session quietly uses the CPU.
        if hasattr(ort, "preload_dlls"):
            ort.preload_dlls()
        os.environ["ONNX_PROVIDER"] = "CUDAExecutionProvider"
        return "cuda"
    except Exception:
        return "cpu"


class Engine:
    def __init__(self, models: Path):
        self.device = _pick_device()
        from kokoro_onnx import Kokoro
        self.k = Kokoro(str(models / "kokoro-v1.0.onnx"), str(models / "voices-v1.0.bin"))
        try:
            providers = self.k.sess.get_providers()
            self.device = "cuda" if "CUDAExecutionProvider" in providers else "cpu"
        except Exception:
            pass
        self.voices = set(self.k.get_voices())
        self.lock = threading.Lock()
        self.synth("Ready.", "bm_lewis")   # warm-up: the first call is slow

    def can_blend(self) -> bool:
        return hasattr(self.k, "get_voice_style")

    def synth(self, text: str, voice: str, speed: float = 1.0) -> bytes:
        import numpy as np
        style = voice
        parts = voice.split("+") if voice.count("+") == 1 else None
        if parts and all(p in self.voices for p in parts) and parts[0] != parts[1] \
                and self.can_blend():
            # A blend "a+b" (D20): half of each voice's style, in a's accent.
            style = (0.5 * self.k.get_voice_style(parts[0])
                     + 0.5 * self.k.get_voice_style(parts[1]))
            voice = parts[0]
        elif voice not in self.voices:
            voice = style = "bm_lewis"
        lang = "en-gb" if voice.startswith("b") else "en-us"
        with self.lock:
            samples, _sr = self.k.create(text, voice=style, speed=speed, lang=lang)
        pcm = np.clip(samples, -1.0, 1.0)
        return (pcm * 32767.0).astype("<i2").tobytes()


def _make_handler(engine: Engine):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):   # quiet: the display has its own log
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                body = json.dumps({"ok": True, "provider": engine.device,
                                   "voices": sorted(engine.voices),
                                   "blend": engine.can_blend()}).encode()
                self._send(200, body, "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/synth":
                self._send(404, b"not found", "text/plain")
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(min(n, 64_000)) or b"{}")
                text = str(data.get("text") or "").strip()[:MAX_CHARS]
                voice = str(data.get("voice") or "bm_lewis")
                speed = max(0.5, min(2.0, float(data.get("speed") or 1.0)))
            except (ValueError, TypeError):
                self._send(400, b"bad request", "text/plain")
                return
            if not text:
                self._send(400, b"empty text", "text/plain")
                return
            try:
                pcm = engine.synth(text, voice, speed)
            except Exception as e:  # keep serving; one bad line isn't fatal
                self._send(500, f"synthesis failed: {e}".encode(), "text/plain")
                return
            self._send(200, pcm, "audio/L16;codec=pcm;rate=24000")

    return Handler


def _parent_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == 259   # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=int(os.environ.get("DND_KOKORO_PORT", "5002")))
    ap.add_argument("--models", default=os.environ.get("DND_KOKORO_MODELS", ""))
    ap.add_argument("--parent-pid", type=int, default=0)
    args = ap.parse_args()
    models = Path(args.models) if args.models else Path(__file__).resolve().parent
    t0 = time.time()
    engine = Engine(models)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), _make_handler(engine))
    print(f"kokoro ready on 127.0.0.1:{args.port} ({engine.device}, "
          f"{time.time() - t0:.1f}s to load)", flush=True)
    if args.parent_pid:
        def _watch():
            while _parent_alive(args.parent_pid):
                time.sleep(3)
            os._exit(0)
        threading.Thread(target=_watch, daemon=True).start()
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
