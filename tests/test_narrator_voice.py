"""The DM narrates in the crawler voice: a snarky, PG-13 announcer over a deadly world.

WHY
===
The table asked for a tone like a dungeon-crawl LitRPG comedy (Dungeon Crawler
Carl was the example) kept to PG-13. The voice lives in SKILL.md under
"The Narrator's Voice"; a campaign can turn it off with `narrator: classic`.
These checks keep the pieces that make it work from quietly disappearing.
"""
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL_DIR = REPO / "skills" / "dnd"


def _read(rel):
    return (SKILL_DIR / rel).read_text(encoding="utf-8")


class NarratorVoice(unittest.TestCase):

    def setUp(self):
        self.skill = _read("SKILL.md")
        start = self.skill.index("## The Narrator's Voice")
        self.section = self.skill[start:self.skill.index("## Table Dials", start)]

    def test_the_voice_is_the_default_and_can_be_switched_off(self):
        self.assertIn("narrator: crawler", self.section)
        self.assertIn("narrator: classic", self.section)
        self.assertIn("narrator: crawler|classic", _read("templates/state.md"))
        self.assertIn("dungeon-crawl comedy (default)", _read("SKILL-commands.md"))

    def test_it_keeps_to_pg13(self):
        for line in ("No f-word streams, no slurs", "no sexual violence ever", "not torture"):
            self.assertIn(line, self.section)

    def test_jokes_never_change_the_rules_or_the_length(self):
        self.assertIn("never a change to what it does", self.section)
        self.assertIn("Funny is the coat of paint, not the difficulty", self.section)
        self.assertIn("jokes come out of the word budget", self.section)

    def test_serious_moments_are_played_straight(self):
        self.assertIn("When to drop the act", self.section)

    def test_it_borrows_qualities_not_someone_elses_book(self):
        self.assertIn("never the names, characters, places, catchphrases or lines", self.section)


if __name__ == "__main__":
    unittest.main()
