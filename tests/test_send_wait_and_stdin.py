"""send.py no longer hangs on an open stdin, and --dice-request --wait returns the rolls.

* `send.py --xp-award '{...}'` from a tool call read stdin, which there is a
  pipe nobody writes or closes, so it hung until killed. Award / stat-only
  sends now read stdin only when data is already there; `--no-body` and
  `--text` skip it entirely.
* `--dice-request --wait` reported only that the rolls happened. The server
  now keeps each roll on the request (and after it drains), and send.py
  prints them, or one JSON object with --json.
* `/player-input/dice` accepts `faces` — a physical die the player rolled.
"""
import importlib.util
import json
import pathlib
import subprocess
import sys
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
sys.path.insert(0, str(DISPLAY))


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_wait", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestStdinNeverBlocksAwards(unittest.TestCase):

    def _run_with_open_stdin(self, *args, timeout=25):
        p = subprocess.Popen([sys.executable, str(DISPLAY / "send.py"), *args],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, encoding="utf-8")
        # stdin stays open and unwritten, like a tool call's
        t0 = time.time()
        while p.poll() is None and time.time() - t0 < timeout:
            time.sleep(0.05)
        hung = p.poll() is None
        if hung:
            p.kill()
        p.stdin.close()
        out, err = p.communicate()
        return hung, p.returncode, err

    def test_xp_award_with_open_pipe_returns(self):
        hung, rc, err = self._run_with_open_stdin(
            "--xp-award", '{"names":["Havoc"],"xp":50,"reason":"rats"}')
        self.assertFalse(hung, "send.py --xp-award blocked on an open stdin pipe")

    def test_no_body_flag_returns(self):
        hung, rc, err = self._run_with_open_stdin("--no-body", "--stat-hp", "Havoc:5:12")
        self.assertFalse(hung)

    def test_text_flag_replaces_stdin(self):
        hung, rc, err = self._run_with_open_stdin("--npc", "Mira", "--text", "Hello there.")
        self.assertFalse(hung, "--text should not read stdin even with a content flag")

    def test_piped_body_with_award_still_read(self):
        sys.path.insert(0, str(DISPLAY))
        r = subprocess.run([sys.executable, "-c",
                            "import sys; sys.argv=['send.py']; import runpy; "
                            "sys.path.insert(0, r'%s'); import send; "
                            "print(send._stdin_has_data())" % DISPLAY],
                           input="Narration body\n", capture_output=True, encoding="utf-8")
        self.assertEqual(r.stdout.strip().splitlines()[-1], "True", r.stderr)


class TestDiceRequestResults(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.mod = _import_app()
        cls.mod._token_ok = lambda: True
        cls.mod._phone_present = lambda c: True
        cls.client = cls.mod.app.test_client()

    def _request(self, chars, dc=12, adv="normal"):
        r = self.client.post("/dice-request", json={
            "characters": chars, "spec": "1d20", "modifier": 3, "dc": dc,
            "advantage": adv, "label": "Stealth"})
        return r.get_json()["request_id"]

    def test_results_kept_after_request_drains(self):
        rid = self._request(["Havoc", "Anony"])
        self.client.post("/player-input/dice", json={
            "character": "Havoc", "spec": "1d20", "modifier": 3, "request_id": rid})
        st = self.client.get(f"/dice-request/{rid}").get_json()
        self.assertFalse(st["complete"])
        self.assertEqual([r["character"] for r in st["results"]], ["Havoc"])
        self.client.post("/player-input/dice", json={
            "character": "anony", "spec": "1d20", "modifier": 3, "request_id": rid})
        st = self.client.get(f"/dice-request/{rid}").get_json()
        self.assertTrue(st["complete"])
        self.assertEqual(sorted(r["character"] for r in st["results"]), ["Anony", "Havoc"])
        for r in st["results"]:
            self.assertEqual(r["total"], r["natural"] + 3)
            self.assertEqual(r["success"], r["total"] >= 12)

    def test_physical_face_adds_modifier(self):
        rid = self._request(["Havoc"], dc=15)
        r = self.client.post("/player-input/dice", json={
            "character": "Havoc", "spec": "1d20", "modifier": 3, "request_id": rid,
            "faces": [14]}).get_json()
        self.assertEqual(r["total"], 17)
        self.assertTrue(r["physical"])
        self.assertIn("rolled at the table", r["text"])
        st = self.client.get(f"/dice-request/{rid}").get_json()
        self.assertTrue(st["results"][0]["physical"])
        self.assertTrue(st["results"][0]["success"])

    def test_physical_advantage_two_faces(self):
        r = self.client.post("/player-input/dice", json={
            "character": "Havoc", "spec": "1d20", "modifier": 0,
            "advantage": "advantage", "faces": [4, 16]}).get_json()
        self.assertEqual(r["total"], 16)
        self.assertEqual(r["both"], [4, 16])

    def test_physical_face_out_of_range_rejected(self):
        r = self.client.post("/player-input/dice", json={
            "character": "Havoc", "spec": "1d20", "faces": [21]})
        self.assertEqual(r.status_code, 400)


class TestPrintResults(unittest.TestCase):

    def test_text_and_json(self):
        import io
        import contextlib
        sys.argv = ["send.py"]
        import send
        st = {"dc": 12, "label": "Stealth", "results": [
            {"character": "Havoc", "total": 17, "natural": 14, "modifier": 3,
             "physical": True, "success": True}]}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            send._print_dice_results("abc", st, False)
        self.assertIn("Havoc: 17 (natural 14, +3) [physical die] vs DC 12 — PASS", buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            send._print_dice_results("abc", st, True)
        self.assertEqual(json.loads(buf.getvalue())["results"][0]["total"], 17)


if __name__ == "__main__":
    unittest.main()
