"""QA (verify, plan step S12): Kokoro blends edge cases the builder's tests miss.

AC16 — after the pool is used up, blended voices are saved per NPC and sound
       distinct.
D20  — a blend never contains the narrator's or a player character's voice.

Found by verify against the real Kokoro helper:
  * "a+b" and "b+a" synthesize byte-identical audio (kokoro_server.py blends
    0.5/0.5 and both parts share a group, so the language is the same). Yet
    `cast.py set --npc` accepts "b+a" when another NPC holds "a+b", and
    `npc_voices` keeps both, so two NPCs sound the same.
  * Giving a player character (or the narrator) a voice that is one part of
    an NPC's blend reassigns NPCs holding that single voice, but leaves the
    blend in npcs.md (and on the laptop's /cast list) until that NPC next
    speaks.
"""
from __future__ import annotations

import unittest

from tests.test_voice_blends import _CampaignCase, _load_cast, _section, _voices_in, tts  # noqa: F401

KOKORO = set(tts.KOKORO_VOICES)


class TestReversedBlendIsTheSameVoice(_CampaignCase):

    def setUp(self):
        super().setUp()
        self.cast = _load_cast()
        self.blend_flag(True)
        self.display_narrator("bm_lewis")

    def test_set_npc_refuses_reversed_pair_held_by_another_npc(self):
        self.cast.set_voice(self.name, "npc", "Garrick", "am_onyx+am_adam")
        with self.assertRaises(ValueError,
                               msg="am_adam+am_onyx is the same voice as Garrick's am_onyx+am_adam"):
            self.cast.set_voice(self.name, "npc", "Hilde", "am_adam+am_onyx")

    def test_npc_voices_moves_a_reversed_duplicate(self):
        # Two NPCs written by hand (or by an older build) with the same pair.
        (self.camp / "npcs.md").write_bytes(
            b"# NPCs - test\n\n### Garrick\n- **Voice:** am_onyx+am_adam\n\n"
            b"### Hilde\n- **Voice:** am_adam+am_onyx\n\n")
        g = self.cast.npc_voices(self.name, "Garrick")["voice"]
        h = self.cast.npc_voices(self.name, "Hilde")["voice"]
        pair = lambda v: frozenset(tts.blend_parts(v) or (v,))  # noqa: E731
        self.assertNotEqual(pair(g), pair(h),
                            f"Garrick {g} and Hilde {h} are the same blend: they sound identical")


class TestReservedPartEvictsBlendAtOnce(_CampaignCase):
    """A single voice an NPC holds is taken away the moment a PC or the
    narrator is given it (_reassign_npcs_holding). A blend containing it
    should be too, so npcs.md and the laptop cast list never show an NPC
    with a player character's or the narrator's voice in it."""

    def setUp(self):
        super().setUp()
        self.cast = _load_cast()
        self.blend_flag(True)
        self.display_narrator("bm_lewis")
        self.pc("Aldric", "am_eric")
        self.cast.set_voice(self.name, "npc", "Garrick", "am_onyx+am_adam")

    def _garrick(self):
        return _voices_in(_section(self.npcs_text(), "Garrick"))

    def test_pc_given_a_blend_part(self):
        self.cast.set_voice(self.name, "pc", "Aldric", "am_onyx")
        [v] = self._garrick()
        self.assertNotIn("am_onyx", (tts.blend_parts(v) or (v,)),
                         f"npcs.md still gives Garrick {v}, which contains Aldric's voice")
        npcs = {n["name"]: n["voice"] for n in self.cast.cast_list(self.name)["npcs"]}
        self.assertNotIn("am_onyx", (tts.blend_parts(npcs["Garrick"]) or (npcs["Garrick"],)))

    def test_narrator_given_a_blend_part(self):
        self.cast.set_voice(self.name, "narrator", None, "am_adam")
        [v] = self._garrick()
        self.assertNotIn("am_adam", (tts.blend_parts(v) or (v,)),
                         f"npcs.md still gives Garrick {v}, which contains the narrator's voice")


if __name__ == "__main__":
    unittest.main()
