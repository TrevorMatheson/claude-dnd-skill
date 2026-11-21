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
    Optional (older files without them load with the defaults):
      token "hidden": true      -- never sent to a screen until revealed (D10)
      token "portrait": "maps/tokens/havoc.png"
      "fog": {"trail": [[x,y]...], "show": [[x,y]...], "hide": [[x,y]...]}
        trail = squares PCs have stood on or passed through (the TV dims
        them as "seen earlier"); show / hide = the DM's manual overrides.
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
# A new battle map is at most this big (spec D7): the whole map always shows
# on the TV, and a square must stay readable from the couch. Older saves up to
# new_map's 80×80 still load.
MAX_W, MAX_H = 30, 20

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


def check_size(w: int, h: int) -> None:
    """Refuse a new map over MAX_W×MAX_H (raises MapError)."""
    if w > MAX_W or h > MAX_H:
        raise MapError(f"A battle map is at most {MAX_W} squares wide and {MAX_H} tall; "
                       f"this layout is {w}×{h}. Split the fight into areas or simplify the drawing.")


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


def find_path(m: dict, start: tuple, goal: tuple, mover: str = "", kind: str = "",
              ignore_walls: bool = False, ignore_creatures: bool = False):
    """(cost_ft, path) for the cheapest route from start to goal around walls,
    obstacles and hostile creatures, or None if there is no way. `path` is the
    list of squares from start to goal, both included. Allies can be passed
    through but not ended on; difficult terrain costs double; a diagonal step
    may not cut the corner of a wall. `ignore_walls` / `ignore_creatures` drop
    those obstacles (used to explain why a route is long)."""
    start, goal = tuple(start), tuple(goal)
    blocking = set(" ") if ignore_walls else BLOCKING
    if cell(m, *goal) in blocking:
        return None
    occ = _occupied(m, mover)
    if goal in occ:
        return None
    # Equal-cost routes are told apart by their number of diagonal steps, so
    # a straight walk comes back straight (a sliding token doesn't wobble).
    best = {start: (0, 0)}
    prev = {}
    heap = [(0, 0, start)]
    while heap:
        cost, diag, (x, y) = heapq.heappop(heap)
        if (x, y) == goal:
            path = [goal]
            while path[-1] != start:
                path.append(prev[path[-1]])
            return cost, path[::-1]
        if (cost, diag) > best.get((x, y), (1 << 30, 0)):
            continue
        for dx, dy in _STEPS:
            nx, ny = x + dx, y + dy
            c = cell(m, nx, ny)
            if c in blocking:
                continue
            if dx and dy and not ignore_walls and (cell(m, x + dx, y) == "#" or cell(m, x, y + dy) == "#"):
                continue      # no squeezing between two wall squares
            other = occ.get((nx, ny))
            if (other is not None and kind and not ignore_creatures
                    and m["tokens"][other].get("kind") != kind):
                continue      # can't move through a hostile creature's space
            step = FT * (2 if c in DIFFICULT else 1)
            key = (cost + step, diag + (1 if dx and dy else 0))
            if key < best.get((nx, ny), (1 << 30, 0)):
                best[(nx, ny)] = key
                prev[(nx, ny)] = (x, y)
                heapq.heappush(heap, (key[0], key[1], (nx, ny)))
    return None


def path_cost_ft(m: dict, start: tuple, goal: tuple, mover: str = "", kind: str = "") -> int | None:
    """Cheapest movement cost from start to goal (see find_path), or None."""
    r = find_path(m, start, goal, mover, kind)
    return r[0] if r else None


# ── tokens ───────────────────────────────────────────────────────────────────

def token(m: dict, name: str) -> tuple:
    """(canonical name, token) — names match case-insensitively."""
    for n, t in m["tokens"].items():
        if n.lower() == name.lower():
            return n, t
    raise MapError(f"no token named {name!r} on the map (have: {', '.join(m['tokens']) or 'none'})")


_PORTRAIT_RE = re.compile(r"[\w\-./ ]{1,120}")


def place(m: dict, name: str, x: int, y: int, kind: str = "npc", speed: int = 30,
          size: int = 1, hidden: bool = False, portrait: str | None = None) -> dict:
    if cell(m, x, y) in BLOCKING:
        raise MapError(f"({x},{y}) is a wall, obstacle or off the map")
    occ = _occupied(m, "")
    if (x, y) in occ and occ[(x, y)].lower() != name.lower():
        raise MapError(f"({x},{y}) is taken by {occ[(x, y)]}")
    for n in list(m["tokens"]):
        if n.lower() == name.lower():
            del m["tokens"][n]
    if portrait is not None and (not _PORTRAIT_RE.fullmatch(portrait) or ".." in portrait
                                 or portrait.startswith("/")
                                 or not portrait.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))):
        raise MapError(f"portrait {portrait!r} must be an image path inside the campaign's maps/ "
                       "folder, e.g. maps/tokens/havoc.png")
    t = {"x": x, "y": y, "kind": kind, "speed": speed, "moved": 0, "size": size}
    if hidden:
        t["hidden"] = True
    if portrait:
        t["portrait"] = portrait
    m["tokens"][name] = t
    if kind == "pc":
        _add_trail(m, [(x, y)])
    return t


def set_hidden(m: dict, name: str, hidden: bool) -> dict:
    """Hide a token from every screen (invisible, hiding, disguised) or
    reveal it again. Claude still sees it in `map show`."""
    _, t = token(m, name)
    if hidden:
        t["hidden"] = True
    else:
        t.pop("hidden", None)
    return t


# ── fog ──────────────────────────────────────────────────────────────────────

FOG_MODES = ("reveal", "hide", "auto")


def fog_of(m: dict) -> dict:
    """The map's fog record, created empty for an older file."""
    f = m.get("fog")
    if not isinstance(f, dict):
        f = m["fog"] = {}
    for k in ("trail", "show", "hide"):
        if not isinstance(f.get(k), list):
            f[k] = []
    return f


def _add_trail(m: dict, squares) -> None:
    f = fog_of(m)
    have = {tuple(p) for p in f["trail"] if isinstance(p, (list, tuple)) and len(p) == 2}
    cap = m["grid"]["w"] * m["grid"]["h"]
    for x, y in squares:
        if (x, y) not in have and len(f["trail"]) < cap:
            have.add((x, y))
            f["trail"].append([x, y])


def set_fog(m: dict, mode: str, x1: int, y1: int, x2: int | None = None, y2: int | None = None) -> int:
    """Manual fog over the rectangle (x1,y1)-(x2,y2): `reveal` shows it
    whatever the PCs can see (scrying, a trapdoor), `hide` darkens it, `auto`
    hands it back to line of sight. Returns the number of squares."""
    if mode not in FOG_MODES:
        raise MapError(f"fog mode is one of {', '.join(FOG_MODES)}, not {mode!r}")
    x2 = x1 if x2 is None else x2
    y2 = y1 if y2 is None else y2
    g = m["grid"]
    xs = range(max(0, min(x1, x2)), min(g["w"] - 1, max(x1, x2)) + 1)
    ys = range(max(0, min(y1, y2)), min(g["h"] - 1, max(y1, y2)) + 1)
    rect = {(x, y) for y in ys for x in xs}
    if not rect:
        raise MapError(f"({x1},{y1})-({x2},{y2}) is off the map")
    f = fog_of(m)
    for key in ("show", "hide"):
        f[key] = [p for p in f[key] if tuple(p) not in rect]
    if mode != "auto":
        key = "show" if mode == "reveal" else "hide"
        f[key].extend([x, y] for y in ys for x in xs)
    return len(rect)


def clear_fog(m: dict) -> None:
    """`fog auto --all`: drop every manual override (the trail stays)."""
    f = fog_of(m)
    f["show"], f["hide"] = [], []


def hp_state(cur, mx) -> str | None:
    """A monster's rough health for the screens (D8): healthy above half,
    bloodied down to a quarter, near_death at a quarter or less."""
    try:
        cur, mx = float(cur), float(mx)
    except (TypeError, ValueError):
        return None
    if mx <= 0:
        return None
    frac = cur / mx
    if frac > 0.5:
        return "healthy"
    if frac > 0.25:
        return "bloodied"
    return "near_death"


def check_move(m: dict, name: str, x: int, y: int, dash: bool = False) -> dict:
    """Dry run of a move: never changes the map. Returns
    {"ok", "name", "from", "to", "cost", "budget", "left", "straight_ft", "path",
     "reason", "occupant", "detour_by", "difficult_ft", "message"}.
    reason is None or one of off_map, blocked, occupied, no_path, too_far;
    detour_by ("walls" | "creatures" | None) says what makes the route longer
    than the straight line; difficult_ft is the extra feet difficult terrain
    costs on the route. The message is one line to tell the player."""
    n, t = token(m, name)
    start = (t["x"], t["y"])
    goal = (int(x), int(y))
    speed = t.get("speed", 30)
    budget = max(speed * (2 if dash else 1) - t.get("moved", 0), 0)
    out = {"ok": False, "name": n, "from": list(start), "to": list(goal), "cost": None,
           "budget": budget, "left": budget, "straight_ft": distance_ft(start, goal),
           "path": [], "reason": None, "occupant": None, "detour_by": None,
           "difficult_ft": 0, "message": ""}
    g = m["grid"]
    where = f"({goal[0]},{goal[1]})"
    if not (0 <= goal[0] < g["w"] and 0 <= goal[1] < g["h"]) or cell(m, *goal) == " ":
        out.update(reason="off_map", message=f"{where} is off the map")
        return out
    if cell(m, *goal) in BLOCKING:
        what = "a wall" if cell(m, *goal) == "#" else "an obstacle"
        out.update(reason="blocked", message=f"{where} is {what} — {n} can't stand there")
        return out
    occ = _occupied(m, n)
    if goal in occ:
        out.update(reason="occupied", occupant=occ[goal],
                   message=f"{where} is taken by {occ[goal]}")
        return out
    kind = t.get("kind", "")
    found = find_path(m, start, goal, n, kind)
    if found is None:
        out.update(reason="no_path", message=f"{n} has no way to reach {where} — blocked or walled off")
        return out
    cost, path = found
    out["cost"], out["path"] = cost, [list(p) for p in path]
    out["difficult_ft"] = sum(FT for p in path[1:] if cell(m, *p) in DIFFICULT)
    if cost > out["straight_ft"]:
        # Whichever obstacle, taken away, shortens the route more is the
        # reason; creatures win a tie (ignoring walls also lets a route step
        # round a creature through the wall, which says nothing about walls).
        no_crit = find_path(m, start, goal, n, kind, ignore_creatures=True)
        no_wall = find_path(m, start, goal, n, kind, ignore_walls=True)
        c_cost = no_crit[0] if no_crit else cost
        w_cost = no_wall[0] if no_wall else cost
        if c_cost < cost and c_cost <= w_cost:
            out["detour_by"] = "creatures"
        elif w_cost < cost:
            out["detour_by"] = "walls"
    why = []
    if out["detour_by"] == "walls":
        why.append(f"walls block the direct route ({out['straight_ft']} ft straight)")
    elif out["detour_by"] == "creatures":
        why.append(f"hostile creatures block the direct route ({out['straight_ft']} ft straight)")
    if out["difficult_ft"]:
        why.append(f"difficult terrain adds {out['difficult_ft']} ft")
    tail = f"; {'; '.join(why)}" if why else ""
    if cost > budget:
        out.update(reason="too_far",
                   message=f"{n} needs {cost} ft to reach {where} but has {budget} ft "
                           f"of movement left (speed {speed}{', dashing' if dash else ''}){tail}")
        return out
    out.update(ok=True, left=budget - cost,
               message=f"{n} ({start[0]},{start[1]}) → {where}: {cost} ft, "
                       f"{budget - cost} ft left{tail}")
    return out


def move(m: dict, name: str, x: int, y: int, dash: bool = False, force: bool = False) -> dict:
    """Move a token, spending movement. Returns {"name","from","to","cost","left","path"}.
    `dash` doubles the speed for this turn; `force` skips the checks (forced
    movement such as a shove or Push mastery, or a DM ruling)."""
    n, t = token(m, name)
    start = (t["x"], t["y"])
    path = [start, (x, y)]
    if force:
        if cell(m, x, y) in BLOCKING:
            raise MapError(f"({x},{y}) is a wall, obstacle or off the map")
        cost = 0
    else:
        r = check_move(m, n, x, y, dash=dash)
        if not r["ok"]:
            raise MapError(r["message"])
        cost = r["cost"]
        path = [tuple(p) for p in r["path"]]
        t["moved"] = t.get("moved", 0) + cost
    t["x"], t["y"] = x, y
    if t.get("kind") == "pc":
        _add_trail(m, [(x, y)] if force else path)
    left = t.get("speed", 30) * (2 if dash else 1) - t.get("moved", 0)
    return {"name": n, "from": start, "to": (x, y), "cost": cost, "left": max(left, 0),
            "path": path}


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


# ── spell areas ──────────────────────────────────────────────────────────────

AREA_SHAPES = ("sphere", "cylinder", "cube", "cone", "line")


def _area_point(m: dict, v, what: str) -> tuple:
    """A token name or an [x, y] square, as the square's centre."""
    if isinstance(v, str):
        _, t = token(m, v)
        return t["x"] + 0.5, t["y"] + 0.5
    if (isinstance(v, (list, tuple)) and len(v) == 2
            and all(type(c) is int for c in v)):
        return v[0] + 0.5, v[1] + 0.5
    raise MapError(f"area {what} must be a token name or an [x, y] square, not {v!r}")


def area_squares(m: dict, area: dict) -> list:
    """The squares a spell area covers, as [[x, y], ...] (D14).

    sphere / cylinder: centred on the centre of square `at`, radius `size` ft.
    cube: top-left square `at`, side `size` ft.
    cone: from `from` (token or square) toward `to`, length `size`, as wide as
    it is long at its end (half-angle atan 1/2). line: from `from` toward
    `to`, length `size`, `width` ft wide (default 5). A square is in when its
    centre lies inside the shape; wall squares and squares off the map never
    are. Raises MapError for a bad area or one with no square on the map."""
    if not isinstance(area, dict):
        raise MapError("area must be an object like {\"shape\": \"sphere\", \"size\": 20, \"at\": [x, y]}")
    shape = area.get("shape")
    if shape not in AREA_SHAPES:
        raise MapError(f"area shape is one of {', '.join(AREA_SHAPES)}, not {shape!r}")
    size = area.get("size")
    if type(size) is not int or not (5 <= size <= 150):
        raise MapError(f"area size is 5-150 ft, not {size!r}")
    n = size / FT
    g = m["grid"]
    inside = []
    if shape in ("sphere", "cylinder", "cube"):
        if "at" not in area:
            raise MapError(f"a {shape} needs \"at\": [x, y]")
        cx, cy = _area_point(m, area["at"], "at")
        if shape == "cube":
            x0, y0 = cx - 0.5, cy - 0.5
            test = lambda px, py: x0 <= px < x0 + n and y0 <= py < y0 + n
        else:
            test = lambda px, py: (px - cx) ** 2 + (py - cy) ** 2 <= n * n + 1e-9
    else:
        if "from" not in area or "to" not in area:
            raise MapError(f"a {shape} needs \"from\" (a token or square) and \"to\" (a square or token)")
        ox, oy = _area_point(m, area["from"], "from")
        tx, ty = _area_point(m, area["to"], "to")
        dx, dy = tx - ox, ty - oy
        length = (dx * dx + dy * dy) ** 0.5
        if length == 0:
            raise MapError(f"a {shape}'s \"to\" must differ from its \"from\"")
        ux, uy = dx / length, dy / length
        if shape == "cone":
            def test(px, py):
                ax, ay = px - ox, py - oy
                along = ax * ux + ay * uy
                perp = abs(-ax * uy + ay * ux)
                return 0 < along <= n + 1e-9 and perp <= along * 0.5 + 1e-9
        else:
            width = area.get("width", FT)
            if type(width) is not int or not (5 <= width <= 30):
                raise MapError(f"line width is 5-30 ft, not {width!r}")
            half = width / FT / 2

            def test(px, py):
                ax, ay = px - ox, py - oy
                along = ax * ux + ay * uy
                perp = abs(-ax * uy + ay * ux)
                return 0 < along <= n + 1e-9 and perp <= half + 1e-9
    for y in range(g["h"]):
        for x in range(g["w"]):
            if cell(m, x, y) in ("#", " "):
                continue
            if test(x + 0.5, y + 0.5):
                inside.append([x, y])
    if not inside:
        raise MapError("the area covers no square on the map")
    return inside


# ── display ──────────────────────────────────────────────────────────────────

def display_payload(m: dict, hp: dict | None = None, conditions: dict | None = None) -> dict:
    """The /stats "map" payload: grid, cells, image, round, fog and tokens.

    Hidden tokens are left out entirely (D10). Monsters carry only a rough
    `hp_state`, never their numbers (D8); a PC keeps `hp` only when set on
    its token (the TV prefers the party's HP). `conditions` maps a name
    (any case) to its condition list, e.g. from tracker.json."""
    conds = {str(k).lower(): v for k, v in (conditions or {}).items()}
    toks = []
    for n, t in m["tokens"].items():
        if t.get("hidden"):
            continue
        kind = t.get("kind", "npc")
        d = {"name": n, "x": t["x"], "y": t["y"], "kind": kind, "size": t.get("size", 1)}
        h = hp[n] if hp and n in hp else t.get("hp")
        if h:
            if kind == "pc":
                d["hp"] = h
            else:
                try:
                    st = hp_state(h[0], h[1])
                except (TypeError, IndexError, KeyError):
                    st = None
                if st:
                    d["hp_state"] = st
        if t.get("portrait"):
            d["portrait"] = t["portrait"]
        c = conds.get(n.lower())
        if c:
            d["conditions"] = list(c)
        toks.append(d)
    f = m.get("fog") if isinstance(m.get("fog"), dict) else {}
    fog = {k: [list(p) for p in f.get(k, []) if isinstance(p, (list, tuple)) and len(p) == 2]
           for k in ("trail", "show", "hide")}
    return {"grid": m["grid"], "cells": m["cells"], "image": m.get("image"),
            "round": m.get("round", 1), "fog": fog, "tokens": toks}


def render_ascii(m: dict) -> str:
    """The map as text, tokens as the first letter of their name."""
    grid = [list(r.ljust(m["grid"]["w"])) for r in m["cells"]]
    legend = []
    for n, t in m["tokens"].items():
        ch = n[0].upper() if t.get("kind") == "pc" else n[0].lower()
        if 0 <= t["y"] < len(grid) and 0 <= t["x"] < len(grid[t["y"]]):
            grid[t["y"]][t["x"]] = ch
        legend.append(f"  {ch} {n} ({t['x']},{t['y']}) moved {t.get('moved', 0)}/{t.get('speed', 30)} ft"
                      + (" (hidden)" if t.get("hidden") else ""))
    head = "   " + "".join(str(x % 10) for x in range(m["grid"]["w"]))
    rows = [f"{y:2d} " + "".join(r) for y, r in enumerate(grid)]
    return "\n".join([head] + rows + legend)
