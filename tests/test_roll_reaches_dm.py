"""A roll made on a phone always reaches the DM, so the game moves on.

WHY
===
At the table a player rolled on their phone, the roll showed on the TV, and
nothing happened: the DM never got it. Rolls only reached Claude through a
`send.py --wait` that was blocking at that moment, and that wait was easy to
lose — Claude Code stops a command after 2 minutes unless told otherwise, the
SKILL told Claude not to run the move-wait while a roll was pending, and a
roll nobody had asked for went nowhere at all.

Now the display puts every roll that no live wait will hand over into the
DM's inbox (.input_queue), the same file player moves use, so whatever the DM
runs next — autorun_wait.py, check_input.py — delivers it as a
`[Name]: rolled …` line. A live wait still gets its rolls directly, and rolls
are never delivered twice.
"""
import importlib.util
import pathlib
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
sys.path.insert(0, str(DISPLAY))


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_rolls", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RollsReachTheDm(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def setUp(self):
        self.qfile = pathlib.Path(self.app.rt("x")).parent / ".input_queue_rolls"
        self.qfile.unlink(missing_ok=True)
        self.app._queue_status.clear()
        self.app._dice_pending.clear()
        self.app._dice_done.clear()
        self.app._current_stats.clear()
        self.app._current_stats.update({"players": [{"name": "Havoc"}, {"name": "Anony"}]})
        self.sent = []
        self._p = [mock.patch.object(self.app, "QUEUE_FILE", str(self.qfile)),
                   mock.patch.object(self.app, "_broadcast", side_effect=self.sent.append)]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        self.qfile.unlink(missing_ok=True)

    # helpers
    def _inbox(self):
        return self.qfile.read_text(encoding="utf-8").splitlines() if self.qfile.exists() else []

    def _request(self, chars, dc=12, label="Stealth"):
        r = self.client.post("/dice-request", json={"characters": chars, "spec": "1d20", "modifier": 4,
                                                    "label": label, "dc": dc})
        return r.get_json()["request_id"]

    def _poll(self, rid, delivered=False):
        return self.client.get(f"/dice-request/{rid}" + ("?delivered=1" if delivered else "")).get_json()

    def _roll(self, who, rid="", face=13):
        return self.client.post("/player-input/dice", json={"character": who, "spec": "1d20", "modifier": 4,
                                                            "faces": [face], "request_id": rid,
                                                            "label": "Stealth"})

    # cases
    def test_a_roll_nobody_asked_for_reaches_the_inbox(self):
        self._roll("Havoc")
        self.assertEqual(self._inbox(), ["[Havoc]: rolled Stealth (1d20+4): 17, natural 13, physical die (not asked for)"])
        self.assertIn("Havoc", self.sent[-1]["queue_status"])

    def test_a_requested_roll_with_no_live_wait_reaches_the_inbox(self):
        rid = self._request(["Havoc"])
        self._roll("Havoc", rid, face=5)
        self.assertEqual(self._inbox(), ["[Havoc]: rolled Stealth (1d20+4): 9, natural 5, vs DC 12 - FAIL, physical die"])

    def test_a_live_wait_gets_it_directly_and_the_inbox_stays_empty(self):
        rid = self._request(["Havoc"])
        self._poll(rid)                       # send.py --wait is polling
        self._roll("Havoc", rid)
        self.assertEqual(self._inbox(), [])
        st = self._poll(rid)
        self.assertTrue(st["complete"])
        self.assertEqual(st["results"][0]["total"], 17)
        self.assertNotIn("dm_line", st["results"][0], "internal fields leaked to send.py")

    def test_a_wait_that_died_hands_over_everything_it_never_printed(self):
        rid = self._request(["Havoc", "Anony"])
        self._poll(rid)
        self._roll("Havoc", rid)              # wait alive: kept for the wait
        self.assertEqual(self._inbox(), [])
        self.app._dice_pending[rid]["polled_at"] -= 60     # the wait was killed
        self._roll("Anony", rid, face=18)
        inbox = self._inbox()
        self.assertEqual(len(inbox), 2)
        self.assertTrue(inbox[0].startswith("[Havoc]: rolled Stealth"))
        self.assertTrue(inbox[1].startswith("[Anony]: rolled Stealth (1d20+4): 22"))

    def test_rolls_a_timed_out_wait_printed_are_not_delivered_twice(self):
        rid = self._request(["Havoc", "Anony"])
        self._poll(rid)
        self._roll("Havoc", rid)
        self._poll(rid, delivered=True)       # send.py gave up and printed Havoc
        self.app._dice_pending[rid]["polled_at"] -= 60
        self._roll("Anony", rid)
        inbox = self._inbox()
        self.assertEqual(len(inbox), 1)
        self.assertTrue(inbox[0].startswith("[Anony]:"))

    def test_moves_and_rolls_share_the_inbox_without_overwriting(self):
        self._roll("Havoc")
        self.app._approved_devices.add("dev")
        self.app._expected_count = 1
        self.client.post("/player-input/stage", json={"character": "Anony", "text": "I hide."},
                         headers={"X-DND-Device": "dev"})
        inbox = self._inbox()
        self.assertEqual(len(inbox), 2, inbox)
        self.assertTrue(inbox[0].startswith("[Havoc]: rolled"))
        self.assertEqual(inbox[1], "[Anony]: I hide.")


class TheDmIsToldHowToWait(unittest.TestCase):

    def test_waits_fit_inside_claude_codes_command_limit(self):
        sys.argv = ["send.py"]
        import send
        self.assertLess(send.DEFAULT_WAIT, 600)

    def test_skill_gives_waits_the_long_timeout_and_allows_waiting_on_rolls(self):
        skill = (REPO / "skills" / "dnd" / "SKILL.md").read_text(encoding="utf-8")
        self.assertGreaterEqual(skill.count("timeout: 600000"), 2)
        self.assertIn("[Name]: rolled", skill)
        self.assertNotIn("a dice roll is pending a player's response", skill)

    def test_check_input_tells_the_screens_the_inbox_was_read(self):
        src = (DISPLAY / "check_input.py").read_text(encoding="utf-8")
        self.assertIn("/queue/consumed", src)


if __name__ == "__main__":
    unittest.main()
