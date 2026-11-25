"""Phone sheet S14: the Map tab's server seam (spec D13, AC14 server half).

map_moves.py is the only file that knows the battle map's interface. Until
battle map milestone 1 is merged, battlemap.check_move doesn't exist on this
branch, so these tests patch in a fake battlemap module with the contract the
battle map plan defines (docs/specs/2026-11-17-battle-map.plan.md, "check_move").
"""
import importlib.util
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "skills" / "dnd" / "display"
if str(DISPLAY) not in sys.path:
    sys.path.insert(0, str(DISPLAY))

import map_moves  # noqa: E402


def _load_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_mapmoves", DISPLAY / "dnd-display-app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


APP = _load_app()


class FakeBM(types.ModuleType):
    """A battlemap module with the plan's check_move contract."""

    class MapError(ValueError):
        pass

    def __init__(self, m, with_check=True):
        super().__init__("battlemap")
        self.m = m
        self.seen_maps = []
        if with_check:
            self.check_move = self._check_move

    def load(self, campaign_dir):
        return json.loads(json.dumps(self.m))

    def _check_move(self, m, name, x, y, dash=False):
        self.seen_maps.append(m)
        t = m["tokens"][name]
        cost = (abs(t["x"] - x) + abs(t["y"] - y)) * 5
        budget = 35 * (2 if dash else 1)
        occ = [n for n, o in m["tokens"].items() if n != name and (o["x"], o["y"]) == (x, y)]
        base = {"name": name, "from": [t["x"], t["y"]], "to": [x, y], "straight_ft": cost,
                "occupant": None, "detour_by": None, "difficult_ft": 0,
                "path": [[t["x"], t["y"]], [x, y]], "cost": cost, "budget": budget,
                "left": budget - cost}
        if occ:
            return dict(base, ok=False, reason="occupied", occupant=occ[0],
                        message=f"({x},{y}) is taken by {occ[0]}")
        if cost > budget:
            return dict(base, ok=False, reason="too_far",
                        message=f"{name} needs {cost} ft to reach ({x},{y}) but has {budget} ft")
        return dict(base, ok=True, reason=None, message=f"{name} → ({x},{y}): {cost} ft")


MAP = {"grid": {"w": 10, "h": 8}, "cells": ["." * 10] * 8,
       "tokens": {"Havoc": {"x": 1, "y": 1, "kind": "pc", "speed": 35},
                  "Goblin": {"x": 3, "y": 1, "kind": "npc"},
                  "Lurker": {"x": 4, "y": 1, "kind": "npc", "hidden": True}}}


class SeamTests(unittest.TestCase):
    def patch_bm(self, fake):
        return mock.patch.object(map_moves, "_bm", lambda: fake)

    def test_unavailable_without_check_move(self):
        with self.patch_bm(FakeBM(MAP, with_check=False)):
            self.assertFalse(map_moves.available())
            with self.assertRaises(map_moves.MoveError) as cm:
                map_moves.check("camp", "Havoc", 2, 1)
            self.assertEqual(cm.exception.status, 501)

    def test_available_with_check_move(self):
        with self.patch_bm(FakeBM(MAP)):
            self.assertTrue(map_moves.available())

    def test_result_is_whitelisted(self):
        with self.patch_bm(FakeBM(MAP)):
            out = map_moves.check("camp", "havoc", 2, 2)
        self.assertEqual(set(out), set(map_moves.FIELDS))
        self.assertTrue(out["ok"])
        self.assertEqual((out["cost"], out["budget"]), (10, 35))

    def test_hidden_tokens_never_reach_the_check(self):
        fake = FakeBM(MAP)
        with self.patch_bm(fake):
            out = map_moves.check("camp", "Havoc", 4, 1)
        self.assertNotIn("Lurker", fake.seen_maps[-1]["tokens"])
        self.assertTrue(out["ok"], out)              # the hidden square looks free
        self.assertNotIn("Lurker", json.dumps(out))

    def test_visible_occupant_is_the_reason(self):
        with self.patch_bm(FakeBM(MAP)):
            out = map_moves.check("camp", "Havoc", 3, 1)
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "occupied")

    def test_unknown_token(self):
        with self.patch_bm(FakeBM(MAP)):
            with self.assertRaises(map_moves.MoveError) as cm:
                map_moves.check("camp", "Mira", 2, 1)
        self.assertEqual(cm.exception.status, 404)

    def test_move_text_survives_the_stage_sanitizer(self):
        text = map_moves.move_text({"cost": 25, "budget": 35}, 6, 1)
        self.assertEqual(text, "moves to square 6,1 - 25 of 35 ft")
        self.assertEqual(APP._sanitize_input(text), text)


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.client = APP.app.test_client()
        self.fake = FakeBM(MAP)
        self.p = [mock.patch.object(map_moves, "_bm", lambda: self.fake),
                  mock.patch.object(APP, "_campaign_dir", lambda: "camp"),
                  mock.patch.object(APP, "_lan_token", None)]
        for x in self.p:
            x.start()
        with APP._stats_lock:
            self._saved_players = list(APP._current_stats.get("players", []))
            APP._current_stats["players"] = [{"name": "Havoc"}, {"name": "Anony"}]
        with APP._staged_lock:
            APP._staged.clear()
        self.stage_calls = []

    def tearDown(self):
        for x in self.p:
            x.stop()
        with APP._stats_lock:
            APP._current_stats["players"] = self._saved_players
        with APP._staged_lock:
            APP._staged.clear()

    def post(self, url, body):
        return self.client.post(url, json=body, headers={"X-DND-Device": "dev-map-test"})

    def test_caps(self):
        j = self.client.get("/map/caps").get_json()
        self.assertTrue(j["check"])
        self.assertIn("board", j)

    def test_check_returns_the_dry_run(self):
        r = self.post("/map/check", {"character": "Havoc", "x": 2, "y": 2})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["cost"], 10)

    def test_check_501_without_the_battle_map(self):
        self.fake = FakeBM(MAP, with_check=False)
        r = self.post("/map/check", {"character": "Havoc", "x": 2, "y": 2})
        self.assertEqual(r.status_code, 501)
        self.assertFalse(r.get_json()["available"])

    def test_check_bad_square(self):
        r = self.post("/map/check", {"character": "Havoc", "x": "a", "y": 2})
        self.assertEqual(r.status_code, 400)

    def test_move_refused_stages_nothing(self):
        r = self.post("/map/move", {"character": "Havoc", "x": 3, "y": 1})
        self.assertEqual(r.status_code, 409)
        self.assertIn("taken", r.get_json()["message"])
        with APP._staged_lock:
            self.assertNotIn("Havoc", APP._staged)

    def test_move_ok_stages_the_action(self):
        with mock.patch.object(APP, "_check_auto_trigger", lambda: None):
            r = self.post("/map/move", {"character": "Havoc", "x": 6, "y": 1, "op": "mv-1"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.get_json()["sent"], "moves to square 6,1 - 25 of 35 ft")
        with APP._staged_lock:
            self.assertEqual(APP._staged["Havoc"]["text"], "moves to square 6,1 - 25 of 35 ft")
            self.assertTrue(APP._staged["Havoc"]["ready"])

    def test_repeated_op_staged_once(self):
        with mock.patch.object(APP, "_check_auto_trigger", lambda: None):
            self.post("/map/move", {"character": "Havoc", "x": 6, "y": 1, "op": "mv-2"})
            r = self.post("/map/move", {"character": "Havoc", "x": 6, "y": 1, "op": "mv-2"})
        self.assertEqual(r.status_code, 200)
        with APP._staged_lock:
            self.assertEqual(APP._staged["Havoc"]["text"].count("moves to square"), 1)

    def test_map_never_written(self):
        before = json.dumps(self.fake.m, sort_keys=True)
        with mock.patch.object(APP, "_check_auto_trigger", lambda: None):
            self.post("/map/check", {"character": "Havoc", "x": 2, "y": 2})
            self.post("/map/move", {"character": "Havoc", "x": 6, "y": 1})
        self.assertEqual(before, json.dumps(self.fake.m, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
