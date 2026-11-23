"""Place image on /tv: geometry, contrast and the place model (scene
backgrounds, plan step S5).

AC6 (dimming and measurement, the pure maths), AC4 (TV side: the place a
stats snapshot names is the one shown), AC9 (TV side: a place with no image
clears to the mood layer), D5 as refined by D17 (a pending place with no
picture clears to its mood at once; its picture fades in when ready), D7, D8.

The browser half of S5 (fade-in within 3 s, contrastReport() >= 4.5 on a
near-white and a dark image, 1080p / 4K screenshots, reload, no console
errors) is checked in verify mode, not here.

Assumed contract, pinned by these tests (plan "Shape", place.js row):

  static/tv/place.js   attaches to window.TV.place and sets module.exports
    coverRect(iw, ih, vw, vh) -> {x, y, w, h}
        object-fit: cover; scale = max(vw/iw, vh/ih), centred, so x / y are
        <= 0 and the rect covers the viewport.
    relLum(r, g, b)           WCAG relative luminance, channels 0-255.
    contrastRatio(l1, l2)     (lighter + .05) / (darker + .05), either order.
    percentile(arr, p)        p on the 0-100 scale; does not sort arr in place.
    scrimFor(lums, {ink, target = 4.5, min, max}) -> alpha
        lums  luminances (0-1) sampled behind the narration column
        ink   the luminance of --ink (a number)
        The alpha of a black scrim that brings the 95th-percentile sample to
        `target` against ink, clamped to [min, max]. The tests accept any
        alpha that reaches the target when the black is composited the way
        the browser does it (in sRGB), so a linear-light solver, which dims
        a little more, passes too.
    makePlaceModel() -> {set(place), loaded(url), failed(url)}
        place   a stats.place: {name, slug, image, pending} or null
        each returns one of
          {action: 'show', url}  start / finish showing the picture at url
          {action: 'keep'}       leave the current picture up
          {action: 'clear'}      fade to the mood layer only
          null / undefined       nothing to do
        Pinned sequences (extra keys on an action are tolerated):
          set(A)                      -> show A      (the caller loads it)
          loaded(A) while A is wanted -> show A      (the caller fades it in)
          set(A), set(B), loaded(A)   -> nothing     (stale load ignored)
          failed(B) while B is wanted -> clear
          A up, set({image: null, pending: true})  -> clear   (D17: the new
                                       place's mood shows alone while its
                                       picture is made)
          then set({image: B})                     -> show B
          A up, set({image: A, pending: true})     -> nothing (a renewal:
                                       the old picture stays until the new)
          set(null) while A is up     -> clear
          set(A) when A is already wanted or up    -> nothing

  static/tv/tv.js   forwards the place to TV.bg.setPlace, each in try/catch:
    {place: P}               -> setPlace(P)   (the worker's event)
    {stats: S}               -> setPlace(S.place); S is a full snapshot, so a
                                snapshot with no place (the campaign changed,
                                dnd-display-app.py pops it) passes null /
                                undefined and the TV clears
    {rewind: {stats: S}}     -> setPlace(S.place)
    {clear: true}            -> setPlace(null)
    any other payload        -> no call
  tv.js is run under node with stub TV modules and a fake document.

  templates/tv.html  loads place.js before bg.js, and scene.css.

Skipped when node is not installed.
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
TV_STATIC = DISPLAY / "static" / "tv"
PLACE_JS = TV_STATIC / "place.js"
TV_JS = TV_STATIC / "tv.js"
NODE = shutil.which("node")

INK_HEX = "#f1ebe1"  # --ink in tokens.css


def _run_node(case: unittest.TestCase, script: str):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
        path = f.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True,
                           encoding="utf-8", timeout=30)
    finally:
        pathlib.Path(path).unlink(missing_ok=True)
    if r.returncode != 0:
        case.fail("node failed:\n" + (r.stderr or r.stdout)[-1500:])
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    case.assertTrue(lines, "scenario printed nothing")
    return json.loads(lines[-1])


_PRELUDE = """
globalThis.window = globalThis.window || { TV: {} };
const P = require(%(place)s);
function need(name) {
  if (!P || typeof P[name] !== 'function') throw new Error('place.js does not export ' + name + '()');
  return P[name];
}
const coverRect = (...a) => need('coverRect')(...a);
const relLum = (...a) => need('relLum')(...a);
const contrastRatio = (...a) => need('contrastRatio')(...a);
const percentile = (...a) => need('percentile')(...a);
const scrimFor = (...a) => need('scrimFor')(...a);
const makePlaceModel = (...a) => need('makePlaceModel')(...a);
const out = (v) => console.log(JSON.stringify(v));
// An action as the tests compare it: {action, url?}, or null for "nothing".
const act = (r) => {
  if (r == null) return null;
  const o = { action: r.action };
  if (r.url !== undefined) o.url = r.url;
  return o;
};
// sRGB transfer functions, to composite a black scrim the way a browser does.
const lin = (v) => (v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
const enc = (L) => (L <= 0.0031308 ? 12.92 * L : 1.055 * Math.pow(L, 1 / 2.4) - 0.055);
const behind = (L, a) => lin(enc(L) * (1 - a));
const ratio = (a, b) => (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
const INK = (() => { const c = [0xf1, 0xeb, 0xe1].map((x) => lin(x / 255));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; })();
const place = (name, image, pending) => ({ name, slug: name.toLowerCase(), image, pending: !!pending });
const A = place('Crypt', '/scene-image/crypt.png?v=1');
const B = place('Tavern', '/scene-image/tavern.jpg?v=2');
"""


@unittest.skipUnless(NODE, "node is not installed")
class _PlaceCase(unittest.TestCase):

    def run_js(self, body: str):
        if not PLACE_JS.exists():
            self.fail("not built yet: static/tv/place.js")
        return _run_node(self, _PRELUDE % {"place": json.dumps(str(PLACE_JS))} + body)


# ── Module shape ─────────────────────────────────────────────────────────

class TestPlaceModuleShape(_PlaceCase):

    # Foundation pattern: attach to window.TV and export to module.
    def test_attaches_to_window_tv_and_exports(self):
        res = self.run_js("""
const names = ['coverRect', 'relLum', 'contrastRatio', 'percentile', 'scrimFor', 'makePlaceModel'];
out({
  exported: names.filter((n) => typeof P[n] === 'function'),
  onWindow: names.filter((n) => window.TV && window.TV.place && typeof window.TV.place[n] === 'function'),
});
""")
        names = ["coverRect", "relLum", "contrastRatio", "percentile", "scrimFor", "makePlaceModel"]
        self.assertEqual(res["exported"], names)
        self.assertEqual(res["onWindow"], names, "place.js must set window.TV.place")


# ── Maths: luminance and contrast (AC6, D8) ──────────────────────────────

class TestLuminanceAndContrast(_PlaceCase):

    # S5 Done when: relLum(#f1ebe1) is about 0.83.
    def test_rel_lum_of_ink(self):
        res = self.run_js("out(relLum(0xf1, 0xeb, 0xe1));")
        self.assertGreaterEqual(res, 0.82)
        self.assertLessEqual(res, 0.85)

    def test_rel_lum_of_white_and_black(self):
        res = self.run_js("out([relLum(255, 255, 255), relLum(0, 0, 0)]);")
        self.assertAlmostEqual(res[0], 1.0, places=4)
        self.assertAlmostEqual(res[1], 0.0, places=4)

    # S5 Done when: white against black gives 21, in either order.
    def test_white_on_black_is_21(self):
        res = self.run_js("out([contrastRatio(1, 0), contrastRatio(0, 1)]);")
        self.assertAlmostEqual(res[0], 21.0, places=4)
        self.assertAlmostEqual(res[1], 21.0, places=4)

    def test_same_luminance_is_1(self):
        res = self.run_js("out(contrastRatio(0.4, 0.4));")
        self.assertAlmostEqual(res, 1.0, places=6)


# ── Maths: percentile ────────────────────────────────────────────────────

class TestPercentile(_PlaceCase):

    def test_95th_of_1_to_100(self):
        res = self.run_js("""
const a = []; for (let i = 1; i <= 100; i++) a.push(i);
out(percentile(a, 95));
""")
        self.assertGreaterEqual(res, 95)
        self.assertLessEqual(res, 96)

    def test_ends_and_single_value(self):
        res = self.run_js("""
out([percentile([5, 1, 9, 3], 0), percentile([5, 1, 9, 3], 100), percentile([0.7], 95)]);
""")
        self.assertEqual(res[0], 1)
        self.assertEqual(res[1], 9)
        self.assertAlmostEqual(res[2], 0.7, places=9)

    # Unsorted input works and is not reordered (bg.js reuses its sample array).
    def test_unsorted_input_not_mutated(self):
        res = self.run_js("""
const a = [0.9, 0.1, 0.5, 0.3, 0.7];
const p = percentile(a, 50);
out({p, a});
""")
        self.assertAlmostEqual(res["p"], 0.5, places=9)
        self.assertEqual(res["a"], [0.9, 0.1, 0.5, 0.3, 0.7])


# ── Maths: scrimFor, the automatic dimming (AC6, D8) ─────────────────────

class TestScrimFor(_PlaceCase):

    # S5 Done when: all-white samples give an alpha whose result is at least
    # 4.5:1 and the alpha is at most max.
    def test_all_white_reaches_target_within_max(self):
        res = self.run_js("""
const lums = new Array(400).fill(1);
const a = scrimFor(lums, { ink: INK, target: 4.5, min: 0.15, max: 0.9 });
out({ a, r: ratio(INK, behind(1, a)) });
""")
        self.assertIsInstance(res["a"], (int, float))
        self.assertLessEqual(res["a"], 0.9)
        self.assertGreaterEqual(res["r"], 4.5 - 1e-6,
                                f"alpha {res['a']} leaves white at {res['r']:.2f}:1 against --ink")

    # target defaults to 4.5.
    def test_target_defaults_to_4_5(self):
        res = self.run_js("""
const a = scrimFor(new Array(50).fill(1), { ink: INK, min: 0.15, max: 0.9 });
out({ a, r: ratio(INK, behind(1, a)) });
""")
        self.assertLessEqual(res["a"], 0.9)
        self.assertGreaterEqual(res["r"], 4.5 - 1e-6)

    # The alpha never goes past max, even when max cannot reach the target.
    def test_clamped_to_max(self):
        res = self.run_js("""
out(scrimFor(new Array(50).fill(1), { ink: INK, target: 4.5, min: 0.1, max: 0.5 }));
""")
        self.assertAlmostEqual(res, 0.5, places=6)

    # S5 Done when: dark samples give min.
    def test_dark_samples_give_min(self):
        res = self.run_js("""
out(scrimFor(new Array(200).fill(0.01), { ink: INK, target: 4.5, min: 0.15, max: 0.9 }));
""")
        self.assertAlmostEqual(res, 0.15, places=6)

    # It solves on the 95th percentile: 6 bright samples in 100 count as
    # bright, 1 bright sample in 100 does not.
    def test_solves_on_the_95th_percentile(self):
        res = self.run_js("""
const opts = { ink: INK, target: 4.5, min: 0.15, max: 0.9 };
const six = new Array(94).fill(0.01).concat(new Array(6).fill(1));
const one = new Array(99).fill(0.01).concat([1]);
const aSix = scrimFor(six, opts), aOne = scrimFor(one, opts);
out({ aSix, aOne, rSix: ratio(INK, behind(1, aSix)) });
""")
        self.assertAlmostEqual(res["aOne"], 0.15, places=6)
        self.assertGreater(res["aSix"], 0.15)
        self.assertGreaterEqual(res["rSix"], 4.5 - 1e-6)

    # Brighter backgrounds never get a lighter scrim.
    def test_monotonic_in_brightness(self):
        res = self.run_js("""
const opts = { ink: INK, target: 4.5, min: 0.1, max: 0.95 };
out([0.05, 0.2, 0.4, 0.6, 0.8, 1].map((L) => scrimFor(new Array(20).fill(L), opts)));
""")
        self.assertEqual(res, sorted(res), f"scrim alpha not monotonic: {res}")
        for a in res:
            self.assertGreaterEqual(a, 0.1 - 1e-9)
            self.assertLessEqual(a, 0.95 + 1e-9)

    # A mid-grey that can reach the target does so (the solver is not just
    # "min unless white").
    def test_mid_grey_reaches_target(self):
        res = self.run_js("""
const a = scrimFor(new Array(20).fill(0.45), { ink: INK, target: 4.5, min: 0.1, max: 0.95 });
out({ a, r: ratio(INK, behind(0.45, a)) });
""")
        self.assertGreaterEqual(res["r"], 4.5 - 1e-6)


# ── Geometry: coverRect ──────────────────────────────────────────────────

class TestCoverRect(_PlaceCase):

    def _check(self, rect, exp):
        self.assertEqual(sorted(rect), ["h", "w", "x", "y"])
        for k in "xywh":
            self.assertAlmostEqual(rect[k], exp[k], places=3, msg=f"{k}: {rect}")

    # S5 Done when: a wide image fills the height and is cropped left/right.
    def test_wide_image(self):
        self._check(self.run_js("out(coverRect(2000, 500, 1920, 1080));"),
                    {"x": -1200, "y": 0, "w": 4320, "h": 1080})

    # S5 Done when: a tall image fills the width and is cropped top/bottom.
    def test_tall_image(self):
        self._check(self.run_js("out(coverRect(500, 2000, 1920, 1080));"),
                    {"x": 0, "y": -3300, "w": 1920, "h": 7680})

    def test_exact_16_9_at_4k(self):
        self._check(self.run_js("out(coverRect(1344, 756, 3840, 2160));"),
                    {"x": 0, "y": 0, "w": 3840, "h": 2160})


# ── The place model (AC4 / AC9 TV side, D5, D7) ──────────────────────────

class TestPlaceModel(_PlaceCase):

    # S5 Done when: set(A) shows A.
    def test_set_then_loaded_shows(self):
        res = self.run_js("""
const m = makePlaceModel();
out([act(m.set(A)), act(m.loaded(A.image))]);
""")
        self.assertEqual(res, [{"action": "show", "url": "/scene-image/crypt.png?v=1"},
                               {"action": "show", "url": "/scene-image/crypt.png?v=1"}])

    # S5 Done when: set(B) before A loads makes A's load ignored.
    def test_stale_load_ignored(self):
        res = self.run_js("""
const m = makePlaceModel();
m.set(A);
const s = act(m.set(B));
const staleA = act(m.loaded(A.image));
const okB = act(m.loaded(B.image));
out({ s, staleA, okB });
""")
        self.assertEqual(res["s"], {"action": "show", "url": "/scene-image/tavern.jpg?v=2"})
        self.assertIsNone(res["staleA"], "A's late load must be ignored once B is wanted")
        self.assertEqual(res["okB"], {"action": "show", "url": "/scene-image/tavern.jpg?v=2"})

    # S5 Done when: failed(B) clears (fade to the mood layer, no TV notice).
    def test_failed_clears(self):
        res = self.run_js("""
const m = makePlaceModel();
m.set(A); m.loaded(A.image);
m.set(B);
out(act(m.failed(B.image)));
""")
        self.assertEqual(res, {"action": "clear"})

    # A stale failure is ignored too.
    def test_stale_failure_ignored(self):
        res = self.run_js("""
const m = makePlaceModel();
m.set(A); m.set(B);
const f = act(m.failed(A.image));
out({ f, okB: act(m.loaded(B.image)) });
""")
        self.assertIsNone(res["f"])
        self.assertEqual(res["okB"], {"action": "show", "url": "/scene-image/tavern.jpg?v=2"})

    # D17 (refines D5, plan S9): arriving somewhere whose picture is still
    # being made fades the previous place's picture out at once; the new place
    # shows its mood layer alone. Once no longer pending with no picture, it
    # stays cleared (D7, AC9).
    def test_pending_without_image_clears(self):
        res = self.run_js("""
const m = makePlaceModel();
m.set(A); m.loaded(A.image);
const k = act(m.set(place('Bridge', null, true)));
const c = act(m.set(place('Bridge', null, false)));
out({ k, c });
""")
        self.assertEqual(res["k"], {"action": "clear"},
                         "D17: a pending place with no picture clears the old one at once")
        self.assertIn(res["c"], ({"action": "clear"}, None))

    # D17: after the pending clear, A's late load must not bring A back.
    def test_pending_clear_ignores_late_load_of_old_picture(self):
        res = self.run_js("""
const m = makePlaceModel();
m.set(A);
m.set(place('Bridge', null, true));
out(act(m.loaded(A.image)));
""")
        self.assertIsNone(res, "the old place's picture must not show once the new place is pending")

    # Plan S9 renewing: a pending place that still carries its old picture
    # (renew) leaves that picture up until the new one is ready.
    def test_pending_with_current_image_keeps_it(self):
        res = self.run_js("""
const m = makePlaceModel();
m.set(A); m.loaded(A.image);
const r = act(m.set(place('Crypt', A.image, true)));
const newer = '/scene-image/crypt.png?v=9';
out({ r, s: act(m.set(place('Crypt', newer, false))), l: act(m.loaded(newer)) });
""")
        self.assertIn(res["r"], (None, {"action": "keep"}))
        self.assertEqual(res["s"], {"action": "show", "url": "/scene-image/crypt.png?v=9"})
        self.assertEqual(res["l"], {"action": "show", "url": "/scene-image/crypt.png?v=9"})

    # D17: a pending place whose image then arrives (the worker's {"place"}
    # event) fades it in.
    def test_pending_then_image_shows(self):
        res = self.run_js("""
const m = makePlaceModel();
m.set(A); m.loaded(A.image);
m.set(place('Bridge', null, true));
const img = '/scene-image/bridge.png?v=3';
out([act(m.set(place('Bridge', img, false))), act(m.loaded(img))]);
""")
        self.assertEqual(res, [{"action": "show", "url": "/scene-image/bridge.png?v=3"}] * 2)

    # AC9 TV side: a place with no image, or no place, clears.
    def test_no_image_or_null_clears(self):
        res = self.run_js("""
const m1 = makePlaceModel(); m1.set(A); m1.loaded(A.image);
const m2 = makePlaceModel(); m2.set(A); m2.loaded(A.image);
const m3 = makePlaceModel(); m3.set(A); m3.loaded(A.image);
out([act(m1.set(place('Road', null, false))), act(m2.set(null)), act(m3.set(undefined))]);
""")
        self.assertEqual(res, [{"action": "clear"}] * 3)

    # S5 Done when: the same URL twice does nothing (while loading and once up).
    def test_same_url_twice_is_a_no_op(self):
        res = self.run_js("""
const m = makePlaceModel();
m.set(A);
const whileLoading = act(m.set(A));
m.loaded(A.image);
const onceUp = act(m.set(A));
const renamed = act(m.set(place('Crypt again', A.image, false)));
out({ whileLoading, onceUp, renamed });
""")
        self.assertIsNone(res["whileLoading"])
        self.assertIsNone(res["onceUp"])
        self.assertIsNone(res["renamed"], "the picture is keyed by its URL, not the place's name")

    # After a clear, the same place coming back shows again (a return visit).
    def test_return_after_clear_shows_again(self):
        res = self.run_js("""
const m = makePlaceModel();
m.set(A); m.loaded(A.image);
m.set(null);
out([act(m.set(A)), act(m.loaded(A.image))]);
""")
        self.assertEqual(res, [{"action": "show", "url": "/scene-image/crypt.png?v=1"}] * 2)


# ── tv.js forwards the place to TV.bg.setPlace ───────────────────────────

_TV_HARNESS = r"""
const calls = [];
let onPayload = null;
let throwOnPlace = false;
function el() {
  return { hidden: false, dataset: {}, textContent: '', style: {}, children: [],
    addEventListener() {}, removeEventListener() {}, appendChild() {}, setAttribute() {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } } };
}
const els = {};
globalThis.document = { getElementById: (id) => (els[id] = els[id] || el()),
  querySelector: () => el(), createElement: () => el(), addEventListener() {} };
const bg = {
  applyScene() {}, setWorldTime() {},
  setPlace(p) { calls.push(p === undefined ? '<undefined>' : p); if (throwOnPlace) throw new Error('boom'); },
};
const TV = {
  page: { token: '', voiceOn: false },
  voice: {
    makeWebAudio: () => ({ unlock: () => Promise.resolve(), isUnlocked: () => true,
      decode: () => Promise.resolve({}), play: () => Promise.resolve(), stop() {} }),
    makeVoice: () => ({ readActions() {}, setSettings() {}, reset() {} }),
  },
  feed: { makeFeedModel: () => ({}), mount: () => ({ apply() {}, settle() {}, render() {}, type() {} }) },
  devices: { makeDeviceModel: () => ({}), mount: () => ({ apply() {} }) },
  panels: { mount: () => ({ render() {} }), makeWaitModel: () => ({ apply() {} }) },
  layout: { resolve: () => ({ mode: 'story', left: [], right: [] }), apply() {} },
  stream: { connect(url, fn) { onPayload = fn; } },
  bg: %(bg)s,
};
globalThis.window = { TV, addEventListener() {}, removeEventListener() {} };
globalThis.setInterval = () => 0;
require(%(tv)s);
if (typeof onPayload !== 'function') throw new Error('tv.js did not connect the stream');
const out = (v) => { console.log(JSON.stringify(v)); process.exit(0); };
const send = (p) => { try { onPayload(p); return null; } catch (e) { return String(e && e.message || e); } };
const PL = { name: 'The Flooded Crypt', slug: 'the-flooded-crypt', image: '/scene-image/the-flooded-crypt.png?v=1', pending: false };
"""


@unittest.skipUnless(NODE, "node is not installed")
class TestTvForwardsPlace(unittest.TestCase):

    def run_tv(self, body: str, bg: str = "bg"):
        if not TV_JS.exists():
            self.fail("static/tv/tv.js is missing")
        script = _TV_HARNESS % {"tv": json.dumps(str(TV_JS)), "bg": bg} + body
        return _run_node(self, script)

    # The worker's small {"place": ...} event.
    def test_place_event(self):
        res = self.run_tv("out({ err: send({ place: PL }), calls });")
        self.assertIsNone(res["err"])
        self.assertEqual(res["calls"], [
            {"name": "The Flooded Crypt", "slug": "the-flooded-crypt",
             "image": "/scene-image/the-flooded-crypt.png?v=1", "pending": False}])

    # AC4 TV side: the place a live / on-connect stats snapshot names.
    def test_stats_place(self):
        res = self.run_tv("out({ err: send({ stats: { players: [], place: PL } }), calls });")
        self.assertIsNone(res["err"])
        self.assertEqual(len(res["calls"]), 1, res["calls"])
        self.assertEqual(res["calls"][0]["image"], "/scene-image/the-flooded-crypt.png?v=1")

    # stats.place: null (the turn sent "location": null) clears.
    def test_stats_place_null(self):
        res = self.run_tv("out({ err: send({ stats: { place: null } }), calls });")
        self.assertIsNone(res["err"])
        self.assertEqual(res["calls"], [None])

    # Stats are a full snapshot: one without a place (the campaign changed and
    # the server popped it) must clear the TV, not leave the old picture.
    def test_stats_snapshot_without_place_clears(self):
        res = self.run_tv("""
send({ stats: { place: PL } });
send({ stats: { players: [{ name: 'Anony' }] } });
out(calls);
""")
        self.assertEqual(len(res), 2, f"setPlace calls: {res}")
        self.assertIn(res[1], (None, "<undefined>"),
                      "a stats snapshot with no place must pass no place to setPlace")

    # Rewind brings the stats of an earlier point back.
    def test_rewind_stats_place(self):
        res = self.run_tv("out({ err: send({ rewind: { stats: { place: PL } } }), calls });")
        self.assertIsNone(res["err"])
        self.assertEqual(len(res["calls"]), 1, res["calls"])
        self.assertEqual(res["calls"][0]["name"], "The Flooded Crypt")

    # The DM's clear wipes the place.
    def test_clear(self):
        res = self.run_tv("out({ err: send({ clear: true }), calls });")
        self.assertIsNone(res["err"])
        self.assertEqual(res["calls"], [None])

    # Narration chunks and other events must not touch the picture.
    def test_other_payloads_do_not_call(self):
        res = self.run_tv("""
send({ hello: { epoch: 'E1' } });
send({ seq: 4, text: 'The door creaks.' });
send({ scene: { name: 'crypt', colors: ['#000', '#111'], particles: 'dust' } });
send({ voice: { on: false } });
out(calls);
""")
        self.assertEqual(res, [])

    # Each call is in try/catch: a throwing setPlace must not break the page.
    def test_throwing_set_place_is_contained(self):
        res = self.run_tv("""
throwOnPlace = true;
const errs = [send({ place: PL }), send({ stats: { place: PL } }),
              send({ rewind: { stats: { place: PL } } }), send({ clear: true })];
out({ errs, n: calls.length });
""")
        self.assertEqual(res["errs"], [None, None, None, None])
        self.assertEqual(res["n"], 4)

    # bg.js failed to load: no TV.bg, place payloads still don't throw.
    def test_no_bg_does_not_throw(self):
        res = self.run_tv("""
out([send({ place: PL }), send({ stats: { place: PL } }), send({ clear: true })]);
""", bg="undefined")
        self.assertEqual(res, [None, None, None])


# ── /tv loads place.js and scene.css ─────────────────────────────────────

def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_tv_place", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestTvLoadsPlace(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(DISPLAY))
        cls.app = _import_app()
        cls.client = cls.app.app.test_client()

    def setUp(self):
        r = self.client.get("/tv")
        self.assertEqual(r.status_code, 200)
        self.body = r.get_data(as_text=True)

    def _script_index(self, name):
        m = re.search(r'<script[^>]+src="/static/tv/%s"' % re.escape(name), self.body)
        return m.start() if m else -1

    def test_place_js_before_bg_js(self):
        i_place, i_bg = self._script_index("place.js"), self._script_index("bg.js")
        self.assertNotEqual(i_place, -1, "/tv does not load place.js")
        self.assertNotEqual(i_bg, -1, "/tv does not load bg.js")
        self.assertLess(i_place, i_bg, "place.js must load before bg.js (bg.js uses TV.place)")

    def test_scene_css_linked(self):
        self.assertRegex(self.body, r'<link[^>]+href="/static/tv/scene\.css"', "/tv does not link scene.css")

    def test_place_js_served(self):
        r = self.client.get("/static/tv/place.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("javascript", r.content_type)
        r.close()

    def test_scene_css_served(self):
        r = self.client.get("/static/tv/scene.css")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content_type.startswith("text/css"), r.content_type)
        r.close()


if __name__ == "__main__":
    unittest.main()
