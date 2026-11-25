"""Phone sheet S15: the Map tab's pure parts (spec D13, AC14, AC15).

static/phone/maptab.js decides when the tab shows (combat, a map, and the
battle map's check and board installed), labels the cost, maps a tap to a
square and centres the player's token. The browser half (tap, path, Move
here) waits for battle map milestone 1 on this branch (plan S16).
"""
import json
import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
JS = ROOT / "skills" / "dnd" / "display" / "static" / "phone" / "maptab.js"
NODE = shutil.which("node")


def run(expr):
    code = f"const M = require({json.dumps(str(JS))}); process.stdout.write(JSON.stringify({expr}));"
    r = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=30)
    if r.returncode:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


FIGHT = {"order": ["Havoc", "Goblin"], "current": "Havoc"}
MAP = {"grid": {"w": 10, "h": 8}, "cells": [], "tokens": []}
CAPS = {"check": True, "board": True}


@unittest.skipUnless(NODE, "node is not installed")
class MapTabTests(unittest.TestCase):
    def test_tab_visible_only_in_combat_with_map_and_caps(self):
        cases = [
            ({"turn_order": FIGHT, "map": MAP}, CAPS, True),
            ({"turn_order": None, "map": MAP}, CAPS, False),                    # AC15
            ({"turn_order": {"order": []}, "map": MAP}, CAPS, False),
            ({"turn_order": FIGHT}, CAPS, False),                               # no map
            ({"turn_order": FIGHT, "map": MAP}, {"check": False, "board": True}, False),
            ({"turn_order": FIGHT, "map": MAP}, {"check": True, "board": False}, False),
            ({"turn_order": FIGHT, "map": MAP}, None, False),
        ]
        for stats, caps, want in cases:
            with self.subTest(stats=stats, caps=caps):
                self.assertEqual(run(f"M.tabVisible({json.dumps(stats)}, {json.dumps(caps)})"), want)

    def test_cost_label(self):
        self.assertEqual(run("M.costLabel({cost: 25, budget: 35})"), "25 of 35 ft")
        self.assertEqual(run("M.costLabel({cost: 30, budget: 30})"), "30 of 30 ft")
        self.assertEqual(run("M.costLabel({cost: null, budget: 30})"), "")

    def test_cell_at(self):
        self.assertEqual(run("M.cellAt(0, 0, 44, 10, 8)"), [0, 0])
        self.assertEqual(run("M.cellAt(43.9, 88, 44, 10, 8)"), [0, 2])
        self.assertEqual(run("M.cellAt(439, 351, 44, 10, 8)"), [9, 7])
        self.assertIsNone(run("M.cellAt(440, 10, 44, 10, 8)"))
        self.assertIsNone(run("M.cellAt(-1, 10, 44, 10, 8)"))

    def test_center_on(self):
        # A 10x8 board of 44 px squares in a 375x500 view, token at (5,4).
        c = run("M.centerOn(5, 4, 44, 375, 500, 440, 352)")
        self.assertEqual(c, {"left": 55, "top": 0})
        # Near the top-left corner it never scrolls past the edge.
        self.assertEqual(run("M.centerOn(0, 0, 44, 200, 200, 440, 352)"), {"left": 0, "top": 0})
        # Near the far corner it stops at the board's end.
        self.assertEqual(run("M.centerOn(9, 7, 44, 200, 200, 440, 352)"), {"left": 240, "top": 152})

    def test_my_token(self):
        m = {"tokens": [{"name": "Goblin", "x": 1, "y": 1}, {"name": "Havoc", "x": 2, "y": 3}]}
        self.assertEqual(run(f"M.myToken({json.dumps(m)}, 'havoc').name"), "Havoc")
        self.assertIsNone(run(f"M.myToken({json.dumps(m)}, 'Mira')"))

    def test_move_here_needs_a_passing_check(self):
        src = JS.read_text(encoding="utf-8")
        self.assertIn("if (!pick || !check || !check.ok) return;", src)

    def test_only_maptab_knows_the_battle_map_modules(self):
        phone = JS.parent
        for f in phone.glob("*.js"):
            if f.name == "maptab.js":
                continue
            text = f.read_text(encoding="utf-8")
            self.assertNotIn("TV.board", text, f.name)
            self.assertNotIn("/map/check", text, f.name)


if __name__ == "__main__":
    unittest.main()
