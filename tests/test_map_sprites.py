"""Attack sprites (battle-map spec, plan step S11, milestone 2).

AC15 — an attack in the turn call (attacker, target, kind, hit) plays the
      right SVG sprite when its block starts: a sword for melee, an arrow for
      a ranged weapon, a bolt for a spell. A hit flashes the target, a miss
      shows a whiff (melee) or a deflection (arrow, bolt).
AC16 — attacks never delay narration, and a lagging TV skips them.
D15, D16 (drawn in code: SVG + CSS, no downloads), D17 (within ~1 s; skipped
blocks play nothing).

map_fx.attacks, as send.py / _clean_map_fx forward it:
    [{"from"?: name, "to": name, "kind": "melee"|"ranged"|"spell", "hit": bool}]
"from" is absent when the attacker is hidden; an attack on a hidden target
never reaches the TV.

Contract assumed (QA, tests-first; the build may add fields, not rename):

skills/dnd/display/static/map/sprites.js -> window.TV.sprites and module:

  plan(attack, ctx) -> item | null
      ctx.pos       {name: [x, y]}   squares of the tokens on screen
      ctx.visible   (name) -> bool   is that token visible now (fog); a name
                                     missing from ctx.pos counts as not visible
      item = {
        sprite:      'sword' | 'arrow' | 'bolt' | null
                     null when the attacker is hidden (no "from") or not
                     visible: a target-only effect.
        outcome:     'hit' | 'whiff' | 'deflect' | null
                     hit -> 'hit'; miss -> 'whiff' for melee, 'deflect' for
                     ranged/spell. null when the target is not visible
                     (only the attacker's swing or launch plays).
        targetClass: CLASSES[outcome] or null (CSS class put on the target)
        from, to:    [x, y] or null
        duration:    ms, <= MAX_ATTACK_MS (900)
      }
      null when nothing at all can be shown (bad kind, no target, neither
      side visible).
  schedule(attacks, ctx) -> [item + {delay}] one per attack that plans to an
      item, in order, back to back from delay 0; any item that would end
      after MAX_TOTAL_MS (2000) is marked {snapped: true} and carries no
      sprite and no targetClass (it snaps: no animation).
  svg(item, squareSize) -> SVG markup string for the sprite/outcome (D16),
      classed with the sprite id and targetClass; no external URLs.
  CLASSES = {hit, whiff, deflect}  distinct non-empty CSS class names
  MAX_ATTACK_MS (<= 900), MAX_TOTAL_MS (2000)

skills/dnd/display/static/map/fx.js (attack playback): the current
`api.onAttacks` hook is replaced by state, so the board draws from one place:

  makeFx(opts): opts.isVisible(name) -> bool  optional fog check; default:
      the token is in shownState().tokens.
  start(id) plans the block's attacks with sprites.schedule() using the
      squares drawn after that block's moves, and
  shownState().attacks -> [item + {delay, key}] the attack effects playing
      now (non-snapped only). Each is removed when delay + duration has
      passed (onChange fires); [] when none. skip(id) adds none; flushAll()
      clears them.

board.js render(box, view) draws view.attacks into svg.map-fx and the TV
adapter (static/tv/map.js) passes shownState().attacks as view.attacks.

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
MAP_STATIC = DISPLAY / "static" / "map"
SPRITES_JS = MAP_STATIC / "sprites.js"
FX_JS = MAP_STATIC / "fx.js"
BOARD_JS = MAP_STATIC / "board.js"
MAP_CSS = MAP_STATIC / "map.css"
TV_MAP_JS = DISPLAY / "static" / "tv" / "map.js"
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


# ── sprites.js: the pure builders ────────────────────────────────────────

_SPRITES_PRELUDE = r"""
globalThis.window = globalThis.window || { TV: {} };
const SP = require(%(sprites)s);
for (const k of ['plan', 'schedule', 'svg']) {
  if (!SP || typeof SP[k] !== 'function') throw new Error('sprites.js does not export ' + k + '()');
}
if (!SP.CLASSES) throw new Error('sprites.js does not export CLASSES');
const out = (v) => console.log(JSON.stringify(v));
const POS = { Havoc: [1, 1], Lyra: [1, 3], 'Giant Rat': [3, 1], Bat: [6, 4] };
const ALL = () => true;
const CTX = { pos: POS, visible: ALL };
const only = (...names) => ({ pos: POS, visible: (n) => names.includes(n) });
const A = (kind, hit, from, to) => {
  const a = { to: to || 'Giant Rat', kind, hit };
  if (from !== null) a.from = from || 'Havoc';
  return a;
};
"""


@unittest.skipUnless(NODE, "node is not installed")
class _SpritesCase(unittest.TestCase):

    def run_js(self, body: str):
        if not SPRITES_JS.exists():
            self.fail("not built yet: static/map/sprites.js")
        return _run_node(self, _SPRITES_PRELUDE % {"sprites": json.dumps(str(SPRITES_JS))} + body)


class TestSpritePlan(_SpritesCase):

    # AC15 / S11 "Each kind gives its sprite id."
    def test_each_kind_gives_its_sprite(self):
        r = self.run_js("""
out(['melee', 'ranged', 'spell'].map((k) => (SP.plan(A(k, true), CTX) || {}).sprite));
""")
        self.assertEqual(r, ["sword", "arrow", "bolt"])

    # AC15 / S11 "hit gives the flash class and miss the whiff or deflection."
    def test_hit_flashes_and_miss_whiffs_or_deflects(self):
        r = self.run_js("""
const res = {};
for (const k of ['melee', 'ranged', 'spell']) for (const h of [true, false]) {
  const p = SP.plan(A(k, h), CTX) || {};
  res[k + (h ? '-hit' : '-miss')] = [p.outcome, p.targetClass];
}
out({ res, C: SP.CLASSES });
""")
        C = r["C"]
        for k in ("hit", "whiff", "deflect"):
            self.assertIsInstance(C.get(k), str, f"CLASSES.{k} must be a CSS class name")
            self.assertTrue(C[k])
        self.assertEqual(len({C["hit"], C["whiff"], C["deflect"]}), 3, "the three classes must differ")
        res = r["res"]
        for k in ("melee", "ranged", "spell"):
            self.assertEqual(res[k + "-hit"], ["hit", C["hit"]], f"a {k} hit must flash the target")
        self.assertEqual(res["melee-miss"], ["whiff", C["whiff"]], "a melee miss is a whiff")
        self.assertEqual(res["ranged-miss"], ["deflect", C["deflect"]], "a missed arrow glances off")
        self.assertEqual(res["spell-miss"], ["deflect", C["deflect"]], "a missed bolt glances off")

    def test_plan_carries_squares_of_attacker_and_target(self):
        r = self.run_js("out(SP.plan(A('ranged', true), CTX));")
        self.assertEqual(r["from"], [1, 1])
        self.assertEqual(r["to"], [3, 1])

    # S11 "Every sprite's duration is <= 1000 ms" and "each attack <= 900 ms".
    def test_every_duration_within_limits(self):
        r = self.run_js("""
const d = [];
for (const k of ['melee', 'ranged', 'spell']) for (const h of [true, false]) {
  d.push(SP.plan(A(k, h), CTX).duration);
  d.push(SP.plan(A(k, h, null), CTX).duration);            // target only
  d.push(SP.plan(A(k, h), only('Havoc')).duration);        // attacker only
}
// a long shot across the board must not take longer
d.push(SP.plan({ from: 'Havoc', to: 'Far', kind: 'ranged', hit: true },
               { pos: Object.assign({ Far: [29, 19] }, POS), visible: ALL }).duration);
out({ d, max: SP.MAX_ATTACK_MS, total: SP.MAX_TOTAL_MS });
""")
        for ms in r["d"]:
            self.assertIsInstance(ms, (int, float))
            self.assertGreater(ms, 0)
            self.assertLessEqual(ms, 900, "each attack lasts <= 900 ms (S11), sprites <= 1 s (D17)")
        self.assertLessEqual(r["max"], 900)
        self.assertEqual(r["total"], 2000)

    # S11 "A hidden attacker gives a target-only effect."
    def test_hidden_attacker_is_target_only(self):
        r = self.run_js("""
out([SP.plan(A('melee', true, null), CTX), SP.plan(A('spell', false, null), CTX)]);
""")
        hit, miss = r
        self.assertIsNone(hit["sprite"], "no swing from a hidden attacker")
        self.assertIsNone(hit["from"])
        self.assertEqual(hit["outcome"], "hit")
        self.assertEqual(hit["to"], [3, 1])
        self.assertIsNone(miss["sprite"])
        self.assertEqual(miss["outcome"], "deflect")

    # S11: an attacker that is not visible (fog) -> only the target's effect.
    def test_attacker_in_fog_is_target_only(self):
        r = self.run_js("out(SP.plan(A('ranged', true), only('Giant Rat')));")
        self.assertIsNone(r["sprite"], "an arrow from an unseen archer must not be drawn")
        self.assertIsNone(r["from"], "the unseen attacker's square must not leak")
        self.assertEqual(r["outcome"], "hit")

    # S11: a target that is not visible -> only the attacker's swing/launch.
    def test_target_in_fog_is_attacker_only(self):
        r = self.run_js("""
out([SP.plan(A('melee', true), only('Havoc')), SP.plan(A('spell', false), only('Havoc'))]);
""")
        for p, sprite in zip(r, ("sword", "bolt")):
            self.assertEqual(p["sprite"], sprite)
            self.assertIsNone(p["outcome"], "no flash on a target the party cannot see")
            self.assertIsNone(p["targetClass"])

    def test_nothing_visible_or_bad_attack_plans_nothing(self):
        r = self.run_js("""
out([
  SP.plan(A('melee', true), only()),
  SP.plan(A('melee', true, null), only()),
  SP.plan({ from: 'Havoc', to: 'Giant Rat', kind: 'psychic', hit: true }, CTX),
  SP.plan({ from: 'Havoc', kind: 'melee', hit: true }, CTX),
  SP.plan(null, CTX),
]);
""")
        self.assertEqual(r, [None] * 5)


class TestSpriteSchedule(_SpritesCase):

    # S11 "Several attacks in one block run back to back, capped at 2 s in
    # total, after which the rest snap."
    def test_attacks_run_back_to_back_capped_at_two_seconds(self):
        r = self.run_js("""
const atks = [];
for (let i = 0; i < 8; i++) atks.push(A(['melee', 'ranged', 'spell'][i % 3], i % 2 === 0));
out(SP.schedule(atks, CTX));
""")
        self.assertEqual(len(r), 8, "one entry per attack (played or snapped)")
        t = 0
        cut = False
        for i, it in enumerate(r):
            ends = t + it["duration"]
            if not cut and ends <= 2000:
                self.assertFalse(it.get("snapped"), f"attack {i} fits in 2 s and must play")
                self.assertEqual(it["delay"], t, f"attack {i} must start when the one before ends")
                self.assertIsNotNone(it["sprite"])
                t = ends
            else:
                cut = True
                self.assertTrue(it.get("snapped"), f"attack {i} ends after 2 s: it must snap")
                self.assertIsNone(it.get("sprite"), "a snapped attack draws no sprite")
                self.assertIsNone(it.get("targetClass"), "a snapped attack has no flash")
        self.assertTrue(cut, "eight attacks of up to 900 ms cannot all fit in 2 s")
        played = [it for it in r if not it.get("snapped")]
        self.assertGreaterEqual(len(played), 2)
        self.assertLessEqual(max(it["delay"] + it["duration"] for it in played), 2000)

    def test_schedule_drops_attacks_that_show_nothing(self):
        r = self.run_js("""
out(SP.schedule([A('melee', true), { to: 'Nobody', kind: 'melee', hit: true }, A('spell', false)], CTX));
""")
        self.assertEqual([it["sprite"] for it in r], ["sword", "bolt"])
        self.assertEqual(r[0]["delay"], 0)

    def test_schedule_of_nothing_is_empty(self):
        r = self.run_js("out([SP.schedule([], CTX), SP.schedule(null, CTX)]);")
        self.assertEqual(r, [[], []])


class TestSpriteSvg(_SpritesCase):

    # D16 — drawn in code: SVG markup, no sprite sheets or downloads.
    def test_svg_builders_draw_in_code(self):
        r = self.run_js("""
const res = {};
for (const k of ['melee', 'ranged', 'spell']) for (const h of [true, false]) {
  res[k + h] = SP.svg(SP.plan(A(k, h), CTX), 64);
}
res.targetOnly = SP.svg(SP.plan(A('melee', true, null), CTX), 64);
out({ res, C: SP.CLASSES });
""")
        res, C = r["res"], r["C"]
        for key, sprite in (("melee", "sword"), ("ranged", "arrow"), ("spell", "bolt")):
            for h, cls in ((True, C["hit"]), (False, C["deflect"] if key != "melee" else C["whiff"])):
                s = res[key + ("true" if h else "false")]
                self.assertIsInstance(s, str)
                self.assertIn("<", s, "svg() must return SVG markup")
                self.assertIn(sprite, s, f"{key} markup must be classed with '{sprite}'")
                self.assertIn(cls, s, f"{key} hit={h} markup must carry {cls}")
        for s in res.values():
            low = s.lower()
            self.assertNotIn("http", low, "no downloads (D16)")
            self.assertNotIn("<image", low, "no bitmap sprites (D16)")
            self.assertNotIn("<script", low)
        t = res["targetOnly"]
        self.assertNotIn("sword", t, "a hidden attacker's swing must not be drawn")
        self.assertIn(C["hit"], t)


# ── fx.js: attacks play when their block starts ──────────────────────────

_FX_PRELUDE = r"""
globalThis.window = globalThis.window || { TV: {} };
const SP = require(%(sprites)s);
window.TV.sprites = window.TV.sprites || SP;
const mod = require(%(fx)s);
let NOW = 1000000, timers = [], nextId = 1;
const clock = {
  now: () => NOW,
  setTimeout: (fn, ms) => { const id = nextId++; timers.push({ id, at: NOW + Math.max(0, ms || 0), fn }); return id; },
  clearTimeout: (id) => { timers = timers.filter((t) => t.id !== id); },
};
let changes = 0;
let VIS = null;   // null: use fx's default visibility
const fx = mod.makeFx(Object.assign({
  onChange: () => { changes++; },
  isVisible: (n) => (VIS ? VIS.includes(n) : true),
}, clock));
function advance(ms) {
  const end = NOW + ms;
  for (;;) {
    timers.sort((a, b) => a.at - b.at || a.id - b.id);
    const t = timers[0];
    if (!t || t.at > end) break;
    timers.shift(); NOW = t.at; t.fn();
  }
  NOW = end;
  if (typeof fx.tick === 'function') fx.tick();
}
function ATK() {
  const s = fx.shownState() || {};
  if (!('attacks' in s)) throw new Error('shownState() has no attacks');
  return (s.attacks || []).map((a) => ({ sprite: a.sprite, outcome: a.outcome, targetClass: a.targetClass,
    from: a.from, to: a.to, delay: a.delay, duration: a.duration }));
}
const out = (v) => console.log(JSON.stringify(v));
const CELLS = ['##########', '#........#', '#........#', '#........#', '#........#', '#........#', '##########'];
const M = (tokens) => ({ grid: { w: 10, h: 7 }, cells: CELLS, tokens });
const HAVOC = { name: 'Havoc', x: 1, y: 1, kind: 'pc', size: 1, hp: [30, 30] };
const LYRA = { name: 'Lyra', x: 1, y: 3, kind: 'pc', size: 1, hp: [20, 20] };
const RAT = { name: 'Giant Rat', x: 3, y: 1, kind: 'npc', size: 1, hp_state: 'healthy' };
const M0 = M([HAVOC, LYRA, RAT]);
const SWING = { attacks: [{ from: 'Havoc', to: 'Giant Rat', kind: 'melee', hit: true }] };
"""


@unittest.skipUnless(NODE, "node is not installed")
class _FxAttackCase(unittest.TestCase):

    def run_js(self, body: str):
        for p in (SPRITES_JS, FX_JS):
            if not p.exists():
                self.fail("not built yet: " + p.name)
        return _run_node(self, _FX_PRELUDE % {"sprites": json.dumps(str(SPRITES_JS)),
                                              "fx": json.dumps(str(FX_JS))} + body)


class TestFxPlaysAttacks(_FxAttackCase):

    # AC15 — the sprite plays when its block starts, not when it arrives,
    # and is gone within ~1 s (D17).
    def test_attack_plays_on_its_block_then_ends(self):
        r = self.run_js("""
fx.setTarget(M0);
fx.arrive(1, null);
fx.arrive(2, SWING);
const queued = ATK();
fx.start(1);
const onA = ATK();
fx.start(2);
const onB = ATK();
const c0 = changes;
advance(1000);
out({ queued, onA, onB, after: ATK(), changed: changes > c0 });
""")
        self.assertEqual(r["queued"], [], "the attack played before its block")
        self.assertEqual(r["onA"], [], "the attack played on the block before its own")
        self.assertEqual(len(r["onB"]), 1, "start(B) must play the attack")
        a = r["onB"][0]
        self.assertEqual(a["sprite"], "sword")
        self.assertEqual(a["outcome"], "hit")
        self.assertEqual(a["from"], [1, 1])
        self.assertEqual(a["to"], [3, 1])
        self.assertEqual(r["after"], [], "the sprite must be gone within 1 s (D17)")
        self.assertTrue(r["changed"], "onChange must fire when the sprite ends")

    # Attacks are drawn from the squares after that block's moves.
    def test_attack_uses_squares_after_the_blocks_move(self):
        r = self.run_js("""
fx.setTarget(M0);
fx.arrive(2, { moves: [{ name: 'Havoc', x: 2, y: 1, path: [[1, 1], [2, 1]] }],
               attacks: [{ from: 'Havoc', to: 'Giant Rat', kind: 'melee', hit: false }] });
fx.setTarget(M([Object.assign({}, HAVOC, { x: 2 }), LYRA, RAT]));
fx.start(2);
out(ATK());
""")
        self.assertEqual(r[0]["from"], [2, 1])
        self.assertEqual(r[0]["outcome"], "whiff")

    # S11 — back to back, capped at 2 s; the rest snap (no sprite).
    def test_many_attacks_capped_at_two_seconds(self):
        r = self.run_js("""
fx.setTarget(M0);
const atks = [];
for (let i = 0; i < 8; i++) atks.push({ from: i % 2 ? 'Lyra' : 'Havoc', to: 'Giant Rat',
                                         kind: ['melee', 'ranged', 'spell'][i % 3], hit: i % 2 === 0 });
fx.arrive(2, { attacks: atks });
fx.start(2);
const onB = ATK();
advance(1999);
const at2 = ATK();
advance(2);
out({ onB, at2, after: ATK() });
""")
        self.assertGreaterEqual(len(r["onB"]), 2)
        self.assertLess(len(r["onB"]), 8, "attacks past 2 s must snap, not queue")
        for a in r["onB"]:
            self.assertIsNotNone(a["sprite"])
            self.assertLessEqual(a["delay"] + a["duration"], 2000)
        self.assertEqual(r["after"], [], "every attack sprite is gone by 2 s")

    # S11 "A skipped block adds no sprite."
    def test_skipped_block_adds_no_sprite(self):
        r = self.run_js("""
fx.setTarget(M0);
fx.arrive(2, SWING);
fx.skip(2);
const s = ATK();
fx.start(2);
const s2 = ATK();
fx.arrive(3, SWING);
fx.arrive(4, null);
fx.start(4);                 // 3 was overtaken: it snaps, no sprite
out({ s, s2, s3: ATK() });
""")
        self.assertEqual(r["s"], [])
        self.assertEqual(r["s2"], [], "a skipped block's attack must not play later")
        self.assertEqual(r["s3"], [], "an overtaken block's attack must not play")

    # AC16 — a backgrounded tab / rewind / clear: flushAll removes sprites.
    def test_flush_all_removes_playing_sprites(self):
        r = self.run_js("""
fx.setTarget(M0);
fx.arrive(2, SWING);
fx.start(2);
const playing = ATK();
fx.flushAll();
const flushed = ATK();
advance(5000);
out({ playing, flushed, later: ATK() });
""")
        self.assertEqual(len(r["playing"]), 1)
        self.assertEqual(r["flushed"], [], "flushAll must remove a playing sprite")
        self.assertEqual(r["later"], [])

    # A pending block flushed after 90 s never plays its attack (no backlog).
    def test_stale_block_never_plays_its_attack(self):
        r = self.run_js("""
fx.setTarget(M0);
fx.arrive(2, SWING);
advance(91000);
fx.start(2);
out(ATK());
""")
        self.assertEqual(r, [])

    # S11 "A hidden attacker gives a target-only effect" (no "from").
    def test_hidden_attacker_target_only(self):
        r = self.run_js("""
fx.setTarget(M0);
fx.arrive(2, { attacks: [{ to: 'Havoc', kind: 'ranged', hit: true }] });
fx.start(2);
out(ATK());
""")
        self.assertEqual(len(r), 1)
        self.assertIsNone(r[0]["sprite"])
        self.assertIsNone(r[0]["from"])
        self.assertEqual(r[0]["outcome"], "hit")
        self.assertEqual(r[0]["to"], [1, 1])

    # S11 — fog: attacker unseen -> target only; target unseen -> swing only.
    def test_fog_hides_the_unseen_side(self):
        r = self.run_js("""
fx.setTarget(M0);
fx.arrive(2, { attacks: [{ from: 'Giant Rat', to: 'Havoc', kind: 'melee', hit: false }] });
fx.arrive(3, { attacks: [{ from: 'Havoc', to: 'Giant Rat', kind: 'spell', hit: true }] });
VIS = ['Havoc', 'Lyra'];
fx.start(2);
const ratUnseen = ATK();
advance(1000);
fx.start(3);
out({ ratUnseen, targetUnseen: ATK() });
""")
        a = r["ratUnseen"][0]
        self.assertIsNone(a["sprite"], "an unseen attacker's swing must not be drawn")
        self.assertIsNone(a["from"])
        self.assertEqual(a["outcome"], "whiff")
        b = r["targetUnseen"][0]
        self.assertEqual(b["sprite"], "bolt")
        self.assertIsNone(b["outcome"], "no flash on an unseen target")

    def test_attacks_without_effects_never_hold_tokens(self):
        # D17 — an attack-only block must not freeze a token's ring/place.
        r = self.run_js("""
fx.setTarget(M0);
fx.arrive(2, SWING);
fx.setTarget(M([HAVOC, LYRA, Object.assign({}, RAT, { x: 4 })]));
const s = fx.shownState();
out(s.tokens.find((t) => t.name === 'Giant Rat').x);
""")
        self.assertEqual(r, 4)


# ── Wiring ───────────────────────────────────────────────────────────────

class TestSpriteWiring(unittest.TestCase):

    def test_tv_html_loads_sprites_before_fx(self):
        html = TV_HTML.read_text(encoding="utf-8")
        self.assertIn('src="/static/map/sprites.js"', html, "tv.html must load /static/map/sprites.js")
        self.assertLess(html.index("/static/map/sprites.js"), html.index("/static/tv/map.js"),
                        "sprites.js must load before the TV map adapter")

    def test_adapter_passes_attacks_to_the_board(self):
        src = TV_MAP_JS.read_text(encoding="utf-8")
        self.assertIn("attacks", src, "static/tv/map.js must pass shownState().attacks to the board")
        self.assertIn("attacks", BOARD_JS.read_text(encoding="utf-8"),
                      "board.js must draw view.attacks in svg.map-fx")

    def test_map_css_has_sprite_keyframes(self):
        css = MAP_CSS.read_text(encoding="utf-8").lower()
        for word in ("sword", "arrow", "bolt"):
            self.assertIn(word, css, f"map.css must style the {word} sprite (D16)")
        self.assertGreaterEqual(css.count("@keyframes"), 2)

    def test_on_attacks_hook_is_not_the_only_path(self):
        # Contract: attacks are exposed as state, not only through a hook
        # nobody sets (static/tv/map.js never assigns onAttacks today).
        src = FX_JS.read_text(encoding="utf-8")
        self.assertIn("attacks", src)
        self.assertRegex(src, r"shownState\s*\(\)\s*\{[^}]*attacks",
                         "fx.shownState() must expose the attacks playing now")


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(NODE, "node is not installed")
class TestArrowMissStopsShort(unittest.TestCase):
    """QA re-verify: a missed arrow's head ended inside the target's disc."""

    def test_missed_arrow_ends_outside_the_disc(self):
        r = _run_node(self, ("const SP = require(%s);\n" % json.dumps(str(SPRITES_JS))) + """
const s = 36;
const it = SP.schedule([{ from: 'A', to: 'B', kind: 'ranged', hit: false }],
  { pos: { A: [3, 2], B: [10, 3] }, visible: () => true })[0];
const m = SP.svg(it, s).match(/--dx:([-0-9.]+)px;--dy:([-0-9.]+)px/);
const dx = +m[1], dy = +m[2];
const L = Math.hypot(dx, dy), ux = dx / L, uy = dy / L;
const F = [(3.5) * s, (2.5) * s], T = [(10.5) * s, (3.5) * s];
const tip = [F[0] + dx + ux * 0.32 * s, F[1] + dy + uy * 0.32 * s];
console.log(JSON.stringify({ gap: Math.hypot(T[0] - tip[0], T[1] - tip[1]) }));
""")
        self.assertGreater(r["gap"], 0.45 * 36, "the arrow tip must stop outside the target's disc")
