"""Place images for the TV background (scene backgrounds spec D1, D5, D14).

A place's picture lives at <campaign>/scenes/<slug>.png. Standard library only.
"""
from __future__ import annotations

import re
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
    return (best[1], "user") if best else None


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
