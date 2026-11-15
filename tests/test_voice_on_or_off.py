"""The narration voice works fully or stays off.

WHY
===
When Gemini had no key, rejected the key or ran out of quota, the page read
the narration in the browser's own speech synthesis instead. At a table that
sounded like the voice had broken: a flat robot voice cutting in mid-session.
Now there is one voice. The server checks it at start (reading the model's
metadata, which spends no quota), sets one state for every screen — on, or
off with a reason — and the page shows play buttons only while it is on.

No request here reaches Google.
"""
import importlib.util
import io
import pathlib
import re
import sys
import unittest
import urllib.error
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
HTML = DISPLAY / "templates" / "index.html"
sys.path.insert(0, str(DISPLAY))

import tts  # noqa: E402


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_voice", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http(code, body=""):
    def raise_(*a, **k):
        raise urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body.encode()))
    return raise_


class CheckKeyTests(unittest.TestCase):
    """tts.check_key turns Google's answer into on/off and a plain reason."""

    def _check(self, side_effect=None, ret=None):
        with mock.patch.object(tts, "_get_api_key", return_value="k"), \
                mock.patch.object(tts.urllib.request, "urlopen", side_effect=side_effect, return_value=ret):
            return tts.check_key()

    def test_no_key(self):
        with mock.patch.object(tts, "_get_api_key", return_value=None):
            self.assertEqual(tts.check_key(), (False, "no voice key is set up", False))

    def test_good_key(self):
        self.assertEqual(self._check(ret=_Resp()), (True, "", False))

    def test_check_reads_metadata_not_generate(self):
        """The start-up check must not spend a voice request."""
        seen = []

        def capture(req, timeout=0):
            seen.append(req)
            return _Resp()
        with mock.patch.object(tts, "_get_api_key", return_value="k"), \
                mock.patch.object(tts.urllib.request, "urlopen", side_effect=capture):
            tts.check_key()
        self.assertEqual(seen[0].get_method(), "GET")
        self.assertNotIn("generateContent", seen[0].full_url)

    def test_rejected_key_is_not_retried(self):
        ok, why, retry = self._check(side_effect=_http(400, '{"error":{"details":[{"reason":"API_KEY_INVALID"}]}}'))
        self.assertFalse(ok)
        self.assertIn("rejected", why)
        self.assertFalse(retry)

    def test_network_failure_is_retried(self):
        def down(*a, **k):
            raise urllib.error.URLError("offline")
        ok, why, retry = self._check(side_effect=down)
        self.assertFalse(ok)
        self.assertTrue(retry)


class ServerStateTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def setUp(self):
        self.app._tts_quota_until = 0.0
        self.app._rate_buckets.clear()
        self.app._voice.update(on=False, reason="checking the voice…")
        self.sent = []
        self._b = mock.patch.object(self.app, "_broadcast", side_effect=self.sent.append)
        self._b.start()

    def tearDown(self):
        self._b.stop()

    def _voice_msgs(self):
        return [m["voice"] for m in self.sent if "voice" in m]

    def test_no_key_is_off_with_a_reason(self):
        with mock.patch.object(self.app._tts, "key_fingerprint", return_value=""):
            self.app._voice_check()
        self.assertEqual(self.app._voice_payload(), {"on": False, "reason": "no voice key is set up"})

    def test_good_key_turns_on_and_tells_every_screen_once(self):
        with mock.patch.object(self.app._tts, "key_fingerprint", return_value="abc"), \
                mock.patch.object(self.app._tts, "check_key", return_value=(True, "", False)):
            self.app._voice_check()
            self.app._voice_check()
        self.assertTrue(self.app._voice_payload()["on"])
        self.assertEqual(self._voice_msgs(), [{"on": True, "reason": ""}])

    def test_tts_refuses_while_off(self):
        """Off means off: no request is passed to Gemini."""
        with mock.patch.object(self.app._tts, "key_source", return_value="env:GEMINI_API_KEY"), \
                mock.patch.object(self.app._tts, "synthesize_strict") as syn:
            r = self.client.post("/tts", json={"text": "a line", "voice": "Kore"})
        self.assertEqual(r.status_code, 503)
        syn.assert_not_called()

    def test_quota_turns_the_voice_off_for_everyone(self):
        self.app._voice.update(on=True, reason="")
        err = tts.TtsError("http 429", status=429,
                           quota_id="GenerateRequestsPerDayPerProjectPerModel-FreeTier", retry_after=7200)
        with mock.patch.object(self.app._tts, "key_source", return_value="env:GEMINI_API_KEY"), \
                mock.patch.object(self.app._tts, "synthesize_strict", side_effect=err):
            self.client.post("/tts", json={"text": "a new line", "voice": "Kore"})
        state = self.app._voice_payload()
        self.assertFalse(state["on"])
        self.assertIn("quota", state["reason"])
        self.assertEqual(len(self._voice_msgs()), 1)
        self.assertGreater(self.app._voice_next_check, self.app._time.time() + 7000,
                           "the voice should be checked again when the quota resets")

    def test_page_and_health_carry_the_state(self):
        self.app._voice.update(on=False, reason="no voice key is set up")
        tpl = HTML.read_text(encoding="utf-8")
        self.assertIn("""<meta name="voice-on" content="{{ '1' if voice_on else '0' }}">""", tpl)
        self.assertIn('<meta name="voice-reason" content="{{ voice_reason }}">', tpl)
        src = (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8")
        self.assertIn('voice_on=_voice_payload()["on"]', src)
        h = self.client.get("/health").get_json()
        self.assertFalse(h["voice_on"])
        self.assertEqual(h["voice_reason"], "no voice key is set up")


class PageTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.src = HTML.read_text(encoding="utf-8")

    def test_no_browser_voice_anywhere(self):
        for word in ("speechSynthesis", "SpeechSynthesisUtterance", "_ttsBrowserSpeak"):
            self.assertNotIn(word, self.src)

    def test_page_follows_the_server_state(self):
        self.assertIn("if (payload.voice) _voiceSet(payload.voice.on, payload.voice.reason);", self.src)
        self.assertIn("body.voice-off .tts-bar", self.src)

    def test_off_stops_playing_and_drops_the_queue(self):
        body = re.search(r"function _voiceSet\(on, reason\) \{(.*?)\n\}", self.src, re.S)
        self.assertIsNotNone(body)
        self.assertIn("_ttsAutoQueue.length = 0", body.group(1))
        self.assertIn("_ttsStopActive()", body.group(1))

    def test_nothing_auto_plays_while_off(self):
        self.assertIn("if (_ttsAutoNarrate && !isReplay && _voiceOn) {", self.src)
        self.assertRegex(self.src, r"async function _ttsPlayBlock\(btn, blockEl\) \{[\s\S]{0,300}if \(!_voiceOn\) return;")


if __name__ == "__main__":
    unittest.main()
