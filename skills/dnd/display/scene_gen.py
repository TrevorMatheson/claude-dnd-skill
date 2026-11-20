"""Gemini image requests for scene backgrounds (scene backgrounds spec D3, D4, D10).

Standard library only, like tts.py: no new dependency (AC10). The key is the
one the voice uses (tts._get_api_key). Every request asks for a wide 16:9
view of the place as the party sees it on arrival, and always says no people,
no creatures and no text (D3, AC5).

Nothing here runs on import. A request spends the user's Gemini image quota,
so callers make one only when told to (scene_trial.py --send).
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

import tts

GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"
_API = "https://generativelanguage.googleapis.com/v1beta/models"
ASPECT_RATIO = "16:9"          # the widest widescreen ratio the model offers (D10)
DEFAULT_TIMEOUT = 120.0

# Said in every request, whatever the place or style (D3, AC5).
RULES = ("No people, no creatures or animals, no text, letters, signs or "
         "writing anywhere in the image.")


class SceneGenError(Exception):
    """A failed image request. `status` is Google's HTTP status (0 if none);
    for a 429, `quota_id` names the exhausted quota and `retry_after` is
    Google's suggested wait in seconds. Never carries the key."""

    def __init__(self, msg: str, status: int = 0, quota_id: str = "", retry_after: float = 0.0):
        super().__init__(msg)
        self.status = status
        self.quota_id = quota_id
        self.retry_after = retry_after

    @property
    def daily_quota(self) -> bool:
        return self.status == 429 and "PerDay" in self.quota_id


def build_prompt(look: str, style: str) -> str:
    look = (look or "").strip() or "an unnamed place"
    style = (style or "").strip()
    parts = [
        f"A wide {ASPECT_RATIO} landscape view of this place, as travellers would "
        f"see it on arriving: {look}.",
        "Show the place itself, empty, as a background painting.",
    ]
    if style:
        parts.append(f"Art style: {style}.")
    parts.append(RULES)
    return " ".join(parts)


def build_request(look: str, style: str) -> dict:
    """The generateContent body for one scene image."""
    return {
        "contents": [{"role": "user", "parts": [{"text": build_prompt(look, style)}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": ASPECT_RATIO},
        },
    }


def _headers(key: str) -> dict:
    # The key goes in a header, never the URL, so it can't leak into a log line.
    return {"Content-Type": "application/json", "x-goog-api-key": key}


def generate(look: str, style: str, model: str = GEMINI_IMAGE_MODEL,
             timeout: float = DEFAULT_TIMEOUT) -> bytes:
    """Make one image and return its bytes (PNG or JPEG, as Google sends it).
    Raises SceneGenError for any failure."""
    key = tts._get_api_key()
    if not key:
        raise SceneGenError("no Gemini key is set up")
    req = urllib.request.Request(
        f"{_API}/{model}:generateContent",
        data=json.dumps(build_request(look, style)).encode("utf-8"),
        headers=_headers(key), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        quota_id, retry = tts._parse_quota(body) if e.code == 429 else ("", 0.0)
        raise SceneGenError(f"Google answered {e.code}", status=e.code,
                            quota_id=quota_id, retry_after=retry) from None
    except urllib.error.URLError as e:
        raise SceneGenError(f"couldn't reach Google ({e.reason})") from None
    except Exception as e:
        raise SceneGenError(f"request failed ({type(e).__name__})") from None
    return _image_from(raw)


def _image_from(raw: bytes) -> bytes:
    try:
        data = json.loads(raw)
        parts = data["candidates"][0]["content"]["parts"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise SceneGenError("Google sent no image") from None
    for p in parts:
        inline = p.get("inlineData") or p.get("inline_data") if isinstance(p, dict) else None
        if inline and inline.get("data"):
            try:
                img = base64.b64decode(inline["data"])
            except Exception:
                raise SceneGenError("Google sent an unreadable image") from None
            if img:
                return img
    raise SceneGenError("Google sent no image (it may have refused the prompt)")


def list_image_models(timeout: float = 20.0) -> list:
    """Names of the models this key can see that make images. A GET that
    spends no generation quota."""
    key = tts._get_api_key()
    if not key:
        raise SceneGenError("no Gemini key is set up")
    req = urllib.request.Request(f"{_API}?pageSize=1000", headers=_headers(key), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise SceneGenError(f"Google answered {e.code}", status=e.code) from None
    except urllib.error.URLError as e:
        raise SceneGenError(f"couldn't reach Google ({e.reason})") from None
    except ValueError:
        raise SceneGenError("Google sent an unreadable model list") from None
    names = []
    for m in data.get("models", []) or []:
        name = str(m.get("name", "")).split("/", 1)[-1]
        if "image" in name or name.startswith("imagen"):
            names.append(name)
    return names
