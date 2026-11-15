"""The story's tone is a darkly funny, PG-13 dungeon-crawl; the narrator stays a normal DM.

WHY
===
The table asked for a story tone like Dungeon Crawler Carl, kept to PG-13 —
the world, situations and NPCs, not a snarky announcer narrating over the top.
The tone lives in SKILL.md under "Story Tone"; a campaign can turn it off with
`story_tone: classic`. These checks keep the pieces that make it work.
"""
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL_DIR = REPO / "skills" / "dnd"


def _read(rel):
    return (SKILL_DIR / rel).read_text(encoding="utf-8")


class StoryTone(unittest.TestCase):

    def setUp(self):
        self.skill = _read("SKILL.md")
        start = self.skill.index("## Story Tone")
        self.section = self.skill[start:self.skill.index("## Table Dials", start)]

    def test_it_is_the_default_and_can_be_switched_off(self):
        self.assertIn("story_tone: crawler", self.section)
        self.assertIn("story_tone: classic", self.section)
        self.assertIn("story_tone: crawler|classic", _read("templates/state.md"))
        self.assertIn("dungeon-crawl comedy (default)", _read("SKILL-commands.md"))

    def test_it_is_the_story_not_a_narrator_persona(self):
        self.assertIn("**not** about the narrator", self.section)
        self.assertIn("no announcer persona", self.section)
        self.assertNotIn("Achievement unlocked", self.skill)
        self.assertNotIn("narrator: crawler", self.skill)

    def test_it_keeps_to_pg13(self):
        for line in ("No f-word streams, no slurs", "no sexual violence ever", "not torture"):
            self.assertIn(line, self.section)

    def test_the_comedy_never_changes_the_rules(self):
        self.assertIn("mechanics stay exact", self.section)
        self.assertIn("The comedy never softens the rules", self.section)

    def test_serious_moments_are_played_straight(self):
        self.assertIn("gets played straight", self.section)

    def test_it_borrows_qualities_not_someone_elses_book(self):
        self.assertIn("never the names, characters, places, catchphrases or plot", self.section)


if __name__ == "__main__":
    unittest.main()
