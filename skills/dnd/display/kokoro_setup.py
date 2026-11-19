#!/usr/bin/env python3
"""kokoro_setup.py — one-time install of the local narrator voice (Kokoro).

    python kokoro_setup.py            # install (or repair) and test it
    python kokoro_setup.py --cpu      # never use the GPU build
    python kokoro_setup.py --check    # say whether it's installed; change nothing

Makes a separate Python environment at <data root>/kokoro-venv (data root is
DND_CAMPAIGN_ROOT, default ~/.claude/dnd), installs kokoro-onnx into it, and
downloads the model (~330 MB) to <data root>/kokoro-models. With an NVIDIA GPU
it installs onnxruntime-gpu plus its CUDA libraries (~2 GB more), which reads
about 17x faster than real time on an RTX 2070 instead of 4x on the CPU.

Nothing here touches the display's own Python. Once installed, the display
starts the voice by itself.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tts  # noqa: E402

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")


def _has_nvidia() -> bool:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "-L"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _pip(py, *args) -> None:
    subprocess.run([str(py), "-m", "pip", "install", "-q", *args], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Install the local Kokoro narrator voice.")
    ap.add_argument("--cpu", action="store_true", help="install the CPU build only")
    ap.add_argument("--check", action="store_true", help="report only")
    a = ap.parse_args()

    py, models = tts.kokoro_python(), tts.kokoro_models()
    if a.check:
        print(f"Kokoro: {'installed' if tts.kokoro_installed() else 'not installed'} ({tts.kokoro_home()})")
        h = tts.kokoro_health()
        print(f"Kokoro helper: {'running on ' + h['provider'] if h else 'not running'}")
        return 0 if tts.kokoro_installed() else 1

    if not py.exists():
        print(f"Making the voice's Python environment at {py.parent.parent} …")
        subprocess.run([sys.executable, "-m", "venv", str(py.parent.parent)], check=True)
        _pip(py, "--upgrade", "pip")
    gpu = _has_nvidia() and not a.cpu
    print("Installing kokoro-onnx" + (" with the NVIDIA GPU build (a few GB) …" if gpu else " (CPU) …"))
    _pip(py, "kokoro-onnx", "soundfile")
    if gpu:
        subprocess.run([str(py), "-m", "pip", "uninstall", "-y", "-q", "onnxruntime"], check=False)
        _pip(py, "onnxruntime-gpu[cuda,cudnn]")

    models.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        dest = models / f
        if dest.exists() and dest.stat().st_size > 1_000_000:
            continue
        print(f"Downloading {f} …")
        tmp = dest.with_suffix(dest.suffix + ".part")
        urllib.request.urlretrieve(f"{RELEASE}/{f}", tmp)
        os.replace(tmp, dest)

    print("Testing the voice …")
    test = ("import sys; sys.path.insert(0, r'%s'); import kokoro_server as k; "
            "from pathlib import Path; e = k.Engine(Path(r'%s')); "
            "print('OK', e.device, len(e.synth('The torchlit hall awaits.', 'bm_lewis')))"
            % (os.path.dirname(os.path.abspath(__file__)), models))
    out = subprocess.run([str(py), "-c", test], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("OK")), "")
    if not line:
        print("The voice didn't start:\n" + (out.stderr or out.stdout)[-1500:])
        return 1
    print(f"Kokoro works ({line.split()[1]}). Restart the display to use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
