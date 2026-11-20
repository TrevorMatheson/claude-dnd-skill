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


# ---------------------------------------------------------------------------
# Battle map S3: the 30x20 cap at map creation (AC2 server side, D7).
# `map init` refuses a layout or --size over 30x20, exits 2 and writes
# nothing; older, larger saves still load, move and show.
# ---------------------------------------------------------------------------

CAP_TEXT = "at most 30 squares wide and 20 tall"


def _layout(w, h):
    """A walled w x h room with Havoc (H) in the top-left floor square."""
    rows = ["#" * w]
    for y in range(1, h - 1):
        rows.append("#" + "." * (w - 2) + "#")
    rows.append("#" * w)
    rows[1] = "#H" + rows[1][2:]
    return "\n".join(rows + ["H = Havoc"]) + "\n"


def _size_re(w, h):
    return re.compile(rf"(?<!\d){w}\s*[x×]\s*{h}(?!\d)")


class TestSizeCapApi(unittest.TestCase):

    def _check_size(self):
        fn = getattr(bm, "check_size", None)
        if fn is None:
            self.fail("battlemap.check_size does not exist")
        return fn

    def test_max_constants(self):
        self.assertEqual(getattr(bm, "MAX_W", None), 30)
        self.assertEqual(getattr(bm, "MAX_H", None), 20)

    def _refusal(self, w, h):
        """check_size may raise MapError or return a message; either way give the text."""
        fn = self._check_size()
        try:
            r = fn(w, h)
        except bm.MapError as e:
            return str(e)
        self.assertIsInstance(r, str, f"check_size({w},{h}) neither raised nor returned a message")
        return r

    def test_over_cap_is_refused_with_the_size(self):
        for w, h in [(31, 20), (30, 21), (34, 22), (40, 30)]:
            msg = self._refusal(w, h)
            self.assertIn(CAP_TEXT, msg)
            self.assertRegex(msg, _size_re(w, h))
            self.assertRegex(msg.lower(), r"split|simplif")

    def test_at_or_under_cap_is_accepted(self):
        fn = self._check_size()
        for w, h in [(30, 20), (1, 1), (14, 6), (20, 20)]:
            try:
                r = fn(w, h)
            except bm.MapError as e:
                self.fail(f"check_size({w},{h}) refused: {e}")
            self.assertFalse(isinstance(r, str) and CAP_TEXT in r, f"check_size({w},{h}) -> {r!r}")

    def test_new_map_still_builds_large_maps(self):
        # The cap is only for `map init`; old saves are built/loaded with up to 80.
        m = bm.new_map(40, 30)
        self.assertEqual((m["grid"]["w"], m["grid"]["h"]), (40, 30))


class TestSizeCapCli(unittest.TestCase):

    def setUp(self):
        self.camp = paths.campaigns_dir() / "bm-cap"
        self.camp.mkdir(parents=True, exist_ok=True)
        self.map_file = self.camp / "battlemap.json"
        if self.map_file.exists():
            self.map_file.unlink()

    def _c(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / "combat.py"), *args, "-c", "bm-cap"],
                              capture_output=True, encoding="utf-8")

    def _init_layout(self, w, h):
        f = self.camp / "layout.txt"
        f.write_text(_layout(w, h), encoding="utf-8")
        return self._c("map", "init", "--layout", str(f), "--place", "--no-push")

    def _assert_refused(self, r, w, h):
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 2, out)
        self.assertIn(CAP_TEXT, out)
        self.assertRegex(out, _size_re(w, h))
        self.assertRegex(out.lower(), r"split|simplif")

    def test_layout_31x20_refused_writes_nothing(self):
        r = self._init_layout(31, 20)
        self._assert_refused(r, 31, 20)
        self.assertFalse(self.map_file.exists(), "battlemap.json was written")

    def test_layout_30x21_refused(self):
        r = self._init_layout(30, 21)
        self._assert_refused(r, 30, 21)
        self.assertFalse(self.map_file.exists(), "battlemap.json was written")

    def test_size_30x21_refused_writes_nothing(self):
        r = self._c("map", "init", "--size", "30x21", "--no-push")
        self._assert_refused(r, 30, 21)
        self.assertFalse(self.map_file.exists(), "battlemap.json was written")

    def test_size_31x20_refused(self):
        r = self._c("map", "init", "--size", "31x20", "--no-push")
        self._assert_refused(r, 31, 20)
        self.assertFalse(self.map_file.exists(), "battlemap.json was written")

    def test_refusal_leaves_existing_map_unchanged(self):
        self.assertEqual(self._init_layout(14, 6).returncode, 0)
        before = self.map_file.read_bytes()
        self._assert_refused(self._init_layout(31, 20), 31, 20)
        self.assertEqual(self.map_file.read_bytes(), before, "battlemap.json changed")
        self._assert_refused(self._c("map", "init", "--size", "30x21", "--no-push"), 30, 21)
        self.assertEqual(self.map_file.read_bytes(), before, "battlemap.json changed")

    def test_layout_30x20_succeeds(self):
        r = self._init_layout(30, 20)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        m = bm.load(self.camp)
        self.assertEqual((m["grid"]["w"], m["grid"]["h"]), (30, 20))
        self.assertIn("Havoc", m["tokens"])

    def test_size_30x20_succeeds(self):
        r = self._c("map", "init", "--size", "30x20", "--no-push")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        m = bm.load(self.camp)
        self.assertEqual((m["grid"]["w"], m["grid"]["h"]), (30, 20))

    def test_old_40x30_save_loads_moves_and_shows(self):
        m = bm.new_map(40, 30)
        bm.place(m, "Havoc", 35, 25, kind="pc")
        bm.save(self.camp, m)
        self.assertEqual(bm.load(self.camp)["grid"]["w"], 40)
        r = self._c("move", "Havoc", "37", "27", "--no-push")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        tok = bm.load(self.camp)["tokens"]["Havoc"]
        self.assertEqual((tok["x"], tok["y"]), (37, 27))
        r = self._c("map", "show")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Havoc", r.stdout)


class TestSizeCapDisplay(unittest.TestCase):

    def test_clean_map_accepts_40x30(self):
        from tests.test_battlemap import _import_app
        app = _import_app()
        m = bm.new_map(40, 30)
        bm.place(m, "Havoc", 35, 25, kind="pc")
        out = app._clean_map(bm.display_payload(m))
        self.assertIsNotNone(out)
        self.assertEqual((out["grid"]["w"], out["grid"]["h"]), (40, 30))


class TestSizeCapSkillDoc(unittest.TestCase):

    def test_section_states_cap_and_split_or_simplify(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        m = re.search(r"^## Battle map on the TV\n(.*?)(?=^## )", text, re.S | re.M)
        self.assertIsNotNone(m, "no 'Battle map on the TV' section")
        sec = m.group(1)
        self.assertRegex(sec, r"30\s*[x×]\s*20|30 squares wide and 20 tall",
                         "section does not state the 30x20 cap")
        self.assertRegex(sec.lower(), r"split", "section does not say to split the fight")
        self.assertRegex(sec.lower(), r"simplif", "section does not say to simplify the drawing")


if __name__ == "__main__":
    unittest.main()


class TestDetourByCreatures(unittest.TestCase):
    """QA verify S2: a goblin in a corridor is the limit, not the walls."""

    def test_creature_blocked_detour_names_creatures(self):
        cells, _, _ = bm.parse_layout("#########\n#H.g....#\n#.#####.#\n#.......#\n#########\n")
        m = bm.new_map(9, 5, cells)
        bm.place(m, "Havoc", 1, 1, kind="pc")
        bm.place(m, "Goblin", 3, 1, kind="npc")
        r = bm.check_move(m, "Havoc", 5, 1)
        self.assertEqual(r["detour_by"], "creatures")
        self.assertIn("creature", r["message"])
        self.assertIn("(1,1)", bm.check_move(m, "Havoc", 1, 3)["message"])
