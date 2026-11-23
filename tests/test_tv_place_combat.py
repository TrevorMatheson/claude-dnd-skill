"""Combat on /tv: plain map area, place image only behind the narration
column (scene backgrounds, plan step S6).

AC7 (the pure part; the screenshot is a browser check in verify mode), D9.

Assumed contract, pinned by these tests (plan "Shape", place.js row and
"Combat"):

  static/tv/place.js   regions(mode, rects) -> {clip, plain, extraDim}
    rects     measured boxes, each {x, y, w, h} in CSS px, keyed
              party, feed, inputbar, map (any may be missing or null).
              A box with zero width or height is a hidden element and is
              ignored (a hidden element measures 0x0 at the origin, which
              would otherwise stretch the union to the top-left corner).
    clip      {x, y, w, h} the place images are clipped to, or null for the
              full screen
                combat-map    the union of party, feed and inputbar
                combat-nomap  the union of feed and inputbar (its party is a
                              column of its own on the right, tv.css:72)
                story/minimal null
                unknown mode  null
    plain     the map rect in combat-map (the area drawn in --scene-plain),
              else null
    extraDim  a number >= 0; combat-map's is greater than story's, and
              combat-nomap gets the same as combat-map ("the same extra
              dimming", plan Shape)

Browser checks for S6 (map box plain outside the board, image only in the
right column, contrastReport().ratio >= 4.5 in combat, full image back
within 3 s after combat.py end) run in verify mode.

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
PLACE_JS = ROOT / "skills" / "dnd" / "display" / "static" / "tv" / "place.js"
NODE = shutil.which("node")

_PRELUDE = """
globalThis.window = globalThis.window || { TV: {} };
const P = require(%(place)s);
if (!P || typeof P.regions !== 'function') throw new Error('place.js does not export regions()');
const regions = P.regions;
const out = (v) => console.log(JSON.stringify(v));
// 1920x1080, combat-map: map left (60fr), party / feed / input bar right.
const MAP = {
  map:      { x: 40,   y: 120, w: 1080, h: 900 },
  party:    { x: 1150, y: 120, w: 730,  h: 200 },
  feed:     { x: 1150, y: 340, w: 730,  h: 560 },
  inputbar: { x: 1140, y: 920, w: 750,  h: 100 },
};
// combat-nomap: feed + input bar left, party a full-height column right,
// the map box hidden.
const NOMAP = {
  map:      { x: 0,    y: 0,   w: 0,    h: 0 },
  party:    { x: 1480, y: 20,  w: 420,  h: 1040 },
  feed:     { x: 40,   y: 140, w: 1400, h: 780 },
  inputbar: { x: 40,   y: 940, w: 1400, h: 100 },
};
const STORY = {
  map:      { x: 0,   y: 0,   w: 0,    h: 0 },
  party:    { x: 0,   y: 0,   w: 0,    h: 0 },
  feed:     { x: 520, y: 120, w: 880,  h: 820 },
  inputbar: { x: 520, y: 960, w: 880,  h: 90 },
};
"""


@unittest.skipUnless(NODE, "node is not installed")
class _RegionsCase(unittest.TestCase):

    def run_js(self, body: str):
        if not PLACE_JS.exists():
            self.fail("not built yet: static/tv/place.js")
        script = _PRELUDE % {"place": json.dumps(str(PLACE_JS))} + body
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

    def assertRect(self, got, exp):
        self.assertIsInstance(got, dict, f"expected a rect, got {got!r}")
        self.assertEqual(sorted(got), ["h", "w", "x", "y"], got)
        for k in "xywh":
            self.assertAlmostEqual(got[k], exp[k], places=3, msg=f"{k}: {got}")


class TestRegionsCombatMap(_RegionsCase):

    # S6 Done when / AC7: the clip is the column union, the plain area is the
    # map rect, and extraDim is greater than in story.
    def test_clip_plain_and_extra_dim(self):
        res = self.run_js("out({ c: regions('combat-map', MAP), s: regions('story', STORY) });")
        c, s = res["c"], res["s"]
        self.assertEqual(sorted(c), ["clip", "extraDim", "plain"])
        self.assertRect(c["clip"], {"x": 1140, "y": 120, "w": 750, "h": 900})
        self.assertRect(c["plain"], {"x": 40, "y": 120, "w": 1080, "h": 900})
        self.assertIsInstance(c["extraDim"], (int, float))
        self.assertIsInstance(s["extraDim"], (int, float))
        self.assertGreaterEqual(s["extraDim"], 0)
        self.assertGreater(c["extraDim"], s["extraDim"])

    # A hidden party box (0x0 at the origin) must not stretch the clip to the
    # top-left corner.
    def test_zero_size_box_ignored(self):
        res = self.run_js("""
const r = Object.assign({}, MAP, { party: { x: 0, y: 0, w: 0, h: 0 } });
out(regions('combat-map', r).clip);
""")
        self.assertRect(res, {"x": 1140, "y": 340, "w": 750, "h": 680})

    # Missing / null boxes are skipped, not a crash.
    def test_missing_boxes(self):
        res = self.run_js("""
out(regions('combat-map', { map: MAP.map, feed: MAP.feed, party: null }).clip);
""")
        self.assertRect(res, {"x": 1150, "y": 340, "w": 730, "h": 560})


class TestRegionsCombatNomap(_RegionsCase):

    # S6 Done when: the clip is its narration column (feed + input bar), with
    # the same extra dimming as combat-map, and no plain area.
    def test_clip_is_the_narration_column(self):
        res = self.run_js("out({ n: regions('combat-nomap', NOMAP), c: regions('combat-map', MAP) });")
        n = res["n"]
        self.assertRect(n["clip"], {"x": 40, "y": 140, "w": 1400, "h": 900})
        self.assertIsNone(n["plain"], "combat-nomap has no map area")
        self.assertAlmostEqual(n["extraDim"], res["c"]["extraDim"], places=6)


class TestRegionsStoryAndMinimal(_RegionsCase):

    # S6 Done when: story and minimal have no clip (the full image is back).
    def test_no_clip(self):
        res = self.run_js("""
out(['story', 'minimal'].map((m) => regions(m, STORY)));
""")
        for mode, r in zip(("story", "minimal"), res):
            with self.subTest(mode=mode):
                self.assertIsNone(r["clip"])
                self.assertIsNone(r["plain"])
                self.assertIsInstance(r["extraDim"], (int, float))

    # Even with a stale map rect passed in (the box is still measured while
    # the mode flips back), story does not clip or paint a plain area.
    def test_story_ignores_a_map_rect(self):
        res = self.run_js("out(regions('story', MAP));")
        self.assertIsNone(res["clip"])
        self.assertIsNone(res["plain"])

    def test_unknown_mode_is_full_screen(self):
        res = self.run_js("out(regions('something-new', MAP));")
        self.assertIsNone(res["clip"])
        self.assertIsNone(res["plain"])


if __name__ == "__main__":
    unittest.main()
