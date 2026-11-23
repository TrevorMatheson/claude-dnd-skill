"""The mood layer on /tv (scene backgrounds, plan step S4).

AC2 — each scene type has its own animated mood layer, and switching scene
      crossfades. The screenshots are browser checks (verify mode); the pure
      parts they rest on are tested here.
D2  — mood follows the scene type the server already picks (SCENES).
D15 — built on the foundation's /tv background layer.

The logic lives in skills/dnd/display/static/tv/mood.js, a DOM-free module
that attaches to window.TV.mood and also sets module.exports (the foundation
pattern, as layout.js does). Assumed contract, pinned by these tests:

  MOODS          {<scene name>: recipe}, one per key of the server's SCENES
  recipe         {layers: [layer, ...], ...}   2-4 layers
  layer          {kind, count, color?, ...}
                   kind   one of the existing particle kinds (bg.js
                          PARTICLE_COUNT) or fogbank / shafts / flicker /
                          glowmotes
                   count  a finite number >= 1 for particle kinds; optional
                          (a number >= 0 when present) for the soft kinds
                          fogbank / shafts / flicker
  signature      the sorted list of a recipe's layer kinds; no two scenes
                 share one (otherwise two scene types look the same)
  recipeFor(scene)   scene is the server's scene object
                     {name, colors, accent, particles, label}. A SCENES name
                     gives MOODS[name]; an unknown name gives a recipe with a
                     layer of kind scene.particles. Never throws on junk.
  makeMixer(fadeMs = 2500) -> {set(name, now), weights(now)}
                 weights(now) -> [{name, w}, ...]; a name not listed has
                 weight 0. A switch fades the old recipe out and the new one
                 in over fadeMs. Setting the name already showing changes
                 nothing. At most 2 entries are ever returned (a third switch
                 during a fade drops the oldest).
  budget(recipe, w, h) -> a recipe with the same layers (same kinds, same
                 order) whose counts are capped for a w x h screen; it does
                 not change its argument.
  PARTICLE_CAP   the total particle cap (sum of layer counts) budget keeps
                 to at 3840 x 2160.

Python: GET /tv loads mood.js before bg.js and links scene.css after tv.css,
and both files are served.

Skipped when node is not installed.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "skills" / "dnd" / "display"
TV_STATIC = DISPLAY / "static" / "tv"
MOOD_JS = TV_STATIC / "mood.js"
BG_JS = TV_STATIC / "bg.js"
SCENE_CSS = TV_STATIC / "scene.css"
NODE = shutil.which("node")

PARTICLE_KINDS = {"embers", "fireflies", "smoke", "dust", "snow", "stars", "bubbles",
                  "sparks", "sand", "rain", "drips", "mist", "leaves", "ripples"}
NEW_KINDS = {"fogbank", "shafts", "flicker", "glowmotes"}
SOFT_KINDS = {"fogbank", "shafts", "flicker"}
ALL_KINDS = PARTICLE_KINDS | NEW_KINDS


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_tv_mood",
                                                  str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_APP = None


def _app():
    global _APP
    if _APP is None:
        sys.path.insert(0, str(DISPLAY))
        _APP = _import_app()
    return _APP


_PRELUDE = """
globalThis.window = globalThis.window || { TV: {} };
const MOOD_FILE = %(mood)s;
function mod() {
  const m = require(MOOD_FILE);
  if (!m || typeof m !== 'object') throw new Error('mood.js sets no module.exports');
  return m;
}
function need(name, type) {
  const v = mod()[name];
  if (typeof v !== type) throw new Error('mood.js does not export ' + name + ' (' + type + ')');
  return v;
}
const MOODS = () => need('MOODS', 'object');
const recipeFor = (...a) => need('recipeFor', 'function')(...a);
const makeMixer = (...a) => need('makeMixer', 'function')(...a);
const budget = (...a) => need('budget', 'function')(...a);
const CAP = () => need('PARTICLE_CAP', 'number');
const total = (r) => (r.layers || []).reduce((s, l) => s + (Number(l.count) || 0), 0);
const wmap = (ws) => { const o = {}; for (const e of ws) o[e.name] = e.w; return o; };
const out = (v) => console.log(JSON.stringify(v));
"""


@unittest.skipUnless(NODE, "node is not installed")
class _NodeCase(unittest.TestCase):

    def run_js(self, body: str):
        if not MOOD_JS.exists():
            self.fail("not built yet: static/tv/mood.js")
        script = _PRELUDE % {"mood": json.dumps(str(MOOD_JS))} + body
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

    @staticmethod
    def scene(name):
        """The scene object the server sends for a SCENES name."""
        s = dict(_app().SCENES[name])
        s.pop("keywords", None)
        s["name"] = name
        return s


# ── Recipes: one per scene type, each its own (AC2, D2) ─────────────────

class TestRecipeCoverage(_NodeCase):

    # AC2 — every scene type the server can pick has a recipe of 2-4 layers.
    def test_every_scene_has_a_recipe_of_two_to_four_layers(self):
        names = sorted(_app().SCENES)
        res = self.run_js("const M = MOODS(); out(%s.map(n => M[n] ? M[n].layers : null));"
                          % json.dumps(names))
        for name, layers in zip(names, res):
            with self.subTest(scene=name):
                self.assertIsNotNone(layers, f"MOODS has no recipe for {name!r}")
                self.assertIsInstance(layers, list)
                self.assertGreaterEqual(len(layers), 2, f"{name}: fewer than 2 layers")
                self.assertLessEqual(len(layers), 4, f"{name}: more than 4 layers")

    # Shape — layers are drawn from the known kinds, with sane counts.
    def test_layers_use_known_kinds_and_counts(self):
        res = self.run_js("out(MOODS());")
        for name, recipe in res.items():
            for i, layer in enumerate(recipe.get("layers", [])):
                with self.subTest(scene=name, layer=i):
                    self.assertIn(layer.get("kind"), ALL_KINDS, f"{name}: unknown kind")
                    count = layer.get("count")
                    if layer.get("kind") in SOFT_KINDS:
                        if count is not None:
                            self.assertIsInstance(count, (int, float))
                            self.assertGreaterEqual(count, 0)
                    else:
                        self.assertIsInstance(count, (int, float), f"{name}: no count")
                        self.assertGreaterEqual(count, 1, f"{name}: count below 1")
                    if "color" in layer:
                        self.assertIsInstance(layer["color"], str)

    # AC2 "its own mood layer" — no two scene types share a set of layer kinds.
    def test_no_two_recipes_share_a_signature(self):
        names = sorted(_app().SCENES)
        res = self.run_js("const M = MOODS(); out(%s.map(n => M[n] ? M[n].layers.map(l => l.kind).sort() : null));"
                          % json.dumps(names))
        seen: dict[tuple, str] = {}
        for name, kinds in zip(names, res):
            with self.subTest(scene=name):
                self.assertIsNotNone(kinds, f"MOODS has no recipe for {name!r}")
                sig = tuple(kinds)
                self.assertNotIn(sig, seen, f"{name} has the same layers as {seen.get(sig)}: {sig}")
                seen[sig] = name

    # Plan Shape — the richer layers are actually used somewhere.
    def test_new_soft_kinds_are_used(self):
        res = self.run_js("out([...new Set(Object.values(MOODS()).flatMap(r => r.layers.map(l => l.kind)))]);")
        self.assertTrue(NEW_KINDS & set(res), "no recipe uses fogbank / shafts / flicker / glowmotes")

    # D15 — the module attaches to window.TV.mood for bg.js in the browser.
    def test_attaches_to_window_tv(self):
        res = self.run_js("""
const vm = require('vm');
const src = require('fs').readFileSync(MOOD_FILE, 'utf8');
const ctx = { window: { TV: {} }, console };
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(src, ctx);
const m = ctx.window.TV && ctx.window.TV.mood;
out(!!m && typeof m.recipeFor === 'function' && typeof m.makeMixer === 'function'
    && typeof m.budget === 'function' && typeof m.MOODS === 'object');
""")
        self.assertTrue(res, "mood.js does not attach {MOODS, recipeFor, makeMixer, budget} to window.TV.mood")


class TestRecipeFor(_NodeCase):

    # D2 — a scene the server knows gives its own recipe.
    def test_known_scene_gives_its_recipe(self):
        for name in ("tavern", "crypt", "city"):
            with self.subTest(scene=name):
                res = self.run_js("out([recipeFor(%s), MOODS()[%s]]);"
                                  % (json.dumps(self.scene(name)), json.dumps(name)))
                self.assertEqual(res[0], res[1])

    # Plan Shape — an unknown name falls back to the server's `particles`.
    def test_unknown_name_falls_back_to_particles(self):
        for particles in ("snow", "embers", "rain", "stars"):
            with self.subTest(particles=particles):
                scene = {"name": "glacier-hall", "colors": ["#0a1a2a", "#1a2a3a"],
                         "accent": "#a0c0e0", "particles": particles, "label": "The Glacier"}
                res = self.run_js("out(recipeFor(%s));" % json.dumps(scene))
                kinds = [l.get("kind") for l in res.get("layers", [])]
                self.assertIn(particles, kinds)

    # Junk from an old server or a bad push must not break the background.
    def test_junk_scene_gives_a_valid_recipe(self):
        res = self.run_js("out([null, undefined, {}, {name: 42}, {name: 'x', particles: 'not-a-kind'}]"
                          ".map(s => { const r = recipeFor(s); return r && Array.isArray(r.layers)"
                          " && r.layers.length >= 1 && r.layers.every(l => typeof l.kind === 'string'); }));")
        self.assertEqual(res, [True] * 5)


# ── Mixer: crossfade over 2.5 s, at most two live recipes (AC2, plan S4) ──

class TestMixer(_NodeCase):

    # Plan S4 — old 1 / new 0 at t0, about 0.5 / 0.5 at 1250 ms, new only at 2500 ms.
    def test_crossfade_weights_default_2500(self):
        res = self.run_js("""
const m = makeMixer();
m.set('tavern', 0);
const settled = wmap(m.weights(9000));
m.set('crypt', 10000);
out({settled, t0: wmap(m.weights(10000)), mid: wmap(m.weights(11250)), end: m.weights(12500),
     q: wmap(m.weights(10625))});
""")
        self.assertAlmostEqual(res["settled"].get("tavern", 0), 1, places=3)
        t0, mid, q = res["t0"], res["mid"], res["q"]
        self.assertAlmostEqual(t0.get("tavern", 0), 1, places=3, msg="old at t0")
        self.assertAlmostEqual(t0.get("crypt", 0), 0, places=3, msg="new at t0")
        self.assertAlmostEqual(mid.get("tavern", 0), 0.5, delta=0.1, msg="old at 1250 ms")
        self.assertAlmostEqual(mid.get("crypt", 0), 0.5, delta=0.1, msg="new at 1250 ms")
        self.assertLess(q.get("crypt", 0), mid.get("crypt", 0), "new weight grows during the fade")
        live = {e["name"]: e["w"] for e in res["end"] if e["w"] > 1e-6}
        self.assertEqual(set(live), {"crypt"}, "only the new recipe at 2500 ms")
        self.assertAlmostEqual(live["crypt"], 1, places=3)

    # A plain crossfade never brightens or dims the whole layer.
    def test_weights_sum_to_one_during_a_plain_fade(self):
        res = self.run_js("""
const m = makeMixer();
m.set('tavern', 0); m.weights(5000);
m.set('forest', 10000);
out([10000, 10400, 11250, 12000, 12500, 15000].map(t => m.weights(t).reduce((s, e) => s + e.w, 0)));
""")
        for s in res:
            self.assertAlmostEqual(s, 1, delta=0.02)

    # makeMixer(fadeMs) honours its argument.
    def test_custom_fade_length(self):
        res = self.run_js("""
const m = makeMixer(1000);
m.set('cave', 0); m.weights(5000);
m.set('ocean', 10000);
out([wmap(m.weights(10500)), wmap(m.weights(11000))]);
""")
        self.assertAlmostEqual(res[0].get("ocean", 0), 0.5, delta=0.1)
        self.assertAlmostEqual(res[1].get("ocean", 0), 1, places=3)
        self.assertAlmostEqual(res[1].get("cave", 0), 0, places=3)

    # Plan S4 — three switches within 1 s leave at most 2 live recipes,
    # and the last one asked for is the one that ends up showing.
    def test_three_quick_switches_keep_two(self):
        res = self.run_js("""
const m = makeMixer();
m.set('tavern', 0); m.weights(5000);
const seen = [];
m.set('dungeon', 10000); seen.push(m.weights(10000));
m.set('crypt', 10300);   seen.push(m.weights(10300));
m.set('fire', 10900);
for (let t = 10900; t <= 14000; t += 100) seen.push(m.weights(t));
out({counts: seen.map(ws => ws.length), names: seen.map(ws => ws.map(e => e.name)),
     last: m.weights(20000)});
""")
        for i, n in enumerate(res["counts"]):
            self.assertLessEqual(n, 2, f"sample {i}: {res['names'][i]}")
        live = {e["name"]: e["w"] for e in res["last"] if e["w"] > 1e-6}
        self.assertEqual(set(live), {"fire"})
        self.assertIn("fire", res["names"][2], "the newest recipe is live right after the switch")

    # Scene is re-sent with every chunk (tv.js applies it per chunk): the
    # same name again must not restart or flicker the mood.
    def test_setting_the_same_name_changes_nothing(self):
        res = self.run_js("""
const m = makeMixer();
m.set('tavern', 0); m.weights(5000);
m.set('tavern', 6000);
const a = m.weights(6000), b = m.weights(7250);
m.set('crypt', 10000); m.set('crypt', 11000);
out({a, b, end: m.weights(12500)});
""")
        for key in ("a", "b"):
            live = {e["name"]: e["w"] for e in res[key] if e["w"] > 1e-6}
            self.assertEqual(set(live), {"tavern"}, key)
            self.assertAlmostEqual(live["tavern"], 1, places=3, msg=key)
        live = {e["name"]: e["w"] for e in res["end"] if e["w"] > 1e-6}
        self.assertEqual(set(live), {"crypt"}, "re-setting crypt mid-fade did not restart the fade")


# ── Budget: particle totals capped at 4K (plan S4, D10) ──────────────────

class TestBudget(_NodeCase):

    def test_cap_is_exported(self):
        res = self.run_js("out(CAP());")
        self.assertIsInstance(res, (int, float))
        self.assertGreater(res, 0)

    # Plan S4 — budget at 3840x2160 stays under the total particle cap, for every recipe.
    def test_every_recipe_under_cap_at_4k(self):
        res = self.run_js("""
const M = MOODS(), cap = CAP(), r = {};
for (const n of Object.keys(M)) r[n] = total(budget(M[n], 3840, 2160));
out({cap, r});
""")
        for name, t in res["r"].items():
            with self.subTest(scene=name):
                self.assertLessEqual(t, res["cap"])
                self.assertGreater(t, 0, f"{name}: budget left no particles")

    # A recipe asking for far too much is still held to the cap.
    def test_oversized_recipe_is_capped(self):
        res = self.run_js("""
const big = {layers: [{kind: 'rain', count: 100000, color: '#88aaff'},
                      {kind: 'mist', count: 50000}, {kind: 'fogbank', count: 3}]};
const b = budget(big, 3840, 2160);
out({cap: CAP(), total: total(b), kinds: b.layers.map(l => l.kind), orig: big.layers.map(l => l.count)});
""")
        self.assertLessEqual(res["total"], res["cap"])
        self.assertEqual(res["kinds"], ["rain", "mist", "fogbank"], "budget keeps the layers in order")
        self.assertEqual(res["orig"], [100000, 50000, 3], "budget changed its argument")

    # A smaller screen never gets more particles than 4K.
    def test_1080p_not_more_than_4k(self):
        res = self.run_js("""
const M = MOODS(), r = {};
for (const n of Object.keys(M)) r[n] = [total(budget(M[n], 1920, 1080)), total(budget(M[n], 3840, 2160))];
out(r);
""")
        for name, (hd, uhd) in res.items():
            with self.subTest(scene=name):
                self.assertLessEqual(hd, uhd)


# ── bg.js uses the module; resize no longer piles up scale (plan S4) ─────

class TestBgUsesMood(unittest.TestCase):

    def setUp(self):
        self.src = BG_JS.read_text(encoding="utf-8")

    # Plan S4 — bg.js draws recipe layers through the mixer.
    def test_bg_uses_mixer_and_recipes(self):
        self.assertTrue("makeMixer" in self.src, "bg.js does not use mood.js's mixer")
        self.assertTrue("recipeFor" in self.src, "bg.js does not use mood.js's recipeFor")

    # Plan Findings 2 — resizeCanvas used ctx.scale on every resize; S4 uses setTransform.
    def test_resize_uses_set_transform(self):
        self.assertTrue("setTransform" in self.src, "bg.js does not use setTransform on resize")
        self.assertFalse("ctx.scale(dpr, dpr)" in self.src, "bg.js still scales the context on every resize")


# ── /tv loads the files in order and serves them (plan S4 "Python") ──────

class TestTvShellMood(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _app()
        cls.client = cls.app.app.test_client()

    def setUp(self):
        r = self.client.get("/tv")
        self.assertEqual(r.status_code, 200)
        self.body = r.get_data(as_text=True)

    def test_mood_js_loaded_before_bg_js(self):
        mood = self.body.find('src="/static/tv/mood.js"')
        bg = self.body.find('src="/static/tv/bg.js"')
        self.assertNotEqual(mood, -1, "/tv does not load mood.js")
        self.assertNotEqual(bg, -1, "/tv does not load bg.js")
        self.assertLess(mood, bg, "mood.js must load before bg.js")

    def test_scene_css_linked_after_tv_css(self):
        scene = self.body.find('href="/static/tv/scene.css"')
        tv = self.body.find('href="/static/tv/tv.css"')
        self.assertNotEqual(scene, -1, "/tv does not link scene.css")
        self.assertNotEqual(tv, -1, "/tv does not link tv.css")
        self.assertLess(tv, scene, "scene.css must come after tv.css")

    def test_files_are_served(self):
        for name, ctype in (("mood.js", "javascript"), ("scene.css", "css")):
            with self.subTest(file=name):
                r = self.client.get(f"/static/tv/{name}")
                self.assertEqual(r.status_code, 200, name)
                self.assertIn(ctype, r.content_type, name)
                r.close()


if __name__ == "__main__":
    unittest.main()
