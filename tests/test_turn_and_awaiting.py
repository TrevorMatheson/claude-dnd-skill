"""One-call DM turns (send.py --turn) and the one-player Send.

* A turn was 4–6 sends. `send.py --turn` takes the whole turn as JSON and
  sends blocks in order, then stats / turn pointer / awaiting, then an
  optional dice request.
* The server waited for the whole party even when one player was asked to
  act. With `awaiting` (or a turn pointer at a PC) that player's Send alone
  completes the turn, and other players' staged text stays staged.

send.py's posts are routed into the Flask test client, so these tests run
the real server code end to end without a network.
"""
import importlib.util
import json
import pathlib
import sys
import unittest
import urllib.parse
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
sys.path.insert(0, str(DISPLAY))


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_turn", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.argv = ["send.py"]
import send  # noqa: E402


class _Base(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def setUp(self):
        self.app._current_stats.clear()
        self.app._current_stats.update({"players": [{"name": "Havoc"}, {"name": "Anony"}, {"name": "Mira"}]})
        self.app._expected_count = 3
        self.app._awaiting = None
        self.app._autorun_threshold = None
        self.app._staged.clear()
        self.app._approved_devices.add("test-device")
        self.broadcasts = []
        self._bpatch = mock.patch.object(self.app, "_broadcast", side_effect=self.broadcasts.append)
        self._bpatch.start()
        self.queue_writes = []
        send._SEND_LOG.clear()

    def tearDown(self):
        self._bpatch.stop()

    def _fake_post(self, url, data, token):
        path = urllib.parse.urlparse(url).path
        r = self.client.post(path, data=data, content_type="application/json")
        send._SEND_LOG.append({"endpoint": path, "ok": r.status_code < 300})
        return r.status_code < 300

    def _stage(self, who, text="I act."):
        return self.client.post("/player-input/stage", json={"character": who, "text": text},
                                headers={"X-DND-Device": "test-device"})


class TestSendTurn(_Base):

    def test_blocks_in_order_then_stats(self):
        turn = {
            "blocks": [
                {"action": "Havoc", "text": "Havoc swings."},
                {"type": "dice", "text": "Havoc: 21 vs AC 12 — HIT"},
                {"text": "The blade bites deep."},
                {"type": "npc", "name": "Mira", "text": "Behind you!", "voice": "Kore"},
                {"tutor": True, "text": "Advantage: keep the higher d20."},
            ],
            "stats": {"players": [{"name": "Havoc", "hp": {"current": 9, "max": 12}}]},
            "turn": "Anony", "round": 2, "awaiting": ["Anony"],
        }
        with mock.patch.object(send, "_post", side_effect=self._fake_post) as p:
            self.assertEqual(send.send_turn(turn), 0)
        calls = [(urllib.parse.urlparse(c.args[0]).path, json.loads(c.args[1])) for c in p.call_args_list]
        self.assertEqual([c[0] for c in calls], ["/chunk"] * 5 + ["/stats"])
        self.assertEqual(calls[0][1]["action"], "Havoc")
        self.assertTrue(calls[1][1]["dice"])
        self.assertNotIn("npc", calls[2][1])
        self.assertEqual(calls[3][1]["npc"], "Mira")
        self.assertEqual(calls[3][1]["voice"], "Kore")
        stats = calls[5][1]
        self.assertEqual(stats["turn_order"], {"current": "Anony", "round": 2})
        self.assertEqual(stats["awaiting"], ["Anony"])
        self.assertEqual(self.app._awaiting, ["Anony"])

    def test_bad_block_sends_nothing(self):
        with mock.patch.object(send, "_post") as p:
            rc = send.send_turn({"blocks": [{"text": "ok"}, {"type": "npc", "text": "no name"}]})
        self.assertEqual(rc, 2)
        p.assert_not_called()

    def test_unknown_key_rejected(self):
        with mock.patch.object(send, "_post") as p:
            self.assertEqual(send.send_turn({"blocks": [], "narration": "x"}), 2)
        p.assert_not_called()

    def test_two_kinds_rejected(self):
        with mock.patch.object(send, "_post"):
            self.assertEqual(send.send_turn({"blocks": [{"npc": "Mira", "dice": True, "text": "x"}]}), 2)

    def test_xp_block(self):
        with mock.patch.object(send, "_post", side_effect=self._fake_post) as p:
            send.send_turn({"blocks": [{"xp_award": {"names": ["Havoc"], "xp": 25}}]})
        body = json.loads(p.call_args_list[0].args[1])
        self.assertEqual(body["xp_award"]["summary"], "Havoc — 25 XP")

    def test_dice_request_last(self):
        order = []
        with mock.patch.object(send, "_post", side_effect=lambda *a: order.append("post") or True), \
                mock.patch.object(send, "dice_request", side_effect=lambda *a, **k: order.append("dice") or 0) as dr:
            send.send_turn({"blocks": [{"text": "Roll!"}],
                            "dice_request": {"character": "Anony", "dc": 13, "wait": True}})
        self.assertEqual(order, ["post", "dice"])
        self.assertEqual(dr.call_args.args[0], ["Anony"])
        self.assertTrue(dr.call_args.kwargs["wait"])


class TestAwaiting(_Base):

    def setUp(self):
        super().setUp()
        self._qpatch = mock.patch.object(self.app, "QUEUE_FILE",
                                         str(pathlib.Path(self.app.rt("x")).parent / ".input_queue_test"))
        self._qpatch.start()

    def tearDown(self):
        self._qpatch.stop()
        super().tearDown()

    def _queued(self):
        return [b["queue_status"] for b in self.broadcasts if "queue_status" in b]

    def test_without_awaiting_one_send_waits_for_party(self):
        self._stage("Havoc")
        self.client.post("/player-input/ready", json={"character": "Havoc"},
                         headers={"X-DND-Device": "test-device"})
        self.assertEqual(self._queued(), [])

    def test_awaiting_one_player_send_completes_turn(self):
        self.client.post("/stats", json={"awaiting": ["Havoc"]})
        self._stage("Havoc", "I hit it.")
        self.assertEqual(self._queued(), [["Havoc"]])

    def test_turn_pointer_at_pc_is_awaited(self):
        self.client.post("/stats", json={"turn_order": {"current": "Anony", "round": 1}})
        self._stage("Anony")
        self.assertEqual(self._queued(), [["Anony"]])

    def test_turn_pointer_at_monster_waits_for_party(self):
        self.client.post("/stats", json={"turn_order": {"current": "Giant Rat", "round": 1}})
        self._stage("Anony")
        self.assertEqual(self._queued(), [])

    def test_other_players_staged_text_stays(self):
        self._stage("Mira", "I keep watch.")           # not ready — party of three
        self.client.post("/stats", json={"awaiting": "Havoc"})
        self._stage("Havoc")
        self.assertEqual(self._queued(), [["Havoc"]])
        self.assertIn("Mira", self.app._staged)

    def test_party_clears_awaiting(self):
        self.client.post("/stats", json={"awaiting": ["Havoc"]})
        self.client.post("/stats", json={"awaiting": "party"})
        self.assertIsNone(self.app._awaiting)


if __name__ == "__main__":
    unittest.main()
