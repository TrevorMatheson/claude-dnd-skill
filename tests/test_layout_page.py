"""The laptop layout page at /layout (display foundation, plan step S10).

AC4 — a layout change on the laptop page reaches the TV within 1s without a
      reload (browser check in verify mode; the page half is tested here).
AC6 — each panel takes 1-3 of the five modules; the Story, Tactical and
      Minimal presets match D7.
AC7 — the manual override works both ways (Auto / Combat / Story).
D6  — layout set from the laptop, saved with the campaign; a campaign with
      no display.json gets the Story preset.

The page's model is pure and lives in skills/dnd/display/static/layout/
layout.js, attached to window.LAYOUT and module.exports. Assumed contract
(from the S10 brief):

  presetConfig(config, name)        layout = {preset: name, left, right} from
                                    PRESETS; unknown name -> unchanged copy
  addModule(config, side, id)       append id to side ('left'|'right'); moves
                                    it off the other side; refused (unchanged
                                    copy) when the side is full (3), the id is
                                    unknown or already on that side
  removeModule(config, side, id)    remove it
  moveModule(config, side, id, d)   reorder by d (-1 up, +1 down), clamped
  presetOf(layout)                  'story'|'tactical'|'minimal' on an exact
                                    (ordered) match, else 'custom'
  overrideValue(choice)             'auto'->null, 'combat'->true, 'story'->false
  overrideChoice(value)             the reverse; anything else -> 'auto'
  PRESETS, MODULES                  equal to display_config.py's

Every function returns a NEW config, never mutates its input, keeps
config.voice and config.version as they were (POST /display-config replaces
the whole config, so the page must send the voice back unchanged), sets
layout.preset to presetOf(new layout), and treats a missing or junk layout
as the Story preset (display_config.default()).

Node tests are skipped when node is not installed.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "skills" / "dnd" / "display"
LAYOUT_PAGE_JS = DISPLAY / "static" / "layout" / "layout.js"
NODE = shutil.which("node")
sys.path.insert(0, str(DISPLAY))


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_layout_page",
                                                  str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_display_config():
    spec = importlib.util.spec_from_file_location("display_config_for_layout_page",
                                                  DISPLAY / "display_config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── GET /layout (Flask) ──────────────────────────────────────────────────

class TestLayoutRoute(unittest.TestCase):
    """S10 Done-when: `/layout` returns 200 and loads the page's own files."""

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.client = cls.app.app.test_client()

    def _page(self):
        r = self.client.get("/layout")
        self.assertEqual(r.status_code, 200, "GET /layout")
        body = r.get_data(as_text=True)
        r.close()
        return body

    # D6 — the laptop page exists.
    def test_layout_route_returns_200(self):
        self._page()

    # D6 — the page loads its pure model.
    def test_page_loads_layout_js(self):
        self.assertRegex(self._page(), r'<script[^>]+src="/static/layout/layout\.js(\?[^"]*)?"')

    # D6 — the page uses the TV's design tokens.
    def test_page_loads_tokens_css(self):
        self.assertRegex(self._page(), r'<link[^>]+href="/static/tv/tokens\.css(\?[^"]*)?"')

    # AC2 carried over — the page's own files are served by Flask.
    def test_layout_static_files_served(self):
        for path in ("/static/layout/layout.js", "/static/layout/layout.css"):
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200, path)
                self.assertGreater(len(r.get_data()), 0, path)
                r.close()

    # AC2 carried over — nothing loaded from the network.
    def test_page_pulls_nothing_from_the_network(self):
        body = self._page()
        for m in re.finditer(r'(?:src|href)="(https?://[^"]+)"', body):
            self.fail("/layout loads from the network: " + m.group(1))

    # / and /tv still work beside /layout.
    def test_tv_and_old_page_unchanged(self):
        for path in ("/", "/tv"):
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200, path)
                r.close()


# ── static/layout/layout.js (node) ───────────────────────────────────────

_PRELUDE = """
'use strict';
globalThis.window = globalThis.window || {};
const L = require(%(path)s);
function need(name) {
  if (!L || typeof L[name] !== 'function') throw new Error('layout.js does not export ' + name + '()');
  return L[name];
}
const presetConfig = (...a) => need('presetConfig')(...a);
const addModule = (...a) => need('addModule')(...a);
const removeModule = (...a) => need('removeModule')(...a);
const moveModule = (...a) => need('moveModule')(...a);
const presetOf = (...a) => need('presetOf')(...a);
const overrideValue = (...a) => need('overrideValue')(...a);
const overrideChoice = (...a) => need('overrideChoice')(...a);
const out = (v) => console.log(JSON.stringify(v));
const VOICE = { narrator: 'bm_lewis', npc_engine: 'gemini', volume: 0.4, speed: 1.3, read_player_actions: true };
function deepFreeze(o) {
  if (o && typeof o === 'object' && !Object.isFrozen(o)) {
    Object.freeze(o);
    for (const k of Object.keys(o)) deepFreeze(o[k]);
  }
  return o;
}
// A config as GET /display-config returns it; frozen so any in-place write throws.
const cfg = (left, right, preset) => deepFreeze({ version: 1,
  layout: { preset: preset || 'custom', left: left, right: right },
  voice: Object.assign({}, VOICE) });
const story = () => cfg(['party'], ['quests', 'clock'], 'story');
// Result plus the checks every function must pass.
function check(input, result) {
  return {
    layout: result && result.layout,
    newObject: result !== input && result.layout !== input.layout,
    voiceKept: JSON.stringify(result.voice) === JSON.stringify(input.voice),
    versionKept: result.version === input.version,
  };
}
"""


@unittest.skipUnless(NODE, "node is not installed")
class _NodeCase(unittest.TestCase):

    def run_js(self, body: str):
        if not LAYOUT_PAGE_JS.exists():
            self.fail("not built yet: static/layout/layout.js")
        script = _PRELUDE % {"path": json.dumps(str(LAYOUT_PAGE_JS))} + body
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

    def assertKept(self, res):
        """The shared checks: new object, voice and version untouched."""
        self.assertTrue(res["newObject"], "returned the input (or its layout) instead of a new config")
        self.assertTrue(res["voiceKept"], "config.voice changed")
        self.assertTrue(res["versionKept"], "config.version changed")


class TestExports(_NodeCase):

    # AC6 / D7 — the page offers exactly the server's modules and presets.
    def test_presets_and_modules_match_display_config(self):
        dc = _load_display_config()
        res = self.run_js("out({PRESETS: L.PRESETS, MODULES: L.MODULES});")
        self.assertEqual(res["PRESETS"], json.loads(json.dumps(dc.PRESETS)))
        self.assertEqual(res["MODULES"], list(dc.MODULES))

    # The browser half: the same object on window.LAYOUT.
    def test_attaches_to_window_layout(self):
        res = self.run_js("out(!!window.LAYOUT && window.LAYOUT.presetConfig === L.presetConfig);")
        self.assertTrue(res, "window.LAYOUT is not set to the exported model")


class TestPresetConfig(_NodeCase):

    # AC6 / D7 — each preset button gives D7's stacks.
    def test_each_preset(self):
        dc = _load_display_config()
        for name in ("story", "tactical", "minimal"):
            with self.subTest(preset=name):
                res = self.run_js(f"""
const c = cfg(['clock'], ['map', 'turns', 'party']);
out(check(c, presetConfig(c, {json.dumps(name)})));
""")
                self.assertEqual(res["layout"], {"preset": name, **dc.PRESETS[name]})
                self.assertKept(res)

    # The preset's lists are copies: editing the result can't change PRESETS.
    def test_preset_lists_are_copies(self):
        res = self.run_js("""
const r = presetConfig(story(), 'tactical');
r.layout.right.push('clock');
out(L.PRESETS.tactical.right);
""")
        self.assertEqual(res, ["turns", "map"])

    def test_unknown_preset_is_unchanged_copy(self):
        res = self.run_js("""
const c = cfg(['map'], ['party'], 'custom');
out(check(c, presetConfig(c, 'battle')));
""")
        self.assertEqual(res["layout"], {"preset": "custom", "left": ["map"], "right": ["party"]})
        self.assertKept(res)


class TestAddModule(_NodeCase):

    # AC6 — a panel takes up to three modules.
    def test_append_to_side(self):
        res = self.run_js("""
const c = story();
out(check(c, addModule(c, 'right', 'map')));
""")
        self.assertEqual(res["layout"], {"preset": "custom", "left": ["party"],
                                         "right": ["quests", "clock", "map"]})
        self.assertKept(res)

    # AC6 — a module is in one panel at most: adding it moves it.
    def test_moves_from_other_side(self):
        res = self.run_js("""
const c = story();
out(check(c, addModule(c, 'right', 'party')));
""")
        self.assertEqual(res["layout"], {"preset": "custom", "left": [],
                                         "right": ["quests", "clock", "party"]})
        self.assertKept(res)

    # AC6 — never more than three.
    def test_refused_when_side_full(self):
        res = self.run_js("""
const c = cfg(['map'], ['quests', 'clock', 'turns']);
out(check(c, addModule(c, 'right', 'party')));
""")
        self.assertEqual(res["layout"], {"preset": "custom", "left": ["map"],
                                         "right": ["quests", "clock", "turns"]})
        self.assertKept(res)

    # A full side refuses even a module that would move from the other side.
    def test_refused_when_full_does_not_remove_from_other_side(self):
        res = self.run_js("""
const c = cfg(['party'], ['quests', 'clock', 'turns']);
out(check(c, addModule(c, 'right', 'party')));
""")
        self.assertEqual(res["layout"]["left"], ["party"])
        self.assertEqual(res["layout"]["right"], ["quests", "clock", "turns"])

    # AC6 — only the five modules.
    def test_refused_unknown_id(self):
        res = self.run_js("""
const c = story();
out(check(c, addModule(c, 'left', 'inventory')));
""")
        self.assertEqual(res["layout"], {"preset": "story", "left": ["party"],
                                         "right": ["quests", "clock"]})
        self.assertKept(res)

    def test_refused_already_on_side(self):
        res = self.run_js("""
const c = story();
out(check(c, addModule(c, 'right', 'clock')));
""")
        self.assertEqual(res["layout"], {"preset": "story", "left": ["party"],
                                         "right": ["quests", "clock"]})
        self.assertKept(res)

    # presetOf recomputed: building Tactical by hand lights the Tactical preset.
    def test_preset_recomputed_on_match(self):
        res = self.run_js("""
const c = cfg(['party'], ['turns']);
out(check(c, addModule(c, 'right', 'map')));
""")
        self.assertEqual(res["layout"], {"preset": "tactical", "left": ["party"],
                                         "right": ["turns", "map"]})


class TestRemoveModule(_NodeCase):

    def test_remove(self):
        res = self.run_js("""
const c = story();
out(check(c, removeModule(c, 'right', 'quests')));
""")
        self.assertEqual(res["layout"], {"preset": "custom", "left": ["party"], "right": ["clock"]})
        self.assertKept(res)

    # Emptying both panels is the Minimal preset.
    def test_remove_to_minimal(self):
        res = self.run_js("""
const c = cfg(['party'], []);
out(check(c, removeModule(c, 'left', 'party')));
""")
        self.assertEqual(res["layout"], {"preset": "minimal", "left": [], "right": []})

    def test_remove_absent_is_unchanged_copy(self):
        res = self.run_js("""
const c = story();
out(check(c, removeModule(c, 'left', 'clock')));
""")
        self.assertEqual(res["layout"], {"preset": "story", "left": ["party"],
                                         "right": ["quests", "clock"]})
        self.assertKept(res)


class TestMoveModule(_NodeCase):

    # AC6 — order is editable.
    def test_move_up_and_down(self):
        res = self.run_js("""
const c = cfg([], ['quests', 'clock', 'map']);
const up = check(c, moveModule(c, 'right', 'map', -1));
const down = check(c, moveModule(c, 'right', 'quests', 1));
out([up, down]);
""")
        up, down = res
        self.assertEqual(up["layout"]["right"], ["quests", "map", "clock"])
        self.assertEqual(down["layout"]["right"], ["clock", "quests", "map"])
        self.assertKept(up)
        self.assertKept(down)

    def test_clamped_at_ends(self):
        res = self.run_js("""
const c = cfg([], ['quests', 'clock', 'map']);
out([moveModule(c, 'right', 'quests', -1).layout.right,
     moveModule(c, 'right', 'map', 1).layout.right]);
""")
        self.assertEqual(res, [["quests", "clock", "map"], ["quests", "clock", "map"]])

    # Order matters: swapping Story's right stack is no longer Story.
    def test_preset_recomputed(self):
        res = self.run_js("""
const c = story();
const swapped = moveModule(c, 'right', 'clock', -1);
out([swapped.layout, moveModule(swapped, 'right', 'clock', 1).layout]);
""")
        self.assertEqual(res[0], {"preset": "custom", "left": ["party"], "right": ["clock", "quests"]})
        self.assertEqual(res[1], {"preset": "story", "left": ["party"], "right": ["quests", "clock"]})


class TestPresetOf(_NodeCase):

    def test_presets_and_custom(self):
        res = self.run_js("""
out([
  presetOf({left: ['party'], right: ['quests', 'clock']}),
  presetOf({left: ['party'], right: ['turns', 'map']}),
  presetOf({left: [], right: []}),
  presetOf({left: ['party'], right: ['clock', 'quests']}),
  presetOf({left: ['party'], right: ['quests']}),
  presetOf({left: [], right: ['party']}),
]);
""")
        self.assertEqual(res, ["story", "tactical", "minimal", "custom", "custom", "custom"])

    # The `preset` field on the input is not trusted: the stacks decide.
    def test_ignores_stale_preset_field(self):
        res = self.run_js("out(presetOf({preset: 'story', left: ['map'], right: []}));")
        self.assertEqual(res, "custom")


class TestOverride(_NodeCase):

    # AC7 — Auto / Combat / Story map to /display-mode's null / true / false.
    def test_override_value(self):
        res = self.run_js("out([overrideValue('auto'), overrideValue('combat'), overrideValue('story')]);")
        self.assertEqual(res, [None, True, False])

    def test_override_choice(self):
        res = self.run_js("""
out([overrideChoice(null), overrideChoice(true), overrideChoice(false),
     overrideChoice(undefined), overrideChoice('yes'), overrideChoice(1)]);
""")
        self.assertEqual(res, ["auto", "combat", "story", "auto", "auto", "auto"])

    def test_round_trip(self):
        res = self.run_js("out(['auto', 'combat', 'story'].map(c => overrideChoice(overrideValue(c))));")
        self.assertEqual(res, ["auto", "combat", "story"])


class TestJunkLayoutIsStory(_NodeCase):
    """D6 — a campaign with no saved layout is the Story preset (as
    display_config.default()); every function starts from that."""

    _JUNK = ("{version: 1, voice: VOICE}",
             "{version: 1, layout: null, voice: VOICE}",
             "{version: 1, layout: 'tactical', voice: VOICE}",
             "{version: 1, layout: {left: 7, right: 'x'}, voice: VOICE}")

    def _each(self, call: str):
        out = []
        for junk in self._JUNK:
            with self.subTest(config=junk):
                res = self.run_js(f"const c = deepFreeze({junk});\nout(check(c, {call}));")
                self.assertKept(res)
                out.append(res["layout"])
        return out

    def test_add_module(self):
        for layout in self._each("addModule(c, 'right', 'map')"):
            self.assertEqual(layout, {"preset": "custom", "left": ["party"],
                                      "right": ["quests", "clock", "map"]})

    def test_remove_module(self):
        for layout in self._each("removeModule(c, 'left', 'party')"):
            self.assertEqual(layout, {"preset": "custom", "left": [], "right": ["quests", "clock"]})

    def test_move_module(self):
        for layout in self._each("moveModule(c, 'right', 'clock', -1)"):
            self.assertEqual(layout, {"preset": "custom", "left": ["party"], "right": ["clock", "quests"]})

    def test_preset_config_unknown(self):
        for layout in self._each("presetConfig(c, 'nope')"):
            self.assertEqual(layout, {"preset": "story", "left": ["party"],
                                      "right": ["quests", "clock"]})

    def test_preset_of_junk(self):
        res = self.run_js("out([presetOf(undefined), presetOf(null), presetOf('x'), presetOf({})]);")
        self.assertEqual(res, ["story"] * 4)


if __name__ == "__main__":
    unittest.main()
