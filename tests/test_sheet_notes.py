"""Phone sheet, plan step S5: hand edits reach Claude with the next input.

AC7 — A hand edit reaches Claude with the next input as a short line with the
      before and after values. Check: unit test on the input drain.
D9  — A hand edit is applied immediately, and Claude is told. DM changes add
      no note.

Lines pinned by the plan: `[Sheet] Havoc: −7 HP (12→5)`,
`[Sheet] Mira: −1 level-2 slot (3→2 left)`, `[Sheet] Havoc: Poisoned ended`.

The edits go through the real route (POST /sheet/<name>/change) on a sheet in
a sandbox campaign; the drain is the real check_input.py / autorun_wait.py run
as subprocesses with the sandbox environment (tests/__init__.py).
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import unittest
import uuid

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
for _p in (str(DISPLAY), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MINUS, ARROW = "−", "→"
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}

SHEET = """# Havoc
**Player:** Test Player

## Combat Stats
- **HP:** 12 / 20 | **Temp HP:** 0
- **AC:** 15 | **Initiative:** +1 | **Speed:** 30 ft.
- **Hit Dice:** 3d10 (remaining: 3)
- **Death Saves:** Successes: 0 | Failures: 0
- **Inspiration:** no
- **Conditions:** Poisoned

## Spell Slots (if applicable)
| Level | Total | Used |
|-------|-------|------|
| 1st | 4 | 0 |
| 2nd | 3 | 0 |

## Equipment & Inventory
**Adventuring Gear:**
- Rope
- Potion of Healing ×2

**Currency:** 15 gp 4 sp

## Backstory & Notes
- owes Brann 5 gp
"""


def _import_app():
    spec = importlib.util.spec_from_file_location(f"dnd_display_app_notes_{uuid.uuid4().hex[:6]}",
                                                  str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(script: str, timeout: int = 60) -> str:
    r = subprocess.run([sys.executable, str(DISPLAY / script)], capture_output=True,
                       timeout=timeout, env=dict(os.environ), cwd=str(REPO))
    return r.stdout.decode("utf-8", errors="replace")


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.client = cls.app.app.test_client()
        cls.rt = pathlib.Path(os.path.dirname(cls.app.QUEUE_FILE))
        cls.qfile = pathlib.Path(cls.app.QUEUE_FILE)
        import sheet_notes
        cls.notes = sheet_notes

    def setUp(self):
        root = pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"])
        self.camp_name = f"notes-{uuid.uuid4().hex[:8]}"
        self.camp = root / "campaigns" / self.camp_name
        (self.camp / "characters").mkdir(parents=True)
        (self.camp / "state.md").write_text("# State\n\n## Session Flags\n", encoding="utf-8")
        (self.camp / "characters" / "Havoc.md").write_text(SHEET, encoding="utf-8")
        self.addCleanup(shutil.rmtree, str(self.camp), True)
        camp_file = pathlib.Path(self.app.CAMP_FILE)
        camp_file.parent.mkdir(parents=True, exist_ok=True)
        old = camp_file.read_bytes() if camp_file.exists() else None
        camp_file.write_text(self.camp_name, encoding="utf-8")
        self.addCleanup(lambda: camp_file.write_bytes(old) if old is not None else camp_file.unlink())
        old_tok = self.app._lan_token
        self.addCleanup(setattr, self.app, "_lan_token", old_tok)
        self.app._lan_token = None
        # The runtime dir is shared by the whole suite: start and end clean.
        self._wipe()
        self.addCleanup(self._wipe)

    def _wipe(self):
        self.notes.clear(self.rt)
        for n in (".input_queue", ".rewind_note", ".autorun-session"):
            try:
                (self.rt / n).unlink()
            except OSError:
                pass

    def change(self, changes, source="player", **extra):
        body = {"op": uuid.uuid4().hex, "source": source, "changes": changes, **extra}
        r = self.client.post("/sheet/Havoc/change", json=body, environ_base=LOCAL,
                             headers={"X-DND-Device": "qa-phone-1"})
        if r.status_code == 404 and not (r.is_json and r.get_json()):
            self.fail("POST /sheet/<name>/change is not routed")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(r.get_json().get("ok"), r.get_json())
        return r.get_json()

    def queue(self, text="[Havoc]: I swing at the goblin"):
        self.rt.mkdir(parents=True, exist_ok=True)
        self.qfile.write_text(text, encoding="utf-8")


class TestDrainCheckInput(_Base):
    """AC7 through check_input.py, the DM's per-turn drain."""

    def test_note_printed_before_action_once(self):
        self.change([{"hp": -7}])
        self.queue()
        out = _run("check_input.py")
        want = f"[Sheet] Havoc: {MINUS}7 HP (12{ARROW}5)"
        self.assertIn(want, out, out)
        self.assertIn("[Havoc]: I swing at the goblin", out, out)
        self.assertLess(out.index(want), out.index("[Havoc]: I swing"), out)
        self.assertEqual(out.count("[Sheet]"), 1, out)
        # Read once: the next drain has neither the note nor the action.
        self.queue("[Havoc]: I run")
        out2 = _run("check_input.py")
        self.assertNotIn("[Sheet]", out2, out2)
        self.assertNotIn("I swing", out2, out2)
        self.assertEqual(self.notes.peek(self.rt), [])

    def test_second_run_with_no_input_prints_nothing(self):
        self.change([{"hp": -7}])
        self.queue()
        _run("check_input.py")
        self.assertEqual(_run("check_input.py").strip(), "")

    def test_no_input_prints_nothing_and_keeps_the_note(self):
        self.change([{"hp": -7}])
        out = _run("check_input.py")
        self.assertEqual(out.strip(), "", "a note alone must not wake Claude")
        self.assertEqual(self.notes.peek(self.rt), [f"[Sheet] Havoc: {MINUS}7 HP (12{ARROW}5)"])


class TestDrainAutorunWait(_Base):
    """autorun_wait.py with input already queued exits at once, note first."""

    def test_note_printed_before_action(self):
        self.change([{"hp": -7}])
        self.queue()
        out = _run("autorun_wait.py", timeout=90)
        want = f"[Sheet] Havoc: {MINUS}7 HP (12{ARROW}5)"
        self.assertIn(want, out, out)
        self.assertIn("[Havoc]: I swing at the goblin", out, out)
        self.assertLess(out.index(want), out.index("[Havoc]: I swing"), out)
        self.assertEqual(self.notes.peek(self.rt), [], "the note is read once")


class TestMerging(_Base):
    def test_two_minus_one_hp_is_one_line(self):
        self.change([{"hp": -1}])
        self.change([{"hp": -1}])
        self.assertEqual(self.notes.peek(self.rt), [f"[Sheet] Havoc: {MINUS}2 HP (12{ARROW}10)"])

    def test_plus_one_then_minus_one_cancels(self):
        self.change([{"hp": 1}])
        self.change([{"hp": -1}])
        self.assertEqual(self.notes.peek(self.rt), [])

    def test_condition_added_then_removed_cancels(self):
        self.change([{"condition": "Prone", "on": True}])
        self.change([{"condition": "Prone", "on": False}])
        self.assertEqual(self.notes.peek(self.rt), [])

    def test_different_fields_stay_separate(self):
        self.change([{"hp": -1}])
        self.change([{"slot": 2, "delta": -1}])
        self.change([{"hp": -2}])
        self.assertEqual(sorted(self.notes.peek(self.rt)), sorted([
            f"[Sheet] Havoc: {MINUS}3 HP (12{ARROW}9)",
            f"[Sheet] Havoc: {MINUS}1 level-2 slot (3{ARROW}2 left)",
        ]))


class TestLineFormats(_Base):
    def only(self):
        lines = self.notes.peek(self.rt)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("[Sheet] Havoc: "), lines)
        return lines[0][len("[Sheet] Havoc: "):]

    def test_slot(self):
        self.change([{"slot": 2, "delta": -1}])
        self.assertEqual(self.only(), f"{MINUS}1 level-2 slot (3{ARROW}2 left)")

    def test_temp_hp(self):
        self.change([{"temp_hp": 5}])
        self.assertEqual(self.only(), f"+5 temp HP (0{ARROW}5)")

    def test_hit_dice(self):
        self.change([{"hit_dice": -1}])
        line = self.only()
        self.assertIn(f"{MINUS}1", line)
        self.assertIn("hit di", line)
        self.assertIn(f"3{ARROW}2", line)

    def test_condition_ended(self):
        self.change([{"condition": "Poisoned", "on": False}])
        self.assertEqual(self.only(), "Poisoned ended")

    def test_condition_added(self):
        self.change([{"condition": "Frightened", "on": True}])
        self.assertEqual(self.only(), "Frightened added")

    def test_condition_already_off_gives_no_note(self):
        self.change([{"condition": "Blinded", "on": False}])
        self.assertEqual(self.notes.peek(self.rt), [])

    def test_item(self):
        self.change([{"item": "Potion of Healing", "delta": -1}])
        line = self.only()
        self.assertIn("Potion of Healing", line)
        self.assertIn(f"{MINUS}1", line)
        self.assertIn(f"2{ARROW}1", line)

    def test_gold_one_line_per_coin(self):
        self.change([{"gold": {"gp": -5, "sp": 2}}])
        lines = sorted(self.notes.peek(self.rt))
        self.assertEqual(lines, sorted([
            f"[Sheet] Havoc: {MINUS}5 gp (15{ARROW}10)",
            f"[Sheet] Havoc: +2 sp (4{ARROW}6)",
        ]))

    def test_inspiration(self):
        self.change([{"inspiration": True}])
        self.assertIn("inspiration", self.only().lower())

    def test_death_save(self):
        self.change([{"death_saves": "failure", "delta": 1}])
        line = self.only()
        self.assertIn("+1", line)
        self.assertIn("death save", line)
        self.assertIn("fail", line)
        self.assertIn(f"0{ARROW}1", line)

    def test_note_added(self):
        self.change([{"note": "the key is under the mat", "on": True}])
        self.assertIn("the key is under the mat", self.only())

    def test_note_removed(self):
        self.change([{"note": "owes Brann 5 gp", "on": False}])
        line = self.only()
        self.assertIn("owes Brann 5 gp", line)
        self.assertIn("removed", line)


class TestNoNote(_Base):
    def test_dm_change_gives_no_note(self):
        self.change([{"hp": -7}], source="dm")
        self.assertEqual(self.notes.peek(self.rt), [])

    def test_dry_run_gives_no_note(self):
        self.change([{"hp": -7}], dry_run=True)
        self.assertEqual(self.notes.peek(self.rt), [])


class TestRewindClears(_Base):
    def test_rewind_clears_notes(self):
        rw = self.app._rewind
        self.assertIsNotNone(rw, "rewind module did not load in the app")
        rw.snapshot(self.rt, self.camp_name, self.camp, [("Havoc", "I hug the owlbear")], {})
        self.addCleanup(shutil.rmtree, str(self.rt / "rewind" / self.camp_name), True)
        self.change([{"hp": -7}])
        self.assertEqual(len(self.notes.peek(self.rt)), 1)
        r = self.client.post("/rewind", environ_base=LOCAL)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.notes.peek(self.rt), [])
        # The file was restored too: HP is back at 12.
        self.assertIn("**HP:** 12 / 20", (self.camp / "characters" / "Havoc.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
