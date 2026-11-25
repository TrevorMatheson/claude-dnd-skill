#!/usr/bin/env python3
"""The phone's tap-to-move, on the server (phone sheet spec D13, AC14).

The seam between the phone sheet and the battle map. Everything the Map tab
needs from the map goes through here, so when the battle map's interface
moves, only this file changes.

  available()  True when battlemap.check_move exists (battle map milestone 1;
               it is on its own branch until that merges).
  check(campaign_dir, character, x, y, dash=False)
               The battle map's dry run for this character's token, from the
               player's point of view: tokens the players can't see (hidden)
               are taken off a copy of the map first, so neither their name
               nor their square leaks. Never changes the map. Returns only
               {ok, cost, budget, left, path, reason, message}.
  move_text(result, x, y)
               The action the phone sends for Claude when the move is OK:
               "moves to square 6,1 - 25 of 35 ft". Claude still moves the
               token through the turn call (battle map D4, D13).
"""
from __future__ import annotations

import copy
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.normpath(os.path.join(_HERE, "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

FIELDS = ("ok", "cost", "budget", "left", "path", "reason", "message")


class MoveError(Exception):
    """Why a check couldn't run (no map, no token). `status` is the HTTP code."""

    def __init__(self, message: str, status: int = 404):
        super().__init__(message)
        self.status = status


def _bm():
    import battlemap
    return battlemap


def available() -> bool:
    try:
        return callable(getattr(_bm(), "check_move", None))
    except Exception:
        return False


def _token_name(m: dict, character: str):
    """The map's name for this character's token (case-insensitive; a full
    sheet name also matches its first word), or None."""
    key = " ".join(str(character).split()).lower()
    names = list((m.get("tokens") or {}).keys())
    for n in names:
        if n.lower() == key:
            return n
    first = [n for n in names if n.lower() == key.split(" ")[0] or n.lower().split(" ")[0] == key]
    return first[0] if len(first) == 1 else None


def check(campaign_dir, character: str, x: int, y: int, dash: bool = False) -> dict:
    if not available():
        raise MoveError("the battle map's move check isn't installed", 501)
    bm = _bm()
    if campaign_dir is None:
        raise MoveError("no campaign is running")
    try:
        m = bm.load(campaign_dir)
    except Exception as e:
        raise MoveError(f"no battle map: {e}")
    # Only the tokens the players can see, plus the mover's own (a hiding
    # rogue still moves). The name is looked up among those, so asking about
    # a hidden creature answers exactly like asking about nobody.
    name = _token_name(m, character)
    toks = m.get("tokens") or {}
    if name is not None and toks[name].get("hidden") and str(toks[name].get("kind")) != "pc":
        name = None
    if name is None:
        raise MoveError(f"{character} has no token on the map")
    seen = copy.deepcopy(m)
    seen["tokens"] = {n: t for n, t in (seen.get("tokens") or {}).items()
                      if n == name or not (isinstance(t, dict) and t.get("hidden"))}
    out = bm.check_move(seen, name, int(x), int(y), dash=bool(dash))
    return {k: out.get(k) for k in FIELDS}


def move_text(result: dict, x: int, y: int) -> str:
    return f"moves to square {int(x)},{int(y)} - {result.get('cost')} of {result.get('budget')} ft"
