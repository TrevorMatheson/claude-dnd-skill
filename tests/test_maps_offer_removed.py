"""The DM no longer offers D&D Beyond Maps (battle-map spec D3, AC13).

The TV battle map replaces the D&D Beyond Maps VTT. The dnd skill must stop
offering it, but the sibling `maps` skill stays in the repo untouched.
"""
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DND_SKILL = ROOT / "skills" / "dnd" / "SKILL.md"
MAPS_DIR = ROOT / "skills" / "maps"
GIT = shutil.which("git")
# Branches the battle-map work was cut from; the first one found is used.
BASE_REFS = ("feat/display-foundation", "origin/feat/display-foundation")


def _git(*args):
    return subprocess.run([GIT, *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


class DndSkillDoesNotOfferMaps(unittest.TestCase):
    """AC13: skills/dnd/SKILL.md has no D&D Beyond Maps offer."""

    @classmethod
    def setUpClass(cls):
        cls.text = DND_SKILL.read_text(encoding="utf-8")

    def test_no_dm_maps_command(self):
        self.assertFalse("/dm:maps" in self.text, "SKILL.md still offers /dm:maps")

    def test_no_vtt_md_trigger(self):
        self.assertFalse("vtt.md" in self.text, "SKILL.md still keys an offer on vtt.md")

    def test_no_dnd_beyond_maps_mention(self):
        self.assertIsNone(re.search(r"D&D Beyond Maps", self.text, re.I))

    def test_no_vtt_section_heading(self):
        self.assertIsNone(re.search(r"^#+ .*\bVTT\b", self.text, re.M))

    def test_no_reference_to_sibling_maps_skill(self):
        # e.g. "the sibling `maps` skill", "maps skill §6b"
        self.assertIsNone(re.search(r"`?\bmaps`? skill", self.text, re.I))

    def test_section_removed_cleanly(self):
        # Deleting the section must not leave two separators back to back.
        self.assertIsNone(re.search(r"^---\s*\n\s*---\s*$", self.text, re.M))

    def test_battle_map_section_still_present(self):
        self.assertIn("## Battle map on the TV", self.text)


class MapsSkillKept(unittest.TestCase):
    """AC13 / D3: the maps skill files are unchanged."""

    def test_maps_skill_file_exists(self):
        self.assertTrue((MAPS_DIR / "SKILL.md").is_file())

    def test_maps_skill_unchanged_since_branch_point(self):
        if not GIT or _git("rev-parse", "--git-dir").returncode != 0:
            self.skipTest("git not available")
        base = None
        for ref in BASE_REFS:
            mb = _git("merge-base", "HEAD", ref)
            if mb.returncode == 0 and mb.stdout.strip():
                base = mb.stdout.strip()
                break
        if base is None:
            self.skipTest("no feat/display-foundation ref to diff against")
        # Committed and uncommitted changes since the branch point.
        diff = _git("diff", "--stat", base, "--", "skills/maps")
        self.assertEqual(diff.returncode, 0, diff.stderr)
        self.assertEqual(diff.stdout.strip(), "",
                         "skills/maps changed since %s:\n%s" % (base[:8], diff.stdout))
        untracked = _git("ls-files", "--others", "--exclude-standard", "--", "skills/maps")
        self.assertEqual(untracked.stdout.strip(), "",
                         "new files under skills/maps:\n" + untracked.stdout)


if __name__ == "__main__":
    unittest.main()
