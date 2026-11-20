"""Battle map S2: a dry-run move check that says why a move fails (AC6, D13, D4).

`battlemap.check_move` answers "can this token get there, and if not, why?"
without touching the map, so the DM can bounce an impossible player move
back instead of narrating it. `combat.py move ... --check [--json]` is the
CLI form: exit 0 or 2, nothing saved, nothing pushed.
"""
import copy
import json
import pathlib
import re
import subprocess
import sys
import unittest

from tests.test_battlemap import CELLAR, SCRIPTS, _fresh  # noqa: F401  (sets sys.path)

import battlemap as bm  # noqa: E402
import paths  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL_MD = REPO / "skills" / "dnd" / "SKILL.md"

KEYS = {"ok", "name", "from", "to", "cost", "budget", "left", "straight_ft", "path",
        "reason", "occupant", "detour_by", "difficult_ft", "message"}


def _check(test, *args, **kw):
    """Call bm.check_move, failing this test alone (not the module) if it is missing."""
    fn = getattr(bm, "check_move", None)
    if fn is None:
        test.fail("battlemap.check_move does not exist")
    return fn(*args, **kw)


def _find(test, *args, **kw):
    fn = getattr(bm, "find_path", None)
    if fn is None:
        test.fail("battlemap.find_path does not exist")
    return fn(*args, **kw)


def _sq(p):
    return tuple(p)


def _walled_in():
    """CELLAR with (7,4) boxed in by obstacles: floor, but no way in."""
    m = _fresh()
    rows = [list(r) for r in m["cells"]]
    for x, y in [(6, 3), (7, 3), (8, 3), (6, 4), (8, 4)]:
        rows[y][x] = "T"
    m["cells"] = ["".join(r) for r in rows]
    return m


class TestCheckMove(unittest.TestCase):

    def test_too_far_through_the_door(self):
        m = _fresh()
        r = _check(self, m, "Havoc", 6, 1)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "too_far")
        self.assertEqual(r["cost"], 35)
        self.assertEqual(r["left"], 30)
        self.assertEqual(r["budget"], 30)
        self.assertEqual(r["straight_ft"], 25)
        self.assertEqual(r["detour_by"], "walls")
        self.assertIn("30 ft", r["message"])
        self.assertIn("wall", r["message"].lower())

    def test_result_has_every_key(self):
        m = _fresh()
        for args in [("Havoc", 4, 1), ("Havoc", 6, 1), ("Havoc", 5, 1), ("Havoc", 2, 3)]:
            r = _check(self, m, *args)
            self.assertTrue(KEYS <= set(r), f"{args}: missing {KEYS - set(r)}")
            self.assertIsInstance(r["message"], str)
            self.assertTrue(r["message"].strip())
            self.assertNotIn("\n", r["message"].strip())     # one line

    def test_goal_on_wall_is_blocked(self):
        r = _check(self, _fresh(), "Havoc", 5, 1)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "blocked")

    def test_goal_off_map(self):
        r = _check(self, _fresh(), "Havoc", 40, 40)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "off_map")

    def test_goal_on_ally_is_occupied(self):
        r = _check(self, _fresh(), "Havoc", 2, 3)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "occupied")
        self.assertEqual(r["occupant"], "Anony")
        self.assertIn("Anony", r["message"])

    def test_walled_in_square_has_no_path(self):
        m = _walled_in()
        self.assertEqual(bm.cell(m, 7, 4), ".")
        r = _check(self, m, "Havoc", 7, 4, dash=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no_path")

    def test_difficult_terrain_reported(self):
        m = _fresh()
        bm.place(m, "Mira", 8, 3, kind="pc")
        r = _check(self, m, "Mira", 10, 3)           # (9,3) and (10,3) are difficult
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["cost"], 20)
        self.assertTrue(r["difficult_ft"], r)        # reported, non-zero
        plain = _check(self, m, "Mira", 8, 4)
        self.assertFalse(plain["difficult_ft"])      # 0 / None on plain floor

    def test_ok_move_path_runs_token_to_goal(self):
        r = _check(self, _fresh(), "Havoc", 4, 1)
        self.assertTrue(r["ok"], r)
        self.assertIsNone(r["reason"])
        self.assertEqual(r["cost"], 15)
        self.assertEqual(r["left"], 15)
        self.assertEqual(_sq(r["from"]), (1, 1))
        self.assertEqual(_sq(r["to"]), (4, 1))
        self.assertEqual(_sq(r["path"][0]), (1, 1))
        self.assertEqual(_sq(r["path"][-1]), (4, 1))

    def test_path_goes_through_the_door(self):
        r = _check(self, _fresh(), "Havoc", 6, 1, dash=True)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["cost"], 35)
        self.assertEqual(r["budget"], 60)
        self.assertEqual(r["left"], 25)
        path = [_sq(p) for p in r["path"]]
        self.assertEqual(path[0], (1, 1))
        self.assertEqual(path[-1], (6, 1))
        self.assertIn((5, 3), path)
        for a, b in zip(path, path[1:]):
            self.assertLessEqual(max(abs(a[0] - b[0]), abs(a[1] - b[1])), 1, path)

    def test_never_mutates_the_map(self):
        m = _fresh()
        bm.move(m, "Havoc", 2, 1)                     # some movement already spent
        before = copy.deepcopy(m)
        for args, kw in [(("Havoc", 4, 1), {}), (("Havoc", 6, 1), {}),
                         (("Havoc", 9, 1), {"dash": True}), (("Havoc", 5, 1), {}),
                         (("Havoc", 2, 3), {}), (("Havoc", 40, 40), {})]:
            _check(self, m, *args, **kw)
            self.assertEqual(m, before, f"check_move{args} changed the map")

    def test_move_refuses_with_the_check_message(self):
        m = _fresh()
        before = copy.deepcopy(m)
        with self.assertRaises(bm.MapError) as cm:
            bm.move(m, "Havoc", 6, 1)
        self.assertEqual(m, before)
        msg = _check(self, _fresh(), "Havoc", 6, 1)["message"]
        self.assertEqual(str(cm.exception), msg)


class TestFindPath(unittest.TestCase):

    def test_returns_cost_and_path(self):
        r = _find(self, _fresh(), (1, 1), (6, 1), "Havoc", "pc")
        self.assertIsNotNone(r)
        cost, path = r
        self.assertEqual(cost, 35)
        self.assertEqual(_sq(path[0]), (1, 1))
        self.assertEqual(_sq(path[-1]), (6, 1))

    def test_none_when_unreachable(self):
        self.assertIsNone(_find(self, _fresh(), (1, 1), (11, 1), "Havoc", "pc"))  # rat's square
        self.assertIsNone(_find(self, _walled_in(), (1, 1), (7, 4), "Havoc", "pc"))

    def test_path_cost_ft_unchanged(self):
        m = _fresh()
        self.assertEqual(bm.path_cost_ft(m, (1, 1), (6, 1), "Havoc", "pc"), 35)
        self.assertIsNone(bm.path_cost_ft(m, (1, 1), (11, 1), "Havoc", "pc"))
        self.assertIsNone(bm.path_cost_ft(m, (1, 1), (5, 1), "Havoc", "pc"))
        bm.place(m, "Mira", 8, 3, kind="pc")
        self.assertEqual(bm.path_cost_ft(m, (8, 3), (10, 3), "Mira", "pc"), 20)


class TestCheckCli(unittest.TestCase):

    def setUp(self):
        self.camp = paths.campaigns_dir() / "bm-check"
        self.camp.mkdir(parents=True, exist_ok=True)
        (self.camp / "layout.txt").write_text(CELLAR, encoding="utf-8")
        r = self._c("map", "init", "--layout", str(self.camp / "layout.txt"), "--place", "--no-push")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.map_file = self.camp / "battlemap.json"
        self.before = self.map_file.read_bytes()

    def _c(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / "combat.py"), *args, "-c", "bm-check"],
                              capture_output=True, encoding="utf-8")

    def _unchanged(self):
        self.assertEqual(self.map_file.read_bytes(), self.before, "battlemap.json changed")

    def test_check_too_far_exits_2_and_saves_nothing(self):
        r = self._c("move", "Havoc", "9", "1", "--check", "--no-push")
        out = r.stdout + r.stderr
        self.assertNotIn("unrecognized arguments", out)
        self.assertEqual(r.returncode, 2, out)
        self.assertIn("40 ft", out)                   # needs 40
        self.assertIn("30 ft", out)                   # has 30
        self._unchanged()

    def test_check_ok_prints_ok_and_saves_nothing(self):
        r = self._c("move", "Havoc", "6", "1", "--dash", "--check", "--no-push")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.strip().startswith("OK:"), r.stdout)
        self.assertIn("Havoc", r.stdout)
        self.assertIn("35 ft", r.stdout)
        self._unchanged()

    def test_check_json_ok(self):
        r = self._c("move", "Havoc", "4", "1", "--check", "--json", "--no-push")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = json.loads(r.stdout)
        self.assertTrue(KEYS <= set(d), KEYS - set(d))
        self.assertTrue(d["ok"])
        self.assertEqual(d["cost"], 15)
        self._unchanged()

    def test_check_json_refused(self):
        r = self._c("move", "Havoc", "6", "1", "--check", "--json", "--no-push")
        self.assertNotIn("unrecognized arguments", r.stderr)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        d = json.loads(r.stdout)
        self.assertFalse(d["ok"])
        self.assertEqual(d["reason"], "too_far")
        self.assertEqual(d["detour_by"], "walls")
        self._unchanged()

    def test_unknown_flag_exits_2(self):
        r = self._c("move", "Havoc", "4", "1", "--bogus", "--no-push")
        self.assertEqual(r.returncode, 2)
        self._unchanged()


class TestSkillDoc(unittest.TestCase):

    def test_dm_checks_before_narrating_a_players_move(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        m = re.search(r"^## Battle map on the TV\n(.*?)(?=^## )", text, re.S | re.M)
        self.assertIsNotNone(m, "no 'Battle map on the TV' section")
        sec = m.group(1)
        self.assertRegex(sec, r"move[^\n]*--check", "section never shows `move … --check`")
        # The instruction ties the check to narrating a player's move.
        near = re.search(r"--check.{0,400}narrat|narrat.{0,400}--check", sec, re.S | re.I)
        self.assertIsNotNone(near, "`--check` is not tied to narrating a move")


if __name__ == "__main__":
    unittest.main()
