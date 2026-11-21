"""Moves and spell areas play when their block starts (battle-map spec, plan
step S9).

AC4 (TV) — a move attached to a later narration block animates when that
      block starts, not before; it never delays the text or voice.
AC10 — a spell area outlines its squares for a few seconds (3 s), starting
      with its narration block.
AC11 — reload / reconnect draws the current state with nothing replayed; a
      backgrounded tab (and rewind, clear, a stale block) jumps to the
      current state (flushAll, 90 s safety flush).
D12, D14, D17.

Contract assumed for skills/dnd/display/static/map/fx.js (the plan's fx.js
row: "pure state machine; timers are injected"), attached to window.TV.fx
and exported to `module` for node:

  makeFx(opts) -> fx
    opts.now()                 ms clock (injected)
    opts.setTimeout(fn, ms)    injected timers
    opts.clearTimeout(id)
    opts.onChange()            optional: called when what is shown changes
  fx.arrive(id, map_fx)        a live block was queued; map_fx may be
                               null/undefined (a block with no effects).
                               map_fx = {moves:[{name,x,y,path?,hp_state?}],
                               area:{squares:[[x,y]..],shape,label?},
                               attacks:[..]} (send.py / _clean_map_fx)
  fx.start(id)                 the block's paced reveal began: play it
  fx.skip(id)                  the block is history / skipped: snap, no anim
  fx.setTarget(map)            the latest stats.map (cleaned)
  fx.flushAll()                snap everything to the target, no animation
  fx.shownState() -> {
    tokens: [...]              tokens as they should be drawn now (name, x, y,
                               hp_state, ...). `map.tokens` is accepted too.
    slides: {name: path|{path}}  tokens sliding now (absent/empty = none)
    area:   {squares, label?, shape?} | null   the area outlined now
                               (`areas: [..]` is accepted, first entry used)
  }
  fx.tick?()                   optional; the harness calls it after every
                               clock advance if it exists.

Timings: a slide ends within ~1 s (600 ms planned), an area lasts 3 s, a
block that never starts is flushed after 90 s.

Feed wiring (plan, "Timing on the TV"): feed.js / voice.js call
TV.map.blockArrived(seq, map_fx) when a live block is queued,
TV.map.blockStarted(seq) when its paced reveal begins and
TV.map.blockSkipped(seq) for history blocks and settleAll(); each call in
try/catch. These run the real feed.mount() + makeVoice() with the fake DOM
of tests/test_tv_voice_feed.py.

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

from tests import test_tv_voice_feed as tvf

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "skills" / "dnd" / "display"
FX_JS = DISPLAY / "static" / "map" / "fx.js"
TV_STATIC = DISPLAY / "static" / "tv"
FEED_JS = TV_STATIC / "feed.js"
VOICE_JS = TV_STATIC / "voice.js"
MAP_JS = TV_STATIC / "map.js"
TV_JS = TV_STATIC / "tv.js"
TV_HTML = DISPLAY / "templates" / "tv.html"
NODE = shutil.which("node")


def _run_node(tc: unittest.TestCase, script: str, timeout: int = 40):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
        path = f.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True,
                           encoding="utf-8", timeout=timeout)
    finally:
        pathlib.Path(path).unlink(missing_ok=True)
    if r.returncode != 0:
        tc.fail("node failed:\n" + (r.stderr or r.stdout)[-1500:])
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    tc.assertTrue(lines, "scenario printed nothing")
    return json.loads(lines[-1])


# ── fx.js: the effect timeline with fake timers ──────────────────────────

_FX_PRELUDE = r"""
globalThis.window = globalThis.window || { TV: {} };
const FX = %(fx)s;
const mod = require(FX);
if (!mod || typeof mod.makeFx !== 'function') throw new Error('fx.js does not export makeFx()');

// Fake clock: timers fire only when the test advances time.
let NOW = 1000000;
let timers = [];
let nextId = 1;
const clock = {
  now: () => NOW,
  setTimeout: (fn, ms) => { const id = nextId++; timers.push({ id, at: NOW + Math.max(0, ms || 0), fn }); return id; },
  clearTimeout: (id) => { timers = timers.filter((t) => t.id !== id); },
};
let changes = 0;
const fx = mod.makeFx(Object.assign({ onChange: () => { changes++; } }, clock));
for (const k of ['arrive', 'start', 'skip', 'setTarget', 'flushAll', 'shownState']) {
  if (typeof fx[k] !== 'function') throw new Error('makeFx() result has no ' + k + '()');
}
function advance(ms) {
  const end = NOW + ms;
  for (;;) {
    timers.sort((a, b) => a.at - b.at || a.id - b.id);
    const t = timers[0];
    if (!t || t.at > end) break;
    timers.shift();
    NOW = t.at;
    t.fn();
  }
  NOW = end;
  if (typeof fx.tick === 'function') fx.tick();
}
function S() {
  const s = fx.shownState() || {};
  const toks = Array.isArray(s.tokens) ? s.tokens : ((s.map && s.map.tokens) || []);
  const pos = {};
  for (const t of toks) pos[t.name] = [t.x, t.y, t.hp_state || null];
  const slides = {};
  for (const [k, v] of Object.entries(s.slides || {})) {
    if (!v) continue;
    slides[k] = Array.isArray(v) ? v : (v && Array.isArray(v.path) ? v.path : true);
  }
  let area = s.area !== undefined ? s.area : (Array.isArray(s.areas) && s.areas.length ? s.areas[0] : null);
  if (area) area = { squares: area.squares || null, label: area.label || null };
  return { pos, slides, area };
}
const out = (v) => console.log(JSON.stringify(v));

const CELLS = ['##########', '#........#', '#........#', '#........#', '#........#', '#........#', '##########'];
function M(tokens) { return { grid: { w: 10, h: 7 }, cells: CELLS, tokens }; }
const HAVOC = (x, y, hp) => ({ name: 'Havoc', x, y, kind: 'pc', size: 1, hp: hp || [30, 30] });
const RAT = (x, y, st) => ({ name: 'Giant Rat', x, y, kind: 'npc', size: 1, hp_state: st || 'healthy' });
const M0 = M([HAVOC(1, 1), RAT(2, 2)]);
const PATH = [[2, 2], [3, 2], [4, 2], [5, 2]];
const MOVE = { moves: [{ name: 'Giant Rat', x: 5, y: 2, path: PATH, hp_state: 'bloodied' }] };
const M1 = M([HAVOC(1, 1), RAT(5, 2, 'bloodied')]);
"""


@unittest.skipUnless(NODE, "node is not installed")
class _FxCase(unittest.TestCase):

    def run_js(self, body: str):
        if not FX_JS.exists():
            self.fail("not built yet: static/map/fx.js")
        return _run_node(self, _FX_PRELUDE % {"fx": json.dumps(str(FX_JS))} + body)


class TestMoveWaitsForItsBlock(_FxCase):

    # AC4 / D12 — S9 "Done when": A and B arrive, B with a move, then
    # stats.map with the moved token. The token stays put through start(A)
    # and moves (sliding, along its path) on start(B).
    def test_token_moves_on_its_own_block_not_before(self):
        r = self.run_js("""
fx.setTarget(M0);
const loaded = S();
fx.arrive(1, null);
fx.arrive(2, MOVE);
fx.setTarget(M1);
const afterTarget = S();
fx.start(1);
advance(2000);
const afterA = S();
const c0 = changes;
fx.start(2);
const onB = S();
const changed = changes > c0;
advance(1000);
out({ loaded, afterTarget, afterA, onB, changed, settled: S() });
""")
        self.assertEqual(r["loaded"]["pos"]["Giant Rat"][:2], [2, 2])
        self.assertEqual(r["afterTarget"]["pos"]["Giant Rat"][:2], [2, 2],
                         "the rat jumped as soon as stats.map came in, before its block")
        self.assertEqual(r["afterA"]["pos"]["Giant Rat"][:2], [2, 2],
                         "the rat moved on block A; its move belongs to block B")
        self.assertEqual(r["onB"]["pos"]["Giant Rat"][:2], [5, 2], "start(B) must move the rat")
        self.assertIn("Giant Rat", r["onB"]["slides"], "start(B) must slide the rat, not snap it")
        self.assertEqual(r["onB"]["slides"]["Giant Rat"], [[2, 2], [3, 2], [4, 2], [5, 2]],
                         "the slide must carry the move's path (fog follows it)")
        self.assertTrue(r["changed"], "onChange must fire when the block plays")
        self.assertEqual(r["settled"]["slides"], {}, "a slide must end within ~1 s (D17)")
        self.assertEqual(r["settled"]["pos"]["Giant Rat"][:2], [5, 2])

    # AC3 ring on AC4 timing — a move's hp_state shows when its block plays,
    # not when stats.map arrives (the token keeps its on-screen state).
    def test_moving_tokens_ring_updates_with_its_block(self):
        r = self.run_js("""
fx.setTarget(M0);
fx.arrive(2, MOVE);
fx.setTarget(M1);
const before = S();
fx.start(2);
advance(1000);
out({ before, after: S() });
""")
        self.assertEqual(r["before"]["pos"]["Giant Rat"][2], "healthy",
                         "the rat looked bloodied before the block that hurts it")
        self.assertEqual(r["after"]["pos"]["Giant Rat"][2], "bloodied")

    # Plan: "everything else takes the latest stats.map at once" — another
    # token's HP, a new token and a removed token without effects.
    def test_tokens_without_effects_take_the_target_at_once(self):
        r = self.run_js("""
const BAT = { name: 'Bat', x: 7, y: 4, kind: 'npc', size: 1, hp_state: 'healthy' };
fx.setTarget(M([HAVOC(1, 1), RAT(2, 2), BAT]));
fx.arrive(2, MOVE);
const GOB = { name: 'Goblin', x: 8, y: 1, kind: 'npc', size: 1, hp_state: 'healthy' };
fx.setTarget(M([HAVOC(1, 3, [12, 30]), RAT(5, 2, 'bloodied'), GOB]));
out(S());
""")
        self.assertEqual(r["pos"]["Havoc"][:2], [1, 3], "Havoc has no pending effect: take the target")
        self.assertIn("Goblin", r["pos"], "a new token must appear at once")
        self.assertNotIn("Bat", r["pos"], "a removed token with no pending effect must go at once")
        self.assertEqual(r["pos"]["Giant Rat"][:2], [2, 2], "the rat waits for its block")
        self.assertEqual(r["slides"], {}, "nothing slides before a block starts")

    def test_removed_token_with_pending_effect_stays_until_flushed(self):
        r = self.run_js("""
fx.setTarget(M0);
fx.arrive(2, MOVE);
fx.setTarget(M([HAVOC(1, 1)]));
const pending = S();
fx.flushAll();
out({ pending, flushed: S() });
""")
        self.assertIn("Giant Rat", r["pending"]["pos"],
                      "a token with an unplayed effect keeps its on-screen state")
        self.assertNotIn("Giant Rat", r["flushed"]["pos"])

    # "keeps its on-screen state until its LAST effect plays" — two moves of
    # one token in blocks B and C.
    def test_two_moves_of_one_token_play_in_turn(self):
        r = self.run_js("""
fx.setTarget(M0);
fx.arrive(2, { moves: [{ name: 'Giant Rat', x: 3, y: 2, path: [[2, 2], [3, 2]] }] });
fx.arrive(3, { moves: [{ name: 'Giant Rat', x: 5, y: 4, path: [[3, 2], [4, 3], [5, 4]] }] });
fx.setTarget(M([HAVOC(1, 1), RAT(5, 4)]));
const s0 = S();
fx.start(2); advance(1000);
const s1 = S();
fx.start(3);
const s2 = S();
advance(1000);
out({ s0, s1, s2, s3: S() });
""")
        self.assertEqual(r["s0"]["pos"]["Giant Rat"][:2], [2, 2])
        self.assertEqual(r["s1"]["pos"]["Giant Rat"][:2], [3, 2],
                         "after block B the rat is at B's square, not the final one")
        self.assertEqual(r["s2"]["pos"]["Giant Rat"][:2], [5, 4])
        self.assertEqual(r["s2"]["slides"].get("Giant Rat"), [[3, 2], [4, 3], [5, 4]])
        self.assertEqual(r["s3"]["pos"]["Giant Rat"][:2], [5, 4])

    # A block that starts before stats.map lands still plays its move from
    # the move's own x, y.
    def test_block_started_before_stats_uses_the_moves_square(self):
        r = self.run_js("""
fx.setTarget(M0);
fx.arrive(2, MOVE);
fx.start(2);
const s = S();
fx.setTarget(M1);
advance(1000);
out({ s, after: S() });
""")
        self.assertEqual(r["s"]["pos"]["Giant Rat"][:2], [5, 2])
        self.assertEqual(r["after"]["pos"]["Giant Rat"][:2], [5, 2])


class TestSkipsAndFlushes(_FxCase):

    # AC11 / D17 — skip(B) snaps with no animation flag.
    def test_skip_snaps_without_animation(self):
        r = self.run_js("""
fx.setTarget(M0);
fx.arrive(1, null);
fx.arrive(2, MOVE);
fx.setTarget(M1);
fx.skip(2);
const s = S();
fx.start(2);
out({ s, restarted: S() });
""")
        self.assertEqual(r["s"]["pos"]["Giant Rat"], [5, 2, "bloodied"])
        self.assertEqual(r["s"]["slides"], {}, "a skipped block must not animate")
        self.assertEqual(r["restarted"]["slides"], {}, "a skipped block must not play later")

    # AC11 — flushAll gives the target state, no slides, no area.
    def test_flush_all_gives_the_target(self):
        r = self.run_js("""
fx.setTarget(M0);
fx.arrive(2, MOVE);
fx.arrive(3, { area: { squares: [[4, 4], [5, 4]], shape: 'sphere', label: 'Fireball' } });
fx.setTarget(M1);
fx.start(3);
fx.flushAll();
const s = S();
fx.start(2);
out({ s, later: S() });
""")
        self.assertEqual(r["s"]["pos"]["Giant Rat"], [5, 2, "bloodied"])
        self.assertEqual(r["s"]["pos"]["Havoc"][:2], [1, 1])
        self.assertEqual(r["s"]["slides"], {})
        self.assertIsNone(r["s"]["area"], "flushAll snaps to the current state: no area left")
        self.assertEqual(r["later"]["slides"], {}, "a flushed block must not replay when it starts")

    # AC11 / D18 — reconnect: a fresh timeline given stats.map draws it at
    # once; a later setTarget with nothing pending also applies at once.
    def test_set_target_without_pending_effects_applies_at_once(self):
        r = self.run_js("""
fx.setTarget(M1);
const first = S();
fx.setTarget(M([HAVOC(2, 3), RAT(6, 4, 'near_death')]));
out({ first, second: S() });
""")
        self.assertEqual(r["first"]["pos"]["Giant Rat"], [5, 2, "bloodied"])
        self.assertEqual(r["first"]["slides"], {})
        self.assertIsNone(r["first"]["area"])
        self.assertEqual(r["second"]["pos"]["Giant Rat"], [6, 4, "near_death"])
        self.assertEqual(r["second"]["pos"]["Havoc"][:2], [2, 3])
        self.assertEqual(r["second"]["slides"], {}, "no effects pending: nothing to animate")

    # Plan — the 90 s safety flush for a block that never starts.
    def test_pending_effect_older_than_90s_is_flushed(self):
        r = self.run_js("""
fx.setTarget(M0);
fx.arrive(2, MOVE);
fx.setTarget(M1);
advance(89000);
const at89 = S();
advance(1500);
const at90 = S();
fx.start(2);
out({ at89, at90, late: S() });
""")
        self.assertEqual(r["at89"]["pos"]["Giant Rat"][:2], [2, 2], "flushed too early")
        self.assertEqual(r["at90"]["pos"]["Giant Rat"], [5, 2, "bloodied"],
                         "a block that never started must be flushed after 90 s")
        self.assertEqual(r["at90"]["slides"], {})
        self.assertEqual(r["late"]["slides"], {}, "a flushed block must not replay")

    # Out of order / unknown ids are ignored safely; the end state is right.
    def test_unknown_and_out_of_order_ids_are_safe(self):
        r = self.run_js("""
fx.setTarget(M0);
fx.start(999); fx.skip(998);
const s0 = S();
fx.arrive(1, { moves: [{ name: 'Havoc', x: 3, y: 3, path: [[1, 1], [2, 2], [3, 3]] }] });
fx.arrive(2, MOVE);
fx.setTarget(M([HAVOC(3, 3), RAT(5, 2, 'bloodied')]));
fx.start(2);
fx.start(1);
advance(1000);
const s1 = S();
fx.start(2);
const again = S();
fx.arrive(5, { attacks: [{ from: 'Havoc', to: 'Giant Rat', kind: 'melee', hit: true }] });
fx.start(5);
fx.arrive(6, {});
fx.start(6);
out({ s0, s1, again, s2: S() });
""")
        self.assertEqual(r["s0"]["pos"]["Giant Rat"][:2], [2, 2])
        self.assertEqual(r["s1"]["pos"]["Giant Rat"], [5, 2, "bloodied"])
        self.assertEqual(r["s1"]["pos"]["Havoc"][:2], [3, 3])
        self.assertEqual(r["again"]["slides"], {}, "starting a played block again must not replay it")
        self.assertEqual(r["s2"]["pos"]["Giant Rat"][:2], [5, 2])


class TestAreas(_FxCase):

    # AC10 / D14 — an area shows for 3 s from its block's start, then goes.
    def test_area_outlines_for_three_seconds_from_its_block(self):
        r = self.run_js("""
fx.setTarget(M1);
const SQ = [[4, 4], [5, 4], [4, 5], [5, 5]];
fx.arrive(1, null);
fx.arrive(2, { area: { squares: SQ, shape: 'sphere', label: 'Fireball' } });
const queued = S();
fx.start(1);
const onA = S();
fx.start(2);
const onB = S();
advance(2800);
const at28 = S();
const c0 = changes;
advance(400);
out({ queued, onA, onB, at28, at32: S(), changed: changes > c0 });
""")
        self.assertIsNone(r["queued"]["area"], "the area showed before its block")
        self.assertIsNone(r["onA"]["area"], "the area showed on the block before its own")
        self.assertIsNotNone(r["onB"]["area"], "the area must outline when its block starts")
        self.assertEqual(sorted(map(tuple, r["onB"]["area"]["squares"])),
                         sorted([(4, 4), (5, 4), (4, 5), (5, 5)]))
        self.assertEqual(r["onB"]["area"]["label"], "Fireball")
        self.assertIsNotNone(r["at28"]["area"], "the area must last ~3 s")
        self.assertIsNone(r["at32"]["area"], "the area must be gone after 3 s")
        self.assertTrue(r["changed"], "onChange must fire when the area ends")

    def test_skipped_area_never_shows(self):
        r = self.run_js("""
fx.setTarget(M1);
fx.arrive(2, { area: { squares: [[4, 4]], shape: 'cube', label: 'Web' } });
fx.skip(2);
const s = S();
fx.start(2);
out({ s, after: S() });
""")
        self.assertIsNone(r["s"]["area"])
        self.assertIsNone(r["after"]["area"], "a skipped area must not play later")


# ── Wiring: tv.html, map.js, feed.js / voice.js ──────────────────────────

class TestStaticWiring(unittest.TestCase):

    def test_tv_html_loads_fx_before_the_map_adapter(self):
        html = TV_HTML.read_text(encoding="utf-8")
        self.assertIn('src="/static/map/fx.js"', html, "tv.html must load /static/map/fx.js")
        self.assertLess(html.index('/static/map/fx.js'), html.index('/static/tv/map.js'),
                        "fx.js must load before static/tv/map.js uses it")

    def test_feed_or_voice_calls_the_three_hooks(self):
        src = FEED_JS.read_text(encoding="utf-8") + "\n" + VOICE_JS.read_text(encoding="utf-8")
        for hook in ("blockArrived", "blockStarted", "blockSkipped"):
            self.assertIn(hook, src, f"feed.js / voice.js must call TV.map.{hook}")

    def test_each_hook_call_is_inside_try(self):
        for path in (FEED_JS, VOICE_JS):
            src = path.read_text(encoding="utf-8")
            for m in re.finditer(r"\bblock(Arrived|Started|Skipped)\s*\(", src):
                before = src[max(0, m.start() - 200):m.start()]
                self.assertRegex(before, r"\btry\s*\{",
                                 f"{path.name}: block{m.group(1)}() must be called inside try/catch")

    # AC11 — the TV flushes on visibilitychange (and rewind/clear/reconnect).
    def test_tv_flushes_on_visibility_change(self):
        src = TV_JS.read_text(encoding="utf-8") + "\n" + MAP_JS.read_text(encoding="utf-8")
        self.assertIn("visibilitychange", src, "tv.js / map.js must listen for visibilitychange")
        self.assertIn("flushAll", src, "tv.js / map.js must call flushAll()")


@unittest.skipUnless(NODE, "node is not installed")
class TestMapAdapterHooks(unittest.TestCase):

    # map.js exposes the three hooks and they are safe before any map draws.
    def test_map_js_exposes_safe_hooks(self):
        script = f"""
globalThis.window = globalThis;
try {{ require({json.dumps(str(FX_JS))}); }} catch (e) {{}}
const m = require({json.dumps(str(MAP_JS))});
const kinds = {{}};
for (const k of ['blockArrived', 'blockStarted', 'blockSkipped']) kinds[k] = typeof m[k];
let err = null;
try {{
  if (typeof m.blockArrived === 'function') m.blockArrived(4, {{ moves: [{{ name: 'Rat', x: 1, y: 1 }}] }});
  if (typeof m.blockStarted === 'function') m.blockStarted(4);
  if (typeof m.blockSkipped === 'function') m.blockSkipped(5);
}} catch (e) {{ err = String(e); }}
console.log(JSON.stringify({{ kinds, err }}));
"""
        r = _run_node(self, script)
        for k in ("blockArrived", "blockStarted", "blockSkipped"):
            self.assertEqual(r["kinds"][k], "function", f"TV.map.{k} is missing")
        self.assertIsNone(r["err"], "a hook threw before any map was drawn")


_HOOKS = r"""
const calls = [];
let mapThrows = false;
const spy = {};
for (const k of ['blockArrived', 'blockStarted', 'blockSkipped']) {
  spy[k] = (seq, fx) => {
    calls.push({ k, seq, fx: fx || null, shown: shown(), ends: ev.end.slice() });
    if (mapThrows) throw new Error('map broke');
  };
}
"""


@unittest.skipUnless(NODE, "node is not installed")
class TestFeedHooks(unittest.TestCase):
    """The real feed + voice (fake DOM and audio from test_tv_voice_feed)."""

    def run_js(self, body: str, opts_extra: str = "") -> dict:
        missing = [p.name for p in (FEED_JS, VOICE_JS) if not p.exists()]
        if missing:
            self.fail("not built yet: static/tv/" + ", ".join(missing))
        prelude = tvf._PRELUDE % {"feed": json.dumps(str(FEED_JS)), "voice": json.dumps(str(VOICE_JS))}
        # The map is reachable as window.TV.map / TV.map (global): feed.js's
        # root is `window`, voice.js falls back to globalThis.
        prelude = ("globalThis.window = globalThis;\nglobalThis.TV = globalThis.TV || {};\n"
                   + prelude.replace("feed = mount(el, makeFeedModel(), { voice });", "")
                   + _HOOKS
                   + "globalThis.TV.map = spy;\n"
                   + "feed = mount(el, makeFeedModel(), { voice, map: spy });\n")
        script = prelude + "\n(async () => {\n" + body + "\nprocess.exit(0);\n})().catch((e) => { console.error(e); process.exit(1); });\n"
        return _run_node(self, script)

    # AC4 / D12 — blockArrived on queueing, blockStarted when block 2 starts
    # typing (after block 1 has played), not when it arrives.
    def test_started_fires_when_the_block_starts_typing(self):
        fx = {"moves": [{"name": "Giant Rat", "x": 5, "y": 2, "path": [[2, 2], [5, 2]]}]}
        r = self.run_js(f"""
feed.apply({{hello: {{epoch: 'E1'}}}});
feed.apply(T(1, 'The rat hisses at Havoc and bares its teeth.'));
feed.apply(T(2, 'It scurries along the wall.', {{map_fx: {json.dumps(fx)}}}));
const atArrival = calls.slice();
await until(() => shown().some((b) => b[0] === 2 && !b[1]), 8000);
out({{atArrival, calls, end: shown()}});
""")
        arrived = [c for c in r["calls"] if c["k"] == "blockArrived" and c["seq"] == 2]
        self.assertTrue(arrived, f"no blockArrived(2, map_fx): {r['calls']}")
        self.assertEqual(arrived[0]["fx"], fx, "blockArrived must pass the block's map_fx")
        self.assertFalse(any(c["k"] == "blockStarted" and c["seq"] == 2 for c in r["atArrival"]),
                         "blockStarted(2) fired when block 2 arrived, before it played")
        started = [c for c in r["calls"] if c["k"] == "blockStarted" and c["seq"] == 2]
        self.assertEqual(len(started), 1, f"blockStarted(2) must fire once: {r['calls']}")
        self.assertIn("The rat hisses at Havoc and bares its teeth.", started[0]["ends"],
                      "blockStarted(2) fired before block 1's voice finished")
        self.assertFalse(any(c["k"] == "blockSkipped" and c["seq"] == 2 for c in r["calls"]))
        self.assertLess(r["calls"].index(arrived[0]), r["calls"].index(started[0]))

    # AC11 — a history block (replay) is a skip, never a start.
    def test_history_block_is_skipped(self):
        fx = {"moves": [{"name": "Giant Rat", "x": 5, "y": 2}]}
        r = self.run_js(f"""
feed.apply({{hello: {{epoch: 'E1'}}}});
feed.apply({{replay_batch: [T(3, 'Earlier, the rat moved.', {{map_fx: {json.dumps(fx)}}})]}});
await sleep(200);
out({{calls}});
""")
        self.assertTrue(any(c["k"] == "blockSkipped" and c["seq"] == 3 for c in r["calls"]),
                        f"a history block must call blockSkipped(seq): {r['calls']}")
        self.assertFalse(any(c["k"] == "blockStarted" and c["seq"] == 3 for c in r["calls"]))

    # AC11 / D17 — settleAll() (voice off, rewind, clear, restart) skips the
    # queued blocks; they never start later.
    def test_settle_skips_queued_blocks(self):
        fx = {"area": {"squares": [[4, 4]], "shape": "sphere", "label": "Fireball"}}
        r = self.run_js(f"""
feed.apply({{hello: {{epoch: 'E1'}}}});
feed.apply(T(4, {json.dumps(tvf.N1)}));
feed.apply(T(5, 'Fire blooms across the room.', {{map_fx: {json.dumps(fx)}}}));
await until(() => ev.play.length >= 1, 3000);
voiceOn = false;
feed.settle();
await sleep(600);
out({{calls, end: shown()}});
""")
        self.assertTrue(any(c["k"] == "blockSkipped" and c["seq"] == 5 for c in r["calls"]),
                        f"settle() must call blockSkipped for a queued block: {r['calls']}")
        self.assertFalse(any(c["k"] == "blockStarted" and c["seq"] == 5 for c in r["calls"]))

    # D17 — a throwing map never breaks the feed: every block still shows.
    def test_throwing_map_does_not_break_the_feed(self):
        fx = {"moves": [{"name": "Giant Rat", "x": 5, "y": 2}]}
        r = self.run_js(f"""
mapThrows = true;
feed.apply({{hello: {{epoch: 'E1'}}}});
feed.apply({{replay_batch: [T(1, 'Earlier.', {{map_fx: {json.dumps(fx)}}})]}});
feed.apply(T(2, 'The rat hisses.'));
feed.apply(T(3, 'It scurries along the wall.', {{map_fx: {json.dumps(fx)}}}));
await until(() => shown().some((b) => b[0] === 3 && !b[1]), 8000);
out({{n: calls.length, end: shown()}});
""")
        self.assertGreater(r["n"], 0, "setup: the hooks were never called")
        self.assertEqual(r["end"], [[1, False, "Earlier."], [2, False, "The rat hisses."],
                                    [3, False, "It scurries along the wall."]])


if __name__ == "__main__":
    unittest.main()
