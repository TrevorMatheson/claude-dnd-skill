"""Battle map S4: hidden flag, fog data and the v2 display payload (scripts side).

AC3 (payload), AC8 (server side), AC9, AC12, D8, D9 (storage), D10, D18.

* A token can carry `hidden: true` (`place(..., hidden=, portrait=)`,
  `set_hidden(m, name, bool)`, `combat.py hide|reveal NAME`). A hidden token
  is never in `display_payload`, even beside a PC; `map show` still lists it,
  marked "(hidden)".
* `battlemap.json` gains `"fog": {"trail", "show", "hide"}`. `trail` holds the
  unique squares PCs stood on or passed through (a PC placement, every square
  of a PC's normal path, a forced move's end square), capped at w*h. `show` /
  `hide` are the DM's overrides, edited by `set_fog(m, mode, x1, y1, x2, y2)`
  with mode reveal|hide|auto and by `combat.py fog ... [--all]`.
* `hp_state(cur, max)`: healthy > 50%, bloodied <= 50% and > 25%,
  near_death <= 25%. Monsters get `hp_state`, never `hp`. PCs keep `hp` only
  when it was set on the token.
* Payload tokens carry `portrait` and `conditions` (combat.py push_map reads
  conditions from <campaign>/tracker.json); the payload carries `fog`.
* An older battlemap.json (tests/fixtures/battlemap_v1.json) loads, moves and
  gives an empty fog.
"""
import contextlib
import copy
import importlib.util
import io
import json
import pathlib
import re
import shutil
import subprocess
import sys
import unittest
from unittest import mock

from tests.test_battlemap import CELLAR, SCRIPTS, _fresh  # noqa: F401  (sets sys.path)

import battlemap as bm  # noqa: E402
import paths  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL_MD = REPO / "skills" / "dnd" / "SKILL.md"
V1 = REPO / "tests" / "fixtures" / "battlemap_v1.json"
EMPTY_FOG = {"trail": [], "show": [], "hide": []}


def _api(test, name):
    """A new battlemap function, failing this test alone if it is missing."""
    fn = getattr(bm, name, None)
    if fn is None:
        test.fail(f"battlemap.{name} does not exist")
    return fn


def _tok(payload, name):
    found = [t for t in payload["tokens"] if t["name"] == name]
    return found[0] if found else None


def _sqs(lst):
    return {tuple(p) for p in lst}


def _fog(test, m):
    fog = m.get("fog")
    test.assertIsInstance(fog, dict, "battlemap has no 'fog' dict")
    return fog


def _place_hidden(test, m, name, x, y, **kw):
    try:
        return bm.place(m, name, x, y, hidden=True, **kw)
    except TypeError as exc:
        test.fail(f"place() does not take hidden=: {exc}")


def _v1():
    return json.loads(V1.read_text(encoding="utf-8"))


# ── hidden tokens (AC9, D10) ─────────────────────────────────────────────────

class TestHidden(unittest.TestCase):

    def test_hidden_token_absent_even_next_to_a_pc(self):
        m = _fresh()
        _place_hidden(self, m, "Lurker", 2, 1)          # beside Havoc at (1,1)
        self.assertTrue(m["tokens"]["Lurker"].get("hidden"))
        p = bm.display_payload(m)
        self.assertIsNone(_tok(p, "Lurker"), "hidden token was sent to the display")
        self.assertNotIn("Lurker", json.dumps(p))

    def test_set_hidden_hides_and_reveal_brings_back(self):
        set_hidden = _api(self, "set_hidden")
        m = _fresh()
        set_hidden(m, "Giant Rat", True)
        self.assertIsNone(_tok(bm.display_payload(m), "Giant Rat"))
        set_hidden(m, "giant rat", False)
        rat = _tok(bm.display_payload(m), "Giant Rat")
        self.assertIsNotNone(rat, "revealed token missing from payload")
        self.assertEqual((rat["x"], rat["y"]), (11, 1))

    def test_set_hidden_unknown_token(self):
        set_hidden = _api(self, "set_hidden")
        with self.assertRaises(bm.MapError):
            set_hidden(_fresh(), "Nobody", True)

    def test_render_ascii_marks_hidden(self):
        m = _fresh()
        _place_hidden(self, m, "Lurker", 3, 4)
        text = bm.render_ascii(m)
        line = [ln for ln in text.splitlines() if "Lurker" in ln]
        self.assertTrue(line, "map show no longer lists the hidden token")
        self.assertIn("(hidden)", line[0])
        for ln in text.splitlines():
            if "Havoc" in ln:
                self.assertNotIn("(hidden)", ln)


# ── monster health (AC3, D8) ─────────────────────────────────────────────────

class TestHpState(unittest.TestCase):

    def test_thresholds(self):
        hp_state = _api(self, "hp_state")
        self.assertEqual(hp_state(7, 7), "healthy")
        self.assertEqual(hp_state(3, 7), "bloodied")
        self.assertEqual(hp_state(1, 7), "near_death")
        self.assertEqual(hp_state(5, 8), "healthy")      # 62.5%
        self.assertEqual(hp_state(4, 8), "bloodied")     # exactly 50%
        self.assertEqual(hp_state(3, 8), "bloodied")     # 37.5%
        self.assertEqual(hp_state(2, 8), "near_death")   # exactly 25%
        self.assertEqual(hp_state(0, 7), "near_death")

    def test_monster_payload_has_state_not_hp(self):
        m = _fresh()
        cases = {"Giant Rat": (3, 7, "bloodied"), "Giant Rat 2": (1, 7, "near_death")}
        for n, (cur, mx, _) in cases.items():
            bm.set_hp(m, n, cur, mx)
        p = bm.display_payload(m)
        for n, (_, _, state) in cases.items():
            t = _tok(p, n)
            self.assertEqual(t.get("hp_state"), state, n)
            self.assertNotIn("hp", t, f"{n} still carries exact hp")
        bm.set_hp(m, "Giant Rat", 7, 7)
        self.assertEqual(_tok(bm.display_payload(m), "Giant Rat").get("hp_state"), "healthy")

    def test_no_monster_ever_carries_hp(self):
        m = _fresh()
        bm.set_hp(m, "Giant Rat", 3, 7)
        # the old hp= override argument must not leak numbers either
        try:
            p = bm.display_payload(m, hp={"Giant Rat": [3, 7], "Giant Rat 2": [6, 7]})
        except TypeError:
            p = bm.display_payload(m)
        for t in p["tokens"]:
            if t["kind"] != "pc":
                self.assertNotIn("hp", t, f"monster {t['name']} carries hp")

    def test_pc_keeps_hp_only_when_set(self):
        m = _fresh()
        bm.set_hp(m, "Havoc", 20, 31)
        p = bm.display_payload(m)
        self.assertEqual(_tok(p, "Havoc").get("hp"), [20, 31])
        self.assertNotIn("hp", _tok(p, "Anony"))


# ── portrait and conditions in the payload (AC3, D8) ─────────────────────────

class TestPortraitAndConditions(unittest.TestCase):

    def test_place_with_portrait(self):
        m = _fresh()
        try:
            bm.place(m, "Ogre", 7, 1, portrait="maps/tokens/ogre.png")
        except TypeError as exc:
            self.fail(f"place() does not take portrait=: {exc}")
        self.assertEqual(m["tokens"]["Ogre"].get("portrait"), "maps/tokens/ogre.png")
        self.assertEqual(_tok(bm.display_payload(m), "Ogre").get("portrait"), "maps/tokens/ogre.png")

    def test_conditions_argument(self):
        m = _fresh()
        p = bm.display_payload(m, conditions={"Giant Rat": ["prone"]})
        self.assertEqual(_tok(p, "Giant Rat").get("conditions"), ["prone"])


# ── fog data (AC8, D9 storage, D18) ──────────────────────────────────────────

class TestTrail(unittest.TestCase):

    def test_pc_placement_square_in_trail(self):
        m = _fresh()
        self.assertIn((1, 1), _sqs(_fog(self, m).get("trail", [])))
        self.assertIn((2, 3), _sqs(_fog(self, m)["trail"]))

    def test_monster_placement_not_in_trail(self):
        m = _fresh()
        trail = _sqs(_fog(self, m).get("trail", []))
        self.assertNotIn((11, 1), trail)
        self.assertNotIn((12, 4), trail)

    def test_pc_move_adds_whole_path(self):
        m = _fresh()
        r = bm.move(m, "Havoc", 6, 1, dash=True)      # round by the door
        path = [tuple(p) for p in r["path"]]
        self.assertGreater(len(path), 3)
        trail = _sqs(_fog(self, m).get("trail", []))
        for sq in path:
            self.assertIn(sq, trail, f"path square {sq} missing from trail")

    def test_monster_move_adds_nothing(self):
        m = _fresh()
        before = copy.deepcopy(m.get("fog", EMPTY_FOG)["trail"] if isinstance(m.get("fog"), dict) else [])
        bm.move(m, "Giant Rat", 8, 1)
        after = _fog(self, m).get("trail")
        self.assertEqual(_sqs(after), _sqs(before))

    def test_forced_pc_move_adds_end_square_only(self):
        m = _fresh()
        bm.move(m, "Havoc", 4, 4, force=True)
        trail = _sqs(_fog(self, m).get("trail", []))
        self.assertIn((4, 4), trail)
        self.assertNotIn((3, 3), trail)

    def test_trail_unique_and_capped(self):
        m = _fresh()
        for _ in range(6):
            bm.start_turn(m, "Havoc")
            bm.move(m, "Havoc", 4, 1)
            bm.start_turn(m, "Havoc")
            bm.move(m, "Havoc", 1, 1)
        trail = [tuple(p) for p in _fog(self, m).get("trail", [])]
        self.assertEqual(len(trail), len(set(trail)), "trail has duplicate squares")
        self.assertLessEqual(len(trail), m["grid"]["w"] * m["grid"]["h"])
        self.assertIn((3, 1), set(trail))


class TestManualFog(unittest.TestCase):

    def test_reveal_rect_then_auto(self):
        set_fog = _api(self, "set_fog")
        m = _fresh()
        set_fog(m, "reveal", 2, 2, 4, 3)
        self.assertEqual(_sqs(_fog(self, m)["show"]),
                         {(x, y) for x in (2, 3, 4) for y in (2, 3)})
        self.assertEqual(len(m["fog"]["show"]), 6)
        set_fog(m, "auto", 2, 2, 4, 3)
        self.assertEqual(m["fog"]["show"], [])

    def test_hide_and_reveal_edit_both_lists(self):
        set_fog = _api(self, "set_fog")
        m = _fresh()
        set_fog(m, "reveal", 2, 2, 3, 2)
        set_fog(m, "hide", 3, 2, 3, 2)
        self.assertEqual(_sqs(_fog(self, m)["hide"]), {(3, 2)})
        self.assertEqual(_sqs(m["fog"]["show"]), {(2, 2)})
        set_fog(m, "reveal", 3, 2, 3, 2)
        self.assertEqual(m["fog"]["hide"], [])
        self.assertEqual(_sqs(m["fog"]["show"]), {(2, 2), (3, 2)})

    def test_bad_mode_refused(self):
        set_fog = _api(self, "set_fog")
        with self.assertRaises(ValueError):
            set_fog(_fresh(), "bogus", 1, 1, 2, 2)

    def test_payload_carries_fog(self):
        set_fog = _api(self, "set_fog")
        m = _fresh()
        set_fog(m, "hide", 6, 1, 7, 1)
        p = bm.display_payload(m)
        self.assertIn("fog", p)
        self.assertEqual(set(p["fog"]), {"trail", "show", "hide"})
        self.assertEqual(_sqs(p["fog"]["hide"]), {(6, 1), (7, 1)})
        self.assertIn((1, 1), _sqs(p["fog"]["trail"]))
        json.dumps(p)                                   # JSON-safe


# ── older saves (AC12, D18) ──────────────────────────────────────────────────

class TestOlderSave(unittest.TestCase):

    def setUp(self):
        self.camp = paths.campaigns_dir() / "bm-fog-v1"
        self.camp.mkdir(parents=True, exist_ok=True)
        shutil.copy(V1, bm.map_path(self.camp))

    def test_fixture_is_todays_shape(self):
        m = _v1()
        self.assertNotIn("fog", m)
        for t in m["tokens"].values():
            self.assertNotIn("hidden", t)
            self.assertNotIn("portrait", t)

    def test_v1_loads_moves_and_gives_empty_fog(self):
        m = bm.load(self.camp)
        bm.move(m, "Giant Rat", 8, 1)
        bm.save(self.camp, m)
        p = bm.display_payload(bm.load(self.camp))
        self.assertEqual(p.get("fog"), EMPTY_FOG)
        self.assertEqual(len(p["tokens"]), 4)
        rat = _tok(p, "Giant Rat")
        self.assertEqual((rat["x"], rat["y"]), (8, 1))
        self.assertEqual(rat.get("hp_state"), "healthy")   # 5/7
        self.assertNotIn("hp", rat)

    def test_v1_pc_move_starts_a_trail(self):
        m = _v1()
        r = bm.move(m, "Havoc", 4, 1)
        self.assertTrue(_sqs(r["path"]) <= _sqs(_fog(self, m).get("trail", [])))

    def test_v1_cli_move_and_show(self):
        r = subprocess.run([sys.executable, str(SCRIPTS / "combat.py"), "move", "Havoc", "3", "1",
                            "--no-push", "-c", "bm-fog-v1"], capture_output=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([sys.executable, str(SCRIPTS / "combat.py"), "map", "show",
                            "--no-push", "-c", "bm-fog-v1"], capture_output=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("(hidden)", r.stdout)


# ── combat.py CLI (AC8, AC9) ─────────────────────────────────────────────────

def _load_combat():
    spec = importlib.util.spec_from_file_location("combat_fog", str(SCRIPTS / "combat.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCli(unittest.TestCase):
    CAMP = "bm-fog-cli"

    def setUp(self):
        self.camp = paths.campaigns_dir() / self.CAMP
        if self.camp.exists():
            shutil.rmtree(self.camp)
        self.camp.mkdir(parents=True)
        (self.camp / "layout.txt").write_text(CELLAR, encoding="utf-8")
        (self.camp / "maps" / "tokens").mkdir(parents=True)
        (self.camp / "maps" / "tokens" / "x.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        r = self._c("map", "init", "--layout", str(self.camp / "layout.txt"), "--place", "--no-push")
        self.assertEqual(r.returncode, 0, r.stderr)

    def _c(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / "combat.py"), *args, "-c", self.CAMP],
                              capture_output=True, encoding="utf-8")

    def _map(self):
        return bm.load(self.camp)

    def test_hide_and_reveal(self):
        r = self._c("hide", "Giant Rat", "--no-push")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self._map()["tokens"]["Giant Rat"].get("hidden"))
        show = self._c("map", "show", "--no-push")
        line = [ln for ln in show.stdout.splitlines() if "Giant Rat" in ln and "Giant Rat 2" not in ln]
        self.assertTrue(line and "(hidden)" in line[0], show.stdout)
        r = self._c("reveal", "Giant Rat", "--no-push")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self._map()["tokens"]["Giant Rat"].get("hidden"))

    def test_place_hidden_with_portrait(self):
        r = self._c("place", "Lurker", "3", "4", "--hidden", "--portrait", "maps/tokens/x.png", "--no-push")
        self.assertEqual(r.returncode, 0, r.stderr)
        t = self._map()["tokens"]["Lurker"]
        self.assertTrue(t.get("hidden"))
        self.assertEqual(t.get("portrait"), "maps/tokens/x.png")

    def test_fog_reveal_auto_and_all(self):
        r = self._c("fog", "reveal", "2", "2", "4", "3", "--no-push")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self._map().get("fog", {}).get("show", [])), 6)
        r = self._c("fog", "auto", "2", "2", "4", "3", "--no-push")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._map()["fog"]["show"], [])
        self.assertEqual(self._c("fog", "hide", "6", "1", "--no-push").returncode, 0)
        self.assertEqual(_sqs(self._map()["fog"]["hide"]), {(6, 1)})
        self.assertEqual(self._c("fog", "reveal", "7", "1", "--no-push").returncode, 0)
        r = self._c("fog", "auto", "--all", "--no-push")
        self.assertEqual(r.returncode, 0, r.stderr)
        fog = self._map()["fog"]
        self.assertEqual((fog["show"], fog["hide"]), ([], []))

    def _assert_bogus(self, *args):
        r = self._c(*args, "--bogus")
        self.assertEqual(r.returncode, 2, r.stderr)
        # argparse exits 2 for an unknown subcommand too; demand the flag is what failed
        self.assertNotIn("invalid choice", r.stderr)
        self.assertIn("--bogus", r.stderr)

    def test_hide_bogus_exits_2(self):
        self._assert_bogus("hide", "Giant Rat")

    def test_reveal_bogus_exits_2(self):
        self._assert_bogus("reveal", "Giant Rat")

    def test_fog_bogus_exits_2(self):
        self._assert_bogus("fog", "reveal", "2", "2")

    def _pushed(self, argv):
        combat = _load_combat()
        sent = []
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(combat, "push_stats_json", side_effect=sent.append), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = combat.main([*argv, "-c", self.CAMP])
            except SystemExit as exc:
                rc = exc.code
        self.assertEqual(rc, 0, err.getvalue())
        maps = [p["map"] for p in sent if isinstance(p, dict) and p.get("map")]
        self.assertTrue(maps, "no map payload pushed")
        return maps[-1]

    def test_push_map_carries_tracker_conditions(self):
        (self.camp / "tracker.json").write_text(json.dumps({
            "giant rat": {"name": "Giant Rat", "conditions": ["prone", "frightened"],
                          "concentration": None, "effects": [],
                          "death_saves": {"successes": 0, "failures": 0, "stable": False}},
        }), encoding="utf-8")
        p = self._pushed(["move", "Giant Rat", "10", "1"])
        self.assertEqual(_tok(p, "Giant Rat").get("conditions"), ["prone", "frightened"])
        self.assertFalse(_tok(p, "Havoc").get("conditions"))

    def test_push_without_tracker_still_works(self):
        p = self._pushed(["move", "Giant Rat", "10", "1"])
        self.assertEqual((_tok(p, "Giant Rat")["x"], _tok(p, "Giant Rat")["y"]), (10, 1))

    def test_hide_pushes_payload_without_token(self):
        p = self._pushed(["hide", "Giant Rat"])
        self.assertIsNone(_tok(p, "Giant Rat"), "hidden token was pushed to the display")
        p = self._pushed(["reveal", "Giant Rat"])
        self.assertIsNotNone(_tok(p, "Giant Rat"))

    def test_fog_pushes_overrides(self):
        p = self._pushed(["fog", "reveal", "2", "2", "4", "3"])
        self.assertEqual(len(p.get("fog", {}).get("show", [])), 6)


# ── server cleaning of the v2 map (S5: AC3, AC9, AC12 server side) ───────────

def _raw_map(**extra_tok):
    """A raw /stats map as `push_stats --json` could send it: exact monster
    HP, a hidden token, fog and a portrait."""
    goblin = {"name": "Goblin", "x": 5, "y": 1, "kind": "npc", "hp": [3, 7],
              "portrait": "maps/tokens/goblin.png"}
    goblin.update(extra_tok)
    return {
        "grid": {"w": 14, "h": 6, "ft": 5},
        "cells": CELLAR.splitlines()[:6],
        "round": 2,
        "tokens": [
            {"name": "Havoc", "x": 1, "y": 1, "kind": "pc", "hp": [10, 12]},
            goblin,
            {"name": "Lurker", "x": 8, "y": 3, "kind": "npc", "hidden": True, "hp": [9, 9]},
        ],
        "fog": {"trail": [[1, 1], [2, 1]], "show": [[6, 2]], "hide": [[12, 4]]},
    }


class TestServerCleanMap(unittest.TestCase):
    """`_clean_map` on /stats and in `_load_stats` (plan S5)."""

    @classmethod
    def setUpClass(cls):
        from tests.test_battlemap import _import_app
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def setUp(self):
        self.app._current_stats.pop("map", None)
        self.sent = []
        self._p = mock.patch.object(self.app, "_broadcast", side_effect=self.sent.append)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self.app._current_stats.pop("map", None)

    def _push(self, m):
        self.sent.clear()
        r = self.client.post("/stats", json={"map": m})
        self.assertLess(r.status_code, 300, r.data)
        stored = self.app._current_stats.get("map")
        self.assertIsInstance(stored, dict, "map was not stored")
        sent = [p["stats"]["map"] for p in self.sent
                if isinstance(p, dict) and isinstance(p.get("stats"), dict) and "map" in p["stats"]]
        self.assertTrue(sent, "no stats broadcast carried the map")
        return stored, sent[-1]

    def _both(self, m):
        stored, sent = self._push(m)
        return [("stored", stored), ("broadcast", sent)]

    def test_hidden_token_dropped_stored_and_broadcast(self):
        for where, m in self._both(_raw_map()):
            with self.subTest(where=where):
                self.assertIsNone(_tok(m, "Lurker"), f"hidden token in the {where} map (AC9)")
                self.assertNotIn("Lurker", json.dumps(m))

    def test_monster_hp_list_becomes_hp_state(self):
        for where, m in self._both(_raw_map()):
            with self.subTest(where=where):
                gob = _tok(m, "Goblin")
                self.assertIsNotNone(gob)
                self.assertNotIn("hp", gob, f"exact monster HP in the {where} map (AC3)")
                self.assertEqual(gob.get("hp_state"), "bloodied")

    def test_monster_near_death_from_hp_list(self):
        stored, _ = self._push(_raw_map(hp=[1, 7]))
        gob = _tok(stored, "Goblin")
        self.assertNotIn("hp", gob)
        self.assertEqual(gob.get("hp_state"), "near_death")

    def test_monster_hp_dict_never_reaches_screens(self):
        stored, sent = self._push(_raw_map(hp={"cur": 3, "max": 7}))
        for m in (stored, sent):
            self.assertNotIn("hp", _tok(m, "Goblin"))

    def test_pc_hp_list_kept(self):
        for where, m in self._both(_raw_map()):
            with self.subTest(where=where):
                self.assertEqual(_tok(m, "Havoc").get("hp"), [10, 12])

    def test_fog_survives(self):
        for where, m in self._both(_raw_map()):
            with self.subTest(where=where):
                fog = m.get("fog")
                self.assertIsInstance(fog, dict, f"fog dropped from the {where} map")
                self.assertEqual(_sqs(fog.get("trail", [])), {(1, 1), (2, 1)})
                self.assertEqual(_sqs(fog.get("show", [])), {(6, 2)})
                self.assertEqual(_sqs(fog.get("hide", [])), {(12, 4)})

    def test_fog_keeps_ints_only(self):
        raw = _raw_map()
        raw["fog"] = {"trail": [[1, 1], ["a", 2], None, [3], "x", [2, 1], [{"k": 1}, 4]],
                      "show": [[6, 2], ["<b>", 0]], "hide": [[None, None], [12, 4]],
                      "junk": [[0, 0]]}
        stored, _ = self._push(raw)
        fog = stored.get("fog")
        self.assertIsInstance(fog, dict, "fog dropped")
        self.assertEqual(_sqs(fog.get("trail", [])), {(1, 1), (2, 1)})
        self.assertEqual(_sqs(fog.get("show", [])), {(6, 2)})
        self.assertEqual(_sqs(fog.get("hide", [])), {(12, 4)})
        self.assertNotIn("junk", fog)
        for key in ("trail", "show", "hide"):
            for sq in fog.get(key, []):
                self.assertTrue(len(sq) == 2 and all(type(v) is int for v in sq), (key, sq))

    def test_portrait_survives(self):
        for where, m in self._both(_raw_map()):
            with self.subTest(where=where):
                self.assertEqual(_tok(m, "Goblin").get("portrait"), "maps/tokens/goblin.png")

    def test_portrait_with_dotdot_dropped(self):
        for bad in ("maps/../battlemap.png", "../../secret.png", "maps/tokens/../../x.png"):
            with self.subTest(portrait=bad):
                stored, sent = self._push(_raw_map(portrait=bad))
                gob = _tok(stored, "Goblin")
                self.assertIsNotNone(gob, "the token itself should stay")
                self.assertNotIn("portrait", gob)
                self.assertNotIn("portrait", _tok(sent, "Goblin"))

    def test_portrait_not_an_image_dropped(self):
        for bad in ("maps/battlemap.json", "maps/tokens/x.txt", "<img src=x onerror=1>.png",
                    "javascript:alert(1)", 42, ["maps/a.png"]):
            with self.subTest(portrait=bad):
                stored, _ = self._push(_raw_map(portrait=bad))
                self.assertNotIn("portrait", _tok(stored, "Goblin"))

    def test_valid_hp_state_kept(self):
        for state in ("healthy", "bloodied", "near_death"):
            with self.subTest(hp_state=state):
                raw = _raw_map()
                gob = _tok(raw, "Goblin")
                del gob["hp"]
                gob["hp_state"] = state
                stored, _ = self._push(raw)
                self.assertEqual(_tok(stored, "Goblin").get("hp_state"), state)

    def test_bad_hp_state_dropped(self):
        for state in ("dead", "3/7", "", "<script>", 5, None, ["bloodied"]):
            with self.subTest(hp_state=state):
                raw = _raw_map()
                gob = _tok(raw, "Goblin")
                del gob["hp"]
                gob["hp_state"] = state
                stored, _ = self._push(raw)
                self.assertNotIn("hp_state", _tok(stored, "Goblin"))

    def test_map_null_still_clears(self):
        self._push(_raw_map())
        self.client.post("/stats", json={"map": None})
        self.assertIsNone(self.app._current_stats.get("map"))

    def test_load_stats_cleans_old_map(self):
        import tempfile
        old = {"map": {"grid": {"w": 14, "h": 6, "ft": 5}, "cells": CELLAR.splitlines()[:6],
                       "round": 1, "image": None,
                       "tokens": [{"name": "Havoc", "x": 1, "y": 1, "kind": "pc", "hp": [10, 12]},
                                  {"name": "Giant Rat", "x": 11, "y": 1, "kind": "npc",
                                   "size": 1, "hp": [3, 7]},
                                  {"name": "Lurker", "x": 8, "y": 3, "kind": "npc",
                                   "hidden": True, "hp": [9, 9]}]}}
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = pathlib.Path(d) / "stats.json"
        path.write_text(json.dumps(old), encoding="utf-8")
        new = str(path) if isinstance(self.app.STATS_FILE, str) else type(self.app.STATS_FILE)(path)
        with mock.patch.object(self.app, "STATS_FILE", new):
            self.app._load_stats()
        m = self.app._current_stats.get("map")
        self.assertIsInstance(m, dict, "_load_stats did not load the map")
        rat = _tok(m, "Giant Rat")
        self.assertIsNotNone(rat)
        self.assertNotIn("hp", rat, "exact monster HP replayed from stats.json (AC3)")
        self.assertEqual(rat.get("hp_state"), "bloodied")
        self.assertIsNone(_tok(m, "Lurker"), "hidden token replayed from stats.json (AC9)")
        self.assertEqual(_tok(m, "Havoc").get("hp"), [10, 12])


# ── SKILL.md documents it ────────────────────────────────────────────────────

class TestSkillDoc(unittest.TestCase):

    def setUp(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        m = re.search(r"^## Battle map on the TV\n(.*?)(?=^## )", text, re.S | re.M)
        self.assertIsNotNone(m, "SKILL.md has no 'Battle map on the TV' section")
        self.section = m.group(1)

    def test_hide_and_reveal_documented(self):
        self.assertRegex(self.section, r"(?<!fog )\bhide\b(?!\s*\d)", "no `hide NAME` in the section")
        self.assertRegex(self.section, r"(?<!fog )\breveal\b(?!\s*\d)", "no `reveal NAME` in the section")

    def test_manual_fog_documented(self):
        self.assertIn("fog reveal", self.section)


if __name__ == "__main__":
    unittest.main()
