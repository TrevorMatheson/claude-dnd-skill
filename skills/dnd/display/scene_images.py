"""Place images for the TV background (scene backgrounds spec D1, D5, D14).

A place's picture lives at <campaign>/scenes/<slug>.png. Standard library only.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

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
