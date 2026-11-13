"""Scripts reject options they do not know, and combat.py rolls advantage.

combat.py scanned argv by hand, so `attack ... --advantage` was dropped and a
Pack Tactics attack rolled one d20. Now combat.py uses argparse, and dice.py /
character.py check every option against a known list before dispatch.
"""
import importlib.util
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


combat = _load("combat_strict", SCRIPTS / "combat.py")


def _run(script, *args):
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                          capture_output=True, encoding="utf-8")


class TestUnknownOptionsRejected(unittest.TestCase):

    def test_combat_attack(self):
        r = _run("combat.py", "attack", "--atk", "4", "--ac", "12", "--dmg", "1d4", "--pack-tactics")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--pack-tactics", r.stderr)

    def test_dice(self):
        r = _run("dice.py", "d20", "--auto", "--lable", "Stealth")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--lable", r.stderr)

    def test_character(self):
        r = _run("character.py", "xp", "--level", "2", "--gaind", "50")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--gaind", r.stderr)

    def test_negative_numbers_are_values_not_options(self):
        r = _run("character.py", "hitdie", "--die", "d8", "--con-mod", "-1", "--hp", "3",
                 "--max-hp", "10", "--remaining", "2")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_known_options_still_work(self):
        r = _run("combat.py", "attack", "--atk", "4", "--ac", "12", "--dmg", "1d4+2", "--adv")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2d20 adv", r.stdout)


class TestAttackAdvantage(unittest.TestCase):

    def _attack(self, faces, **kw):
        with mock.patch.object(combat, "d", side_effect=list(faces) + [1] * 10):
            return combat.resolve_attack(0, 15, "1d4", **kw)

    def test_advantage_keeps_higher(self):
        r = self._attack([3, 17], advantage=True)
        self.assertEqual(r["d20"], 17)
        self.assertEqual(r["d20_rolls"], [3, 17])
        self.assertTrue(r["hit"])

    def test_disadvantage_keeps_lower(self):
        r = self._attack([3, 17], disadvantage=True)
        self.assertEqual(r["d20"], 3)
        self.assertFalse(r["hit"])

    def test_both_cancel_to_one_die(self):
        r = self._attack([12], advantage=True, disadvantage=True)
        self.assertEqual(r["d20_rolls"], [12])
        self.assertIsNone(r["adv_mode"])

    def test_advantage_natural_20_crits(self):
        r = self._attack([20, 2], advantage=True)
        self.assertTrue(r["crit"])

    def test_format_shows_both_dice(self):
        r = self._attack([3, 17], advantage=True)
        self.assertIn("2d20 adv [3, 17]→17", combat.format_attack(r))


if __name__ == "__main__":
    unittest.main()
