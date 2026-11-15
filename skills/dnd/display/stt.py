"""Speech-to-text for "pass the mic", on this PC with faster-whisper.

A player taps the mic on their phone, speaks, and taps again; the phone
uploads the clip to /stt and this module turns it into text, which lands in
the player's action box for them to check and send. Nothing leaves the PC.

Like the narration voice, it works fully or stays off: if faster-whisper is
not installed or the model will not load, `state()` says so in plain words
and the phones show no mic button.

Setup: `pip install faster-whisper`. The first load downloads the model
(small.en is about 480 MB) to the Hugging Face cache; later starts reuse it.

Settings (environment variables):
  DND_STT_MODEL   tiny.en | base.en | small.en (default) | medium.en | …
  DND_STT_DEVICE  cpu (default) | cuda | auto
"""

from __future__ import annotations

import io
import os
import threading
from typing import Iterable, Optional

DEFAULT_MODEL = "small.en"
MAX_CLIP_BYTES = 8 * 1024 * 1024     # ~8 MB: far more than 60 s of opus/aac
MAX_SECONDS = 60

_lock = threading.Lock()             # one transcription at a time (one mic)
_model = None
_state = {"on": False, "reason": "speech-to-text is loading…"}


def model_name() -> str:
    return (os.environ.get("DND_STT_MODEL") or DEFAULT_MODEL).strip()


def state() -> dict:
    return dict(_state)


def load() -> dict:
    """Load the model once. Safe to call from a background thread at start.

    Returns the resulting state ({"on": bool, "reason": str}).
    """
    global _model
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception:
        _state.update(on=False, reason="speech-to-text isn't installed (pip install faster-whisper)")
        return state()
    device = (os.environ.get("DND_STT_DEVICE") or "cpu").strip().lower()
    tries = [("cuda", "float16"), ("cpu", "int8")] if device in ("cuda", "auto") else [("cpu", "int8")]
    last = ""
    for dev, ctype in tries:
        try:
            _model = WhisperModel(model_name(), device=dev, compute_type=ctype)
            _state.update(on=True, reason="")
            return state()
        except Exception as e:  # missing CUDA libraries, no disk space, no network on first run
            last = f"{type(e).__name__}: {str(e)[:120]}"
    _state.update(on=False, reason=f"the speech model didn't load ({last})")
    return state()


def transcribe(audio: bytes, hint_words: Optional[Iterable[str]] = None) -> str:
    """Turn one recorded clip (webm/opus, mp4/aac, wav, …) into text.

    `hint_words` (party and NPC names) go in the prompt so Whisper spells
    "Anony" and "Vac'uum" the table's way rather than as common words.
    Raises RuntimeError when speech-to-text is off.
    """
    if _model is None or not _state["on"]:
        raise RuntimeError(_state["reason"] or "speech-to-text is off")
    if not audio:
        return ""
    prompt = None
    words = [w for w in (hint_words or []) if w]
    if words:
        prompt = "Names at this table: " + ", ".join(dict.fromkeys(words)) + "."
    with _lock:
        segments, _info = _model.transcribe(
            io.BytesIO(audio),
            language="en" if model_name().endswith(".en") else None,
            beam_size=5,
            vad_filter=True,                 # drop the silence before and after
            initial_prompt=prompt,
            condition_on_previous_text=False,
        )
        parts = []
        for seg in segments:
            if seg.start > MAX_SECONDS:
                break
            parts.append(seg.text)
    return " ".join(p.strip() for p in parts).strip()
