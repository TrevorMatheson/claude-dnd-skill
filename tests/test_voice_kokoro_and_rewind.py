"""Local narrator voice (Kokoro) routing, and rewinding the last turn.

Kokoro reads the narration on this machine; Gemini voices NPC lines and, when
it can't (quota, failure), a matching Kokoro voice reads the line instead so
the table keeps its voice. Nothing here reaches Gemini or a real Kokoro helper.

Rewind: the display snapshots the campaign files, the TV feed and the stats
when player actions reach the DM, and puts them back on /rewind.
"""
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
sys.path.insert(0, str(DISPLAY))

import rewind  # noqa: E402
import tts  # noqa: E402


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_kokoro", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class KokoroVoices(unittest.TestCase):

    def test_every_gemini_voice_has_a_local_standin(self):
        for v in tts.VALID_VOICES:
            self.assertIn(tts.kokoro_standin(v), tts.KOKORO_VOICES)

    def test_default_narrator_is_lewis(self):
        self.assertEqual(tts.DEFAULT_NARRATOR, "bm_lewis")
        self.assertTrue(tts.valid_voice("bm_lewis"))
        self.assertTrue(tts.valid_voice("Kore"))
        self.assertFalse(tts.valid_voice("nobody"))


class KokoroRouting(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def setUp(self):
        a = self.app
        a._tts_quota_until = 0.0
        a._rate_buckets.clear()
        a._kokoro_ok = True
        a._gemini_state.update(on=True, reason="")
        a._voice.update(on=True, reason="")
        self.sent = []
        self._p = [mock.patch.object(a, "_broadcast", side_effect=self.sent.append),
                   mock.patch.object(a._tts, "key_source", return_value="env:GEMINI_API_KEY"),
                   mock.patch.object(a._tts, "kokoro_synthesize", return_value=b"\x01\x00" * 8)]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        self.app._kokoro_ok = False

    def test_narration_is_read_locally(self):
        with mock.patch.object(self.app._tts, "synthesize_strict") as gem:
            r = self.client.post("/tts", json={"text": "The door creaks.", "voice": "bm_lewis"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["X-Audio-Engine"], "kokoro")
        gem.assert_not_called()

    def test_npc_line_uses_gemini(self):
        with mock.patch.object(self.app._tts, "synthesize_strict", return_value=b"\x02\x00" * 4) as gem:
            r = self.client.post("/tts", json={"text": "Sprouts.", "voice": "Kore"})
        self.assertEqual(r.headers["X-Audio-Engine"], "gemini")
        gem.assert_called_once()

    def test_out_of_quota_npc_is_read_locally_and_voice_stays_on(self):
        err = tts.TtsError("http 429", status=429,
                           quota_id="GenerateRequestsPerDayPerProjectPerModel-FreeTier", retry_after=7200)
        with mock.patch.object(self.app._tts, "synthesize_strict", side_effect=err) as gem:
            r1 = self.client.post("/tts", json={"text": "first", "voice": "Kore"})
            r2 = self.client.post("/tts", json={"text": "second", "voice": "Kore"})
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertEqual(r2.headers["X-Audio-Voice"], "af_kore")
        self.assertEqual(gem.call_count, 1, "Gemini was called again while out of quota")
        self.assertTrue(self.app._voice_payload()["on"])
        self.assertFalse([m for m in self.sent if "tts_quota" in m],
                         "the table was told the voice is gone while the local voice still works")

    def test_a_long_turn_read_by_sentence_is_not_rate_limited(self):
        codes = [self.client.post("/tts", json={"text": f"Sentence {i}.", "voice": "bm_lewis"}).status_code
                 for i in range(40)]
        self.assertEqual(set(codes), {200})


class RewindFiles(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.rt = root / "rt"
        self.rt.mkdir()
        self.camp = root / "camp"
        (self.camp / "characters").mkdir(parents=True)
        (self.camp / "state.md").write_text("HP 20", encoding="utf-8")
        (self.camp / "characters" / "Havoc.md").write_text("HP 20/20", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_restore_undoes_changes_and_keeps_the_old_branch(self):
        rewind.snapshot(self.rt, "c", self.camp, [("Havoc", "I hug the owlbear")], {"log": [{"text": "a"}]})
        (self.camp / "state.md").write_text("HP 3", encoding="utf-8")
        (self.camp / "tracker.json").write_text("{}", encoding="utf-8")       # made during the turn
        snap, meta = rewind.latest(self.rt, "c")
        state = rewind.restore(snap, self.camp)
        self.assertEqual((self.camp / "state.md").read_text(encoding="utf-8"), "HP 20")
        self.assertFalse((self.camp / "tracker.json").exists())
        self.assertEqual((snap / "abandoned" / "state.md").read_text(encoding="utf-8"), "HP 3")
        self.assertEqual(state["log"], [{"text": "a"}])
        self.assertEqual(meta["inputs"][0]["text"], "I hug the owlbear")
        self.assertEqual(rewind.latest(self.rt, "c"), (None, None), "a snapshot is used once")

    def test_note_is_read_once(self):
        rewind.write_note(self.rt, rewind.dm_note({"inputs": [{"character": "Anony", "text": "x"}]}))
        self.assertIn("REWIND", rewind.take_note(self.rt))
        self.assertEqual(rewind.take_note(self.rt), "")

    def test_only_recent_snapshots_are_kept(self):
        for i in range(rewind.KEEP + 3):
            rewind.snapshot(self.rt, "c", self.camp, [("Havoc", str(i))], {})
        self.assertEqual(len(list((self.rt / "rewind" / "c").iterdir())), rewind.KEEP)


class RewindRoute(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def test_rewind_restores_feed_stats_and_files_and_tells_the_dm(self):
        a = self.app
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            camp = root / "camp"
            camp.mkdir()
            (camp / "state.md").write_text("before", encoding="utf-8")
            qfile = root / ".input_queue"
            sent = []
            with mock.patch.object(a, "QUEUE_FILE", str(qfile)), \
                    mock.patch.object(a, "_active_campaign_name", return_value="camp"), \
                    mock.patch.object(a, "_find_campaign", return_value=camp), \
                    mock.patch.object(a, "_broadcast", side_effect=sent.append), \
                    mock.patch.object(a, "_persist_log"), mock.patch.object(a, "_persist_tail"), \
                    mock.patch.object(a, "_persist_stats"):
                a._text_log.clear()
                a._text_log.append({"text": "the story so far"})
                a._current_stats = {"players": [{"name": "Havoc", "hp": {"current": 20, "max": 20}}]}
                a._rewind_mark([("Havoc", "I hug the owlbear")])
                # the misunderstood turn
                a._text_log.append({"player": "Havoc", "text": "Havoc attacks the owlbear"})
                a._current_stats = {"players": [{"name": "Havoc", "hp": {"current": 2, "max": 20}}]}
                (camp / "state.md").write_text("after", encoding="utf-8")
                qfile.write_text("[Havoc]: rolled 3", encoding="utf-8")

                r = self.client.post("/rewind")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(list(a._text_log), [{"text": "the story so far"}])
                self.assertEqual(a._current_stats["players"][0]["hp"]["current"], 20)
                self.assertEqual((camp / "state.md").read_text(encoding="utf-8"), "before")
                self.assertFalse(qfile.exists(), "rolls for the undone turn were kept")
                msg = [m["rewind"] for m in sent if "rewind" in m][0]
                self.assertEqual(msg["inputs"][0]["text"], "I hug the owlbear")
                note = rewind.take_note(root)
                self.assertIn("I hug the owlbear", note)
                self.assertEqual(self.client.post("/rewind").status_code, 404)


if __name__ == "__main__":
    unittest.main()
