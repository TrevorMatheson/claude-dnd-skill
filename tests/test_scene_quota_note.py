"""Scene backgrounds S10: quota out, generation off or an error -> a laptop
note, the mood layer only on the TV (AC8, D7, D17).

Covers, per the plan's "Done when" for S10:
  - The place ends pending: false, image: null.
  - GET /display-config has status.scene_note with the right text:
      off      "Scene pictures are off — places show the mood layer only."
      no key   "No Gemini key — …"                         (prefix only)
      quota    "Gemini's image quota is used up — back in about N h"
      error    "Couldn't make the picture for <name> (<reason>)", 10 minutes
  - No captured TV broadcast (_broadcast) contains the note text, and the
    stats that reach the phones don't either.
  - No generate calls inside the quota window.
  - The note goes when the window ends (time.time patched forward).

Interface assumptions beyond the plan's wording (flag in the QA report):
  - The worker calls the generator as the module attribute
    `scene_gen.generate(...)` (the plan: "scene_gen.generate is stubbed"),
    so patching the attribute on the sys.modules["scene_gen"] the app uses
    reaches it. Arguments are not inspected here (S9 checks the prompt).
  - Quota and note state live in module globals of scene_images and/or the
    app. Each test re-imports the app with fresh scene_images / scene_gen
    modules (sys.modules patched), so no state leaks between tests and no
    reset API is assumed.
  - Clock: the window and the 10-minute note are compared against
    time.time() (the app's _time.time and scene_images' time.time), so
    patching time.time moves past them. "End of the day" for a daily quota
    without retry_after is taken as at most 26 h away (any time zone).
  - Generation is on by default when a key exists (D17, S8); turning it off
    is POST /display-config {"scenes": {"generate": false}} (S8).
  - A failure is reflected in _current_stats["place"] (pending False); a
    TV broadcast for the failure is not required.
  - The quota note's N is round() or ceil() of the hours left.

No network: the key env vars are cleared, the key file points nowhere,
urllib.request.urlopen raises, and scene_gen.generate is a stub. A fake key
is set through GEMINI_API_KEY where a key must exist.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
SCENES_JS = DISPLAY / "static" / "layout" / "scenes.js"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SCRIPTS))

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 64
FAKE_KEY = "test-fake-key-S10-not-real"
DAILY_ID = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
MINUTE_ID = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"

NOTE_OFF = "Scene pictures are off — places show the mood layer only."
NOTE_NO_KEY_PREFIX = "No Gemini key — "
NOTE_QUOTA_RE = re.compile(r"^Gemini's image quota is used up — back in about (\d+) (?:h|min)$")  # minutes under an hour (review nit)
NOTE_ERROR_PREFIX = "Couldn't make the picture for "
# Any of these in a TV broadcast or in the phones' stats is a leak (D7).
NOTE_MARKERS = ("Scene pictures are off", "No Gemini key", "image quota",
                "Couldn't make the picture", "mood layer only")

CRYPT, ABBEY, SQUARE = "The Flooded Crypt", "The Ruined Abbey", "Town Square"
LOOK = "a half-submerged stone hall, pillars in black water"

WAIT = 5.0          # seconds to wait for the worker
QUIET = 0.6         # seconds to watch for a call that must not happen


def _no_network(*_a, **_k):
    raise AssertionError("network call attempted in a scene test")


class _Stub:
    """Stands in for scene_gen.generate: records calls, then raises or
    returns whatever `script` says (the last entry repeats)."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, *a, **k):
        with self.lock:
            self.calls.append((a, k))
            n = len(self.calls)
        act = self.script[min(n, len(self.script)) - 1] if self.script else PNG
        if isinstance(act, BaseException):
            raise act
        return act

    @property
    def n(self):
        with self.lock:
            return len(self.calls)


def _wait(pred, timeout=WAIT):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


class _QuotaCase(unittest.TestCase):
    """A fresh app (and fresh scene_images / scene_gen) per test, broadcasts
    captured, a real campaign under the sandboxed root, no network."""

    def setUp(self):
        # No key anywhere, no network.
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("DND_TTS_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        net = mock.patch("urllib.request.urlopen", side_effect=_no_network)
        net.start()
        self.addCleanup(net.stop)

        # Fresh scene modules for this test only.
        mods = mock.patch.dict(sys.modules)
        mods.start()
        self.addCleanup(mods.stop)
        for m in ("scene_images", "scene_gen"):
            sys.modules.pop(m, None)
        spec = importlib.util.spec_from_file_location(
            f"dnd_display_app_s10_{uuid.uuid4().hex[:6]}", str(DISPLAY / "dnd-display-app.py"))
        app = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(app)
        except ImportError as exc:                    # flask/deps absent
            raise unittest.SkipTest(f"display app not importable: {exc}")
        app._token_ok = lambda: True
        self.app = app
        self.client = app.app.test_client()
        self.scene_gen = importlib.import_module("scene_gen")
        self.tts = importlib.import_module("tts")

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = pathlib.Path(self._tmp.name)
        tts_mods = [self.tts]
        if getattr(app, "_tts", None) is not None and app._tts is not self.tts:
            tts_mods.append(app._tts)
        for t in tts_mods:
            p = mock.patch.object(t, "KEY_FILE", root / "no-such-dir" / "tts.key")
            p.start()
            self.addCleanup(p.stop)

        self.name = f"s10-{uuid.uuid4().hex[:8]}"
        camps = pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"]) / "campaigns"
        self.camp = camps / self.name
        self.camp.mkdir(parents=True, exist_ok=True)
        (self.camp / "state.md").write_text("# State\n", encoding="utf-8")
        self.addCleanup(shutil.rmtree, str(self.camp), True)
        camp_file = root / ".campaign"
        camp_file.write_text(self.name, encoding="utf-8")
        self.stats_file = root / "stats.json"
        self.sent = []
        for p in (mock.patch.object(app, "_broadcast", side_effect=self.sent.append),
                  mock.patch.object(app, "_persist_log"),
                  mock.patch.object(app, "_persist_tail"),
                  mock.patch.object(app, "CAMP_FILE", str(camp_file)),
                  mock.patch.object(app, "STATS_FILE", str(self.stats_file))):
            p.start()
            self.addCleanup(p.stop)
        app._current_stats.pop("place", None)

    # helpers -------------------------------------------------------------

    def give_key(self):
        os.environ["GEMINI_API_KEY"] = FAKE_KEY

    def stub(self, *script):
        s = _Stub(*script)
        p = mock.patch.object(self.scene_gen, "generate", side_effect=s)
        p.start()
        self.addCleanup(p.stop)
        return s

    def err(self, msg, status=0, quota_id="", retry_after=0.0):
        return self.scene_gen.SceneGenError(msg, status=status, quota_id=quota_id,
                                            retry_after=retry_after)

    def arrive(self, name):
        r = self.client.post("/stats", data=json.dumps({"place": {"name": name, "look": LOOK}}),
                             content_type="application/json")
        self.assertIn(r.status_code, (200, 204), r.get_data(as_text=True))

    def place(self):
        return self.app._current_stats.get("place")

    def settled(self, name):
        p = self.place()
        return isinstance(p, dict) and p.get("name") == name and p.get("pending") is False

    def note(self):
        r = self.client.get("/display-config")
        self.assertEqual(r.status_code, 200)
        status = r.get_json().get("status") or {}
        return status.get("scene_note") or ""

    def wait_note(self, pred=bool):
        got = []
        _wait(lambda: (got.append(self.note()) or pred(got[-1])))
        return got[-1] if got else ""

    def assert_ends_mood_only(self, name, stub=None, calls=1):
        """The place ends pending false, image null (AC8), in memory and on disk."""
        if stub is not None:
            _wait(lambda: stub.n >= calls and self.settled(name))
            self.assertEqual(stub.n, calls, f"generate calls: {stub.calls!r}")
        else:
            _wait(lambda: self.settled(name))
        p = self.place()
        self.assertIsInstance(p, dict, f"no place in stats: {self.app._current_stats!r}")
        self.assertEqual(p.get("name"), name)
        self.assertIs(p.get("pending"), False, f"place still pending: {p!r}")
        self.assertIsNone(p.get("image"), f"place has an image: {p!r}")
        if self.stats_file.exists():
            self.assertTrue(_wait(lambda: (json.loads(self.stats_file.read_text(encoding="utf-8"))
                                           .get("place") or {}).get("pending") is False),
                            "stats.json still has the place pending")

    def assert_tv_never_told(self, note):
        """D7: nothing on the TV. No broadcast carries the note; neither do
        the stats the phones get."""
        self.assertTrue(note)
        for ev in self.sent:
            blob = json.dumps(ev, default=str, ensure_ascii=False)
            self.assertNotIn(note, blob, f"TV broadcast carries the note: {ev!r}")
            for m in NOTE_MARKERS:
                self.assertNotIn(m, blob, f"TV broadcast carries a scene note: {ev!r}")
        blob = json.dumps(self.app._current_stats, default=str, ensure_ascii=False)
        for m in NOTE_MARKERS + (note,):
            self.assertNotIn(m, blob, "the phones' stats carry the scene note")
        if self.stats_file.exists():
            text = self.stats_file.read_text(encoding="utf-8")
            for m in NOTE_MARKERS + (note,):
                self.assertNotIn(m, text, "stats.json carries the scene note")

    def assert_no_call(self, stub, before):
        time.sleep(QUIET)
        self.assertEqual(stub.n, before, f"a request was made inside the quota window: {stub.calls!r}")


# ─── Daily quota (429 ... PerDay ...) ─────────────────────────────────────────

class TestDailyQuota(_QuotaCase):

    def _hit(self, retry_after=33703.0):
        self.give_key()
        s = self.stub(self.err("Google answered 429", 429, DAILY_ID, retry_after), PNG)
        t0 = time.time()
        self.arrive(CRYPT)
        self.assert_ends_mood_only(CRYPT, s)
        return s, t0

    # AC8: place ends mood only; the note names the quota and the hours
    def test_quota_note_text(self):
        s, _ = self._hit()
        note = self.wait_note(lambda n: bool(NOTE_QUOTA_RE.match(n)))
        m = NOTE_QUOTA_RE.match(note)
        self.assertIsNotNone(m, f"scene_note = {note!r}")
        self.assertIn(int(m.group(1)), (9, 10), f"33703 s should read about 9 h: {note!r}")

    # D7: the note is for the laptop only
    def test_quota_note_never_on_tv(self):
        self._hit()
        note = self.wait_note(lambda n: bool(NOTE_QUOTA_RE.match(n)))
        self.arrive(ABBEY)        # a second arrival while out of quota
        self.assert_tv_never_told(note)

    # S10: no requests while the quota window lasts
    def test_no_calls_inside_window(self):
        s, t0 = self._hit()
        self.arrive(ABBEY)
        self.assert_no_call(s, 1)
        self.assert_ends_mood_only(ABBEY)
        with mock.patch("time.time", return_value=t0 + 33703 - 600):
            self.arrive(SQUARE)
            self.assert_no_call(s, 1)
            self.assert_ends_mood_only(SQUARE)
            self.assertRegex(self.note(), NOTE_QUOTA_RE)

    # S10: the note goes when the window ends, and requests resume
    def test_note_goes_when_window_ends(self):
        s, t0 = self._hit()
        self.assertRegex(self.wait_note(lambda n: bool(NOTE_QUOTA_RE.match(n))), NOTE_QUOTA_RE)
        with mock.patch("time.time", return_value=t0 + 33703 + 120):
            self.assertEqual(self.note(), "", "the quota note outlived the window")
            self.arrive(ABBEY)
            self.assertTrue(_wait(lambda: s.n >= 2), "no request after the window ended")
            self.assertEqual(s.n, 2)

    # quota_until from the end of the day when Google gives no retry_after
    def test_daily_without_retry_after_waits_to_end_of_day(self):
        s, t0 = self._hit(retry_after=0.0)
        note = self.wait_note(lambda n: bool(NOTE_QUOTA_RE.match(n)))
        m = NOTE_QUOTA_RE.match(note)
        self.assertIsNotNone(m, f"scene_note = {note!r}")
        self.assertTrue(1 <= int(m.group(1)) <= 24, note)
        # Still out a minute later (not a 60 s retry) ...
        with mock.patch("time.time", return_value=t0 + 60):
            self.arrive(ABBEY)
            self.assert_no_call(s, 1)
            self.assertRegex(self.note(), NOTE_QUOTA_RE)
        # ... and back after the day is over.
        with mock.patch("time.time", return_value=t0 + 26 * 3600):
            self.assertEqual(self.note(), "")
            self.arrive(SQUARE)
            self.assertTrue(_wait(lambda: s.n >= 2), "no request after the day ended")


# ─── A short (per-minute) 429 ────────────────────────────────────────────────

class TestShortQuota(_QuotaCase):

    # S10: the window comes from retry_after; no calls inside it
    def test_per_minute_window_from_retry_after(self):
        self.give_key()
        s = self.stub(self.err("Google answered 429", 429, MINUTE_ID, 40.0), PNG)
        t0 = time.time()
        self.arrive(CRYPT)
        self.assert_ends_mood_only(CRYPT, s)
        with mock.patch("time.time", return_value=t0 + 10):
            self.arrive(ABBEY)
            self.assert_no_call(s, 1)
            self.assert_ends_mood_only(ABBEY)
        with mock.patch("time.time", return_value=t0 + 120):
            self.arrive(SQUARE)
            self.assertTrue(_wait(lambda: s.n >= 2), "no request after a 40 s window")
            self.assertNotRegex(self.note(), NOTE_QUOTA_RE)


# ─── An error: a 500, a network error, any exception ─────────────────────────

class TestError(_QuotaCase):

    def _fail(self, exc):
        self.give_key()
        s = self.stub(exc, PNG)
        t0 = time.time()
        self.arrive(CRYPT)
        self.assert_ends_mood_only(CRYPT, s)
        note = self.wait_note(lambda n: n.startswith(NOTE_ERROR_PREFIX))
        return s, t0, note

    def _assert_error_note(self, note, name=CRYPT):
        self.assertTrue(note.startswith(f"{NOTE_ERROR_PREFIX}{name} ("), f"scene_note = {note!r}")
        self.assertTrue(note.endswith(")"), f"scene_note = {note!r}")
        reason = note[len(f"{NOTE_ERROR_PREFIX}{name} ("):-1]
        self.assertTrue(reason.strip(), f"no reason in {note!r}")
        self.assertNotIn(FAKE_KEY, note)

    # AC8: a 500 -> mood only, a note with the place and a reason
    def test_500_note(self):
        _, _, note = self._fail(self.err("Google answered 500", 500))
        self._assert_error_note(note)
        self.assertIn("500", note)

    # AC8: a network error
    def test_network_error_note(self):
        _, _, note = self._fail(self.err("couldn't reach Google (timed out)"))
        self._assert_error_note(note)

    # D7: the error note is never on the TV
    def test_error_note_never_on_tv(self):
        _, _, note = self._fail(self.err("Google answered 500", 500))
        self.assert_tv_never_told(note)

    # S10: the error note lasts 10 minutes
    def test_error_note_lasts_ten_minutes(self):
        _, t0, note = self._fail(self.err("Google answered 500", 500))
        self._assert_error_note(note)
        with mock.patch("time.time", return_value=t0 + 9 * 60):
            self.assertEqual(self.note(), note, "the error note went before 10 minutes")
        with mock.patch("time.time", return_value=t0 + 11 * 60 + WAIT):
            self.assertEqual(self.note(), "", "the error note outlived 10 minutes")

    # An error is not a quota window: the next place is still requested
    def test_error_opens_no_window(self):
        s, _, _ = self._fail(self.err("Google answered 500", 500))
        self.arrive(ABBEY)
        self.assertTrue(_wait(lambda: s.n >= 2), "a 500 stopped later requests")

    # S9 limits: every exception is caught; the worker lives on
    def test_unexpected_exception_caught(self):
        self.give_key()
        s = self.stub(RuntimeError("boom"), PNG)
        self.arrive(CRYPT)
        self.assert_ends_mood_only(CRYPT, s)
        note = self.wait_note(lambda n: n.startswith(NOTE_ERROR_PREFIX))
        self._assert_error_note(note)
        self.arrive(ABBEY)
        self.assertTrue(_wait(lambda: s.n >= 2), "the worker died after an exception")


# ─── Generation off, no key ──────────────────────────────────────────────────

class TestOffAndNoKey(_QuotaCase):

    def _set_generate(self, on):
        r = self.client.post("/display-config", data=json.dumps({"scenes": {"generate": on}}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    # AC8, D7: generation off -> mood only, the off note, no request
    def test_off_note(self):
        self.give_key()
        s = self.stub(PNG)
        self._set_generate(False)
        self.arrive(CRYPT)
        self.assert_no_call(s, 0)
        self.assert_ends_mood_only(CRYPT)
        self.assertEqual(self.wait_note(lambda n: n == NOTE_OFF), NOTE_OFF)

    def test_off_note_never_on_tv(self):
        self.give_key()
        self.stub(PNG)
        self._set_generate(False)
        self.arrive(CRYPT)
        note = self.wait_note(lambda n: n == NOTE_OFF)
        self.assertEqual(note, NOTE_OFF)
        self.assert_tv_never_told(note)

    def test_off_note_goes_when_turned_on(self):
        self.give_key()
        self.stub(PNG)
        self._set_generate(False)
        self.arrive(CRYPT)
        self.assertEqual(self.wait_note(lambda n: n == NOTE_OFF), NOTE_OFF)
        self._set_generate(True)
        self.assertNotEqual(self.note(), NOTE_OFF)

    # D17: on by default, but no key -> mood only and the no-key note
    def test_no_key_note(self):
        s = self.stub(PNG)
        self.arrive(CRYPT)
        self.assert_no_call(s, 0)
        self.assert_ends_mood_only(CRYPT)
        note = self.wait_note(lambda n: n.startswith(NOTE_NO_KEY_PREFIX))
        self.assertTrue(note.startswith(NOTE_NO_KEY_PREFIX), f"scene_note = {note!r}")
        self.assertGreater(len(note), len(NOTE_NO_KEY_PREFIX), "the no-key note says nothing after the dash")
        self.assert_tv_never_told(note)

    # A key and generation on, nothing wrong: no note
    def test_no_note_when_all_is_well(self):
        self.give_key()
        s = self.stub(PNG)
        self.arrive(CRYPT)
        _wait(lambda: s.n >= 1 and self.settled(CRYPT))
        self.assertEqual(s.n, 1)
        self.assertEqual(self.note(), "")


# ─── The laptop page renders the note ────────────────────────────────────────

class TestLayoutRendersNote(unittest.TestCase):

    def test_scenes_js_renders_scene_note(self):
        self.assertTrue(SCENES_JS.exists(), f"{SCENES_JS} does not exist (S8 not built)")
        js = SCENES_JS.read_text(encoding="utf-8")
        self.assertIn("scene_note", js, "scenes.js never reads status.scene_note")
        self.assertIn("lp-scene-note", js, "scenes.js never writes #lp-scene-note")
        self.assertIn("textContent", js, "the note (it holds a place name) should be set as text")


if __name__ == "__main__":
    unittest.main()


class TestQuotaWindowMaths(unittest.TestCase):
    """Review S1, S2: every 429 leaves a laptop note; the daily window ends at
    Gemini's reset (midnight Pacific), not local midnight."""

    def setUp(self):
        import importlib
        self.si = importlib.reload(importlib.import_module("scene_images"))

    def _exc(self, quota_id, retry):
        import scene_gen
        return scene_gen.SceneGenError("Google answered 429", status=429,
                                       quota_id=quota_id, retry_after=retry)

    def test_per_minute_429_has_a_note(self):
        now = 1_800_000_000.0
        self.si.record_failure("The Crypt", self._exc("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", 40), now)
        note = self.si.status(True, True, now + 1)
        self.assertIn("limiting image requests", note)
        self.assertIn("min", note)
        self.assertEqual(self.si.status(True, True, now + 60), "")

    def test_daily_without_retry_ends_at_0800_utc(self):
        now = 1_800_000_000.0                       # 2027-01-15 08:00 UTC is a boundary check below
        self.si.record_failure("The Crypt", self._exc("GenerateRequestsPerDayPerProjectPerModel-FreeTier", 0), now)
        until = self.si._state["quota_until"]
        self.assertGreater(until, now)
        self.assertLessEqual(until - now, 86400)
        self.assertEqual(int(until) % 86400, 8 * 3600)

    def test_short_daily_window_says_minutes(self):
        now = 1_800_000_000.0
        self.si.record_failure("The Crypt", self._exc("GenerateRequestsPerDayPerProjectPerModel-FreeTier", 600), now)
        self.assertIn("10 min", self.si.status(True, True, now))
