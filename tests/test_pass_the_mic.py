"""Pass the mic: one speaker at a time, transcribed on this PC.

A phone claims the mic, records, uploads the clip to /stt, and the words come
back into that player's action box. What matters here:

  * only one player holds the mic; anyone else is told who is talking;
  * only the holder can upload, and uploading frees the mic;
  * a phone that vanishes mid-sentence does not keep the mic forever;
  * with faster-whisper missing, the mic is off and says why (no button).

No model is loaded: stt's model is replaced with a stub.
"""
import importlib.util
import io
import pathlib
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
sys.path.insert(0, str(DISPLAY))

import stt  # noqa: E402


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_mic", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Seg:
    def __init__(self, text, start=0.0):
        self.text, self.start = text, start


class _FakeModel:
    def __init__(self):
        self.prompts = []

    def transcribe(self, f, **kw):
        self.prompts.append(kw.get("initial_prompt"))
        return iter([_Seg(" I swing my axe"), _Seg(" at the goblin.")]), None


class SttModuleTests(unittest.TestCase):

    def tearDown(self):
        stt._model = None
        stt._state.update(on=False, reason="speech-to-text is loading…")

    def test_missing_package_is_off_with_the_install_hint(self):
        with mock.patch.dict(sys.modules, {"faster_whisper": None}):
            st = stt.load()
        self.assertFalse(st["on"])
        self.assertIn("pip install faster-whisper", st["reason"])

    def test_transcribe_refuses_while_off(self):
        with self.assertRaises(RuntimeError):
            stt.transcribe(b"x")

    def test_names_go_in_the_prompt(self):
        fake = _FakeModel()
        stt._model = fake
        stt._state.update(on=True, reason="")
        with mock.patch.object(stt, "_decode", return_value=[0.0] * 160):
            self.assertEqual(stt.transcribe(b"x", ["Anony", "Vac'uum", "Anony"]), "I swing my axe at the goblin.")
        self.assertEqual(fake.prompts[0], "Names at this table: Anony, Vac'uum.")

    def test_the_campaign_calendar_does_not_hide_the_real_one(self):
        # scripts/calendar.py shadowed the stdlib calendar, so in the running
        # display faster-whisper failed to import ("cannot import name 'timegm'").
        src = (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8")
        first = src.index("import calendar as _stdlib_calendar")
        self.assertLess(first, src.index("from runtime_paths import"))   # it adds scripts/ to sys.path
        self.assertLess(first, src.index("sys.path.insert(0, SCRIPTS_DIR)"))

    def test_a_clip_is_decoded_to_16khz_mono_for_whisper(self):
        # faster-whisper's own decoder breaks on PyAV 15+, so stt decodes clips itself.
        try:
            import av  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("PyAV not installed")
        import io, math, struct, wave
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:            # 1 s of stereo 44.1 kHz tone
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
            w.writeframes(b"".join(struct.pack("<hh", v, v) for v in
                                   (int(8000 * math.sin(i / 20)) for i in range(44100))))
        samples = stt._decode(buf.getvalue())
        self.assertAlmostEqual(len(samples), 16000, delta=400)
        self.assertEqual(str(samples.dtype), "float32")
        self.assertLessEqual(float(abs(samples).max()), 1.0)


class MicServerTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def setUp(self):
        self.app._rate_buckets.clear()
        self.app._mic.update(holder="", device="", since=0.0)
        self.app._current_stats = {"players": [{"name": "Havoc"}, {"name": "Anony"}]}
        self.sent = []
        self.fake = _FakeModel()
        self._p = [mock.patch.object(self.app, "_broadcast", side_effect=self.sent.append),
                   mock.patch.object(self.app, "_device_ok", return_value="approved"),
                   # the uploads here are fake bytes; decoding is tested on its own
                   mock.patch.object(self.app._stt, "_decode", return_value=[0.0] * 160)]
        for p in self._p:
            p.start()
        self.app._stt._model = self.fake
        self.app._stt._state.update(on=True, reason="")

    def tearDown(self):
        for p in self._p:
            p.stop()
        self.app._stt._model = None
        self.app._stt._state.update(on=False, reason="speech-to-text is loading…")

    def _claim(self, who):
        return self.client.post("/mic/claim", json={"character": who})

    def _upload(self, who, audio=b"\x1a\x45\xdf\xa3fake-webm"):
        return self.client.post("/stt", data={"character": who, "audio": (io.BytesIO(audio), "clip.webm")},
                                content_type="multipart/form-data")

    def test_one_speaker_at_a_time(self):
        self.assertEqual(self._claim("Havoc").status_code, 200)
        r = self._claim("Anony")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["holder"], "Havoc")
        self.assertEqual(self.sent[-1]["mic"]["holder"], "Havoc")

    def test_only_the_holder_can_upload_and_upload_frees_the_mic(self):
        self._claim("Havoc")
        self.assertEqual(self._upload("Anony").status_code, 409)
        r = self._upload("Havoc")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["text"], "I swing my axe at the goblin.")
        self.assertEqual(self.app._mic_payload()["holder"], "")
        self.assertEqual(self._claim("Anony").status_code, 200)

    def test_party_names_reach_whisper(self):
        self._claim("Havoc")
        self._upload("Havoc")
        self.assertIn("Anony", self.fake.prompts[0])

    def test_a_vanished_phone_frees_the_mic(self):
        self._claim("Havoc")
        self.app._mic["since"] -= self.app._MIC_HOLD_MAX + 1
        self.assertEqual(self._claim("Anony").status_code, 200)

    def test_release(self):
        self._claim("Havoc")
        self.assertEqual(self.client.post("/mic/release", json={"character": "Havoc"}).status_code, 204)
        self.assertEqual(self.app._mic_payload()["holder"], "")

    def test_everybody_cannot_take_the_mic(self):
        self.assertEqual(self._claim("Everybody").status_code, 400)

    def test_strangers_cannot_take_the_mic(self):
        r = self._claim("Bob")
        self.assertNotEqual(r.status_code, 200)
        self.assertEqual(self.app._mic_payload()["holder"], "")

    def test_off_means_no_mic(self):
        self.app._stt._state.update(on=False, reason="speech-to-text isn't installed (pip install faster-whisper)")
        r = self._claim("Havoc")
        self.assertEqual(r.status_code, 503)
        self.assertIn("faster-whisper", r.get_json()["error"])
        self.assertFalse(self.client.get("/health").get_json()["mic"]["on"])

    def test_a_failed_transcription_still_frees_the_mic(self):
        self._claim("Havoc")
        with mock.patch.object(self.fake, "transcribe", side_effect=ValueError("bad audio")):
            r = self._upload("Havoc")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.app._mic_payload()["holder"], "")


class MicPageTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.src = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")

    def test_button_follows_the_server_state(self):
        self.assertIn('<button id="mic-btn" type="button" hidden', self.src)
        self.assertIn("if (payload.mic) { _micState = payload.mic; _micRender(); }", self.src)
        self.assertIn("_micBtn.hidden = !document.body.classList.contains('input-only') || !_micState.on;", self.src)

    def test_words_land_in_the_box_not_sent(self):
        """The player checks the transcript before it goes to the DM."""
        self.assertIn("_inputText.value = cur ? cur + ' ' + said : said;", self.src)
        body = self.src[self.src.index("async function _micSend()"):self.src.index("if (_micBtn) {")]
        self.assertNotIn("_stageAction(", body)

    def test_recording_is_capped(self):
        self.assertIn("const _MIC_MAX_MS = 45000;", self.src)


if __name__ == "__main__":
    unittest.main()
