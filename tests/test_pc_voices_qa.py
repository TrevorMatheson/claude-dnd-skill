"""QA findings for S14/S15 (player-character voices, the laptop cast list):
cases tests/test_pc_voices.py and tests/test_cast.py do not cover.

AC18 — Importing or creating a player character stores a voice in its file
       that no NPC is ever given; when "Read player actions aloud" is on,
       typed actions are read in that voice.
D22  — Player characters have voices ... never given to an NPC.
D18  — The cast list lists every character and their voice, with change.

Each test here reproduces a bug found in QA verify (2026-11-20).
"""
from __future__ import annotations

import json
import unittest

from tests.test_cast import _CampaignCase, _load_cast, _import_app


class TestNarratorCannotTakeAPlayerCharactersVoice(_CampaignCase):
    """Repro: on /layout, pick a player character's voice (e.g. Gojo's
    George) in the Narrator menu. The server saves it (200, "Narrator saved")
    and now the narrator and Gojo share one voice, so with "Read player
    actions aloud" on, Gojo's typed actions sound like the DM.

    The reverse is already refused (`cast.py set --pc X <narrator's voice>`
    -> "is the narrator's voice"), and NPCs are refused both; the narrator
    side is the one gap. Expected: refused with a ValueError (POST /cast ->
    400 with a message), files left as they were."""

    def setUp(self):
        super().setUp()
        self.cast = _load_cast()
        self.pc("Gojo", "bm_george")

    def test_cast_set_narrator_to_pc_voice_is_refused(self):
        with self.assertRaises(ValueError):
            self.cast.set_voice(self.name, "narrator", None, "bm_george")
        self.assertNotEqual(self.cast.narrator_voice(self.name), "bm_george",
                            "the narrator now speaks in Gojo's voice")

    def test_post_cast_narrator_to_pc_voice_is_400_and_leaves_display_json(self):
        app = _import_app()
        before = (self.camp / "display.json").read_bytes() if (self.camp / "display.json").exists() else None
        with open(app.CAMP_FILE, "w", encoding="utf-8") as f:
            f.write(self.name)
        client = app.app.test_client()
        r = client.post("/cast", json={"kind": "narrator", "voice": "bm_george"},
                        environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        self.assertIn("error", r.get_json() or {})
        after = (self.camp / "display.json").read_bytes() if (self.camp / "display.json").exists() else None
        self.assertEqual(before, after, "display.json changed on a refused narrator voice")
        if after:
            self.assertNotEqual((json.loads(after).get("voice") or {}).get("narrator"), "bm_george")


if __name__ == "__main__":
    unittest.main()
