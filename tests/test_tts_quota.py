"""Out of Gemini quota: stop calling, keep cached clips, tell the table.

Found timing a real turn after the table-test release: the key's free tier
allows 10 TTS requests a day for this model (quotaId
GenerateRequestsPerDayPerProjectPerModel-FreeTier), a turn makes 3-4, and
every request after that came back 429 → our 502 → the page silently fell
back to the browser's robot voice. Now the server parses the quota and the
retry delay, answers 429 at once without calling Gemini until it resets
(cached clips still play), and broadcasts tts_quota so the TV says why.
No request here reaches Gemini.
"""
import importlib.util
import io
import json
import pathlib
import sys
import unittest
import urllib.error
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
sys.path.insert(0, str(DISPLAY))

import tts  # noqa: E402

BODY_429 = json.dumps({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota", "details": [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
        {"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
         "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": "10"}]},
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "33703s"}]}})


def _http_429(*a, **k):
    raise urllib.error.HTTPError("u", 429, "Too Many Requests", {}, io.BytesIO(BODY_429.encode()))


class TestParse(unittest.TestCase):

    def test_quota_and_retry_parsed(self):
        self.assertEqual(tts._parse_quota(BODY_429),
                         ("GenerateRequestsPerDayPerProjectPerModel-FreeTier", 33703.0))

    def test_synthesize_raises_with_details(self):
        with mock.patch.object(tts, "_get_api_key", return_value="k"), \
                mock.patch.object(tts.urllib.request, "urlopen", side_effect=_http_429):
            with self.assertRaises(tts.TtsError) as cm:
                tts.synthesize_strict("a brand new line nobody cached", "Kore", use_cache=False)
        self.assertEqual(cm.exception.status, 429)
        self.assertTrue(cm.exception.daily_quota)
        self.assertEqual(cm.exception.retry_after, 33703.0)


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_quota", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def setUp(self):
        self.app._tts_quota_until = 0.0
        self.app._rate_buckets.clear()
        self.sent = []
        self._p = [mock.patch.object(self.app, "_broadcast", side_effect=self.sent.append),
                   mock.patch.object(self.app._tts, "key_source", return_value="env:GEMINI_API_KEY")]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()

    def test_429_stops_further_calls_and_broadcasts(self):
        err = tts.TtsError("http 429", status=429,
                           quota_id="GenerateRequestsPerDayPerProjectPerModel-FreeTier", retry_after=33703)
        with mock.patch.object(self.app._tts, "synthesize_strict", side_effect=err) as syn:
            r1 = self.client.post("/tts", json={"text": "first new line", "voice": "Kore"})
            r2 = self.client.post("/tts", json={"text": "second new line", "voice": "Kore"})
        self.assertEqual(r1.status_code, 429)
        self.assertEqual(r2.status_code, 429)
        self.assertEqual(syn.call_count, 1, "Gemini was called again while out of quota")
        q = [b["tts_quota"] for b in self.sent if "tts_quota" in b]
        self.assertEqual(len(q), 1)
        self.assertTrue(q[0]["daily"])
        self.assertGreater(int(r2.headers["Retry-After"]), 33000)

    def test_cached_clip_still_plays_while_out(self):
        self.app._tts_quota_until = self.app._time.time() + 600
        with mock.patch.object(self.app._tts, "cache_get", return_value=b"\x00\x01" * 10):
            r = self.client.post("/tts", json={"text": "an old line", "voice": "Kore"})
        self.assertEqual(r.status_code, 200)

    def test_health_reports_wait(self):
        self.app._tts_quota_until = self.app._time.time() + 120
        self.assertGreater(self.client.get("/health").get_json()["tts_quota_wait"], 100)


class TestPage(unittest.TestCase):

    def test_page_handles_quota(self):
        src = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("if (payload.tts_quota) _ttsQuotaNotice(", src)
        # a page opened after the quota ran out learns it from the 429 reply
        self.assertIn("if (resp.status === 429) _ttsQuotaNotice(resp.headers.get('Retry-After'));", src)
        self.assertIn("if (err.message === 'TTS 429')", src)


if __name__ == "__main__":
    unittest.main()
