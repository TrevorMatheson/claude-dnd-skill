"""The themed battle-map board on /tv (battle-map spec, plan step S7).

AC1 — a text layout renders as themed tiles (floor, wall, door, difficult
      terrain, obstacle); the pure part is tileKind().
AC2 (TV) — in combat mode a 30x20 layout fits its ~60% area with no panning;
      the pure part is fit().
AC3 (TV) — tokens show initials (or a portrait), an HP ring and condition
      icons; PCs show exact HP, monsters only a rough state; the current turn
      is highlighted. The pure part is tokenView().
The screenshots for AC1-AC3 are browser checks done in verify mode.

Contract assumed (plan, "The TV: shared map code in static/map/"), file
skills/dnd/display/static/map/board.js, attached to window.TV and exported to
`module` for node:

  fit(w, h, boxW, boxH, dpr) -> {size, x, y, ...}
      size  side of one square in CSS px; size*dpr is a whole number of
            backing pixels; the whole w x h grid fits inside boxW x boxH and
            the square is as large as fits (floor(min(boxW/w, boxH/h)), give
            or take a pixel).
      x, y  offsets of the grid inside the box; they centre it.
      Accepted key spellings: size|cell|square|cs, x|left|offsetX|ox|dx,
      y|top|offsetY|oy|dy.
  tileKind(ch) -> string. '#', '.', '~', 'T', 'D', ' ' give six distinct
      kinds; the same char always gives the same kind; an unknown letter (a
      token letter left in the layout) is floor.
  tokenView(token, players, current, mine) -> object with
      initials  first letters of up to two words, upper case
                ("Giant Rat" -> "GR", "Havoc" -> "H", as static/tv/map.js)
      ring      fraction 0..1 (keys ring|frac|fraction|hpFrac|ringFrac)
                exact cur/max for a PC; fixed steps for a monster's hp_state
      hpLabel   "cur/max" for a PC, empty/null for a monster
                (keys hpLabel|hpText|hp_label)
      icons     one entry per condition (keys icons|conditionIcons|conds)
      current   true for the turn holder (or a highlight class
                current|now|turn in `classes`)
      mine      passes through (or a `mine` class)
  `players` is stats.players; the test passes an array that also carries
  lower-case name keys, so either lookup style works. `current` is
  stats.turn_order.current (any case).

Box used for the combat-map column (static/tv/tv.css): at 1920x1080 the root
font is 16 px; .stage[data-mode=combat-map] .layer pads 28/40/36 px with a
30 px column gap and 60fr/40fr columns -> (1920-80-30)*0.6 = 1086 px wide;
the map spans rows 2-4 under the turn bar -> about 1016-22-64 = 930 px tall.
At 3840x2160 the root font is 32 px, so the same box is 2172x1860 CSS px at
dpr 1, or 1086x930 CSS px at dpr 2 when the TV scales 200%.

Skipped when node is not installed.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "skills" / "dnd" / "display"
BOARD_JS = DISPLAY / "static" / "map" / "board.js"
MAP_CSS = DISPLAY / "static" / "map" / "map.css"
TV_HTML = DISPLAY / "templates" / "tv.html"
NODE = shutil.which("node")

BOX_1080 = (1086, 930)
BOX_4K = (2172, 1860)

_PRELUDE = """
globalThis.window = globalThis.window || { TV: {} };
const BOARD = %(board)s;
function need(name) {
  const mod = require(BOARD);
  if (!mod || typeof mod[name] !== 'function') throw new Error('board.js does not export ' + name + '()');
  return mod[name];
}
function pick(o, keys, what) {
  for (const k of keys) if (o && Object.prototype.hasOwnProperty.call(o, k)) return o[k];
  throw new Error(what + ': none of ' + keys.join('|') + ' in ' + JSON.stringify(o));
}
function fitN(...a) {
  const r = need('fit')(...a);
  return { size: pick(r, ['size', 'cell', 'square', 'cs'], 'fit size'),
           x: pick(r, ['x', 'left', 'offsetX', 'ox', 'dx'], 'fit x'),
           y: pick(r, ['y', 'top', 'offsetY', 'oy', 'dy'], 'fit y') };
}
const tileKind = (...a) => need('tileKind')(...a);
function classesOf(v) {
  const c = v && v.classes;
  if (Array.isArray(c)) return c.map(String);
  if (typeof c === 'string') return c.split(/\\s+/).filter(Boolean);
  if (c && typeof c === 'object') return Object.keys(c).filter((k) => c[k]);
  return [];
}
function strings(v, acc) {
  acc = acc || [];
  if (typeof v === 'string') acc.push(v);
  else if (v && typeof v === 'object') for (const k of Object.keys(v)) strings(v[k], acc);
  return acc;
}
function viewN(...a) {
  const v = need('tokenView')(...a);
  if (!v || typeof v !== 'object') throw new Error('tokenView returned ' + JSON.stringify(v));
  const cls = classesOf(v);
  const has = (k) => Object.prototype.hasOwnProperty.call(v, k);
  const labelKey = ['hpLabel', 'hpText', 'hp_label'].find(has);
  const iconKey = ['icons', 'conditionIcons', 'conds'].find(has);
  return {
    raw: v,
    initials: pick(v, ['initials'], 'tokenView initials'),
    ring: pick(v, ['ring', 'frac', 'fraction', 'hpFrac', 'ringFrac'], 'tokenView ring'),
    hpLabel: labelKey ? v[labelKey] : null,
    icons: iconKey ? v[iconKey] : [],
    current: v.current === true || cls.some((c) => /^(current|now|turn)$/.test(c)),
    mine: v.mine === true || cls.includes('mine'),
    strings: strings(v),
  };
}
// stats.players as an array that also answers lower-case name lookups.
function players(list) {
  const arr = list.slice();
  for (const p of list) arr[String(p.name).toLowerCase()] = p;
  return arr;
}
const out = (v) => console.log(JSON.stringify(v));
"""


@unittest.skipUnless(NODE, "node is not installed")
class _NodeCase(unittest.TestCase):

    def run_js(self, body: str):
        if not BOARD_JS.exists():
            self.fail("not built yet: static/map/board.js")
        script = _PRELUDE % {"board": json.dumps(str(BOARD_JS))} + body
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True,
                               encoding="utf-8", timeout=30)
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        if r.returncode != 0:
            self.fail("node failed:\n" + (r.stderr or r.stdout)[-1500:])
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        self.assertTrue(lines, "scenario printed nothing")
        return json.loads(lines[-1])


# ── fit(): the whole map in its box, no panning (AC2 TV, D7) ──────────────

class TestFit(_NodeCase):

    def _fit(self, w, h, bw, bh, dpr):
        return self.run_js(f"out(fitN({w}, {h}, {bw}, {bh}, {dpr}));")

    def _assert_fits(self, r, w, h, bw, bh, dpr):
        size = r["size"]
        self.assertGreater(size, 0)
        self.assertAlmostEqual(size * dpr, round(size * dpr), places=6,
                               msg=f"square {size} CSS px is not whole backing pixels at dpr {dpr}")
        self.assertLessEqual(size * w, bw, "grid wider than the box")
        self.assertLessEqual(size * h, bh, "grid taller than the box")
        best = min(bw / w, bh / h)
        self.assertGreaterEqual(size, int(best) - 1, f"square {size} wastes the box (best {best:.1f})")
        self.assertGreaterEqual(r["x"], 0)
        self.assertGreaterEqual(r["y"], 0)
        self.assertLessEqual(r["x"] + size * w, bw)
        self.assertLessEqual(r["y"] + size * h, bh)

    # AC2 / D7 — 30x20 in the combat-map column at 1920x1080: an integer
    # square, the whole grid inside the box.
    def test_30x20_fits_combat_column_1080p(self):
        bw, bh = BOX_1080
        r = self._fit(30, 20, bw, bh, 1)
        self._assert_fits(r, 30, 20, bw, bh, 1)
        self.assertEqual(r["size"], int(r["size"]), "square size is not an integer")
        self.assertGreaterEqual(r["size"], 32, "a 30x20 square should be readable from the couch")

    # AC2 / D7 — offsets centre the grid in the box.
    def test_offsets_centre_the_grid(self):
        bw, bh = BOX_1080
        r = self._fit(30, 20, bw, bh, 1)
        self.assertLessEqual(abs(r["x"] - (bw - r["size"] * 30) / 2), 1)
        self.assertLessEqual(abs(r["y"] - (bh - r["size"] * 20) / 2), 1)
        r = self._fit(10, 20, bw, bh, 1)  # a tall map: centred left-right
        self.assertLessEqual(abs(r["x"] - (bw - r["size"] * 10) / 2), 1)
        self.assertGreater(r["x"], 100)

    # AC2 / D7 — at 3840x2160 (root font doubles) the board keeps the same
    # proportions of its box.
    def test_4k_same_proportions(self):
        a = self._fit(30, 20, *BOX_1080, 1)
        b = self._fit(30, 20, *BOX_4K, 1)
        self._assert_fits(b, 30, 20, *BOX_4K, 1)
        self.assertAlmostEqual(b["size"] * 30 / BOX_4K[0], a["size"] * 30 / BOX_1080[0], delta=0.01)
        self.assertAlmostEqual(b["size"] * 20 / BOX_4K[1], a["size"] * 20 / BOX_1080[1], delta=0.01)
        self.assertLessEqual(abs(b["size"] - 2 * a["size"]), 1)

    # AC2 / D7 — a TV scaled 200% (same CSS box, dpr 2): same layout in CSS
    # px, square still whole backing pixels.
    def test_dpr2_same_css_layout(self):
        a = self._fit(30, 20, *BOX_1080, 1)
        b = self._fit(30, 20, *BOX_1080, 2)
        self._assert_fits(b, 30, 20, *BOX_1080, 2)
        self.assertLessEqual(abs(b["size"] - a["size"]), 0.5)
        self.assertLessEqual(abs(b["x"] - a["x"]), 1)
        self.assertLessEqual(abs(b["y"] - a["y"]), 1)


# ── tileKind(): text layout to themed tiles (AC1, D6) ─────────────────────

class TestTileKind(_NodeCase):

    # AC1 / D6 — wall, floor, rough, obstacle, door and void are distinct.
    def test_six_distinct_kinds(self):
        r = self.run_js("out(['#', '.', '~', 'T', 'D', ' '].map((c) => tileKind(c)));")
        for k in r:
            self.assertIsInstance(k, str)
            self.assertTrue(k)
        self.assertEqual(len(set(r)), 6, f"kinds not distinct: {r}")

    # AC1 — same char, same kind; an unknown letter is floor.
    def test_stable_and_unknown_is_floor(self):
        r = self.run_js("""
out({ again: ['#', '.', '~', 'T', 'D', ' '].map((c) => tileKind(c)),
      first: ['#', '.', '~', 'T', 'D', ' '].map((c) => tileKind(c)),
      floor: tileKind('.'), h: tileKind('h'), g: tileKind('g') });
""")
        self.assertEqual(r["again"], r["first"])
        self.assertEqual(r["h"], r["floor"])
        self.assertEqual(r["g"], r["floor"])


# ── tokenView(): discs, rings, HP and highlights (AC3 TV, D8) ─────────────

_PARTY = """
const party = players([
  { name: 'Havoc', hp: { current: 18, max: 24 }, conditions: ['poisoned'] },
  { name: 'Anony', hp: { current: 9, max: 30 } },
]);
"""


class TestTokenView(_NodeCase):

    # AC3 / D8 — a PC with hp on its token: exact "cur/max" and fraction.
    def test_pc_exact_hp_from_token(self):
        r = self.run_js(_PARTY + """
const v = viewN({ name: 'Havoc', kind: 'pc', x: 1, y: 1, hp: [12, 24] }, party, 'Anony', false);
out({ ring: v.ring, label: v.hpLabel });
""")
        self.assertAlmostEqual(r["ring"], 0.5, places=6)
        self.assertEqual(r["label"], "12/24")

    # AC3 / D8 — a PC without token hp takes it from players, by name in any case.
    def test_pc_exact_hp_from_players_case_insensitive(self):
        r = self.run_js(_PARTY + """
const v = viewN({ name: 'HAVOC', kind: 'pc', x: 1, y: 1 }, party, null, false);
out({ ring: v.ring, label: v.hpLabel });
""")
        self.assertAlmostEqual(r["ring"], 18 / 24, places=6)
        self.assertEqual(r["label"], "18/24")

    # AC3 / D8 — a bloodied monster: a stepped ring, no number anywhere.
    def test_monster_bloodied_no_number(self):
        r = self.run_js(_PARTY + """
const v = viewN({ name: 'Giant Rat', kind: 'monster', x: 4, y: 2, hp_state: 'bloodied' }, party, null, false);
out({ ring: v.ring, label: v.hpLabel, strings: v.strings });
""")
        self.assertFalse(r["label"], f"monster shows an HP label: {r['label']!r}")
        self.assertGreater(r["ring"], 0)
        self.assertLess(r["ring"], 1)
        for s in r["strings"]:
            self.assertIsNone(re.search(r"\d+\s*/\s*\d+", s), f"a number leaks: {s!r}")

    # AC3 / D8 — fixed steps: healthy > bloodied > near_death, each a single value.
    def test_monster_steps_are_fixed_and_ordered(self):
        r = self.run_js(_PARTY + """
const ring = (st) => viewN({ name: 'Orc', kind: 'monster', x: 0, y: 0, hp_state: st }, party, null, false).ring;
out({ healthy: ring('healthy'), bloodied: ring('bloodied'), near: ring('near_death'),
      bloodied2: ring('bloodied') });
""")
        self.assertGreater(r["healthy"], r["bloodied"])
        self.assertGreater(r["bloodied"], r["near"])
        self.assertGreater(r["near"], 0)
        self.assertLessEqual(r["healthy"], 1)
        self.assertEqual(r["bloodied"], r["bloodied2"])

    # AC3 / D8 — an old payload with an exact monster hp list still shows no
    # number; the ring is the derived state's step, not the exact fraction.
    def test_monster_old_exact_hp_shows_state_only(self):
        r = self.run_js(_PARTY + """
const a = viewN({ name: 'Giant Rat', kind: 'monster', x: 4, y: 2, hp: [9, 20] }, party, null, false);
const b = viewN({ name: 'Giant Rat', kind: 'monster', x: 4, y: 2, hp: [6, 20] }, party, null, false);
const s = viewN({ name: 'Giant Rat', kind: 'monster', x: 4, y: 2, hp_state: 'bloodied' }, party, null, false);
out({ a: a.ring, b: b.ring, s: s.ring, label: a.hpLabel, strings: a.strings.concat(b.strings) });
""")
        self.assertFalse(r["label"])
        self.assertEqual(r["a"], r["s"], "9/20 is bloodied: same step as hp_state bloodied")
        self.assertEqual(r["b"], r["s"], "6/20 is bloodied: same step as hp_state bloodied")
        for s in r["strings"]:
            self.assertNotIn("9/20", s)
            self.assertNotIn("6/20", s)
            self.assertIsNone(re.search(r"\d+\s*/\s*\d+", s), f"a number leaks: {s!r}")

    # AC3 / D8 — initials: first letters of up to two words.
    def test_initials(self):
        r = self.run_js(_PARTY + """
const ini = (n, k) => viewN({ name: n, kind: k, x: 0, y: 0 }, party, null, false).initials;
out({ rat: ini('Giant Rat', 'monster'), havoc: ini('Havoc', 'pc'), three: ini('big bad wolf', 'monster') });
""")
        self.assertEqual(r["rat"], "GR")
        self.assertEqual(r["havoc"], "H")
        self.assertEqual(r["three"], "BB")

    # AC3 / D8 — the turn holder (any case) is highlighted, no one else.
    def test_current_turn_holder(self):
        r = self.run_js(_PARTY + """
const t = { name: 'Giant Rat', kind: 'monster', x: 4, y: 2, hp_state: 'healthy' };
out({ yes: viewN(t, party, 'giant rat', false).current,
      no: viewN(t, party, 'Havoc', false).current,
      none: viewN(t, party, null, false).current,
      pc: viewN({ name: 'Havoc', kind: 'pc', x: 1, y: 1 }, party, 'HAVOC', false).current });
""")
        self.assertEqual(r, {"yes": True, "no": False, "none": False, "pc": True})

    # AC3 — `mine` passes through (the phone's own token, later).
    def test_mine_passes_through(self):
        r = self.run_js(_PARTY + """
const t = { name: 'Havoc', kind: 'pc', x: 1, y: 1 };
out({ yes: viewN(t, party, null, true).mine, no: viewN(t, party, null, false).mine });
""")
        self.assertEqual(r, {"yes": True, "no": False})

    # AC3 / D8 — one condition icon per condition, from the token or players.
    def test_condition_icons(self):
        r = self.run_js(_PARTY + """
out({ tok: viewN({ name: 'Orc', kind: 'monster', x: 0, y: 0, hp_state: 'healthy',
                   conditions: ['prone', 'frightened'] }, party, null, false).icons,
      pl: viewN({ name: 'havoc', kind: 'pc', x: 1, y: 1 }, party, null, false).icons,
      none: viewN({ name: 'Anony', kind: 'pc', x: 2, y: 1 }, party, null, false).icons });
""")
        self.assertIsInstance(r["tok"], list)
        self.assertEqual(len(r["tok"]), 2)
        self.assertEqual(len(r["pl"]), 1)
        self.assertEqual(len(r["none"] or []), 0)


# ── tv.html loads the shared map files (AC1/AC3 TV, D5) ───────────────────

class TestTvLoadsBoard(unittest.TestCase):

    def test_tv_html_loads_board_and_css(self):
        html = TV_HTML.read_text(encoding="utf-8")
        self.assertRegex(html, r'<script[^>]+src="/static/map/board\.js"')
        self.assertRegex(html, r'<link[^>]+href="/static/map/map\.css"')
        # board.js must load before the TV adapter that uses it.
        self.assertLess(html.index("/static/map/board.js"), html.index("/static/tv/map.js"))

    def test_files_exist(self):
        self.assertTrue(BOARD_JS.exists(), "static/map/board.js missing")
        self.assertTrue(MAP_CSS.exists(), "static/map/map.css missing")


if __name__ == "__main__":
    unittest.main()
