"""The Play tab (phone sheet spec, plan step S9).

AC1 — device approval and the token work on /phone (the stage/dice routes the
      Play tab calls keep their 202/403 answers).
AC3 — a roll request jumps the phone to Play from any tab (pure part:
      requestIsMine / stillOpen; the tab switch itself is a browser check).
AC9 — physical-dice entry (faceCount) and answering a request (`asked`).
D3, D11, D14 — Send is one request; a repeated `op` is applied once.

Server contract (dnd-display-app.py):
  POST /player-input/stage  optional  ready:true  staged *and* ready in one call
                                      append:true joined to the still-staged
                                                  text with " then " (413 > 500)
                                      op          a repeat is not staged twice
  POST /player-input/dice   optional  op          a repeat returns the first
                                                  result: no second roll or log
                            response  asked       true when it answered a request
Old-page bodies (none of these fields) behave as before.

Front-end contract (static/phone/play.js, module.exports):
  requestIsMine(req, me), stillOpen(req, pending, me), faceCount(spec, adv)
"""
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "skills" / "dnd" / "display"
PLAY_JS = DISPLAY / "static" / "phone" / "play.js"
PHONE_HTML = DISPLAY / "templates" / "phone.html"
NODE = shutil.which("node")
sys.path.insert(0, str(DISPLAY))

DEV = {"X-DND-Device": "test-device"}


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_play", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Flask(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def setUp(self):
        a = self.app
        a._current_stats.clear()
        a._current_stats.update({"players": [{"name": "Havoc"}, {"name": "Anony"}]})
        a._expected_count = 2
        a._awaiting = None
        a._autorun_threshold = None
        a._staged.clear()
        a._queue_status.clear()
        a._dice_pending.clear()
        a._dice_done.clear()
        with a._sheet_ops_lock:
            a._sheet_ops.clear()
        a._approved_devices.add("test-device")
        self.qfile = pathlib.Path(a.rt("x")).parent / ".input_queue_play"
        self.qfile.unlink(missing_ok=True)
        self.sent = []
        self._p = [
            mock.patch.object(a, "QUEUE_FILE", str(self.qfile)),
            mock.patch.object(a, "_broadcast", side_effect=self.sent.append),
            mock.patch.object(a, "_rate_ok", lambda *x, **k: True),
            mock.patch.object(a, "_read_actions_aloud", lambda *x, **k: None),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        self.qfile.unlink(missing_ok=True)

    def inbox(self):
        return self.qfile.read_text(encoding="utf-8").splitlines() if self.qfile.exists() else []

    def stage(self, who, text, **extra):
        return self.client.post("/player-input/stage", json={"character": who, "text": text, **extra}, headers=DEV)

    def dice(self, **body):
        body.setdefault("character", "Havoc")
        body.setdefault("spec", "1d20")
        return self.client.post("/player-input/dice", json=body, headers=DEV)


class StageReady(_Flask):

    def test_ready_true_marks_ready_in_one_call(self):
        r = self.stage("Havoc", "I draw my axe", ready=True)
        self.assertEqual(r.status_code, 204)
        self.assertTrue(self.app._staged["Havoc"]["ready"], "ready:true must stage the action as ready")

    def test_ready_true_reaches_the_queue_in_one_call_each(self):
        # 2-player party, nothing awaited: one call per player, no /ready.
        self.assertEqual(self.stage("Havoc", "I draw my axe", ready=True).status_code, 204)
        self.assertEqual(self.inbox(), [], "one of two players must not fire the turn")
        self.assertEqual(self.stage("Anony", "I hide", ready=True).status_code, 204)
        self.assertEqual(self.inbox(), ["[Havoc]: I draw my axe", "[Anony]: I hide"])
        self.assertEqual(self.app._staged, {})
        self.assertEqual(self.app._queue_status, ["Havoc", "Anony"])

    def test_ready_true_with_threshold_one_queues_at_once(self):
        self.app._autorun_threshold = 1
        self.stage("Havoc", "I draw my axe", ready=True)
        self.assertEqual(self.inbox(), ["[Havoc]: I draw my axe"])

    def test_ready_true_same_as_stage_then_ready(self):
        # The old two-request path, for comparison: identical inbox.
        self.stage("Havoc", "I draw my axe")
        self.client.post("/player-input/ready", json={"character": "Havoc", "ready": True}, headers=DEV)
        self.stage("Anony", "I hide")
        self.client.post("/player-input/ready", json={"character": "Anony", "ready": True}, headers=DEV)
        old = self.inbox()
        self.qfile.unlink(missing_ok=True)
        self.app._queue_status.clear()
        self.stage("Havoc", "I draw my axe", ready=True)
        self.stage("Anony", "I hide", ready=True)
        self.assertEqual(self.inbox(), old)

    def test_pending_device_gets_202_and_nothing_staged(self):
        # AC1: approval still gates the one-call Send.
        with mock.patch.object(self.app, "_device_ok", lambda *a: "pending"):
            r = self.stage("Havoc", "I act", ready=True, op="ph-a-1")
        self.assertEqual(r.status_code, 202)
        self.assertEqual(self.app._staged, {})
        # Once approved, the same op must still go through (it was never applied).
        r = self.stage("Havoc", "I act", ready=True, op="ph-a-1")
        self.assertEqual(r.status_code, 204)
        self.assertIn("Havoc", self.app._staged)

    def test_denied_device_gets_403(self):
        with mock.patch.object(self.app, "_device_ok", lambda *a: "denied"):
            r = self.stage("Havoc", "I act", ready=True)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.app._staged, {})


class StageAppend(_Flask):

    def test_append_joins_with_then(self):
        self.stage("Havoc", "I draw my axe", append=True)
        self.stage("Havoc", "I charge", append=True)
        self.assertEqual(self.app._staged["Havoc"]["text"], "I draw my axe then I charge")

    def test_append_with_nothing_staged_is_plain(self):
        self.stage("Havoc", "I charge", append=True)
        self.assertEqual(self.app._staged["Havoc"]["text"], "I charge")

    def test_append_only_joins_own_text(self):
        self.stage("Anony", "I hide", append=True)
        self.stage("Havoc", "I charge", append=True)
        self.assertEqual(self.app._staged["Havoc"]["text"], "I charge")
        self.assertEqual(self.app._staged["Anony"]["text"], "I hide")

    def test_append_past_500_chars_is_413_and_keeps_the_old_text(self):
        self.stage("Havoc", "a" * 300, append=True)
        r = self.stage("Havoc", "b" * 250, append=True)
        self.assertEqual(r.status_code, 413)
        self.assertEqual(self.app._staged["Havoc"]["text"], "a" * 300)

    def test_append_and_ready_join_then_queue(self):
        self.stage("Havoc", "I draw my axe", append=True)
        self.stage("Havoc", "I charge", append=True, ready=True)
        self.stage("Anony", "I hide", append=True, ready=True)
        self.assertEqual(self.inbox(), ["[Havoc]: I draw my axe then I charge", "[Anony]: I hide"])


class StageOp(_Flask):

    def test_repeated_op_not_staged_twice(self):
        self.stage("Havoc", "I charge", append=True, op="ph-action-1")
        r = self.stage("Havoc", "I charge", append=True, op="ph-action-1")
        self.assertEqual(r.status_code, 204, "a repeat answers like the first")
        self.assertEqual(self.app._staged["Havoc"]["text"], "I charge")

    def test_repeated_op_after_queue_does_not_queue_twice(self):
        self.app._autorun_threshold = 1
        self.stage("Havoc", "I charge", ready=True, append=True, op="ph-action-2")
        self.stage("Havoc", "I charge", ready=True, append=True, op="ph-action-2")
        self.assertEqual(self.inbox(), ["[Havoc]: I charge"])
        self.assertEqual(self.app._staged, {})

    def test_different_ops_both_apply(self):
        self.stage("Havoc", "I charge", append=True, op="ph-action-3")
        self.stage("Havoc", "I charge", append=True, op="ph-action-4")
        self.assertEqual(self.app._staged["Havoc"]["text"], "I charge then I charge")

    def test_failed_op_is_not_remembered(self):
        # A 413 did not apply, so the same op can go again later (after a Send).
        self.stage("Havoc", "a" * 300, append=True)
        self.assertEqual(self.stage("Havoc", "b" * 250, append=True, op="ph-action-5").status_code, 413)
        self.app._staged.clear()
        self.stage("Havoc", "b" * 250, append=True, op="ph-action-5")
        self.assertEqual(self.app._staged["Havoc"]["text"], "b" * 250)


class StageOldBodies(_Flask):

    def test_old_body_replaces_and_is_not_ready(self):
        self.stage("Havoc", "I draw my axe")
        self.stage("Havoc", "I charge")
        self.assertEqual(self.app._staged["Havoc"], {**self.app._staged["Havoc"], "text": "I charge", "ready": False})
        self.assertEqual(self.inbox(), [])

    def test_old_body_solo_still_fires(self):
        self.app._expected_count = 1
        self.app._current_stats["players"] = [{"name": "Havoc"}]
        self.stage("Havoc", "I charge")
        self.assertEqual(self.inbox(), ["[Havoc]: I charge"])

    def test_old_body_awaited_player_still_fires_alone(self):
        self.app._awaiting = ["Havoc"]
        self.stage("Havoc", "I charge")
        self.assertEqual(self.inbox(), ["[Havoc]: I charge"])


class DiceOp(_Flask):

    def log_len(self):
        return len(self.app._text_log)

    def test_repeated_op_one_roll_one_log_entry_same_total(self):
        before = self.log_len()
        a = self.dice(modifier=3, label="Athletics", op="ph-roll-1")
        b = self.dice(modifier=3, label="Athletics", op="ph-roll-1")
        self.assertEqual(a.status_code, 200)
        self.assertEqual(b.status_code, 200)
        ja, jb = a.get_json(), b.get_json()
        self.assertEqual(ja["total"], jb["total"])
        self.assertEqual(ja["rolls"], jb["rolls"])
        self.assertEqual(ja["text"], jb["text"])
        self.assertEqual(self.log_len() - before, 1, "a Retry must not log a second roll")
        self.assertEqual(len(self.inbox()), 1, "a Retry must not reach the DM twice")
        self.assertEqual(sum(1 for p in self.sent if p.get("dice")), 1, "the TV must show one roll")

    def test_repeated_op_answering_a_request_answers_once(self):
        rid = self.client.post("/dice-request", json={"characters": ["Havoc", "Anony"], "spec": "1d20",
                                                       "label": "Stealth", "dc": 12}).get_json()["request_id"]
        a = self.dice(faces=[14], request_id=rid, op="ph-roll-2").get_json()
        b = self.dice(faces=[14], request_id=rid, op="ph-roll-2").get_json()
        self.assertTrue(a["asked"])
        self.assertEqual(a, b)
        self.assertEqual(len(self.app._dice_pending[rid]["results"]), 1)
        self.assertEqual(self.app._dice_pending[rid]["chars"], {"Anony"})

    def test_different_ops_roll_twice(self):
        before = self.log_len()
        self.dice(op="ph-roll-3")
        self.dice(op="ph-roll-4")
        self.assertEqual(self.log_len() - before, 2)

    def test_asked_true_only_when_it_answers_a_request(self):
        rid = self.client.post("/dice-request", json={"characters": ["Havoc"], "spec": "1d20"}).get_json()["request_id"]
        self.assertIs(self.dice(faces=[9], request_id=rid).get_json()["asked"], True)
        self.assertIs(self.dice(faces=[9]).get_json()["asked"], False)
        self.assertIs(self.dice(faces=[9], request_id="nope").get_json()["asked"], False)

    def test_old_body_without_op_rolls_every_time(self):
        before = self.log_len()
        r1 = self.dice(faces=[5])
        r2 = self.dice(faces=[5])
        self.assertEqual(self.log_len() - before, 2)
        for key in ("character", "spec", "modifier", "advantage", "rolls", "kept", "both",
                    "subtotal", "total", "text", "physical", "request_id"):
            self.assertIn(key, r1.get_json())
        self.assertEqual(r2.get_json()["total"], 5)
        self.assertEqual(len(self.inbox()), 2)

    def test_physical_advantage_two_faces(self):
        # AC9 physical entry, server half: two d20 faces with advantage keep the higher.
        j = self.dice(faces=[4, 17], advantage="advantage", modifier=2).get_json()
        self.assertEqual(j["both"], [4, 17])
        self.assertEqual(j["total"], 19)
        self.assertTrue(j["physical"])


# ── node: pure helpers in play.js ─────────────────────────────────────────

@unittest.skipIf(NODE is None, "node not installed")
class PlayHelpers(unittest.TestCase):

    def call(self, fn, *args):
        self.assertTrue(PLAY_JS.exists(), "static/phone/play.js is missing")
        js = (f"const p = require({json.dumps(str(PLAY_JS))});"
              f"console.log(JSON.stringify(p[{json.dumps(fn)}](...{json.dumps(list(args))})));")
        r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    # The payload shape /dice-request broadcasts.
    @staticmethod
    def req(chars, rid="r1"):
        return {"request_id": rid, "characters": chars,
                "character": chars[0] if len(chars) == 1 else "any", "spec": "1d20"}

    def test_request_is_mine_named(self):
        self.assertTrue(self.call("requestIsMine", self.req(["Havoc"]), "Havoc"))
        self.assertTrue(self.call("requestIsMine", self.req(["havoc"]), "HAVOC"))

    def test_request_is_mine_any(self):
        self.assertTrue(self.call("requestIsMine", self.req(["any"]), "Havoc"))

    def test_request_for_someone_else(self):
        self.assertFalse(self.call("requestIsMine", self.req(["Mira"]), "Havoc"))

    def test_request_characters_list(self):
        self.assertTrue(self.call("requestIsMine", self.req(["Mira", "Havoc"]), "Havoc"))
        self.assertFalse(self.call("requestIsMine", self.req(["Mira", "Anony"]), "Havoc"))

    def test_legacy_single_character_field(self):
        self.assertTrue(self.call("requestIsMine", {"request_id": "r", "character": "Havoc"}, "Havoc"))
        self.assertFalse(self.call("requestIsMine", {"request_id": "r", "character": "Mira"}, "Havoc"))

    def test_no_character_bound_is_never_mine(self):
        self.assertFalse(self.call("requestIsMine", self.req(["any"]), ""))

    # stillOpen against the server's dice_pending snapshot shape:
    # [{request_id, pending: [names], label}]
    def test_still_open_while_i_am_pending(self):
        snap = [{"request_id": "r1", "pending": ["Anony", "Havoc"], "label": "Stealth"}]
        self.assertTrue(self.call("stillOpen", self.req(["Havoc", "Anony"]), snap, "havoc"))

    def test_closed_once_i_have_rolled(self):
        snap = [{"request_id": "r1", "pending": ["Anony"], "label": "Stealth"}]
        self.assertFalse(self.call("stillOpen", self.req(["Havoc", "Anony"]), snap, "Havoc"))

    def test_closed_when_request_finished(self):
        self.assertFalse(self.call("stillOpen", self.req(["Havoc"]), [], "Havoc"))

    def test_other_requests_do_not_count(self):
        snap = [{"request_id": "r2", "pending": ["Havoc"], "label": "Perception"}]
        self.assertFalse(self.call("stillOpen", self.req(["Havoc"], rid="r1"), snap, "Havoc"))

    def test_any_request_not_closed_by_an_unrelated_snapshot(self):
        # The server never tracks an "any" request in dice_pending (it is
        # fire-and-forget), so a dice_pending broadcast for some other request
        # says nothing about it. Closing it would wipe the roll card the
        # moment the DM asks anyone else for a roll.
        snap = [{"request_id": "r9", "pending": ["Anony"], "label": "Perception"}]
        self.assertTrue(self.call("stillOpen", self.req(["any"]), snap, "Havoc"))
        self.assertTrue(self.call("stillOpen", self.req(["any"]), [], "Havoc"))

    def test_face_count(self):
        self.assertEqual(self.call("faceCount", "1d20", "advantage"), 2)
        self.assertEqual(self.call("faceCount", "1d20", "disadvantage"), 2)
        self.assertEqual(self.call("faceCount", "1d20", "normal"), 1)
        self.assertEqual(self.call("faceCount", "3d6", "normal"), 3)
        self.assertEqual(self.call("faceCount", "3d6", "advantage"), 3)
        self.assertEqual(self.call("faceCount", "2d8"), 2)


class PhoneHtmlLoadsPlay(unittest.TestCase):

    def test_net_and_play_load_before_phone_js(self):
        html = PHONE_HTML.read_text(encoding="utf-8")
        srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)
        for s in ("/static/phone/net.js", "/static/phone/play.js", "/static/phone/phone.js"):
            self.assertIn(s, srcs)
        i = srcs.index
        self.assertLess(i("/static/phone/net.js"), i("/static/phone/phone.js"))
        self.assertLess(i("/static/phone/play.js"), i("/static/phone/phone.js"))
        self.assertLess(i("/static/phone/net.js"), i("/static/phone/play.js"))
        # play.js uses TV.panels.makeWaitModel at start(), so panels.js comes first.
        self.assertLess(i("/static/tv/panels.js"), i("/static/phone/play.js"))

    def test_play_tab_has_the_slots_play_js_fills(self):
        html = PHONE_HTML.read_text(encoding="utf-8")
        for el_id in ("wait-banner", "roll-card", "play-body", "talk-slot", "unsent"):
            self.assertRegex(html, rf'id="{el_id}"', el_id)


if __name__ == "__main__":
    unittest.main()
