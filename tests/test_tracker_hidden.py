"""AC9 / D10: a hidden battle-map token never reaches a screen — not even
through tracker.py's condition push, which sends an entity's conditions to
the display as a party row (QA verify S4 found the goblin's name leaking)."""
import contextlib
import io
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import battlemap as bm  # noqa: E402
import paths  # noqa: E402
import tracker  # noqa: E402


class TestHiddenConditionPush(unittest.TestCase):
    CAMP = "bm-tracker-hidden"

    def setUp(self):
        self.camp = paths.campaigns_dir() / self.CAMP
        self.camp.mkdir(parents=True, exist_ok=True)
        m = bm.new_map(8, 4)
        bm.place(m, "Havoc", 1, 1, kind="pc")
        bm.place(m, "Goblin", 2, 1, hidden=True)
        bm.place(m, "Orc", 4, 1)
        bm.save(self.camp, m)
        for f in ("tracker.json",):
            (self.camp / f).unlink(missing_ok=True)

    def _pushed(self, name, cond):
        calls = []
        with mock.patch.object(subprocess, "run", side_effect=lambda *a, **k: calls.append(a[0])):
            with mock.patch.object(tracker, "_CAMPAIGN", self.CAMP, create=True),                     contextlib.redirect_stdout(io.StringIO()):
                tracker.cmd_condition(self.CAMP, name, "add", cond)
        return [c for c in calls if "--player" in c]

    def test_hidden_token_conditions_not_pushed(self):
        self.assertEqual(self._pushed("Goblin", "invisible"), [])

    def test_visible_token_conditions_still_pushed(self):
        self.assertTrue(self._pushed("Orc", "prone"))

    def test_revealed_token_pushed_again(self):
        m = bm.load(self.camp)
        bm.set_hidden(m, "Goblin", False)
        bm.save(self.camp, m)
        self.assertTrue(self._pushed("Goblin", "prone"))

    def test_no_map_still_pushes(self):
        bm.map_path(self.camp).unlink()
        self.assertTrue(self._pushed("Havoc", "prone"))


class TestPortraitPath(unittest.TestCase):
    """QA verify S4: an absolute portrait path is saved but can never load."""

    def test_absolute_portrait_refused(self):
        m = bm.new_map(4, 4)
        with self.assertRaises(bm.MapError):
            bm.place(m, "Goblin", 1, 1, portrait="/abs/x.png")
        bm.place(m, "Goblin", 1, 1, portrait="maps/tokens/x.png")


if __name__ == "__main__":
    unittest.main()
