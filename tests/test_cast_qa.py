"""QA findings for S11 (the cast): cases tests/test_cast.py does not cover.

AC15 — An NPC that speaks with no voice is given one, written to npcs.md,
       never the narrator's.
AC16 — No two NPCs share a voice until the pool is used up.

Each test here is a reproduction of a bug found in QA verify for S11.
"""
from __future__ import annotations

import re
import unittest

from tests.test_cast import (_CampaignCase, _load_cast, _npc, _section, _voices_in,
                             NARRATOR_DEFAULT)


class TestFirstWordMatchIsTooLoose(_CampaignCase):
    """Plan S11: "Then a unique heading whose first word matches." The risk
    note's example is `--npc "Odessa"` against `### Odessa Varn`: the name
    the DM sent IS the first word. A full, different name that only shares
    a first word (a title such as Captain, Brother, Lady, The) is a new NPC
    and must not take the other NPC's voice (AC16)."""

    def setUp(self):
        super().setUp()
        self.cast = _load_cast()

    def test_different_npc_with_same_title_gets_own_voice(self):
        self.npcs(_npc("Captain Harrow", "bm_george"))
        v = self.cast.npc_voices(self.name, "Captain Vex")["voice"]
        self.assertNotEqual(v, "bm_george", "Captain Vex took Captain Harrow's voice")
        self.assertEqual(_voices_in(_section(self.npcs_text(), "Captain Vex")), [v],
                         "no stub was written for Captain Vex")

    def test_different_npc_with_same_first_name_gets_own_voice(self):
        self.npcs(_npc("Brother Thew", "am_fenrir"))
        v = self.cast.npc_voices(self.name, "Brother Aldous")["voice"]
        self.assertNotEqual(v, "am_fenrir")


class TestNamesThatAreNeverSections(_CampaignCase):
    """An NPC whose name is a template subheading (Notes, Personality,
    Relationships) or starts with '<' gets a stub that the parser then
    ignores: every line appends another stub, and its voice is not counted as
    held, so the next new NPC is given the same voice (AC16)."""

    def setUp(self):
        super().setUp()
        self.cast = _load_cast()

    def _check(self, odd_name):
        self.npcs(_npc("Mira Ashgrove", "af_bella"))
        a = self.cast.npc_voices(self.name, odd_name)["voice"]
        self.cast.npc_voices(self.name, odd_name)
        heads = re.findall(rf"^###[ \t]+{re.escape(odd_name)}[ \t]*$", self.npcs_text(), re.M)
        self.assertLessEqual(len(heads), 1, f"{len(heads)} stubs for {odd_name!r}")
        b = self.cast.npc_voices(self.name, "Zed Quorn")["voice"]
        self.assertNotEqual(a, b, f"{odd_name!r} and Zed Quorn share {a}")

    def test_npc_named_notes(self):
        self._check("Notes")

    def test_npc_name_in_angle_brackets(self):
        self._check("<Hooded Figure>")


class TestHeldVoiceThatIsNowReserved(_CampaignCase):
    """AC15 / D19: no NPC is read in the narrator's voice. npc_voices returns
    an NPC's existing Kokoro voice without checking it against the narrator
    and player characters."""

    def setUp(self):
        super().setUp()
        self.cast = _load_cast()

    def test_hand_written_narrator_voice_is_not_used(self):
        self.npcs(_npc("Old Tom", NARRATOR_DEFAULT))
        v = self.cast.npc_voices(self.name, "Old Tom")["voice"]
        self.assertNotEqual(v, NARRATOR_DEFAULT, "Old Tom speaks in the narrator's voice")

    def test_narrator_changed_to_gemini_name_frees_the_default_reader(self):
        # Narrator af_river: bm_lewis is free and an NPC may hold it.
        self.display_narrator("af_river")
        self.npcs(_npc("Pip Tansy", NARRATOR_DEFAULT))
        # The narrator is changed to a Gemini voice: with Kokoro installed the
        # narration is read by bm_lewis (cast.heard_narrator).
        self.cast.set_voice(self.name, "narrator", None, "Kore")
        self.assertEqual(self.cast.heard_narrator(self.name), NARRATOR_DEFAULT)
        v = self.cast.npc_voices(self.name, "Pip Tansy")["voice"]
        self.assertNotEqual(v, NARRATOR_DEFAULT, "Pip Tansy now shares the voice that reads narration")


if __name__ == "__main__":
    unittest.main()
