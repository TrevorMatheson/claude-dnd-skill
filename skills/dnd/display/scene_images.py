"""Place images for the TV background (scene backgrounds spec D1, D4-D7, D14).

A place's picture lives at <campaign>/scenes/<slug>.png. Pictures the user
drops in are used as they are; missing ones are made by Gemini in the
background (one worker, one job at a time), saved once and reused. A picture
we made is recorded in scenes/generated.json by its hash, so a file the user
has since replaced counts as theirs and is never overwritten.

The laptop page's note (D7) comes from status(): generation off, no key, the
image quota used up, or the last failure. Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Optional
from urllib.parse import quote

SLUG_MAX = 60
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul",
                     *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def slug(name) -> Optional[str]:
    """A file-safe name for a place: lowercase, words joined by hyphens,
    letters in any script kept, at most 60 characters. None if nothing is
    left. "The Flooded Crypt" -> "the-flooded-crypt"."""
    if not isinstance(name, str):
        return None
    text = unicodedata.normalize("NFKC", name).casefold()
    out, dash = [], False
    for ch in text:
        if ch.isalnum():
            out.append(ch)
            dash = False
        elif out and not dash:
            out.append("-")
            dash = True
    s = "".join(out).strip("-")[:SLUG_MAX].strip("-")
    if not s:
        return None
    if s in _WINDOWS_RESERVED or re.fullmatch(r"(con|prn|aux|nul|com\d|lpt\d)", s):
        s = f"{s}-place"
    return s


NAME_MAX = 80
LOOK_MAX = 400
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")   # preference order on a tie


def clean_place(obj) -> Optional[dict]:
    """A place from a /stats post as {"name", "look", "renew"}, or None when
    it isn't a usable place (no name, nothing file-safe left in the name)."""
    if not isinstance(obj, dict):
        return None
    name = obj.get("name")
    if not isinstance(name, str):
        return None
    name = name.strip()
    if not name or len(name) > NAME_MAX or slug(name) is None:
        return None
    look = obj.get("look")
    look = look.strip()[:LOOK_MAX] if isinstance(look, str) else ""
    return {"name": name, "look": look, "renew": obj.get("renew") is True}


def scenes_dir(camp_dir) -> Path:
    return Path(camp_dir) / "scenes"


def find_image(camp_dir, place_slug: str) -> Optional[tuple]:
    """(path, source) of the picture for a place, or None. Files are matched
    leniently: "The Flooded Crypt.jpg" is the-flooded-crypt's picture. A
    campaign with no scenes/ folder simply has none (D14). Never raises."""
    if not place_slug or camp_dir is None:
        return None
    try:
        folder = scenes_dir(camp_dir)
        if not folder.is_dir():
            return None
        best = None
        for p in folder.iterdir():
            ext = p.suffix.lower()
            if ext not in IMAGE_EXTS or not p.is_file():
                continue
            if slug(p.stem) != place_slug:
                continue
            rank = (IMAGE_EXTS.index(ext), p.stem != place_slug, p.name)
            if best is None or rank < best[0]:
                best = (rank, p)
    except OSError:
        return None
    if not best:
        return None
    return best[1], ("generated" if is_generated(camp_dir, place_slug, best[1]) else "user")


def image_url(path) -> str:
    """/scene-image/<file>?v=<mtime>: the version changes when the file does,
    so a replaced picture isn't served from the browser's cache."""
    p = Path(path)
    try:
        v = int(p.stat().st_mtime)
    except OSError:
        v = 0
    return f"/scene-image/{quote(p.name)}?v={v}"


def sniff_mimetype(path) -> Optional[str]:
    """The image type from the file's first bytes (a Gemini JPEG may sit in a
    .png file), or None if it isn't an image we serve."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return None
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def resolve_file(camp_dir, name: str) -> Optional[Path]:
    """The file a /scene-image/<name> request may serve: a plain file name
    (no folders, no "..") with an image extension, inside scenes/."""
    if not isinstance(name, str) or not name or len(name) > 200:
        return None
    if "/" in name or "\\" in name or ".." in name or ":" in name or "\x00" in name:
        return None
    if Path(name).suffix.lower() not in IMAGE_EXTS:
        return None
    try:
        base = scenes_dir(camp_dir).resolve()
        path = (base / name).resolve()
        path.relative_to(base)
    except (OSError, ValueError, TypeError):
        return None
    return path if path.is_file() else None


# ── Pictures we made: scenes/generated.json ────────────────────────────────

MANIFEST = "generated.json"
_manifest_lock = threading.Lock()


def _sha256(path) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def read_manifest(camp_dir) -> dict:
    try:
        with open(scenes_dir(camp_dir) / MANIFEST, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def is_generated(camp_dir, place_slug: str, path) -> bool:
    """True when `path` is a picture we made for this place and nobody has
    changed it since (its hash is the one recorded)."""
    entry = read_manifest(camp_dir).get(place_slug)
    if not isinstance(entry, dict) or not entry.get("sha256"):
        return False
    return entry["sha256"] == _sha256(path)


def save_generated(camp_dir, place_slug: str, data: bytes, style: str) -> Path:
    """Write a made picture to scenes/<slug>.png (temp file, then replace) and
    record it in the manifest. Creates scenes/ if needed."""
    folder = scenes_dir(camp_dir)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{place_slug}.png"
    tmp = folder / f".{place_slug}.part"
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    with _manifest_lock:
        man = read_manifest(camp_dir)
        man[place_slug] = {"sha256": hashlib.sha256(data).hexdigest(),
                           "made": int(time.time()), "style": style}
        mtmp = folder / f".{MANIFEST}.part"
        mtmp.write_text(json.dumps(man, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(mtmp, folder / MANIFEST)
    return dest


# ── Why pictures aren't being made: the laptop note (D7) ───────────────────

ERROR_NOTE_SECONDS = 600
_state = {"quota_until": 0.0, "daily": False, "error": "", "error_until": 0.0}
_state_lock = threading.Lock()


def _end_of_day(now: float) -> float:
    lt = time.localtime(now)
    return now + 86400 - (lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec)


def quota_out(now: Optional[float] = None) -> bool:
    """True while a 429's window lasts: no requests are made until it ends."""
    now = time.time() if now is None else now
    with _state_lock:
        return now < _state["quota_until"]


def record_failure(name: str, exc: BaseException, now: Optional[float] = None) -> None:
    """Remember a failed request: a 429 opens a quota window; anything else
    is a note for ten minutes."""
    now = time.time() if now is None else now
    status_code = getattr(exc, "status", 0)
    with _state_lock:
        if status_code == 429:
            daily = "PerDay" in str(getattr(exc, "quota_id", "") or "")
            wait = float(getattr(exc, "retry_after", 0) or 0)
            if daily:
                _state["quota_until"] = now + wait if wait > 0 else _end_of_day(now)
            else:
                _state["quota_until"] = now + (wait if wait > 0 else 60.0)
            _state["daily"] = daily
            return
        reason = (str(exc) if hasattr(exc, "status") else "") or type(exc).__name__
        _state["error"] = f"Couldn't make the picture for {name} ({reason})"
        _state["error_until"] = now + ERROR_NOTE_SECONDS


def status(generate_on: bool, has_key: bool, now: Optional[float] = None) -> str:
    """The note for the laptop page (never the TV), or "" when all is well."""
    now = time.time() if now is None else now
    if not generate_on:
        return "Scene pictures are off — places show the mood layer only."
    if not has_key:
        return ("No Gemini key — places show the mood layer only. Add the voice's "
                "Gemini key to have pictures made.")
    with _state_lock:
        if now < _state["quota_until"] and _state["daily"]:
            hours = max(1, round((_state["quota_until"] - now) / 3600))
            return f"Gemini's image quota is used up — back in about {hours} h"
        if _state["error"] and now < _state["error_until"]:
            return _state["error"]
    return ""


# ── The worker: one job at a time, never blocking a request ────────────────

class Worker:
    """Makes pictures in the background. submit() returns at once; when a job
    ends, on_done(camp_dir, slug, url_or_None) is called on the worker thread.
    A place already queued or being made is not queued twice."""

    def __init__(self, on_done):
        self.on_done = on_done
        self.q = queue.Queue()
        self.busy = set()
        self.lock = threading.Lock()
        self.thread = None

    def submit(self, camp_dir, place_slug: str, name: str, look: str, style: str) -> None:
        key = (str(camp_dir), place_slug)
        with self.lock:
            if key in self.busy:
                return                      # already on its way
            self.busy.add(key)
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, name="scene-pictures", daemon=True)
                self.thread.start()
        self.q.put((camp_dir, place_slug, name, look, style))

    def pending(self, camp_dir, place_slug: str) -> bool:
        with self.lock:
            return (str(camp_dir), place_slug) in self.busy

    def _run(self):
        import scene_gen   # looked up per call, so scene_gen.generate can be replaced
        while True:
            camp_dir, place_slug, name, look, style = self.q.get()
            url = None
            try:
                if not quota_out():
                    data = scene_gen.generate(look, style)
                    if not data:
                        raise scene_gen.SceneGenError("Google sent no image")
                    url = image_url(save_generated(camp_dir, place_slug, data, style))
            except BaseException as exc:    # a job never kills the worker
                try:
                    record_failure(name, exc)
                except Exception:
                    pass
            finally:
                with self.lock:
                    self.busy.discard((str(camp_dir), place_slug))
            try:
                self.on_done(camp_dir, place_slug, url)
            except Exception:
                pass
