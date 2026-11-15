"""The "Waiting on …" banner on the TV and the phones.

The phone used to have per-player tabs, a staged list with Ready/Skip, and a
Stage button. Now each phone has one box (Send or Talk) and every screen shows
one line saying who the table is waiting on. These tests run the real banner
code (sliced out of index.html) through node with the page state stubbed.
"""
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = ROOT / "skills" / "dnd" / "display" / "templates" / "index.html"
NODE = shutil.which("node")

_SLICE = re.compile(r"(let _waitQueued = \[\];.*?)\nfunction _renderQueueStatus\(chars\) \{", re.S)

_HARNESS = """
const el = { hidden: true, textContent: '', className: '', parentNode: null };
let PHONE = %(phone)s;
const document = {
  getElementById: id => id === 'wait-banner' ? el : null,
  body: { classList: { contains: c => c === 'input-only' ? PHONE : false }, appendChild: e => { e.parentNode = 'body'; } },
};
const _phoneChar = () => %(me)s;
let _awaitingNames = %(awaiting)s;
let _currentTurnOrder = %(turn)s;
let _playerData = %(party)s;
let _stagedData = %(staged)s;
SLICE
%(extra)s
_waitRender();
console.log(JSON.stringify({ text: el.textContent, hidden: el.hidden, cls: el.className }));
"""


@unittest.skipIf(NODE is None, "node not installed")
class WaitBanner(unittest.TestCase):

    def run_banner(self, *, phone=False, me="", awaiting=None, turn=None,
                   party=("Havoc", "Anony", "Mira"), staged=None, extra=""):
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertIsNotNone(m, "banner block not found in index.html")
        js = (_HARNESS % {
            "phone": json.dumps(phone), "me": json.dumps(me), "awaiting": json.dumps(awaiting),
            "turn": json.dumps(turn), "party": json.dumps({n: {} for n in party}),
            "staged": json.dumps(staged or {}), "extra": extra,
        }).replace("SLICE", m.group(1))
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "b.js"
            p.write_text(js, encoding="utf-8")
            out = subprocess.run([NODE, str(p)], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_whole_party_minus_who_sent(self):
        r = self.run_banner(staged={"Mira": {"text": "x", "ready": True}})
        self.assertEqual(r["text"], "Waiting on Havoc and Anony")

    def test_one_player(self):
        r = self.run_banner(turn={"current": "Anony"})
        self.assertEqual(r["text"], "Waiting on Anony's response")

    def test_dm_awaiting_list_wins_over_turn_order(self):
        r = self.run_banner(awaiting=["Havoc", "Mira"], turn={"current": "Anony"})
        self.assertEqual(r["text"], "Waiting on Havoc and Mira")

    def test_staged_but_not_sent_still_waits(self):
        r = self.run_banner(turn={"current": "Anony"}, staged={"Anony": {"text": "x", "ready": False}})
        self.assertEqual(r["text"], "Waiting on Anony's response")

    def test_phone_says_you(self):
        r = self.run_banner(phone=True, me="Anony", turn={"current": "Anony"})
        self.assertEqual(r["text"], "Waiting on your response")
        self.assertEqual(r["cls"], "you")
        r = self.run_banner(phone=True, me="anony", awaiting=["Havoc", "Anony"])
        self.assertEqual(r["text"], "Waiting on you and Havoc")

    def test_the_dm_responding(self):
        r = self.run_banner(extra="_waitQueued = ['Havoc'];")
        self.assertEqual(r["text"], "The DM is responding…")

    def test_nobody_left_hides_or_hands_to_the_dm(self):
        r = self.run_banner(party=())
        self.assertTrue(r["hidden"])
        r = self.run_banner(turn={"current": "Anony"}, staged={"Anony": {"text": "x", "ready": True}})
        self.assertEqual(r["text"], "Sent — the DM is up next")

    def test_tv_moves_the_banner_onto_the_page(self):
        """It sits in the phone panel, which the TV hides."""
        r = self.run_banner(extra="")
        self.assertFalse(r["hidden"])


class PhoneComposer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.src = HTML.read_text(encoding="utf-8")

    def test_send_not_stage(self):
        self.assertIn('<button id="stage-btn">Send</button>', self.src)

    def test_no_per_player_tabs_or_staged_list_on_the_phone(self):
        for sel in ("#char-tabs", "#staged-queue", "#waiting-indicator", "#phone-status"):
            self.assertIn(f"body.input-only {sel}", self.src)

    def test_phone_sends_as_its_own_character(self):
        self.assertIn("if (document.body.classList.contains('input-only')) return _phoneChar() || 'Everybody';", self.src)
        self.assertIn("body: JSON.stringify({ character: who, text }),", self.src)


if __name__ == "__main__":
    unittest.main()
