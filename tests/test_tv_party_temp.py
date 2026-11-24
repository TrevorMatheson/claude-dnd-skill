"""Phone sheet, plan step S7: the TV Party panel shows temp HP.

AC5 (TV) — A turn call carrying temp HP updates the TV Party panel. The
       server puts the file's temp HP on the player record as hp.temp
       (_sync_stats_from_sheet); the panel must carry and draw it.

Interface pinned (static/tv/panels.js):
  partyView(stats) -> [{..., temp}]   temp is a number, 0 when absent,
                                      negative or not a number
  panelHtml(['party'], stats)          shows "+N temp" only when N > 0

Runs the real panels.js through node, as tests/test_tv_layout.py does.
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
PANELS_JS = ROOT / "skills" / "dnd" / "display" / "static" / "tv" / "panels.js"
NODE = shutil.which("node")

_PRELUDE = """
globalThis.window = globalThis.window || { TV: {} };
const P = require(%(panels)s);
const out = (v) => console.log(JSON.stringify(v));
const party = (players) => P.panelHtml(['party'], { players: players });
"""


@unittest.skipUnless(NODE, "node is not installed")
class _NodeCase(unittest.TestCase):

    def run_js(self, body: str):
        script = _PRELUDE % {"panels": json.dumps(str(PANELS_JS))} + body
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


class TestPartyViewTemp(_NodeCase):
    """AC5 (TV): partyView carries temp HP."""

    def test_temp_carried(self):
        res = self.run_js("out(P.partyView({players: [{name: 'Havoc', hp: {current: 5, max: 12, temp: 5}}]}));")
        self.assertEqual(res[0].get("temp"), 5)

    def test_temp_zero_when_absent(self):
        res = self.run_js("out(P.partyView({players: [{name: 'Havoc', hp: {current: 5, max: 12}},"
                          " {name: 'Mira'}]}));")
        self.assertEqual([p.get("temp") for p in res], [0, 0])

    def test_temp_zero_when_negative_or_junk(self):
        res = self.run_js("out(P.partyView({players: ["
                          "{name: 'A', hp: {current: 5, max: 12, temp: -3}},"
                          "{name: 'B', hp: {current: 5, max: 12, temp: 'lots'}},"
                          "{name: 'C', hp: {current: 5, max: 12, temp: null}}]}).map(p => p.temp));")
        self.assertEqual(res, [0, 0, 0])

    def test_numeric_string_temp(self):
        res = self.run_js("out(P.partyView({players: [{name: 'A', hp: {current: 5, max: 12, temp: '4'}}]})[0].temp);")
        self.assertEqual(res, 4)


class TestPartyHtmlTemp(_NodeCase):
    """AC5 (TV): the Party panel shows "+N temp" only when N > 0."""

    def test_shows_temp_when_positive(self):
        html = self.run_js("out(party([{name: 'Havoc', hp: {current: 5, max: 12, temp: 5}}]));")
        self.assertIn("+5 temp", html)
        self.assertIn('class="pc-temp"', html)

    def test_temp_inside_hp_figure(self):
        html = self.run_js("out(party([{name: 'Havoc', hp: {current: 5, max: 12, temp: 5}}]));")
        hp_at = html.find('class="pc-hp"')
        self.assertGreaterEqual(hp_at, 0, html)
        self.assertGreater(html.find("+5 temp"), hp_at, "temp sits with the HP figure")

    def test_no_temp_when_zero_absent_or_negative(self):
        html = self.run_js("out([party([{name: 'A', hp: {current: 5, max: 12, temp: 0}}]),"
                           " party([{name: 'B', hp: {current: 5, max: 12}}]),"
                           " party([{name: 'C', hp: {current: 5, max: 12, temp: -2}}])]);")
        for h in html:
            self.assertNotIn("temp", h.replace('data-module', ''), h)

    def test_hostile_temp_is_not_markup(self):
        html = self.run_js("out(party([{name: 'A', hp: {current: 5, max: 12, "
                           "temp: '<img src=x onerror=alert(1)>'}}]));")
        self.assertNotIn("<img", html)
        self.assertNotIn("onerror", html)
