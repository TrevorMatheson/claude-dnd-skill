"""Battle map: positions, movement around walls, reach and range, display payload.

D&D Beyond Maps can't follow a fight (no API). Positions now live in
<campaign>/battlemap.json, moves are checked (walls, obstacles, difficult
terrain, hostile squares, speed), `combat.py attack --from/--target` checks
reach and range, the display stores and broadcasts the map, and a
`send.py --turn` with "moves" is validated before anything is sent.
"""
import importlib.util
import pathlib
import subprocess
import sys
import unittest
import urllib.parse
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
DISPLAY = REPO / "skills" / "dnd" / "display"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(DISPLAY))

import battlemap as bm  # noqa: E402
import paths  # noqa: E402

CELLAR = "\n".join([
    "##############",
    "#H...#.....r.#",
    "#....#..TT...#",
    "#.A..D...~~..#",
    "#....#...~~.s#",
    "##############",
    "H = Havoc",
    "A = Anony",
    "r = Giant Rat",
    "s = Giant Rat 2",
]) + "\n"


def _fresh():
    cells, markers, legend = bm.parse_layout(CELLAR)
    m = bm.new_map(len(cells[0]), len(cells), cells)
    for ch, (x, y) in markers.items():
        bm.place(m, legend[ch], x, y, kind="pc" if ch.isupper() else "npc")
    return m


class TestRules(unittest.TestCase):

    def test_layout(self):
        cells, markers, legend = bm.parse_layout(CELLAR)
        self.assertEqual(len(cells), 6)
        self.assertEqual(markers["H"], (1, 1))
        self.assertEqual(legend["s"], "Giant Rat 2")
        self.assertEqual(cells[1][1], ".")       # the marker square is floor

    def test_wall_forces_the_door(self):
        m = _fresh()
        # (6,1) is 5 squares east as the crow flies; the wall sends Havoc via the door at (5,3)
        self.assertEqual(bm.distance_ft((1, 1), (6, 1)), 25)
        self.assertEqual(bm.path_cost_ft(m, (1, 1), (6, 1), "Havoc", "pc"), 35)

    def test_speed_enforced_and_dash(self):
        m = _fresh()
        with self.assertRaises(bm.MapError):
            bm.move(m, "Havoc", 9, 1)
        r = bm.move(m, "havoc", 9, 1, dash=True)
        self.assertEqual(r["name"], "Havoc")
        self.assertEqual(r["cost"], 40)

    def test_movement_spent_until_turn_starts(self):
        m = _fresh()
        bm.move(m, "Havoc", 4, 1)          # 15 ft
        bm.move(m, "Havoc", 4, 4)          # 15 ft more
        with self.assertRaises(bm.MapError):
            bm.move(m, "Havoc", 3, 4)
        bm.start_turn(m, "Havoc")
        bm.move(m, "Havoc", 3, 4)

    def test_difficult_terrain_costs_double(self):
        m = _fresh()
        bm.place(m, "Mira", 8, 3, kind="pc")
        # (9,3) and (10,3) are both difficult: 10 ft each
        self.assertEqual(bm.path_cost_ft(m, (8, 3), (10, 3), "Mira", "pc"), 20)
        self.assertEqual(bm.path_cost_ft(m, (8, 3), (8, 4), "Mira", "pc"), 5)

    def test_cannot_end_on_ally_or_pass_hostile(self):
        m = _fresh()
        with self.assertRaises(bm.MapError):
            bm.move(m, "Havoc", 2, 3)                     # Anony's square
        self.assertIsNone(bm.path_cost_ft(m, (1, 1), (11, 1), "Havoc", "pc"))  # the rat's square
        # Anony's square can be passed through on the way to the door
        self.assertIsNotNone(bm.path_cost_ft(m, (1, 3), (4, 3), "Havoc", "pc"))

    def test_obstacle_blocks(self):
        m = _fresh()
        with self.assertRaises(bm.MapError):
            bm.place(m, "Crate goblin", 8, 2)

    def test_force_move_skips_speed(self):
        m = _fresh()
        r = bm.move(m, "Giant Rat", 6, 1, force=True)
        self.assertEqual(r["cost"], 0)

    def test_reach_and_range(self):
        m = _fresh()
        self.assertFalse(bm.attack_range(m, "Havoc", "Giant Rat")["ok"])
        bm.place(m, "Havoc", 10, 1, kind="pc")
        self.assertTrue(bm.attack_range(m, "Havoc", "Giant Rat")["ok"])
        far = bm.attack_range(m, "Anony", "Giant Rat 2", rng=(30, 120))
        self.assertTrue(far["ok"])
        self.assertTrue(far["disadvantage"])        # 50 ft is past normal range 30
        self.assertFalse(bm.attack_range(m, "Anony", "Giant Rat 2", rng=(10, 40))["ok"])

    def test_ranged_with_hostile_adjacent_has_disadvantage(self):
        m = _fresh()
        bm.place(m, "Giant Rat", 3, 3)
        r = bm.attack_range(m, "Anony", "Giant Rat 2", rng=(80, 320))
        self.assertTrue(r["disadvantage"])
        self.assertIn("Giant Rat", r["why"])

    def test_payload(self):
        m = _fresh()
        bm.set_hp(m, "Giant Rat", 3, 7)
        p = bm.display_payload(m)
        rat = [t for t in p["tokens"] if t["name"] == "Giant Rat"][0]
        self.assertEqual(rat["hp"], [3, 7])
        self.assertEqual(p["grid"]["w"], 14)


class TestCli(unittest.TestCase):

    def setUp(self):
        self.camp = paths.campaigns_dir() / "bm-cli"
        self.camp.mkdir(parents=True, exist_ok=True)
        (self.camp / "layout.txt").write_text(CELLAR, encoding="utf-8")

    def _c(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / "combat.py"), *args, "-c", "bm-cli"],
                              capture_output=True, encoding="utf-8")

    def test_flow(self):
        r = self._c("map", "init", "--layout", str(self.camp / "layout.txt"), "--place", "--no-push")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.camp / "battlemap.json").exists())
        r = self._c("move", "Havoc", "9", "1", "--no-push")
        self.assertEqual(r.returncode, 2)
        self.assertIn("40 ft", r.stderr)
        r = self._c("move", "Havoc", "9", "1", "--dash", "--no-push")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self._c("attack", "--atk", "5", "--ac", "12", "--dmg", "1d8+3",
                    "--from", "Havoc", "--target", "Giant Rat 2")
        self.assertEqual(r.returncode, 3)
        self.assertIn("out of 5-ft reach", r.stdout)
        r = self._c("dist", "Havoc", "Giant Rat")
        self.assertIn("10 ft", r.stdout)
        r = self._c("hp", "Giant Rat", "3/7", "--no-push")
        self.assertEqual(r.returncode, 0, r.stderr)


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_bm", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestServerAndTurn(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()
        sys.argv = ["send.py"]
        import send
        cls.send = send
        cls.camp = paths.campaigns_dir() / "bm-turn"
        cls.camp.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        bm.save(self.camp, _fresh())
        self.app._current_stats.pop("map", None)
        self.sent = []
        self._p = mock.patch.object(self.app, "_broadcast", side_effect=self.sent.append)
        self._p.start()

    def tearDown(self):
        self._p.stop()

    def _fake_post(self, url, data, token):
        r = self.client.post(urllib.parse.urlparse(url).path, data=data, content_type="application/json")
        return r.status_code < 300

    def test_server_stores_moves_and_clears_map(self):
        self.client.post("/stats", json={"map": bm.display_payload(_fresh())})
        self.assertEqual(len(self.app._current_stats["map"]["tokens"]), 4)
        self.client.post("/stats", json={"map_moves": [{"name": "giant rat", "x": 7, "y": 1}]})
        rat = [t for t in self.app._current_stats["map"]["tokens"] if t["name"] == "Giant Rat"][0]
        self.assertEqual((rat["x"], rat["y"]), (7, 1))
        self.client.post("/stats", json={"map": None})
        self.assertIsNone(self.app._current_stats["map"])

    def test_server_rejects_bad_map(self):
        self.client.post("/stats", json={"map": {"grid": {"w": 500, "h": 2}}})
        self.assertIsNone(self.app._current_stats["map"])

    def test_map_image_path_traversal_refused(self):
        with mock.patch.object(self.app, "_active_campaign_name", return_value="bm-turn"):
            self.assertEqual(self.client.get("/map-image/maps/../battlemap.json").status_code, 404)
            self.assertEqual(self.client.get("/map-image/maps/nothere.png").status_code, 404)

    def test_turn_moves_validated_and_sent(self):
        turn = {"campaign": "bm-turn",
                "blocks": [{"text": "The rat darts along the wall."}],
                "moves": [{"name": "Giant Rat", "x": 8, "y": 1, "turn": True, "hp": "3/7"}]}
        with mock.patch.object(self.send, "_post", side_effect=self._fake_post):
            self.assertEqual(self.send.send_turn(turn), 0)
        rat = [t for t in self.app._current_stats["map"]["tokens"] if t["name"] == "Giant Rat"][0]
        self.assertEqual((rat["x"], rat["y"]), (8, 1))
        self.assertEqual(rat["hp"], [3, 7])
        self.assertEqual(bm.load(self.camp)["tokens"]["Giant Rat"]["x"], 8)

    def test_bad_move_sends_nothing(self):
        turn = {"campaign": "bm-turn", "blocks": [{"text": "Havoc runs."}],
                "moves": [{"name": "Havoc", "x": 12, "y": 1}]}
        with mock.patch.object(self.send, "_post") as p:
            self.assertEqual(self.send.send_turn(turn), 2)
        p.assert_not_called()
        self.assertEqual(bm.load(self.camp)["tokens"]["Havoc"]["x"], 1)


if __name__ == "__main__":
    unittest.main()
