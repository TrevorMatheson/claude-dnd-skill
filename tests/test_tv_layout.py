"""Panels and the story layouts on /tv (display foundation, plan step S7).

AC6 — each panel takes 1-3 of the five modules; the Story, Tactical and
      Minimal presets match D7 (Story: L party, R quests + clock; Tactical:
      L party, R turns + map; Minimal: no panels).
AC3 / AC4 (TV side) are browser checks; the pure parts they rest on are here:
      the layout the TV resolves from the saved settings, and the module
      view models the panels draw.

The logic lives in DOM-free functions under skills/dnd/display/static/tv/,
each file ending with `if (typeof module !== 'undefined') module.exports = ...`
(Shape, "Each script attaches to window.TV and also sets module.exports"):

  layout.js  resolve(config, stats, override) -> {mode, left, right}
               config   the `config` of a {"display": {config, mode_override}}
                        event (display_config.validate output), or junk/null
               stats    the merged stats snapshot (S7: no turn_order)
               override the `mode_override` (null | false here; S8 covers
                        true and the combat modes)
               mode     'story' when any panel holds a module,
                        'minimal' when both panels are empty
               left / right  arrays of module ids, fresh copies
  panels.js  partyView(stats)  -> [{name, hp:{current,max}, hpPct, conditions}]
             turnsView(stats)  -> {round, items:[{name, current}]} | null
             questsView(stats) -> [{name, status}]
             clockView(stats)  -> {date, time, effects:[{owner, name}]}

Shapes of stats come from the old page's renderers: party card
index.html:6234-6346 (hp.current/hp.max, pct clamped 0-100, 100 when max is
unknown; conditions list), quests :7014-7037 (stats.quests [{name,status}]),
turn order :7040-7062 (order, current, round), world clock :9051-9065
(world_time {date, time, season, day_name, weather}) and timed effects
players[].effects [{name, duration_type, ...}] :6336-6346.

S8 (combat modes, plan step S8; AC7, AC8, AC9, AC13, D9-D11) is tested in
the classes at the bottom, against this assumed contract:

  layout.js  resolve(config, stats, override) -> {mode, left, right}
               mode  'combat-map'   combat is on and stats.map is set
                     'combat-nomap' combat is on and there is no map
                     else 'story' / 'minimal' as in S7
               combat is on when override === true, or when override is not
               false and stats.turn_order.order is a list naming >= 1
               combatant (same rule as the server's _combat_on, S4)
               left / right  always the saved panels, in combat modes too,
                             so the page can restore them
  panels.js  turnBarView(stats) -> {round, items:[{name, current, foe}]} | null
               foe   the name is not one of stats.players (case-insensitive)
               null  when there is no turn order naming anyone
  map.js     canvasSize(cssW, cssH, cols, rows, dpr) -> {width, height, cell}
               cell    CSS px per grid square, as the old _bmRender's cs:
                       max(8, floor(min(cssW / cols, cssH / rows)))
               width   canvas backing pixels = round(cols * cell * dpr)
               height  canvas backing pixels = round(rows * cell * dpr)
               dpr     missing, 0 or not a number counts as 1

Skipped when node is not installed.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "skills" / "dnd" / "display"
TV_STATIC = DISPLAY / "static" / "tv"
LAYOUT_JS = TV_STATIC / "layout.js"
PANELS_JS = TV_STATIC / "panels.js"
MAP_JS = TV_STATIC / "map.js"
NODE = shutil.which("node")


def _load_display_config():
    spec = importlib.util.spec_from_file_location("display_config_for_tv_layout",
                                                  DISPLAY / "display_config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_PRELUDE = """
globalThis.window = globalThis.window || { TV: {} };
const FILES = { layout: %(layout)s, panels: %(panels)s, map: %(map)s };
function need(key, name) {
  const mod = require(FILES[key]);
  if (!mod || typeof mod[name] !== 'function') {
    throw new Error(key + '.js does not export ' + name + '()');
  }
  return mod[name];
}
const resolve = (...a) => need('layout', 'resolve')(...a);
const partyView = (...a) => need('panels', 'partyView')(...a);
const turnsView = (...a) => need('panels', 'turnsView')(...a);
const questsView = (...a) => need('panels', 'questsView')(...a);
const clockView = (...a) => need('panels', 'clockView')(...a);
const turnBarView = (...a) => need('panels', 'turnBarView')(...a);
const canvasSize = (...a) => need('map', 'canvasSize')(...a);
const out = (v) => console.log(JSON.stringify(v));
// A config as /display-config and the `display` event carry it.
const cfg = (layout) => ({ version: 1, layout: layout,
  voice: { narrator: 'bm_lewis', npc_engine: 'kokoro', volume: 1, speed: 1, read_player_actions: false } });
"""


@unittest.skipUnless(NODE, "node is not installed")
class _NodeCase(unittest.TestCase):
    NEEDS: tuple = (LAYOUT_JS,)

    def run_js(self, body: str):
        missing = [p.name for p in self.NEEDS if not p.exists()]
        if missing:
            self.fail("not built yet: static/tv/" + ", static/tv/".join(missing))
        script = _PRELUDE % {"layout": json.dumps(str(LAYOUT_JS)),
                             "panels": json.dumps(str(PANELS_JS)),
                             "map": json.dumps(str(MAP_JS))} + body
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


# ── layout.js: resolve() for the story layouts (AC6, D5, D7) ─────────────

class TestResolvePresets(_NodeCase):

    # AC6 / D7 — Story: Party left; Quests + Clock right.
    def test_story_preset(self):
        res = self.run_js("""
out(resolve(cfg({preset: 'story', left: ['party'], right: ['quests', 'clock']}), {}, null));
""")
        self.assertEqual(res, {"mode": "story", "left": ["party"], "right": ["quests", "clock"]})

    # AC6 / D7 — Tactical: Party left; Turn order + Map right. Outside combat
    # it is drawn in the story grid (narration centre, panels either side).
    def test_tactical_preset(self):
        res = self.run_js("""
out(resolve(cfg({preset: 'tactical', left: ['party'], right: ['turns', 'map']}), {}, null));
""")
        self.assertEqual(res, {"mode": "story", "left": ["party"], "right": ["turns", "map"]})

    # AC6 / D7 — Minimal has no panels.
    def test_minimal_has_no_panels(self):
        res = self.run_js("""
out(resolve(cfg({preset: 'minimal', left: [], right: []}), {}, null));
""")
        self.assertEqual(res, {"mode": "minimal", "left": [], "right": []})

    # AC6 — a preset named with no stacks (hand-edited file) still gives D7's stacks.
    def test_preset_name_alone_gives_its_stacks(self):
        res = self.run_js("""
out(['story', 'tactical', 'minimal'].map(p => resolve(cfg({preset: p}), {}, null)));
""")
        self.assertEqual(res, [
            {"mode": "story", "left": ["party"], "right": ["quests", "clock"]},
            {"mode": "story", "left": ["party"], "right": ["turns", "map"]},
            {"mode": "minimal", "left": [], "right": []},
        ])

    # D6 — no saved settings (old campaign, junk config) gives the Story preset.
    def test_missing_or_junk_config_is_story(self):
        res = self.run_js("""
out([null, undefined, {}, {layout: null}, 'story', 42].map(c => resolve(c, {}, null)));
""")
        story = {"mode": "story", "left": ["party"], "right": ["quests", "clock"]}
        self.assertEqual(res, [story] * 6)

    # AC6 — the TV and the server agree on D7 (display_config.PRESETS).
    def test_presets_match_server(self):
        dc = _load_display_config()
        body = "out({%s});" % ", ".join(
            "%s: resolve(cfg({preset: %s}), {}, null)" % (json.dumps(n), json.dumps(n))
            for n in dc.PRESETS)
        res = self.run_js(body)
        for name, stacks in dc.PRESETS.items():
            self.assertEqual(res[name]["left"], stacks["left"], name)
            self.assertEqual(res[name]["right"], stacks["right"], name)

    # AC6 — what the server validates and broadcasts resolves to the same stacks.
    def test_server_validated_config_round_trips(self):
        dc = _load_display_config()
        saved = dc.validate({"layout": {"left": ["clock", "party"], "right": ["quests"]}})
        res = self.run_js("out(resolve(%s, {}, null));" % json.dumps(saved))
        self.assertEqual(res, {"mode": "story", "left": ["clock", "party"], "right": ["quests"]})

    # D9 — override false (host forced story) with no turn order is the saved layout.
    def test_override_false_keeps_saved_layout(self):
        res = self.run_js("""
out(resolve(cfg({preset: 'tactical', left: ['party'], right: ['turns', 'map']}), {}, false));
""")
        self.assertEqual(res, {"mode": "story", "left": ["party"], "right": ["turns", "map"]})

    # S7 — stats with no turn order (players, quests, clock) never change the layout.
    def test_stats_without_turn_order_do_not_change_layout(self):
        res = self.run_js("""
const st = {players: [{name: 'Bram', hp: {current: 5, max: 10}}], quests: [{name: 'Q', status: 'active'}],
            world_time: {date: '3 Mirtul', time: 'dusk'}, turn_order: null};
out(resolve(cfg({preset: 'story', left: ['party'], right: ['quests', 'clock']}), st, null));
""")
        self.assertEqual(res, {"mode": "story", "left": ["party"], "right": ["quests", "clock"]})

    # The result is a copy: drawing code that mutates it cannot corrupt the settings.
    def test_result_is_a_copy(self):
        res = self.run_js("""
const c = cfg({preset: 'story', left: ['party'], right: ['quests', 'clock']});
const r = resolve(c, {}, null);
r.left.push('map'); r.right.length = 0;
out(c.layout);
""")
        self.assertEqual(res, {"preset": "story", "left": ["party"], "right": ["quests", "clock"]})


class TestResolveCustomAndValidation(_NodeCase):

    # AC6 — a custom 3-module panel is kept, order and all.
    def test_custom_three_module_panel_kept(self):
        res = self.run_js("""
out(resolve(cfg({preset: 'custom', left: ['clock', 'quests', 'party'], right: ['turns']}), {}, null));
""")
        self.assertEqual(res, {"mode": "story", "left": ["clock", "quests", "party"], "right": ["turns"]})

    # Shape — a panel of 0 modules is hidden; the other side still shows.
    def test_one_empty_panel_is_still_story(self):
        res = self.run_js("""
out(resolve(cfg({preset: 'custom', left: [], right: ['party', 'map']}), {}, null));
""")
        self.assertEqual(res, {"mode": "story", "left": [], "right": ["party", "map"]})

    # Shape — both panels empty is the Minimal layout, whatever it is called.
    def test_custom_with_no_modules_is_minimal(self):
        res = self.run_js("""
out(resolve(cfg({preset: 'custom', left: [], right: []}), {}, null));
""")
        self.assertEqual(res, {"mode": "minimal", "left": [], "right": []})

    # Shape — at most 3 modules per panel.
    def test_panel_capped_at_three(self):
        res = self.run_js("""
out(resolve(cfg({preset: 'custom', left: ['party', 'turns', 'map', 'quests'], right: []}), {}, null));
""")
        self.assertEqual(res["left"], ["party", "turns", "map"])
        self.assertEqual(res["right"], [])

    # Shape — unknown ids are dropped.
    def test_unknown_ids_dropped(self):
        res = self.run_js("""
out(resolve(cfg({preset: 'custom', left: ['party', 'dice', 42, null, 'PARTY'], right: ['weather', 'clock']}), {}, null));
""")
        self.assertEqual(res, {"mode": "story", "left": ["party"], "right": ["clock"]})

    # Shape — each module at most once, across both panels (first one wins).
    def test_module_at_most_once(self):
        res = self.run_js("""
out(resolve(cfg({preset: 'custom', left: ['party', 'party', 'quests'], right: ['quests', 'party', 'clock']}), {}, null));
""")
        self.assertEqual(res, {"mode": "story", "left": ["party", "quests"], "right": ["clock"]})

    # Shape — a non-list panel counts as empty.
    def test_non_list_panel_is_empty(self):
        res = self.run_js("""
out(resolve(cfg({preset: 'custom', left: 'party', right: ['quests']}), {}, null));
""")
        self.assertEqual(res, {"mode": "story", "left": [], "right": ["quests"]})


# ── panels.js: the module view models (D7, plan S7 "Modules") ────────────

STATS = {
    "players": [
        {"name": "Bram", "hp": {"current": 7, "max": 28}, "conditions": ["Poisoned", "Prone"],
         "effects": [{"name": "Bless", "duration_type": "rounds", "duration_remaining": 8,
                      "concentration": True}]},
        {"name": "Ysolde", "hp": {"current": 31, "max": 31}, "conditions": []},
        {"name": "Kett", "hp": {"current": -3, "max": 20}},
        {"name": "Odd", "hp": {"current": 50, "max": 40},
         "effects": [{"name": "Mage Armor", "duration_type": "hours",
                      "started_at": 1000, "duration_seconds": 28800}]},
        {"name": "Nohp"},
    ],
    "quests": [{"name": "The Sunken Bell", "status": "active"},
               {"name": "Ashes of Corlen", "status": "resolved"}],
    "world_time": {"date": "3 Mirtul", "time": "dusk", "season": "Spring",
                   "day_name": "Fourthday", "weather": "light rain"},
}


class TestPartyView(_NodeCase):
    NEEDS = (PANELS_JS,)

    # D7 Party — one entry per player, in order, with name, HP and conditions.
    def test_party_names_hp_and_conditions(self):
        res = self.run_js("out(partyView(%s));" % json.dumps(STATS))
        self.assertEqual([p["name"] for p in res], ["Bram", "Ysolde", "Kett", "Odd", "Nohp"])
        self.assertEqual(res[0]["hp"], {"current": 7, "max": 28})
        self.assertEqual(res[0]["conditions"], ["Poisoned", "Prone"])
        self.assertEqual(res[1]["conditions"], [])
        self.assertEqual(res[2]["conditions"], [], "missing conditions read as none")

    # HP bar width as the old card computes it: clamped 0-100, 100 when unknown.
    def test_hp_bar_percent(self):
        res = self.run_js("out(partyView(%s).map(p => p.hpPct));" % json.dumps(STATS))
        self.assertEqual(res[0], 25)
        self.assertEqual(res[1], 100)
        self.assertEqual(res[2], 0, "below zero clamps to 0")
        self.assertEqual(res[3], 100, "over max clamps to 100")
        self.assertEqual(res[4], 100, "unknown HP draws a full bar")

    def test_no_players(self):
        res = self.run_js("out([partyView({}), partyView({players: null}), partyView(null)]);")
        self.assertEqual(res, [[], [], []])


class TestTurnsView(_NodeCase):
    NEEDS = (PANELS_JS,)

    # D7 Turn order — the list in order with the current turn highlighted.
    def test_current_turn_highlighted(self):
        res = self.run_js("""
out(turnsView({turn_order: {order: ['Ysolde', 'Goblin 1', 'Bram'], current: 'Goblin 1', round: 2}}));
""")
        self.assertEqual(res["round"], 2)
        self.assertEqual(res["items"], [
            {"name": "Ysolde", "current": False},
            {"name": "Goblin 1", "current": True},
            {"name": "Bram", "current": False},
        ])

    def test_no_turn_order_is_null(self):
        res = self.run_js("out([turnsView({}), turnsView({turn_order: null}), turnsView(null)]);")
        self.assertEqual(res, [None, None, None])


class TestQuestsView(_NodeCase):
    NEEDS = (PANELS_JS,)

    # D7 Quests — from stats.quests, in order, name and status.
    def test_quests_from_stats(self):
        res = self.run_js("out(questsView(%s));" % json.dumps(STATS))
        self.assertEqual(res, [{"name": "The Sunken Bell", "status": "active"},
                               {"name": "Ashes of Corlen", "status": "resolved"}])

    def test_no_quests(self):
        res = self.run_js("out([questsView({}), questsView({quests: null}), questsView(null)]);")
        self.assertEqual(res, [[], [], []])


class TestClockView(_NodeCase):
    NEEDS = (PANELS_JS,)

    # D7 World clock & effects — date and time from stats.world_time.
    def test_clock_from_world_time(self):
        res = self.run_js("out(clockView(%s));" % json.dumps(STATS))
        self.assertEqual(res["date"], "3 Mirtul")
        self.assertEqual(res["time"], "dusk")

    # ... and the timed effects from players[].effects, with whose they are.
    def test_effects_from_players(self):
        res = self.run_js("out(clockView(%s).effects.map(e => [e.owner, e.name]));" % json.dumps(STATS))
        self.assertEqual(res, [["Bram", "Bless"], ["Odd", "Mage Armor"]])

    def test_empty_stats(self):
        res = self.run_js("const v = clockView({}); out({date: v.date || '', time: v.time || '', effects: v.effects});")
        self.assertEqual(res, {"date": "", "time": "", "effects": []})


# ── S8: combat modes in resolve() (AC7, D9, D10, D11) ────────────────────

# A battle map as the server stores it (_clean_map output).
MAP = {"grid": {"w": 12, "h": 8, "ft": 5},
       "cells": ["#" * 12] + ["#" + "." * 10 + "#"] * 6 + ["#" * 12],
       "image": None, "round": 1,
       "tokens": [{"name": "Bram", "x": 2, "y": 3, "kind": "pc", "size": 1},
                  {"name": "Goblin 1", "x": 8, "y": 4, "kind": "npc", "size": 1}]}
TURNS = {"order": ["Bram", "Goblin 1", "Ysolde"], "current": "Bram", "round": 1}
STORY = {"preset": "story", "left": ["party"], "right": ["quests", "clock"]}
TACTICAL = {"preset": "tactical", "left": ["party"], "right": ["turns", "map"]}
MINIMAL = {"preset": "minimal", "left": [], "right": []}
STORY_RES = {"mode": "story", "left": ["party"], "right": ["quests", "clock"]}


class _CombatCase(_NodeCase):

    def resolve(self, layout, stats, override="null"):
        return self.run_js("out(resolve(cfg(%s), %s, %s));"
                           % (json.dumps(layout), json.dumps(stats), override))


class TestResolveCombatAuto(_CombatCase):

    # AC7 / D10 — a turn order naming someone plus a map is combat with the map.
    def test_turn_order_with_map_is_combat_map(self):
        res = self.resolve(STORY, {"turn_order": TURNS, "map": MAP})
        self.assertEqual(res["mode"], "combat-map")

    # AC7 / D11 — a turn order with no map is combat without one.
    def test_turn_order_without_map_is_combat_nomap(self):
        for stats in ({"turn_order": TURNS}, {"turn_order": TURNS, "map": None}):
            with self.subTest(stats=stats):
                self.assertEqual(self.resolve(STORY, stats)["mode"], "combat-nomap")

    # D9 — combat replaces whichever layout is saved, Tactical and Minimal too.
    def test_combat_from_any_saved_layout(self):
        for layout in (TACTICAL, MINIMAL):
            with self.subTest(layout=layout["preset"]):
                self.assertEqual(self.resolve(layout, {"turn_order": TURNS, "map": MAP})["mode"], "combat-map")
                self.assertEqual(self.resolve(layout, {"turn_order": TURNS})["mode"], "combat-nomap")

    # A single combatant is still a fight.
    def test_one_combatant_is_combat(self):
        res = self.resolve(STORY, {"turn_order": {"order": ["Ogre"], "current": "Ogre"}})
        self.assertEqual(res["mode"], "combat-nomap")

    # Override null and override left out both mean automatic.
    def test_undefined_override_is_automatic(self):
        res = self.run_js("out(resolve(cfg(%s), %s));"
                          % (json.dumps(STORY), json.dumps({"turn_order": TURNS, "map": MAP})))
        self.assertEqual(res["mode"], "combat-map")

    # D10 / D11 — the saved panels come back in combat modes too, unchanged,
    # so the page can restore them when the fight ends.
    def test_combat_keeps_saved_panels(self):
        res = self.resolve(TACTICAL, {"turn_order": TURNS, "map": MAP})
        self.assertEqual((res["left"], res["right"]), (["party"], ["turns", "map"]))
        res = self.resolve(STORY, {"turn_order": TURNS})
        self.assertEqual((res["left"], res["right"]), (["party"], ["quests", "clock"]))


class TestResolveNotCombat(_CombatCase):

    # S4 _combat_on — a bare {"current": ...} or an empty order is not a fight.
    def test_turn_order_naming_nobody_is_not_combat(self):
        for to in ({"current": "Bram"}, {"order": []}, {"order": [], "current": "Bram", "round": 2},
                   {"order": "Bram, Goblin"}, {}, "Bram"):
            with self.subTest(turn_order=to):
                self.assertEqual(self.resolve(STORY, {"turn_order": to, "map": MAP}), STORY_RES)

    # S4 — the TV agrees with the server's _combat_on on every shape.
    def test_matches_server_combat_on(self):
        spec = importlib.util.spec_from_file_location("dnd_display_app_tv_layout",
                                                      str(DISPLAY / "dnd-display-app.py"))
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
        shapes = [None, {}, {"current": "Bram"}, {"order": []}, {"order": ["Bram"]},
                  {"order": "Bram"}, TURNS, [], "x"]
        res = self.run_js("out(%s.map(t => resolve(cfg(%s), {turn_order: t}, null).mode));"
                          % (json.dumps(shapes), json.dumps(STORY)))
        expected = ["combat-nomap" if app._combat_on(t) else "story" for t in shapes]
        self.assertEqual(res, expected)

    # D9 — a map with no turn order is not combat (it shows in its panel).
    def test_map_alone_is_not_combat(self):
        res = self.resolve(TACTICAL, {"map": MAP, "turn_order": None})
        self.assertEqual(res, {"mode": "story", "left": ["party"], "right": ["turns", "map"]})

    # AC7 / D9 — a cleared turn order (combat.py end, --turn-clear) goes back
    # to the saved layout: story, or minimal when that is what is saved.
    def test_cleared_turn_order_restores_saved_layout(self):
        res = self.run_js("""
const c = cfg(%s), m = cfg(%s);
const fight = {turn_order: %s, map: %s};
const after = {turn_order: null, map: null};
out([resolve(c, fight, null).mode, resolve(c, after, null), resolve(m, fight, null).mode, resolve(m, after, null)]);
""" % (json.dumps(STORY), json.dumps(MINIMAL), json.dumps(TURNS), json.dumps(MAP)))
        self.assertEqual(res, ["combat-map", STORY_RES,
                               "combat-map", {"mode": "minimal", "left": [], "right": []}])


class TestResolveOverride(_CombatCase):

    # AC7 "the manual override works both ways" — false forces the saved
    # layout even while a turn order is set.
    def test_override_false_beats_turn_order(self):
        self.assertEqual(self.resolve(STORY, {"turn_order": TURNS, "map": MAP}, "false"), STORY_RES)
        self.assertEqual(self.resolve(MINIMAL, {"turn_order": TURNS}, "false"),
                         {"mode": "minimal", "left": [], "right": []})

    # AC7 — true forces combat with no turn order: with the map if there is one.
    def test_override_true_without_turn_order(self):
        self.assertEqual(self.resolve(STORY, {"map": MAP}, "true")["mode"], "combat-map")
        self.assertEqual(self.resolve(STORY, {}, "true")["mode"], "combat-nomap")
        self.assertEqual(self.resolve(STORY, {"turn_order": None, "map": None}, "true")["mode"], "combat-nomap")
        self.assertEqual(self.resolve(MINIMAL, {"turn_order": {"order": []}}, "true")["mode"], "combat-nomap")

    # AC7 — true with a turn order is combat as usual.
    def test_override_true_with_turn_order(self):
        self.assertEqual(self.resolve(STORY, {"turn_order": TURNS, "map": MAP}, "true")["mode"], "combat-map")

    # The override keeps the saved panels too.
    def test_override_true_keeps_saved_panels(self):
        res = self.resolve(TACTICAL, {}, "true")
        self.assertEqual((res["left"], res["right"]), (["party"], ["turns", "map"]))


# ── S8: the combat turn bar (D10 "turn order as a slim bar") ─────────────

class TestTurnBarView(_NodeCase):
    NEEDS = (PANELS_JS,)

    # D10 — the round, a token per combatant in order, the current one
    # marked, and foes (anyone not in the party) told apart from players.
    def test_round_order_current_and_foes(self):
        stats = {"players": [{"name": "Bram"}, {"name": "Ysolde"}],
                 "turn_order": {"order": ["Ysolde", "Goblin 1", "bram"], "current": "Goblin 1", "round": 3}}
        res = self.run_js("out(turnBarView(%s));" % json.dumps(stats))
        self.assertEqual(res, {"round": 3, "items": [
            {"name": "Ysolde", "current": False, "foe": False},
            {"name": "Goblin 1", "current": True, "foe": True},
            {"name": "bram", "current": False, "foe": False},
        ]})

    # With no party listed, everyone in the order is a foe; round defaults to 1.
    def test_no_players_all_foes_round_one(self):
        res = self.run_js("out(turnBarView({turn_order: {order: ['Ogre'], current: 'Ogre'}}));")
        self.assertEqual(res, {"round": 1, "items": [{"name": "Ogre", "current": True, "foe": True}]})

    # Same rule as combat: no one named, no bar.
    def test_no_bar_without_combatants(self):
        res = self.run_js("out([turnBarView({}), turnBarView(null), turnBarView({turn_order: null}),"
                          " turnBarView({turn_order: {current: 'Bram'}}), turnBarView({turn_order: {order: []}})]);")
        self.assertEqual(res, [None] * 5)


# ── S8: map.js canvas sizing (AC13: no blurry map at 4K) ─────────────────

class TestCanvasSize(_NodeCase):
    NEEDS = (MAP_JS,)

    # The cell is the old _bmRender cs: the largest whole square that fits.
    def test_cell_fits_container(self):
        res = self.run_js("out([canvasSize(1152, 1000, 12, 8, 1), canvasSize(1000, 400, 12, 8, 1)]);")
        self.assertEqual(res, [{"width": 1152, "height": 768, "cell": 96},
                               {"width": 600, "height": 400, "cell": 50}])

    # Plan S8 "the canvas is sized at devicePixelRatio": same CSS cell, more pixels.
    def test_backing_store_scales_with_dpr(self):
        res = self.run_js("out([canvasSize(1000, 400, 12, 8, 2), canvasSize(1000, 400, 12, 8, 1.5)]);")
        self.assertEqual(res, [{"width": 1200, "height": 800, "cell": 50},
                               {"width": 900, "height": 600, "cell": 50}])

    # AC13 — the map column at 1080p and at a 4K viewport keeps the same
    # share of the screen: cell and canvas scale by exactly 2.
    def test_4k_is_twice_1080p(self):
        res = self.run_js("out([canvasSize(1152, 1000, 24, 16, 1), canvasSize(2304, 2000, 24, 16, 1)]);")
        self.assertEqual(res[1]["cell"], 2 * res[0]["cell"])
        self.assertEqual((res[1]["width"], res[1]["height"]), (2 * res[0]["width"], 2 * res[0]["height"]))

    # Never smaller than 8 px a square, as before.
    def test_minimum_cell(self):
        res = self.run_js("out(canvasSize(100, 100, 80, 80, 1));")
        self.assertEqual(res, {"width": 640, "height": 640, "cell": 8})

    # A missing or broken dpr counts as 1.
    def test_bad_dpr_is_one(self):
        res = self.run_js("out([canvasSize(1000, 400, 12, 8), canvasSize(1000, 400, 12, 8, 0),"
                          " canvasSize(1000, 400, 12, 8, NaN), canvasSize(1000, 400, 12, 8, 'x')]);")
        self.assertEqual(res, [{"width": 600, "height": 400, "cell": 50}] * 4)


if __name__ == "__main__":
    unittest.main()
