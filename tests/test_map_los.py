"""Fog from the PCs' line of sight on /tv (battle-map spec, plan step S8).

AC7 — squares no PC can see are dark, previously seen squares are dim with no
      monsters, and a monster appears only when a PC could see it; moving a PC
      round a corner reveals the monster there. (pure part: los.js)
AC8 (TV) — Claude's manual `show` / `hide` overrides change what the TV
      shows. (pure part: fog() applying fog.show / fog.hide)
AC9 (TV side) — a token the TV is sent but no PC can see is not drawn.
D9 (fog worked out by the TV), D11 (everything lit), D18 (fog as seen
after a reload: `seen` starts from fog.trail).
The browser halves (move round a corner, fog reveal/hide within 1 s, reload
keeps dim squares) are verify-mode checks.

Contract assumed (plan, "The TV: shared map code", los.js row), file
skills/dnd/display/static/map/los.js, attached to window.TV (TV.los) and
exported to `module` for node:

  visibleFrom(cells, x, y) -> the squares visible from square (x, y).
      `cells` is the payload's list of row strings, cells[y][x]. Rays run
      centre to centre and stop at the first '#' or ' '; the blocker itself
      is visible; a ray passing exactly through a corner whose two diagonal
      squares are both blockers is stopped. 'T' (obstacle), '~' and 'D' do
      not block sight. Memoised per origin per `cells` array.
  fog(map, shownTokens, seen) -> {visible, seen}
      `map` is the /stats map payload ({cells, fog?, tokens}); `shownTokens`
      the tokens on screen (each {name, x, y, kind, size}); the PCs among
      them (kind "pc") are the eyes. `seen` is the previous result's seen.
      visible = union of visibleFrom(each PC) + fog.show - fog.hide.
      seen    = previous seen + visible. A hidden square is dark, so it is
                not added to seen.
      D18: when `seen` is null/undefined the TV seeds it from fog.trail
      (the line of sight from every trail square). If los.js exports a
      helper for that instead (seenFromTrail | seedSeen | trailSeen |
      fromTrail, taking the map), the tests use it.
  tokenShown(tok, fogResult) -> bool: PCs always; anything else only when
      one of its size x size squares is in fogResult.visible.

Data shapes accepted from los.js: a Set or an array, whose items are
"x,y" strings or [x, y] pairs. Squares going in are [x, y].

Skipped when node is not installed.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "skills" / "dnd" / "display"
LOS_JS = DISPLAY / "static" / "map" / "los.js"
TV_HTML = DISPLAY / "templates" / "tv.html"
NODE = shutil.which("node")

_PRELUDE = """
globalThis.window = globalThis.window || { TV: {} };
const LOS = %(los)s;
const mod = require(LOS);
function need(name) {
  if (!mod || typeof mod[name] !== 'function') throw new Error('los.js does not export ' + name + '()');
  return mod[name];
}
// Set | array of "x,y" | [x,y]  ->  sorted array of "x,y"
function keys(v) {
  if (v == null) throw new Error('expected a set of squares, got ' + v);
  const items = (v instanceof Set) ? Array.from(v) : (Array.isArray(v) ? v : null);
  if (!items) throw new Error('not a Set or array: ' + JSON.stringify(v));
  const out = new Set();
  for (const it of items) {
    if (typeof it === 'string') out.add(it.replace(/\\s+/g, ''));
    else if (Array.isArray(it) && it.length === 2) out.add(it[0] + ',' + it[1]);
    else throw new Error('square not "x,y" or [x,y]: ' + JSON.stringify(it));
  }
  return Array.from(out).sort();
}
const visibleFrom = (cells, x, y) => keys(need('visibleFrom')(cells, x, y));
const SEED = ['seenFromTrail', 'seedSeen', 'trailSeen', 'fromTrail'].find((n) => typeof mod[n] === 'function');
function fogRaw(map, toks, seen) {
  if (seen == null && SEED) seen = mod[SEED](map);
  const r = need('fog')(map, toks, seen);
  if (!r || typeof r !== 'object' || !('visible' in r) || !('seen' in r))
    throw new Error('fog() did not return {visible, seen}: ' + JSON.stringify(r));
  return r;
}
function fogN(map, toks, seen) {
  const r = fogRaw(map, toks, seen);
  return { raw: r, visible: keys(r.visible), seen: keys(r.seen) };
}
const shown = (tok, r) => need('tokenShown')(tok, r) === true;
const out = (v) => console.log(JSON.stringify(v));
const pc = (name, x, y) => ({ name, x, y, kind: 'pc', size: 1 });
const mon = (name, x, y, size) => ({ name, x, y, kind: 'monster', size: size || 1 });
"""

# L-shaped corridor: east along row 1, then south down column 5.
L_CELLS = [
    "#######",
    "#.....#",
    "#####.#",
    "#####.#",
    "#####.#",
    "#######",
]

# A 5x3 room with a strip of open ground outside its south wall.
ROOM_CELLS = [
    "#######",
    "#.....#",
    "#.....#",
    "#.....#",
    "#######",
    ".......",
]


@unittest.skipUnless(NODE, "node is not installed")
class _NodeCase(unittest.TestCase):

    def run_js(self, body: str, **data):
        if not LOS_JS.exists():
            self.fail("not built yet: static/map/los.js")
        consts = "".join(f"const {k} = {json.dumps(v)};\n" for k, v in data.items())
        script = _PRELUDE % {"los": json.dumps(str(LOS_JS))} + consts + body
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


def _sq(x, y):
    return f"{x},{y}"


# ── visibleFrom(): rays, walls, corners (AC7, D9, D11) ─────────────────────

class TestVisibleFrom(_NodeCase):

    def _vis(self, cells, x, y):
        return set(self.run_js("out(visibleFrom(CELLS, X, Y));", CELLS=cells, X=x, Y=y))

    # AC7 — round the corner of an L corridor is out of sight; from the
    # corner square it is in sight.
    def test_l_corridor_corner(self):
        v = self._vis(L_CELLS, 1, 1)
        self.assertIn(_sq(1, 1), v, "the origin square is visible")
        self.assertIn(_sq(5, 1), v, "the far end of the straight corridor is visible")
        self.assertNotIn(_sq(5, 4), v, "a square round the corner is visible through walls")
        self.assertNotIn(_sq(5, 3), v)
        v = self._vis(L_CELLS, 5, 1)
        self.assertIn(_sq(5, 4), v, "from the corner square the south leg is visible")
        self.assertIn(_sq(1, 1), v, "from the corner square the west leg is visible")

    # AC7 — the walls bordering a visible room are visible; what is behind
    # them is not.
    def test_room_walls_visible_outside_dark(self):
        v = self._vis(ROOM_CELLS, 3, 2)
        for x in range(1, 6):
            for y in range(1, 4):
                self.assertIn(_sq(x, y), v, f"floor ({x},{y}) of the PC's room is dark")
        for w in [(3, 0), (3, 4), (0, 2), (6, 2), (2, 0), (4, 0), (2, 4), (4, 4)]:
            self.assertIn(_sq(*w), v, f"wall {w} bordering the room is dark")
        for x in range(7):
            self.assertNotIn(_sq(x, 5), v, f"({x},5) behind the south wall is visible")

    # AC7 — a gap (' ') blocks sight like a wall, and is itself visible.
    def test_gap_blocks_and_is_visible(self):
        cells = ["... ..."]
        v = self._vis(cells, 0, 0)
        self.assertIn(_sq(3, 0), v, "the blocking gap itself should be visible")
        self.assertNotIn(_sq(4, 0), v, "sight passes through a ' ' gap")

    # D11 / plan — only '#' and ' ' block: obstacles, rough ground and doors
    # do not.
    def test_obstacles_rough_and_doors_do_not_block(self):
        cells = [".T~D..."]
        v = self._vis(cells, 0, 0)
        self.assertIn(_sq(6, 0), v, "a T, ~ or D square blocked line of sight")

    # AC7 / plan — a ray exactly through a corner between two diagonal
    # blockers is stopped (matching the no-corner-cutting move rule).
    def test_diagonal_wall_corner_blocks(self):
        cells = [
            ".....",
            "..#..",
            ".#...",
            ".....",
        ]
        # From (1,1) the ray to (2,2) runs exactly through the corner shared
        # by walls (2,1) and (1,2).
        v = self._vis(cells, 1, 1)
        self.assertNotIn(_sq(2, 2), v, "sight squeezed between two diagonal walls")
        self.assertNotIn(_sq(3, 3), v, "sight squeezed between two diagonal walls (longer ray)")
        self.assertIn(_sq(2, 1), v, "the corner walls themselves are visible")
        self.assertIn(_sq(1, 2), v)

    # Control for the corner rule: one diagonal wall alone does not stop it.
    def test_single_corner_wall_does_not_block(self):
        cells = [
            ".....",
            "..#..",
            ".....",
            ".....",
        ]
        v = self._vis(cells, 1, 1)
        self.assertIn(_sq(2, 2), v, "one wall on a corner should not block the diagonal")
        self.assertIn(_sq(3, 3), v)

    # Plan — memoised per origin *per cells*: a new layout is not answered
    # from the old layout's cache, and a repeat call gives the same answer.
    def test_memo_is_per_cells(self):
        r = self.run_js("""
          const a = ['.#.'];
          const first = visibleFrom(a, 0, 0);
          const again = visibleFrom(a, 0, 0);
          const b = ['...'];
          const opened = visibleFrom(b, 0, 0);
          out({first, again, opened});
        """)
        self.assertEqual(r["first"], r["again"])
        self.assertNotIn(_sq(2, 0), r["first"])
        self.assertIn(_sq(2, 0), r["opened"], "a new cells array reused the old layout's sight")


# ── fog() and tokenShown(): what the TV draws (AC7, AC8 TV, AC9 TV, D18) ───

class TestFog(_NodeCase):

    # AC7 — monster round the corner hidden; after the PC steps to the
    # corner square, shown.
    def test_monster_round_corner_revealed_by_step(self):
        r = self.run_js("""
          const gob = mon('Goblin', 5, 4);
          const m1 = {cells: CELLS, fog: {trail: [[1,1]], show: [], hide: []},
                      tokens: [pc('Havoc', 1, 1), gob]};
          const a = fogRaw(m1, m1.tokens, []);
          const m2 = {cells: CELLS, fog: {trail: [[1,1],[2,1],[3,1],[4,1],[5,1]], show: [], hide: []},
                      tokens: [pc('Havoc', 5, 1), gob]};
          const b = fogRaw(m2, m2.tokens, a.seen);
          out({before: shown(gob, a), after: shown(gob, b),
               pcShown: shown(m1.tokens[0], a)});
        """, CELLS=L_CELLS)
        self.assertFalse(r["before"], "monster round the corner is drawn")
        self.assertTrue(r["after"], "monster not drawn after the PC reached the corner")
        self.assertTrue(r["pcShown"], "a PC is always drawn")

    # AC7 — squares seen earlier stay in `seen` (dim) after the PC leaves,
    # are not `visible`, and a monster on one is not drawn.
    def test_seen_squares_stay_dim_without_monsters(self):
        r = self.run_js("""
          const m1 = {cells: CELLS, tokens: [pc('Havoc', 5, 4)]};
          const a = fogN(m1, m1.tokens, []);
          // the PC walks back round the corner to (1,1)... then down is out of sight
          const rat = mon('Rat', 5, 3);
          const m2 = {cells: CELLS, tokens: [pc('Havoc', 1, 1), rat]};
          const b = fogN(m2, m2.tokens, a.raw.seen);
          out({aVis: a.visible, bVis: b.visible, bSeen: b.seen, ratShown: shown(rat, b.raw)});
        """, CELLS=L_CELLS)
        self.assertIn(_sq(5, 3), r["aVis"])
        self.assertNotIn(_sq(5, 3), r["bVis"], "a square out of sight is still visible")
        self.assertIn(_sq(5, 3), r["bSeen"], "a square seen earlier was forgotten (should be dim)")
        self.assertIn(_sq(5, 4), r["bSeen"])
        self.assertFalse(r["ratShown"], "a monster on a dim (seen earlier) square is drawn")
        self.assertTrue(set(r["bVis"]) <= set(r["bSeen"]), "visible squares should also count as seen")

    # AC8 — `show` makes a walled-off square visible, with its monster.
    def test_show_reveals_walled_off_square_and_monster(self):
        r = self.run_js("""
          const gob = mon('Goblin', 5, 4);
          const m = {cells: CELLS, fog: {trail: [], show: [[5,4]], hide: []},
                     tokens: [pc('Havoc', 1, 1), gob]};
          const f = fogN(m, m.tokens, []);
          out({vis: f.visible, gob: shown(gob, f.raw)});
        """, CELLS=L_CELLS)
        self.assertIn(_sq(5, 4), r["vis"], "fog.show square is not visible")
        self.assertTrue(r["gob"], "monster on a fog.show square is not drawn")
        self.assertNotIn(_sq(5, 3), r["vis"], "fog.show leaked beyond its squares")

    # AC8 — `hide` darkens a square in plain sight and hides its monster.
    def test_hide_darkens_square_in_sight(self):
        r = self.run_js("""
          const gob = mon('Goblin', 5, 2);
          const m = {cells: CELLS, fog: {trail: [], show: [], hide: [[5,2]]},
                     tokens: [pc('Havoc', 3, 2), gob]};
          const f = fogN(m, m.tokens, []);
          out({vis: f.visible, seen: f.seen, gob: shown(gob, f.raw)});
        """, CELLS=ROOM_CELLS)
        self.assertNotIn(_sq(5, 2), r["vis"], "fog.hide square is still visible")
        self.assertFalse(r["gob"], "monster on a fog.hide square is drawn")
        self.assertNotIn(_sq(5, 2), r["seen"], "fog.hide should darken the square, not dim it")
        self.assertIn(_sq(4, 2), r["vis"], "fog.hide darkened more than its squares")

    # D18 — a map with no `fog` key works fog out from the current PCs only.
    def test_no_fog_key_uses_current_pcs_only(self):
        r = self.run_js("""
          const m = {cells: CELLS, tokens: [pc('Havoc', 1, 1), mon('Goblin', 5, 4)]};
          const f = fogN(m, m.tokens, null);
          out({vis: f.visible, seen: f.seen, expect: visibleFrom(CELLS, 1, 1),
               gob: shown(m.tokens[1], f.raw)});
        """, CELLS=L_CELLS)
        self.assertEqual(sorted(r["vis"]), sorted(r["expect"]))
        self.assertEqual(sorted(r["seen"]), sorted(r["expect"]),
                         "with no fog key, seen should be just what the PCs see now")
        self.assertFalse(r["gob"])

    # D18 — on load, `seen` starts from fog.trail: what was seen from every
    # trail square is dim after a reload, with the PC now elsewhere.
    def test_seen_seeded_from_trail_on_load(self):
        r = self.run_js("""
          const m = {cells: CELLS,
                     fog: {trail: [[1,1],[2,1],[3,1],[4,1],[5,1],[5,2],[5,3],[5,4]], show: [], hide: []},
                     tokens: [pc('Havoc', 5, 4), mon('Rat', 1, 1)]};
          const f = fogN(m, m.tokens, null);
          out({vis: f.visible, seen: f.seen, rat: shown(m.tokens[1], f.raw)});
        """, CELLS=L_CELLS)
        self.assertNotIn(_sq(1, 1), r["vis"])
        for s in [(1, 1), (2, 1), (1, 0), (0, 1)]:
            self.assertIn(_sq(*s), r["seen"], f"{s} (seen from the trail) is not dim after a reload")
        self.assertFalse(r["rat"], "monster on a dim trail square is drawn")
        self.assertNotIn(_sq(3, 3), r["seen"], "solid rock no trail square could see is dim")

    # AC7 / AC9 TV — a big monster is drawn when any one of its squares is
    # visible; NPCs are fogged like monsters.
    def test_token_shown_by_any_square_and_npcs_fogged(self):
        r = self.run_js("""
          // Ogre (size 2) covers (4,1)-(5,2). The wall at (3,1) hides its
          // top-left square from the PC at (0,2); (4,2) is in plain sight.
          const cells = ['.......', '...#...', '.......'];
          const ogre = mon('Ogre', 4, 1, 2);
          const npc = {name: 'Brother Tam', x: 5, y: 0, kind: 'npc', size: 1};
          const m = {cells, tokens: [pc('Havoc', 0, 2), ogre, npc]};
          const f = fogRaw(m, m.tokens, []);
          out({ogre: shown(ogre, f), npc: shown(npc, f), vis: keys(f.visible)});
        """)
        self.assertNotIn(_sq(4, 1), r["vis"], "fixture: the ogre's anchor square should be hidden")
        self.assertIn(_sq(4, 2), r["vis"])
        self.assertTrue(r["ogre"], "a size-2 monster with a visible square is not drawn")
        self.assertNotIn(_sq(5, 0), r["vis"])
        self.assertFalse(r["npc"], "an NPC out of sight is drawn (NPCs are fogged like monsters)")

    # Plan risk "Line-of-sight cost on a TV browser" — 30x20 with a
    # 200-square trail computes in under 200 ms in node (cold memo).
    def test_30x20_with_200_square_trail_under_200ms(self):
        cells = []
        for y in range(20):
            row = ""
            for x in range(30):
                edge = x in (0, 29) or y in (0, 19)
                pillar = (x % 4 == 2 and y % 3 == 1)
                row += "#" if edge or pillar else "."
            cells.append(row)
        trail = [[x, y] for y in range(1, 19) for x in range(1, 29) if cells[y][x] == "."][:200]
        self.assertEqual(len(trail), 200)
        r = self.run_js("""
          const m = {cells: CELLS, fog: {trail: TRAIL, show: [], hide: []},
                     tokens: [pc('A', 1, 1), pc('B', 28, 18), pc('C', 15, 10), pc('D', 5, 17),
                              mon('M1', 20, 5), mon('M2', 10, 12)]};
          const t0 = process.hrtime.bigint();
          const f = fogRaw(m, m.tokens, null);
          const ms = Number(process.hrtime.bigint() - t0) / 1e6;
          out({ms, seen: keys(f.seen).length});
        """, CELLS=cells, TRAIL=trail)
        self.assertGreater(r["seen"], 200, "the trail did not seed the seen squares")
        self.assertLess(r["ms"], 200, f"fog for 30x20 + 200 trail took {r['ms']:.0f} ms")


# ── tv.html loads los.js before the TV map adapter (S8 wiring) ─────────────

class TestTvLoadsLos(unittest.TestCase):

    def test_tv_html_loads_los_before_map_adapter(self):
        html = TV_HTML.read_text(encoding="utf-8")
        self.assertIn("/static/map/los.js", html, "tv.html does not load static/map/los.js")
        self.assertLess(html.index("/static/map/los.js"), html.index("/static/tv/map.js"),
                        "los.js must load before static/tv/map.js")

    def test_los_attaches_to_window_tv(self):
        if not LOS_JS.exists():
            self.fail("not built yet: static/map/los.js")
        src = LOS_JS.read_text(encoding="utf-8")
        self.assertRegex(src, r"\.TV\.los\b|TV\s*\.\s*los\s*=|TV\[['\"]los['\"]\]",
                         "los.js does not attach itself as window.TV.los")
        self.assertIn("module.exports", src)


if __name__ == "__main__":
    unittest.main()
