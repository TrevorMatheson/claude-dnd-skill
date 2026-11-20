"""Regression tests for the reviewer's findings on S11-S16 (2026-11-20).

1. A narrator saved from the laptop (POST /cast) or the old page (/voice)
   reaches the TV: a `display` broadcast carries the new narrator.
2. A legacy GBK npcs.md / state.md is read (and migrated), not refused.
3. A typed action whose character has no sheet is not read aloud (it would
   be read in the narrator's voice).
5. A narrator set from the terminal survives the next laptop setting.
6. GET /cast from another device writes nothing.
8. A player character can't take the voice that reads the narration.
9. /tts reads a Kokoro voice in that voice, whatever `fallback` says.
Prose "Voice: Gravelly" is the DM's description, not a voice to overwrite.
"""
import json
import unittest
from unittest import mock

from tests.test_cast import (_AppCase, _CampaignCase, _load_cast, _npc, _section, _voices_in,
                             KOKORO)


class TestNarratorReachesTheTv(_AppCase):

    def displays(self):
        return [m["display"] for m in self.sent if isinstance(m, dict) and "display" in m]

    def test_post_cast_narrator_broadcasts(self):
        self.display_narrator("bm_lewis")
        self.npcs(_npc("Mira Ashgrove", "af_bella"))
        r = self.client.post("/cast", json={"kind": "narrator", "voice": "bm_george"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        sent = self.displays()
        self.assertTrue(sent, "no display broadcast after the narrator changed")
        self.assertEqual(sent[-1]["config"]["voice"]["narrator"], "bm_george")

    def test_voice_route_broadcasts_a_kokoro_narrator(self):
        self.display_narrator("bm_lewis")
        self.npcs()
        r = self.client.post("/voice", json={"voice": "bm_george"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.displays()[-1]["config"]["voice"]["narrator"], "bm_george")

    def test_voice_route_keeps_a_gemini_name_out_of_display_json(self):
        self.display_narrator("bm_lewis")
        self.npcs()
        self.client.post("/voice", json={"voice": "Kore"})
        saved = json.loads((self.camp / "display.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["voice"]["narrator"], "bm_lewis")

    def test_cli_narrator_survives_a_laptop_setting(self):
        self.display_narrator("bm_lewis")
        self.npcs()
        self.client.get("/display-config")                 # cache warmed
        _load_cast().set_voice(self.name, "narrator", None, "bm_george")   # the terminal
        r = self.client.post("/display-config", json={"voice": {"speed": 1.2}},
                             environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(r.status_code, 200)
        saved = json.loads((self.camp / "display.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["voice"]["narrator"], "bm_george")
        self.assertEqual(saved["voice"]["speed"], 1.2)

    def test_get_cast_from_another_device_writes_nothing(self):
        self.display_narrator("bm_lewis")
        self.pc("Aldric")
        self.npcs(_npc("Old Tam"))
        before = ((self.camp / "npcs.md").read_bytes(),
                  (self.camp / "characters" / "aldric.md").read_bytes())
        r = self.client.get("/cast", environ_base={"REMOTE_ADDR": "192.168.1.50"})
        self.assertEqual(r.status_code, 200)
        after = ((self.camp / "npcs.md").read_bytes(),
                 (self.camp / "characters" / "aldric.md").read_bytes())
        self.assertEqual(after, before)

    def test_kokoro_voice_is_not_overridden_by_fallback(self):
        self.app._kokoro_ok = True
        self.addCleanup(setattr, self.app, "_kokoro_ok", False)
        with mock.patch.object(self.app._tts, "kokoro_synthesize", return_value=b"\x01\x00" * 8):
            r = self.client.post("/tts", json={"text": "Hi.", "voice": "af_bella", "fallback": "bf_lily"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["X-Audio-Voice"], "af_bella")


class TestReadActionsNeedAVoice(_AppCase):

    def test_character_without_a_sheet_is_not_read(self):
        self.npcs()
        with mock.patch.object(self.app, "_display_config",
                               return_value={"voice": {"read_player_actions": True}}):
            self.app._read_actions_aloud([("Stranger", "I wave.")])
        self.assertFalse([m for m in self.sent if isinstance(m, dict) and "read_actions" in m])


class TestLegacyAndProse(_CampaignCase):

    def setUp(self):
        super().setUp()
        self.cast = _load_cast()

    def test_gbk_npcs_md_is_read_and_migrated(self):
        text = "# NPCs\n\n### 王掌柜 Wang\n- **Voice:** af_bella\n\n### Old Tam\n- **Role:** 渔夫\n"
        (self.camp / "npcs.md").write_bytes(text.encode("gbk"))
        self.assertEqual(self.cast.npc_voices(self.name, "王掌柜 Wang")["voice"], "af_bella")
        v = self.cast.npc_voices(self.name, "Old Tam")["voice"]
        self.assertIn(v, KOKORO)
        self.assertNotEqual(v, "af_bella")
        out = (self.camp / "npcs.md").read_bytes().decode("utf-8")   # migrated to UTF-8
        self.assertIn("渔夫", out)
        self.assertEqual(_voices_in(_section(out, "Old Tam")), [v])

    def test_gbk_state_md_narrator_is_read(self):
        (self.camp / "state.md").write_bytes("# 状态\n\n## Session Flags\ntts_voice: bm_george\n".encode("gbk"))
        self.assertEqual(self.cast.narrator_voice(self.name), "bm_george")

    def test_one_word_prose_voice_is_kept(self):
        self.npcs("### Grue\n- **Voice:** Gravelly\n\n")
        v = self.cast.npc_voices(self.name, "Grue")["voice"]
        self.assertIn(v, KOKORO)
        self.assertIn("Gravelly", self.npcs_text())

    def test_pc_cannot_take_the_voice_reading_narration(self):
        self.state_narrator("Kore")              # Gemini name: bm_lewis reads narration
        self.pc("Mira")
        with self.assertRaises(ValueError):
            self.cast.set_voice(self.name, "pc", "Mira", "bm_lewis")


if __name__ == "__main__":
    unittest.main()
