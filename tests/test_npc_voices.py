"""NPCs speak in their own voice; replayed history is never read aloud.

* /chunk now carries a `voice` for read-aloud: the block's own `voice`, else
  the NPC's `**Voice:**` line in npcs.md, else (tutor hints) the campaign's
  `tutor_voice` session flag. Narration keeps the narrator voice.
* Blocks replayed on page load used to go through auto-narrate like new ones.
"""
import importlib.util
import pathlib
import re
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
HTML = DISPLAY / "templates" / "index.html"
sys.path.insert(0, str(DISPLAY))

NPCS = """# NPCs — test

### Mira Ashgrove
- **Role:** innkeeper
- **Speech quirk:** hums
- **Voice:** Kore

### Old Tam
- **Voice:** <read-aloud voice — pick one>

### Brother Hale
- **Voice:** Charon
"""


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_voices", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestNpcVoices(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        if cls.app._tts is None:
            raise unittest.SkipTest("tts module unavailable")
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()
        import paths
        cls.camp = paths.campaigns_dir() / "voices-test"
        cls.camp.mkdir(parents=True, exist_ok=True)
        (cls.camp / "npcs.md").write_text(NPCS, encoding="utf-8")
        (cls.camp / "state.md").write_text("## Session Flags\ntutor_voice: Zephyr\n", encoding="utf-8")

    def setUp(self):
        self.sent = []
        self._p = [mock.patch.object(self.app, "_broadcast", side_effect=self.sent.append),
                   mock.patch.object(self.app, "_active_campaign_name", return_value="voices-test")]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()

    def _chunk(self, **body):
        self.client.post("/chunk", json=body)
        return [b for b in self.sent if b.get("text")][-1]

    def test_npc_voice_from_npcs_md(self):
        self.assertEqual(self._chunk(npc="Mira Ashgrove", text="Welcome.").get("voice"), "Kore")
        self.assertEqual(self._chunk(npc="brother hale", text="Peace.").get("voice"), "Charon")

    def test_placeholder_voice_ignored(self):
        self.assertNotIn("voice", self._chunk(npc="Old Tam", text="Eh?"))

    def test_explicit_voice_wins(self):
        self.assertEqual(self._chunk(npc="Mira Ashgrove", text="Hm.", voice="Aoede")["voice"], "Aoede")

    def test_tutor_voice_flag(self):
        self.assertEqual(self._chunk(tutor=True, text="Roll a d20.")["voice"], "Zephyr")

    def test_narration_keeps_narrator(self):
        self.assertNotIn("voice", self._chunk(text="The rain falls."))

    def test_invalid_voice_dropped(self):
        self.assertNotIn("voice", self._chunk(npc="Nobody", text="Hi.", voice="Robot"))


class TestPageWiring(unittest.TestCase):

    def setUp(self):
        self.src = HTML.read_text(encoding="utf-8")

    def test_replay_sites_never_auto_play(self):
        calls = re.findall(r"_addTtsBar\(([^)]*)\)", self.src)
        bare = [c for c in calls if c.strip() in ("block", "currentEl")]
        self.assertEqual(len(bare), 1, f"only the live DM stream may call _addTtsBar without opts: {calls}")
        self.assertIn("if (_ttsAutoNarrate && !isReplay)", self.src)

    def test_voice_reaches_deferred_renderers(self):
        # renderNPCBlock / renderTutorBlock defer to *Now functions; the voice
        # was once dropped on the way and the NPC block threw ReferenceError.
        self.assertIn("_renderNPCBlockNow(name, text, isReplay, voice)", self.src)
        self.assertIn("function _renderNPCBlockNow(name, text, isReplay, voice)", self.src)
        self.assertIn("_renderTutorBlockNow(text, isReplay, voice)", self.src)
        self.assertIn("function _renderTutorBlockNow(text, isReplay, voice)", self.src)

    def test_block_voice_used_for_tts(self):
        self.assertIn("_ttsFetchPcm(text, _ttsBlockVoice(blockEl))", self.src)

    def test_next_block_prefetched(self):
        self.assertIn("if (_ttsAutoQueue.length) _ttsPrefetchBlock(_ttsAutoQueue[0][1]);", self.src)


if __name__ == "__main__":
    unittest.main()
