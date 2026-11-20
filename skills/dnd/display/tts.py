"""Narrator TTS via Gemini Flash TTS (Google AI Studio API key).

Server-side wrapper called by /tts in dnd-display-app.py. Reads the API key
from DND_TTS_KEY env var → GEMINI_API_KEY env var → ~/.config/claude-dnd/tts.key
(in that order). Returns None on any failure so the caller can silently
degrade to text-only narration.

stdlib only — no requests, no google-cloud-sdk, no dependencies.
Setup walkthrough: docs/SKILL-tts.md.

Synthesized clips are cached on disk, keyed by a hash of (voice, model,
text), so replaying a block plays the saved clip instead of spending the
user's Gemini quota on the same request again. The cache is bounded
(DND_TTS_CACHE_MB, default 64) and evicts least-recently-played clips.
DND_TTS_CACHE_DIR moves it; DND_TTS_CACHE_MB=0 turns it off.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# ── Configuration ───────────────────────────────────────────────────────────

GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
GEMINI_TTS_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_TTS_MODEL}:generateContent"
)

KEY_FILE = Path.home() / ".config" / "claude-dnd" / "tts.key"

# Curated 9-voice catalog. The full Gemini API exposes 30 voices; this
# subset keeps the per-block dropdown scannable. Extend VALID_VOICES and
# the matching arrays in display/templates/index.html if you want the
# full catalog.
VALID_VOICES = frozenset({
    # Male
    "Charon", "Enceladus", "Fenrir", "Umbriel",
    # Female
    "Aoede", "Gacrux", "Kore", "Vindemiatrix", "Zephyr",
})
DEFAULT_VOICE = "Enceladus"

# Gemini Flash TTS responses degrade in latency past this point and may
# truncate. Bigger blocks should be chunked by the caller; the narration
# path naturally chunks at block flush.
MAX_TEXT_CHARS = 2000

# Request timeout — Gemini Flash TTS typical latency is 1-3s; we give it
# substantial headroom for cold-start or transient slowness.
DEFAULT_TIMEOUT = 90.0


# ── Clip cache ──────────────────────────────────────────────────────────────

DEFAULT_CACHE_MB = 64


def cache_dir() -> Path:
    raw = os.environ.get("DND_TTS_CACHE_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".cache" / "claude-dnd" / "tts"


def _cache_limit_bytes() -> int:
    try:
        mb = float(os.environ.get("DND_TTS_CACHE_MB", DEFAULT_CACHE_MB))
    except ValueError:
        mb = DEFAULT_CACHE_MB
    return max(0, int(mb * 1024 * 1024))


def cache_key(voice: str, text: str, model: str = GEMINI_TTS_MODEL) -> str:
    """Hash of everything that changes the audio. The text is never a filename."""
    h = hashlib.sha256()
    for part in (voice, model, text):
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def cache_get(key: str) -> Optional[bytes]:
    if _cache_limit_bytes() <= 0:
        return None
    p = cache_dir() / f"{key}.pcm"
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if not data:
        return None
    try:
        os.utime(p, None)   # mark as recently played, for eviction order
    except OSError:
        pass
    return data


def cache_put(key: str, pcm: bytes) -> None:
    """Store a clip, then evict least-recently-played clips over the bound.

    Best effort: a cache that cannot be written costs a re-synthesis later,
    never the narration now.
    """
    limit = _cache_limit_bytes()
    if limit <= 0 or not pcm or len(pcm) > limit:
        return
    d = cache_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{key}.tmp"
        tmp.write_bytes(pcm)
        os.replace(tmp, d / f"{key}.pcm")
        clips = sorted(d.glob("*.pcm"), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in clips)
        for f in clips:
            if total <= limit:
                break
            total -= f.stat().st_size
            f.unlink()
    except OSError:
        pass


# ── Key resolution ──────────────────────────────────────────────────────────

def _get_api_key() -> Optional[str]:
    """Resolve the Gemini API key. Env vars take precedence over the key file."""
    for env in ("DND_TTS_KEY", "GEMINI_API_KEY"):
        v = os.environ.get(env)
        if v and v.strip():
            return v.strip()
    if KEY_FILE.exists():
        try:
            return KEY_FILE.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    return None


def key_source() -> str:
    """Return a human description of where the key came from, for diagnostics.

    Used by the verify path and the /dnd load surface — does not return the key.
    """
    for env in ("DND_TTS_KEY", "GEMINI_API_KEY"):
        v = os.environ.get(env)
        if v and v.strip():
            return f"env:{env}"
    if KEY_FILE.exists() and KEY_FILE.read_text(encoding="utf-8").strip():
        return f"file:{KEY_FILE}"
    return "unset"


def key_fingerprint() -> str:
    """A short hash of the current key, so a replaced key can be noticed
    without keeping or printing the key itself. "" when no key is set."""
    key = _get_api_key()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12] if key else ""


def check_key(timeout: float = 10.0) -> tuple:
    """Ask Google whether this key can use the voice model, without speaking.

    Reads the model's metadata (GET models/<model>), which spends none of
    the key's generation quota, so it is safe to run at every start.
    Returns (ok, reason, retry): reason is a short plain-English cause when
    ok is False, and retry says whether trying again later could help (a
    network failure) or not (a rejected key).
    """
    key = _get_api_key()
    if not key:
        return False, "no voice key is set up", False
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_TTS_MODEL}?key={key}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout) as resp:
            if resp.status == 200:
                return True, "", False
            return False, f"Google answered {resp.status}", True
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        if (e.code == 400 and "API_KEY_INVALID" in body) or e.code == 401:
            return False, "Google rejected the voice key", False
        if e.code == 403:
            return False, "the voice key isn't allowed to use Gemini", False
        if e.code == 404:
            return False, "this key can't use the voice model", False
        if e.code == 429:
            return False, "the voice key is rate-limited", True
        return False, f"Google answered {e.code}", True
    except urllib.error.URLError:
        return False, "couldn't reach Google to check the voice", True
    except Exception as e:  # pragma: no cover - defensive
        return False, f"voice check failed ({type(e).__name__})", True


# ── Synthesis ───────────────────────────────────────────────────────────────

class TtsError(Exception):
    """Raised by synthesize_strict; caught by synthesize to fail silently.

    `status` is the upstream HTTP status (0 if none). For a 429, `quota_id`
    names the exhausted quota (e.g. GenerateRequestsPerDayPerProjectPerModel-
    FreeTier, which is 10 requests a day for this model) and `retry_after`
    is Gemini's suggested wait in seconds."""

    def __init__(self, msg, status: int = 0, quota_id: str = "", retry_after: float = 0.0):
        super().__init__(msg)
        self.status = status
        self.quota_id = quota_id
        self.retry_after = retry_after

    @property
    def daily_quota(self) -> bool:
        return self.status == 429 and "PerDay" in self.quota_id


def _parse_quota(body: str) -> tuple:
    """(quota_id, retry_after seconds) from a Gemini 429 body; ("", 0) if absent."""
    try:
        err = json.loads(body).get("error", {})
    except (ValueError, AttributeError):
        return "", 0.0
    quota_id, retry = "", 0.0
    for det in err.get("details", []) or []:
        t = str(det.get("@type", ""))
        if t.endswith("QuotaFailure"):
            for v in det.get("violations", []) or []:
                quota_id = quota_id or str(v.get("quotaId", ""))
        elif t.endswith("RetryInfo"):
            try:
                retry = float(str(det.get("retryDelay", "0")).rstrip("s") or 0)
            except ValueError:
                pass
    return quota_id, retry


def synthesize_strict(
    text: str,
    voice: str = DEFAULT_VOICE,
    timeout: float = DEFAULT_TIMEOUT,
    use_cache: bool = True,
) -> bytes:
    """Call Gemini Flash TTS and return raw L16 PCM bytes (24 kHz mono).

    Raises TtsError for any failure. Use synthesize() for the silent-fail path.
    """
    key = _get_api_key()
    if not key:
        raise TtsError("no api key configured")

    text = (text or "").strip()
    if not text:
        raise TtsError("empty text")
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]

    if voice not in VALID_VOICES:
        voice = DEFAULT_VOICE

    key_hash = cache_key(voice, text)
    cached = cache_get(key_hash) if use_cache else None
    if cached is not None:
        return cached

    body = {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice}
                }
            },
        },
    }
    body_bytes = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        f"{GEMINI_TTS_URL}?key={key}",
        data=body_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise TtsError(f"http {resp.status}")
            raw = resp.read()
    except urllib.error.HTTPError as e:
        body_txt = ""
        try:
            body_txt = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        quota_id, retry = _parse_quota(body_txt) if e.code == 429 else ("", 0.0)
        raise TtsError(f"http {e.code}: {body_txt[:200]}", status=e.code,
                       quota_id=quota_id, retry_after=retry) from e
    except urllib.error.URLError as e:
        raise TtsError(f"network: {e.reason}") from e
    except Exception as e:
        raise TtsError(f"unexpected: {e}") from e

    try:
        data = json.loads(raw)
        b64 = data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        raise TtsError(f"bad response shape: {e}") from e

    try:
        pcm = base64.b64decode(b64)
    except Exception as e:
        raise TtsError(f"bad base64: {e}") from e

    if not pcm:
        raise TtsError("empty pcm payload")
    cache_put(key_hash, pcm)
    return pcm


def synthesize(text: str, voice: str = DEFAULT_VOICE) -> Optional[bytes]:
    """Silent-fail wrapper. Returns L16 PCM bytes or None on any failure."""
    try:
        return synthesize_strict(text, voice)
    except TtsError:
        return None


# ── Kokoro (local narrator voice) ───────────────────────────────────────────
#
# Kokoro runs on this machine in its own Python environment (kokoro_server.py,
# set up by kokoro_setup.py), so the narrator never spends Gemini quota — the
# free tier allows 10 Gemini voice requests a day. The display reads narration
# with a Kokoro voice and keeps Gemini for NPC lines; when Gemini fails or is
# out of quota, an NPC line is read by a matching Kokoro voice instead.

KOKORO_PORT = int(os.environ.get("DND_KOKORO_PORT", "5002"))
KOKORO_URL = os.environ.get("DND_KOKORO_URL", f"http://127.0.0.1:{KOKORO_PORT}")
KOKORO_MODEL = "kokoro-v1.0"

# English Kokoro voices: id -> label shown in the voice menu.
KOKORO_VOICES = {
    "bm_lewis": "Lewis", "bm_george": "George", "bm_fable": "Fable", "bm_daniel": "Daniel",
    "bf_emma": "Emma", "bf_isabella": "Isabella", "bf_alice": "Alice", "bf_lily": "Lily",
    "am_michael": "Michael", "am_fenrir": "Fenrir", "am_adam": "Adam", "am_echo": "Echo",
    "am_eric": "Eric", "am_liam": "Liam", "am_onyx": "Onyx", "am_puck": "Puck", "am_santa": "Santa",
    "af_heart": "Heart", "af_bella": "Bella", "af_nicole": "Nicole", "af_aoede": "Aoede",
    "af_kore": "Kore", "af_sarah": "Sarah", "af_nova": "Nova", "af_sky": "Sky",
    "af_alloy": "Alloy", "af_jessica": "Jessica", "af_river": "River",
}
DEFAULT_NARRATOR = "bm_lewis"

# A Gemini voice's stand-in when Gemini can't speak: same sex, similar weight.
GEMINI_TO_KOKORO = {
    "Charon": "bm_george", "Enceladus": "am_michael", "Fenrir": "am_fenrir",
    "Umbriel": "bm_daniel", "Aoede": "af_aoede", "Gacrux": "bf_emma",
    "Kore": "af_kore", "Vindemiatrix": "bf_isabella", "Zephyr": "af_nova",
}

ALL_VOICES = frozenset(VALID_VOICES) | frozenset(KOKORO_VOICES)


def kokoro_home() -> Path:
    """Where kokoro_setup.py puts Kokoro's Python environment and model:
    the D&D data root (DND_CAMPAIGN_ROOT, default ~/.claude/dnd)."""
    root = os.environ.get("DND_CAMPAIGN_ROOT", "").strip() or "~/.claude/dnd"
    return Path(root).expanduser()


def kokoro_python() -> Path:
    venv = kokoro_home() / "kokoro-venv"
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def kokoro_models() -> Path:
    return kokoro_home() / "kokoro-models"


def kokoro_installed() -> bool:
    m = kokoro_models()
    return (kokoro_python().exists() and (m / "kokoro-v1.0.onnx").exists()
            and (m / "voices-v1.0.bin").exists())


def blend_parts(voice) -> Optional[tuple]:
    """("am_onyx", "am_adam") for the blend "am_onyx+am_adam" when both parts
    are Kokoro voices; None for anything else (D20)."""
    if not isinstance(voice, str) or voice.count("+") != 1:
        return None
    a, b = voice.split("+")
    return (a, b) if a in KOKORO_VOICES and b in KOKORO_VOICES and a != b else None


def is_kokoro(voice: str) -> bool:
    return voice in KOKORO_VOICES or blend_parts(voice) is not None


def valid_voice(voice: str) -> bool:
    return voice in ALL_VOICES or blend_parts(voice) is not None


def kokoro_standin(voice: str) -> str:
    """The Kokoro voice that reads a line when its own voice can't."""
    if is_kokoro(voice):
        return voice
    return GEMINI_TO_KOKORO.get(voice, DEFAULT_NARRATOR)


_blend_flag: Optional[bool] = None   # from the last good /health; None = unknown


def kokoro_health(timeout: float = 1.5) -> Optional[dict]:
    """{"ok": True, "provider": "cuda"|"cpu", "blend": bool, ...} from the
    local Kokoro helper, or None when it isn't running. Remembers whether the
    helper can blend two voices (an older helper has no "blend" key: False)."""
    global _blend_flag
    try:
        with urllib.request.urlopen(f"{KOKORO_URL}/health", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not data.get("ok"):
        return None
    _blend_flag = data.get("blend") is True
    return data


def kokoro_blend_supported() -> Optional[bool]:
    """Whether the running Kokoro helper blends voices: True / False from the
    last good kokoro_health(), None when not known yet. No network call."""
    return _blend_flag


def kokoro_synthesize(text: str, voice: str = DEFAULT_NARRATOR, speed: float = 1.0,
                      timeout: float = 30.0, use_cache: bool = True) -> bytes:
    """Read text with a local Kokoro voice; raw L16 PCM (24 kHz mono).

    Raises TtsError when the helper is down or fails."""
    text = (text or "").strip()
    if not text:
        raise TtsError("empty text")
    text = text[:MAX_TEXT_CHARS]
    voice = kokoro_standin(voice)
    key_hash = cache_key(voice, f"{speed:.2f}|{text}", model=KOKORO_MODEL)
    cached = cache_get(key_hash) if use_cache else None
    if cached is not None:
        return cached
    req = urllib.request.Request(
        f"{KOKORO_URL}/synth",
        data=json.dumps({"text": text, "voice": voice, "speed": speed}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            pcm = resp.read()
    except urllib.error.HTTPError as e:
        raise TtsError(f"kokoro http {e.code}", status=e.code) from e
    except Exception as e:
        raise TtsError(f"kokoro unavailable: {e}") from e
    if not pcm:
        raise TtsError("kokoro returned no audio")
    cache_put(key_hash, pcm)
    return pcm


# ── CLI for verification ────────────────────────────────────────────────────

def _cli() -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(
        description="Verify Gemini Flash TTS setup for the DnD display companion.",
    )
    p.add_argument(
        "--test", action="store_true",
        help="Check key source + run a synthesis call. Writes nothing.",
    )
    p.add_argument(
        "--speak", action="store_true",
        help="Also play the synthesized audio via afplay (macOS).",
    )
    p.add_argument(
        "--text", default="Hello, narrator voice test. The torchlit hall awaits.",
        help="Override the default test phrase.",
    )
    p.add_argument(
        "--voice", default=DEFAULT_VOICE,
        help=f"Voice name (default: {DEFAULT_VOICE}). Valid: {sorted(VALID_VOICES)}",
    )
    args = p.parse_args()

    if not args.test:
        p.print_help()
        return 0

    src = key_source()
    print(f"API key source: {src}")
    if src == "unset":
        print("  → no key found. See docs/SKILL-tts.md to configure one.")
        return 2

    print(f"Model: {GEMINI_TTS_MODEL}")
    print(f"Voice: {args.voice}")
    print(f"Text:  {args.text!r}")
    print("Calling Gemini Flash TTS…")
    try:
        # --test checks the key and the API, so it never answers from cache.
        pcm = synthesize_strict(args.text, args.voice, use_cache=False)
    except TtsError as e:
        print(f"  FAIL: {e}")
        return 1

    print(f"  OK — received {len(pcm)} bytes of L16 PCM (24 kHz mono).")

    if args.speak:
        import struct
        import subprocess
        import tempfile

        sample_rate = 24000
        n_samples = len(pcm) // 2
        byte_rate = sample_rate * 2
        wav_header = (
            b"RIFF"
            + struct.pack("<I", 36 + len(pcm))
            + b"WAVE"
            + b"fmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, byte_rate, 2, 16)
            + b"data"
            + struct.pack("<I", len(pcm))
        )
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_header + pcm)
            wav_path = f.name
        print(f"  Playing via afplay: {wav_path}")
        try:
            subprocess.run(["afplay", wav_path], check=False)
        except FileNotFoundError:
            print("  (afplay not found — non-macOS? PCM file kept at the path above.)")
            return 0
        os.unlink(wav_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
