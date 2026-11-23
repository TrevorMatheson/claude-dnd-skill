"""Layout choice and tabs on /phone (phone sheet spec, plan step S1).

AC2 — phone width on a touch device gets the phone layout (tabs), desktop
      width gets Play and Sheet side by side; the saved switch wins.
AC15 / plan note 12 — the Map tab is offered only in combat, with a map,
      and only when the map capabilities are on (milestone 2). In milestone 1
      (no caps) tabsFor never includes 'map'.

Contract pinned for skills/dnd/display/static/phone/layout.js (attaches to
window.PH.layout and sets module.exports; loading it must not need a DOM,
localStorage or matchMedia):

  LAYOUT_KEY                         'dnd-phone-layout'
  chooseLayout({width, coarse, saved}) -> 'phone' | 'desktop'
      saved 'phone' / 'desktop' wins; otherwise 'phone' when width < 900 and
      coarse (a touch pointer), else 'desktop'. Any other saved value is ignored.
  combatOn(stats) -> bool
      stats.turn_order is an object whose `order` is a non-empty list
      (the server's _combat_on, dnd-display-app.py).
  tabsFor(layout, stats, caps) -> array
      ['play', 'sheet'], plus 'map' only when combatOn(stats) && stats.map
      && caps && caps.check && caps.board — for both layouts.

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
LAYOUT_JS = ROOT / "skills" / "dnd" / "display" / "static" / "phone" / "layout.js"
NODE = shutil.which("node")

_PRELUDE = """
globalThis.window = { PH: {} };
const mod = require(%(layout)s);
function need(name) {
  if (!mod || typeof mod[name] !== 'function') {
    throw new Error('layout.js does not export ' + name + '()');
  }
  return mod[name];
}
const chooseLayout = (...a) => need('chooseLayout')(...a);
const combatOn = (...a) => need('combatOn')(...a);
const tabsFor = (...a) => need('tabsFor')(...a);
const out = (v) => console.log(JSON.stringify(v));
const FIGHT = { turn_order: { order: ['Mira', 'Goblin'], current: 'Mira', round: 1 } };
const MAP = { cols: 10, rows: 8, tokens: [] };
const CAPS = { check: true, board: true };
"""


@unittest.skipUnless(NODE, "node is not installed")
class _NodeCase(unittest.TestCase):

    def run_js(self, body: str):
        if not LAYOUT_JS.exists():
            self.fail("not built yet: static/phone/layout.js")
        script = _PRELUDE % {"layout": json.dumps(str(LAYOUT_JS))} + body
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


class TestExports(_NodeCase):

    def test_layout_key(self):
        self.assertEqual(self.run_js("out(mod.LAYOUT_KEY);"), "dnd-phone-layout")

    # Plan Shape: each phone file attaches to window.PH as well as module.exports.
    def test_attaches_to_window_ph(self):
        res = self.run_js("""
const L = window.PH && window.PH.layout;
out(!!L && ['chooseLayout', 'combatOn', 'tabsFor'].every(n => typeof L[n] === 'function')
    && L.LAYOUT_KEY === 'dnd-phone-layout');
""")
        self.assertTrue(res, "window.PH.layout is missing or incomplete")


class TestChooseLayout(_NodeCase):

    # AC2 / D2 — narrow touch screen gets the phone layout.
    def test_phone_width_touch_is_phone(self):
        res = self.run_js("out([375, 414, 899].map(w => chooseLayout({width: w, coarse: true, saved: null})));")
        self.assertEqual(res, ["phone"] * 3)

    # AC2 / D2 — wide screens get the desktop layout, touch or not.
    def test_wide_is_desktop(self):
        res = self.run_js("""
out([[900, true], [1440, true], [1440, false], [1920, false]]
    .map(([w, c]) => chooseLayout({width: w, coarse: c, saved: null})));
""")
        self.assertEqual(res, ["desktop"] * 4)

    # D2 — a narrow window with a mouse (not touch) is desktop.
    # D2: "a wider screen gets the desktop layout". A window too narrow for
    # two columns gets the phone layout even with a mouse (a 400–560 px
    # desktop window clipped the side-by-side layout); 700 px and up with a
    # mouse stays desktop.
    def test_narrow_mouse_is_phone(self):
        res = self.run_js("""
out([
  chooseLayout({width: 375, coarse: false, saved: null}),
  chooseLayout({width: 561, coarse: false, saved: null}),
  chooseLayout({width: 699, coarse: false, saved: null}),
  chooseLayout({width: 700, coarse: false, saved: null}),
  chooseLayout({width: 899, coarse: false, saved: null}),
]);""")
        self.assertEqual(res, ["phone", "phone", "phone", "desktop", "desktop"])

    # AC2 — the saved switch overrides the device in both directions.
    def test_saved_choice_wins(self):
        res = self.run_js("""
out([
  chooseLayout({width: 375, coarse: true, saved: 'desktop'}),
  chooseLayout({width: 1440, coarse: false, saved: 'phone'}),
  chooseLayout({width: 375, coarse: true, saved: 'phone'}),
  chooseLayout({width: 1440, coarse: false, saved: 'desktop'}),
]);
""")
        self.assertEqual(res, ["desktop", "phone", "phone", "desktop"])

    # A junk saved value (hand-edited localStorage) is ignored.
    def test_junk_saved_ignored(self):
        res = self.run_js("""
out(['', 'tablet', undefined, null, 42].map(s => [
  chooseLayout({width: 375, coarse: true, saved: s}),
  chooseLayout({width: 1440, coarse: false, saved: s}),
]));
""")
        self.assertEqual(res, [["phone", "desktop"]] * 5)


class TestCombatOn(_NodeCase):

    # Same rule as the server's _combat_on.
    def test_combat_on_rule(self):
        res = self.run_js("""
out([
  combatOn(FIGHT),
  combatOn({turn_order: {order: ['Mira']}}),
  combatOn({turn_order: {order: []}}),
  combatOn({turn_order: {current: 'Mira'}}),
  combatOn({turn_order: {order: 'Mira'}}),
  combatOn({turn_order: null}),
  combatOn({turn_order: ['Mira']}),
  combatOn({}),
  combatOn(null),
  combatOn(undefined),
]);
""")
        self.assertEqual(res, [True, True, False, False, False, False, False, False, False, False])


class TestTabsFor(_NodeCase):

    # AC2 / plan note 12 — milestone 1 (no caps): never 'map', even in combat with a map.
    def test_milestone1_never_map(self):
        res = self.run_js("""
const cases = [{}, FIGHT, Object.assign({map: MAP}, FIGHT), {map: MAP}];
const capsList = [undefined, null, {}, {check: false, board: false}, {check: true}, {board: true}];
const seen = [];
for (const layout of ['phone', 'desktop'])
  for (const s of cases)
    for (const c of capsList) seen.push(tabsFor(layout, s, c));
out(seen);
""")
        for tabs in res:
            self.assertEqual(tabs, ["play", "sheet"])

    # AC15 — outside combat there is no Map tab even with a map and caps.
    def test_no_map_outside_combat(self):
        res = self.run_js("""
out([
  tabsFor('phone', {map: MAP}, CAPS),
  tabsFor('phone', {map: MAP, turn_order: {order: []}}, CAPS),
  tabsFor('desktop', {map: MAP, turn_order: {current: 'Mira'}}, CAPS),
]);
""")
        self.assertEqual(res, [["play", "sheet"]] * 3)

    # Combat without a map: no Map tab.
    def test_no_map_without_map(self):
        res = self.run_js("out([tabsFor('phone', FIGHT, CAPS), tabsFor('desktop', FIGHT, CAPS)]);")
        self.assertEqual(res, [["play", "sheet"]] * 2)

    # Milestone 2 condition: combat + map + both caps adds 'map', both layouts.
    def test_map_with_combat_map_and_caps(self):
        res = self.run_js("""
const s = Object.assign({map: MAP}, FIGHT);
out([tabsFor('phone', s, CAPS), tabsFor('desktop', s, CAPS)]);
""")
        self.assertEqual(res, [["play", "sheet", "map"]] * 2)

    # Each call returns a fresh array (callers may mutate it).
    def test_fresh_array(self):
        res = self.run_js("""
const a = tabsFor('phone', {}, null); a.push('x');
out(tabsFor('phone', {}, null));
""")
        self.assertEqual(res, ["play", "sheet"])


if __name__ == "__main__":
    unittest.main()
