"""QA (verify S13): Gemini NPC mode after a quota hit, beyond the builder's tests.

D17 — NPC voices "Gemini, falling back to Kokoro": Kokoro is the stand-in
      while Gemini can't speak, not for the rest of the night.
AC14 — "a Gemini daily-quota error shows the note on the laptop page"; the
      note promises "back in about N h".
Plan S13 — "When the daily quota runs out with Kokoro up, the server stores
      voice_note".

Found by a live run of the app (real Kokoro helper, Gemini stubbed): after a
429 with Kokoro up, _gemini_state stays off for good. _tts_key_watch only
re-checks Gemini when the combined voice is off, and with Kokoro up it never
is, so every later NPC line is read by Kokoro even once the quota is back.
"""
from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from tests.test_npc_engine import _AppCase, _npc, DAILY_429, tts

MINUTE_429 = dict(status=429, quota_id="GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
                  retry_after=30)


class TestGeminiComesBack(_AppCase):

    def setUp(self):
        super().setUp()
        self.display("gemini")
        self.npcs(_npc("Mira Ashgrove", "af_bella", "Zephyr"))
        a = self.app
        self.addCleanup(setattr, a, "_voice_next_check", 0.0)
        p = mock.patch.object(a._tts, "check_key", return_value=(True, "", False))
        p.start()
        self.addCleanup(p.stop)

    def _start_watcher(self):
        """Run the app's real key watcher (as at server start) until stopped.

        key_fingerprint keeps returning the same key (no key change) and
        raises SystemExit once stop is set, which ends the thread."""
        a = self.app
        a._voice_key_fp = "same-key"
        stop = threading.Event()

        def _fp():
            if stop.is_set():
                raise SystemExit
            return "same-key"
        p = mock.patch.object(a._tts, "key_fingerprint", side_effect=_fp)
        p.start()
        self.addCleanup(p.stop)
        t = threading.Thread(target=a._tts_key_watch, kwargs={"interval": 0.01}, daemon=True)
        t.start()

        def _stop():
            stop.set()
            t.join(5)
            self.assertFalse(t.is_alive(), "watcher did not stop")
        self.addCleanup(stop.set)
        time.sleep(0.2)          # the start-up check has run
        return _stop

    def _quota_then_window_passes(self, err_kw):
        a = self.app
        stop = self._start_watcher()
        b = self.block("Mira Ashgrove")
        with mock.patch.object(a._tts, "synthesize_strict", side_effect=tts.TtsError("http 429", **err_kw)):
            r = self.tts(b, "first")
        self.assertEqual(r.headers["X-Audio-Engine"], "kokoro")
        # The quota window ends (as if retry_after seconds went by) while the
        # watcher keeps running.
        a._tts_quota_until = time.time() - 1
        a._voice_next_check = time.time() - 1
        time.sleep(0.3)
        stop()
        with mock.patch.object(a._tts, "synthesize_strict", return_value=b"\x02\x00" * 8) as gem:
            r = self.tts(b, "after the window")
        return r, gem

    def test_gemini_reads_npcs_again_after_a_daily_quota_resets(self):
        r, gem = self._quota_then_window_passes(DAILY_429)
        self.assertEqual(r.headers["X-Audio-Engine"], "gemini",
                         "the quota window passed but the NPC is still read by Kokoro: "
                         "Gemini is never re-checked while Kokoro is up")
        gem.assert_called_once()

    def test_gemini_reads_npcs_again_after_a_per_minute_429(self):
        # A burst of NPC lines hits the per-minute limit; 30 s later Gemini
        # should speak again, not stay off for the rest of the night.
        r, gem = self._quota_then_window_passes(MINUTE_429)
        self.assertEqual(r.headers["X-Audio-Engine"], "gemini",
                         "a 30-second per-minute 429 turned Gemini off for the session")
        gem.assert_called_once()


class TestPerMinuteNote(_AppCase):

    def setUp(self):
        super().setUp()
        self.display("gemini")
        self.npcs(_npc("Mira Ashgrove", "af_bella", "Zephyr"))

    def test_per_minute_429_does_not_claim_back_in_about_1_h(self):
        # S13: the note is for the daily quota. A 30-second per-minute limit
        # must not tell the host Gemini is gone for an hour.
        b = self.block("Mira Ashgrove")
        err = tts.TtsError("http 429", **MINUTE_429)
        with mock.patch.object(self.app._tts, "synthesize_strict", side_effect=err):
            r = self.tts(b, "first")
        self.assertEqual(r.headers["X-Audio-Voice"], "af_bella")
        note = str((self.client.get("/display-config").get_json().get("status") or {}).get("voice_note", ""))
        self.assertNotRegex(note, r"back in about 1 h",
                            f"per-minute 429 (retry in 30 s) produced the daily-quota note: {note!r}")


if __name__ == "__main__":
    unittest.main()
