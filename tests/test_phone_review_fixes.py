"""Regression tests for the phone sheet review (2026-11-24).

1. A player's change can't write new lines into the character file
   (condition and item names, notes).            [blocking: D7, D10, AC6]
2. Two changes landing together leave the TV's record equal to the file. [AC5, AC8]
3. The map check answers the same for a hidden creature as for nobody,
   a hiding PC can still move, and only party members can ask.   [D13, AC14]
5. The sheet event names both the file's heading and the URL name. [AC5]
7. A Retry that arrives while the first request still runs isn't applied twice. [D14]
"""
import importlib.util
import json
import pathlib
import shutil
import sys
import threading
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "skills" / "dnd" / "display"
FIX = ROOT / "tests" / "fixtures" / "sheets"
if str(DISPLAY) not in sys.path:
    sys.path.insert(0, str(DISPLAY))

import sheet  # noqa: E402
import map_moves  # noqa: E402
from tests.test_map_moves import FakeBM, MAP  # noqa: E402


def _load_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_reviewfix", DISPLAY / "dnd-display-app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


APP = _load_app()
TEMPLATE = (FIX / "template_filled.md").read_text(encoding="utf-8")


class NoInjection(unittest.TestCase):
    def refused(self, entry):
        new, res = sheet.apply(TEMPLATE, [entry], sheet.PLAYER_FIELDS)
        self.assertFalse(res[0]["ok"], res)
        self.assertEqual(new, TEMPLATE)

    def test_condition_with_a_heading(self):
        self.refused({"condition": "Prone\n## Ability Scores", "on": True})

    def test_item_with_a_new_line(self):
        self.refused({"item": "Rope\n**HP:** 99/99", "delta": 1})

    def test_item_with_bar_or_comma(self):
        self.refused({"item": "Rope | 99", "delta": 1})
        self.refused({"item": "Rope, Torch", "delta": 1})

    def test_note_with_a_new_line_or_heading(self):
        self.refused({"note": "hello\n## Ability Scores", "on": True})
        self.refused({"note": "## Ability Scores", "on": True})

    def test_ordinary_names_still_work(self):
        new, res = sheet.apply(TEMPLATE, [{"item": "Arcane focus (rod)", "delta": 1},
                                          {"condition": "Poisoned", "on": True},
                                          {"note": "owes Brann 5 gp", "on": True}], sheet.PLAYER_FIELDS)
        self.assertTrue(all(r["ok"] for r in res), res)


class Campaign(unittest.TestCase):
    """A sandbox campaign holding Havoc (heading "# Havoc Ironfist")."""

    def setUp(self):
        from paths import campaigns_dir
        self.camp = "review-fix-camp"
        d = campaigns_dir() / self.camp / "characters"
        if d.parent.exists():
            shutil.rmtree(d.parent)
        d.mkdir(parents=True)
        text = TEMPLATE.split("\n", 1)[1]
        text = "# Havoc Ironfist\n" + text.replace("**HP:** 14 / 17", "**HP:** 10 / 17").replace(
            "**Temp HP:** 3", "**Temp HP:** 0")
        (d / "Havoc.md").write_text(text, encoding="utf-8")
        self.path = d / "Havoc.md"
        self.p = [mock.patch.object(APP, "_active_campaign_name", lambda: self.camp),
                  mock.patch.object(APP, "_lan_token", None)]
        for x in self.p:
            x.start()
        with APP._stats_lock:
            self.saved = list(APP._current_stats.get("players", []))
            APP._current_stats["players"] = [{"name": "Havoc", "hp": {"current": 10, "max": 17}}]
        self.sent = []
        self.b = mock.patch.object(APP, "_broadcast", lambda p: self.sent.append(p))
        self.b.start()

    def tearDown(self):
        self.b.stop()
        for x in self.p:
            x.stop()
        with APP._stats_lock:
            APP._current_stats["players"] = self.saved
        shutil.rmtree(self.path.parent.parent, ignore_errors=True)


class SyncUnderLoad(Campaign):
    def test_tv_record_matches_the_file_after_concurrent_changes(self):
        for _ in range(4):
            # Back to 10 HP each round.
            text = self.path.read_text(encoding="utf-8")
            cur = sheet.parse(text)["combat"]["hp"]["current"]
            self.path.write_text(text.replace(f"**HP:** {cur} / 17", "**HP:** 10 / 17"), encoding="utf-8")
            go = threading.Barrier(2)

            def run(changes, source):
                go.wait()
                APP._apply_sheet_change("Havoc", changes, source)

            t1 = threading.Thread(target=run, args=([{"damage": 7}], "dm"))
            t2 = threading.Thread(target=run, args=([{"hp": 5}], "player"))
            t1.start(); t2.start(); t1.join(10); t2.join(10)
            file_hp = sheet.parse(self.path.read_text(encoding="utf-8"))["combat"]["hp"]["current"]
            self.assertEqual(file_hp, 8)
            with APP._stats_lock:
                tv_hp = APP._current_stats["players"][0]["hp"]["current"]
            self.assertEqual(tv_hp, file_hp)


class SheetEventNames(Campaign):
    def test_event_names_heading_and_url_name(self):
        APP._apply_sheet_change("Havoc", [{"hp": -1}], "dm")
        ev = [p["sheet"] for p in self.sent if "sheet" in p][-1]
        self.assertEqual(ev["name"], "Havoc Ironfist")
        self.assertIn("Havoc", ev["names"])


class OpReserved(Campaign):
    def test_retry_while_first_still_running_is_not_applied(self):
        client = APP.app.test_client()
        APP._sheet_op_reserve("sheet:op-busy")            # the first request is mid-flight
        r = client.post("/sheet/Havoc/change", json={"source": "player", "op": "op-busy",
                                                      "changes": [{"hp": -1}]},
                        headers={"X-DND-Device": "dev-review"})
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.get_json()["pending"])
        self.assertIn("**HP:** 10 / 17", self.path.read_text(encoding="utf-8"))


class MapNoLeak(unittest.TestCase):
    def setUp(self):
        m = json.loads(json.dumps(MAP))
        m["tokens"]["Rogue"] = {"x": 1, "y": 3, "kind": "pc", "speed": 30, "hidden": True}
        self.fake = FakeBM(m)
        self.p = mock.patch.object(map_moves, "_bm", lambda: self.fake)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_hidden_creature_answers_like_nobody(self):
        with self.assertRaises(map_moves.MoveError) as hidden:
            map_moves.check("camp", "Lurker", 2, 1)
        with self.assertRaises(map_moves.MoveError) as nobody:
            map_moves.check("camp", "Nobody", 2, 1)
        self.assertEqual(hidden.exception.status, nobody.exception.status)
        self.assertEqual(str(hidden.exception).replace("Lurker", "X"), str(nobody.exception).replace("Nobody", "X"))

    def test_a_hiding_pc_can_still_move(self):
        out = map_moves.check("camp", "Rogue", 2, 3)
        self.assertTrue(out["ok"], out)
        self.assertNotIn("Lurker", self.fake.seen_maps[-1]["tokens"])

    def test_only_party_members_may_ask(self):
        client = APP.app.test_client()
        with mock.patch.object(APP, "_lan_token", None), \
                mock.patch.object(APP, "_campaign_dir", lambda: "camp"):
            with APP._stats_lock:
                saved = list(APP._current_stats.get("players", []))
                APP._current_stats["players"] = [{"name": "Havoc"}]
            try:
                r = client.post("/map/check", json={"character": "Goblin", "x": 2, "y": 1})
                self.assertEqual(r.status_code, 403)
                r = client.post("/map/check", json={"character": "Havoc", "x": 2, "y": 2})
                self.assertEqual(r.status_code, 200)
            finally:
                with APP._stats_lock:
                    APP._current_stats["players"] = saved


if __name__ == "__main__":
    unittest.main()
