#!/usr/bin/env python3
"""
battlemap.py — token positions on a square grid, for the TV battle map.

D&D Beyond Maps has no API, so a map there can't follow a fight turn by turn.
This keeps the positions here instead: `<campaign>/battlemap.json` holds the
grid, walls and obstacles, an optional background image, and every token's
square. combat.py's `map`, `place`, `move`, `turn`, `dist` and `attack --from`
use it, and each change is pushed to the display's map panel.

Grid rules (5e, both rulesets as played on a grid): one square is 5 ft, every
step — diagonal included — costs 5 ft, difficult terrain costs double, walls
and obstacles block movement. A move is checked by the cheapest path around
them, so a token cannot slip through a wall corner-to-corner.

Layout text (one character per square, used by `map init --layout FILE`):
    #   wall              T   obstacle (table, crate, statue) — blocks movement
    .   floor             ~   difficult terrain (rubble, water, undergrowth)
    D   door (open)       (space) outside the map — blocks movement
A letter or digit other than D/T places a start marker, bound to a token by a
legend line such as `H = Havoc` when `map init --place` is given. An UPPERCASE
marker is a player character, a lowercase one (or a digit) a monster or NPC:
    H = Havoc      r = Giant Rat      s = Giant Rat 2

battlemap.json:
    {"grid": {"w": 20, "h": 12, "ft": 5}, "cells": ["####...", ...],
     "image": "maps/cellar.png" | null, "round": 1,
     "tokens": {"Havoc": {"x": 3, "y": 4, "kind": "pc", "speed": 30,
                          "moved": 0, "size": 1}}}
"""
from __future__ import annotations

import heapq
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from utf8io import read_text, write_text as write_keep_eol  # noqa: E402

BLOCKING = set("#T ")
DIFFICULT = set("~")
FT = 5

# (dx, dy) for the 8 neighbours
_STEPS = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]


class MapError(ValueError):
    """A request the map refuses (off the grid, blocked, too far)."""


# ── storage ──────────────────────────────────────────────────────────────────

def map_path(campaign_dir) -> pathlib.Path:
    return pathlib.Path(campaign_dir) / "battlemap.json"


def load(campaign_dir) -> dict:
    p = map_path(campaign_dir)
    if not p.exists():
        raise MapError(f"no battle map for this campaign yet — run `combat.py map init` ({p})")
    return json.loads(read_text(p))


def save(campaign_dir, m: dict) -> None:
    write_keep_eol(map_path(campaign_dir), json.dumps(m, indent=1, ensure_ascii=False) + "\n")


def new_map(w: int, h: int, cells: list | None = None, image: str | None = None) -> dict:
    if not (1 <= w <= 80 and 1 <= h <= 80):
        raise MapError("a map is 1–80 squares on each side")
    if cells is None:
        cells = ["." * w for _ in range(h)]
    return {"grid": {"w": w, "h": h, "ft": FT}, "cells": cells, "image": image,
            "round": 1, "tokens": {}}


def parse_layout(text: str) -> tuple:
    """(cells, markers, legend) from a layout drawing.

    Lines after a blank line, or lines like `a = Havoc`, form the legend.
    Rows are padded with spaces (outside the map) to the widest row."""
    rows, legend = [], {}
    for line in text.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^\s*([A-Za-z0-9])\s*=\s*(.+?)\s*$", line)
        if m:
            legend[m.group(1)] = m.group(2)
            continue
        if line.strip() == "" and rows:
            continue
        if line.strip():
            rows.append(line.rstrip())
    if not rows:
        raise MapError("layout has no rows")
    w = max(len(r) for r in rows)
    markers = {}
    cells = []
    for y, r in enumerate(rows):
        r = r.ljust(w)
        out = []
        for x, ch in enumerate(r):
            if ch in "#.~T D":
                out.append(ch)
            elif ch.isalnum():
                markers[ch] = (x, y)
                out.append(".")
            else:
                out.append(".")
        cells.append("".join(out))
    return cells, markers, legend


# ── geometry ─────────────────────────────────────────────────────────────────

def cell(m: dict, x: int, y: int) -> str:
    g = m["grid"]
    if not (0 <= x < g["w"] and 0 <= y < g["h"]):
        return " "
    row = m["cells"][y] if y < len(m["cells"]) else ""
    return row[x] if x < len(row) else " "


def distance_ft(a: tuple, b: tuple) -> int:
    """Straight-line grid distance (every diagonal 5 ft) — for reach and range."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1])) * FT


def _occupied(m: dict, ignore: str) -> dict:
    return {(t["x"], t["y"]): n for n, t in m["tokens"].items() if n != ignore}


def path_cost_ft(m: dict, start: tuple, goal: tuple, mover: str = "", kind: str = "") -> int | None:
    """Cheapest movement cost from start to goal around walls, obstacles and
    hostile creatures, or None if there is no way. Allies can be passed through
    but not ended on; difficult terrain costs double; a diagonal step may not
    cut the corner of a wall."""
    if cell(m, *goal) in BLOCKING:
        return None
    occ = _occupied(m, mover)
    if goal in occ:
        return None
    best = {start: 0}
    heap = [(0, start)]
    while heap:
        cost, (x, y) = heapq.heappop(heap)
        if (x, y) == goal:
            return cost
        if cost > best.get((x, y), 1 << 30):
            continue
        for dx, dy in _STEPS:
            nx, ny = x + dx, y + dy
            c = cell(m, nx, ny)
            if c in BLOCKING:
                continue
            if dx and dy and (cell(m, x + dx, y) == "#" or cell(m, x, y + dy) == "#"):
                continue      # no squeezing between two wall squares
            other = occ.get((nx, ny))
            if other is not None and kind and m["tokens"][other].get("kind") != kind:
                continue      # can't move through a hostile creature's space
            step = FT * (2 if c in DIFFICULT else 1)
            nc = cost + step
            if nc < best.get((nx, ny), 1 << 30):
                best[(nx, ny)] = nc
                heapq.heappush(heap, (nc, (nx, ny)))
    return None


# ── tokens ───────────────────────────────────────────────────────────────────

def token(m: dict, name: str) -> tuple:
    """(canonical name, token) — names match case-insensitively."""
    for n, t in m["tokens"].items():
        if n.lower() == name.lower():
            return n, t
    raise MapError(f"no token named {name!r} on the map (have: {', '.join(m['tokens']) or 'none'})")


def place(m: dict, name: str, x: int, y: int, kind: str = "npc", speed: int = 30,
          size: int = 1) -> dict:
    if cell(m, x, y) in BLOCKING:
        raise MapError(f"({x},{y}) is a wall, obstacle or off the map")
    occ = _occupied(m, "")
    if (x, y) in occ and occ[(x, y)].lower() != name.lower():
        raise MapError(f"({x},{y}) is taken by {occ[(x, y)]}")
    for n in list(m["tokens"]):
        if n.lower() == name.lower():
            del m["tokens"][n]
    m["tokens"][name] = {"x": x, "y": y, "kind": kind, "speed": speed, "moved": 0, "size": size}
    return m["tokens"][name]


def move(m: dict, name: str, x: int, y: int, dash: bool = False, force: bool = False) -> dict:
    """Move a token, spending movement. Returns {"name","from","to","cost","left"}.
    `dash` doubles the speed for this turn; `force` skips the checks (forced
    movement such as a shove or Push mastery, or a DM ruling)."""
    n, t = token(m, name)
    start = (t["x"], t["y"])
    if force:
        if cell(m, x, y) in BLOCKING:
            raise MapError(f"({x},{y}) is a wall, obstacle or off the map")
        cost = 0
    else:
        cost = path_cost_ft(m, start, (x, y), mover=n, kind=t.get("kind", ""))
        if cost is None:
            raise MapError(f"{n} can't reach ({x},{y}) — blocked or occupied")
        budget = t.get("speed", 30) * (2 if dash else 1) - t.get("moved", 0)
        if cost > budget:
            raise MapError(f"{n} needs {cost} ft to reach ({x},{y}) but has {max(budget, 0)} ft "
                           f"of movement left (speed {t.get('speed', 30)}"
                           f"{', dashing' if dash else ''})")
        t["moved"] = t.get("moved", 0) + cost
    t["x"], t["y"] = x, y
    left = t.get("speed", 30) * (2 if dash else 1) - t.get("moved", 0)
    return {"name": n, "from": start, "to": (x, y), "cost": cost, "left": max(left, 0)}


def set_hp(m: dict, name: str, cur: int, mx: int | None = None) -> dict:
    """Record a token's hit points for its ring on the map (monsters; PCs
    take theirs from the party sidebar)."""
    _, t = token(m, name)
    old = t.get("hp") or [cur, mx or cur]
    t["hp"] = [max(0, cur), mx if mx is not None else old[1]]
    return t


def start_turn(m: dict, name: str) -> None:
    """A creature's turn starts: its movement is fresh."""
    _, t = token(m, name)
    t["moved"] = 0


def remove(m: dict, name: str) -> None:
    n, _ = token(m, name)
    del m["tokens"][n]


def attack_range(m: dict, attacker: str, target: str, reach: int | None = None,
                 rng: tuple | None = None) -> dict:
    """Check an attack's reach or range from positions.

    Returns {"distance", "ok", "disadvantage", "why"}. Melee: target within
    `reach` ft. Ranged (`rng` = (normal, long)): beyond long range is
    impossible, beyond normal range is at disadvantage, and so is a ranged
    attack made with a hostile creature within 5 ft of the attacker."""
    an, a = token(m, attacker)
    tn, t = token(m, target)
    dist = distance_ft((a["x"], a["y"]), (t["x"], t["y"]))
    out = {"distance": dist, "ok": True, "disadvantage": False, "why": ""}
    if rng:
        normal, long_ = rng
        if dist > long_:
            out.update(ok=False, why=f"{tn} is {dist} ft away, beyond long range {long_} ft")
        elif dist > normal:
            out.update(disadvantage=True, why=f"{dist} ft is beyond normal range {normal} ft")
        adjacent = [n for n, o in m["tokens"].items()
                    if n not in (an,) and o.get("kind") != a.get("kind")
                    and distance_ft((a["x"], a["y"]), (o["x"], o["y"])) <= FT]
        if out["ok"] and adjacent:
            out.update(disadvantage=True,
                       why=(out["why"] + "; " if out["why"] else "") +
                       f"hostile within 5 ft ({', '.join(adjacent)})")
    else:
        r = reach or FT
        if dist > r:
            out.update(ok=False, why=f"{tn} is {dist} ft away, out of {r}-ft reach")
    return out


# ── display ──────────────────────────────────────────────────────────────────

def display_payload(m: dict, hp: dict | None = None, conditions: dict | None = None) -> dict:
    """The /stats "map" payload: grid, cells, image, round and tokens."""
    toks = []
    for n, t in m["tokens"].items():
        d = {"name": n, "x": t["x"], "y": t["y"], "kind": t.get("kind", "npc"),
             "size": t.get("size", 1)}
        if hp and n in hp:
            d["hp"] = hp[n]
        elif t.get("hp"):
            d["hp"] = t["hp"]
        if conditions and conditions.get(n):
            d["conditions"] = conditions[n]
        toks.append(d)
    return {"grid": m["grid"], "cells": m["cells"], "image": m.get("image"),
            "round": m.get("round", 1), "tokens": toks}


def render_ascii(m: dict) -> str:
    """The map as text, tokens as the first letter of their name."""
    grid = [list(r.ljust(m["grid"]["w"])) for r in m["cells"]]
    legend = []
    for n, t in m["tokens"].items():
        ch = n[0].upper() if t.get("kind") == "pc" else n[0].lower()
        if 0 <= t["y"] < len(grid) and 0 <= t["x"] < len(grid[t["y"]]):
            grid[t["y"]][t["x"]] = ch
        legend.append(f"  {ch} {n} ({t['x']},{t['y']}) moved {t.get('moved', 0)}/{t.get('speed', 30)} ft")
    head = "   " + "".join(str(x % 10) for x in range(m["grid"]["w"]))
    rows = [f"{y:2d} " + "".join(r) for y, r in enumerate(grid)]
    return "\n".join([head] + rows + legend)
