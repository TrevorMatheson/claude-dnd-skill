"""Scene backgrounds S9: generation in the background, saved and reused.

Covers AC3 (a new place starts generation in the background; /stats never
waits), AC4 (saved to <campaign>/scenes/<slug>.png and reused with no new
request; the user's file wins; Claude can force a new picture with renew),
AC5 (every request carries the campaign's style and says no people, no
creatures, no text), D3, D5 and D17 (generation is on by default whenever a
key exists; the first picture creates scenes/).

scene_gen.generate is replaced by a stub that records each call and blocks on
a threading.Event until the test releases it. The network is never touched:
DND_TTS_KEY / GEMINI_API_KEY are cleared, tts.KEY_FILE points at a missing
file, and urllib.request.urlopen raises. Tests that need "a key exists" set a
fake GEMINI_API_KEY in the (restored) environment.

Interface assumptions beyond the plan's wording (flagged in the QA report):
  - The worker calls scene_gen.generate through the module attribute at call
    time (so patching scene_gen.generate stubs it), as
    generate(look, style, ...) with `style` the campaign style's prompt text
    (positional or keyword).
  - The ready event is a top-level broadcast {"place": {name, slug, image,
    pending: False}}; `image` is "/scene-image/<slug>.png?v=<int mtime>".
  - The manifest is <campaign>/scenes/generated.json,
    {slug: {"sha256": <hex of the file bytes>, "made": ..., "style": ...}}.
  - While a renewal runs, stats.place keeps the old image with pending: True
    (plan S9: "the old one shows until the new one is ready").
  - With no key or generation off, no scenes/ folder is created (D14).
  - S8's display.json "scenes": {"generate": bool, "style": id}; default
    generate True, style "painterly" (display_config.SCENE_STYLES).
  - scene_gen.build_request(look, style) refuses an empty style (TypeError
    or ValueError): "the campaign style becomes required".
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import sys
import threading
import time
import unittest
from unittest import mock

from tests.test_scene_places import (  # noqa: E402  (same sandbox + app setup)
    DISPLAY, JPEG, LOOK, PNG, _AppCase,
)

sys.path.insert(0, str(DISPLAY))
import scene_gen  # noqa: E402
import tts  # noqa: E402

PNG1 = PNG + b"generated-one"
PNG2 = PNG + b"generated-two-renewed"
PNG3 = PNG + b"users-own-edit"
FAKE_KEY = "test-key-not-real-0000"
SLUG = "the-flooded-crypt"
NAME = "The Flooded Crypt"

# D17's one preset, "Painterly, muted" (the trial's style).
D17_STYLE = "painterly fantasy illustration, muted colours, soft atmospheric light, dark edges"


def _style_prompt() -> str:
    """The campaign's default style prompt (S8's SCENE_STYLES), or D17's text."""
    try:
        import display_config
        styles = getattr(display_config, "SCENE_STYLES", None)
        if isinstance(styles, dict) and "painterly" in styles:
            return styles["painterly"]["prompt"]
    except Exception:
        pass
    return D17_STYLE


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class _Stub:
    """Stands in for scene_gen.generate. Blocks on `gate` until released."""

    def __init__(self, images, block=True):
        self.images = list(images)
        self.calls = []
        self.bodies = []
        self.done = 0
        self.started = threading.Event()
        self.gate = threading.Event()
        if not block:
            self.gate.set()
        self._lock = threading.Lock()

    def __call__(self, look=None, style=None, *args, **kwargs):
        with self._lock:
            n = len(self.calls)
            self.calls.append({"look": look, "style": style, "args": args, "kwargs": kwargs})
        try:
            body = scene_gen.build_request(look, style)
            self.bodies.append(json.dumps(body, ensure_ascii=False))
        except Exception as exc:              # recorded; asserted in AC5 tests
            self.bodies.append(exc)
        self.started.set()
        try:
            if not self.gate.wait(10):
                raise scene_gen.SceneGenError("test stub was never released")
            return self.images[min(n, len(self.images) - 1)]
        finally:
            with self._lock:
                self.done += 1


class _GenCase(_AppCase):
    """_AppCase (fresh campaign, broadcasts captured) plus a stubbed generator."""

    KEY = True        # put a fake key in the environment
    BLOCK = True      # the stub waits for the test to release it
    IMAGES = (PNG1, PNG2)

    def setUp(self):
        super().setUp()
        missing = pathlib.Path(os.environ["DND_TEST_SANDBOX"]) / "no-such-dir" / "tts.key"
        p = mock.patch.object(tts, "KEY_FILE", missing)
        p.start()
        self.addCleanup(p.stop)
        if self.KEY:
            os.environ["GEMINI_API_KEY"] = FAKE_KEY
        self.stub = _Stub(self.IMAGES, block=self.BLOCK)
        p = mock.patch.object(scene_gen, "generate", self.stub)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._drain)        # runs before the patches stop

    def _drain(self):
        """Let any job finish while the stub and broadcast capture are in place."""
        self.stub.gate.set()
        end = time.monotonic() + 5
        while time.monotonic() < end:
            if self.stub.done >= len(self.stub.calls):
                break
            time.sleep(0.01)
        time.sleep(0.2)                     # on_done / _scene_ready after the stub returns

    # ── helpers ──────────────────────────────────────────────────────────

    def _wait(self, cond, timeout=5.0, msg="condition not met"):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return
            time.sleep(0.01)
        self.fail(msg)

    def _settle(self, secs=0.3):
        time.sleep(secs)

    def _arrive(self, name=NAME, look=LOOK, **extra):
        place = {"name": name, "look": look}
        place.update(extra)
        r = self._post({"place": place})
        self.assertEqual(r.status_code, 204)
        return r

    def _place_events(self, slug=None):
        """Top-level {"place": ...} broadcasts (the worker's ready event)."""
        out = [e["place"] for e in self.sent
               if isinstance(e, dict) and "place" in e and "stats" not in e]
        if slug is not None:
            out = [p for p in out if isinstance(p, dict) and p.get("slug") == slug]
        return out

    def _first_stats_place(self):
        for ev in self._stats_events():
            if "place" in ev:
                return ev["place"]
        self.fail(f"no stats broadcast with a place; sent = {self.sent!r}")

    def _png_path(self, slug=SLUG):
        return self.camp / "scenes" / f"{slug}.png"

    def _manifest(self):
        p = self.camp / "scenes" / "generated.json"
        self.assertTrue(p.is_file(), "scenes/generated.json was not written")
        return json.loads(p.read_text(encoding="utf-8"))

    def _generate_once(self, image=PNG1):
        """Arrive, let the picture be made, return the ready event's place."""
        self.stub.gate.set()
        self._arrive()
        self._wait(lambda: self._place_events(SLUG), msg="no ready {'place'} broadcast")
        self._wait(lambda: self.stub.done >= 1, msg="the stub never returned")
        return self._place_events(SLUG)[-1]

    def _write_display_json(self, scenes):
        (self.camp / "display.json").write_text(
            json.dumps({"version": 1, "scenes": scenes}), encoding="utf-8")


# ─── AC3: generation in the background, narration never waits ───────────────

class TestGenerateInBackground(_GenCase):

    # AC3 / plan S9 Done when: /stats < 0.3 s while the stub blocks; first
    # broadcast pending:true image:null; on release one {"place"} broadcast,
    # the file in scenes/, a manifest entry.
    def test_new_place_generates_in_background(self):
        t0 = time.perf_counter()
        self._arrive()
        dt = time.perf_counter() - t0
        self.assertLess(dt, 0.3, f"/stats took {dt:.3f}s while generation was blocked")

        self._wait(self.stub.started.is_set, msg="generation never started (scene_gen.generate not called)")
        self.assertEqual(len(self.stub.calls), 1)
        first = self._first_stats_place()
        self.assertEqual(first["slug"], SLUG)
        self.assertIs(first["pending"], True, first)
        self.assertIsNone(first["image"], first)
        self.assertEqual(self._place_events(), [], "ready event sent before the picture was made")
        self.assertFalse(self._png_path().exists(), "file written before the stub returned")

        self.stub.gate.set()
        self._wait(lambda: self._place_events(SLUG), msg="no {'place'} broadcast after the picture was made")
        self._settle()
        evs = self._place_events()
        self.assertEqual(len(evs), 1, f"expected exactly one place event, got {evs!r}")
        ev = evs[0]
        self.assertEqual(ev["name"], NAME)
        self.assertIs(ev["pending"], False, ev)
        self.assertRegex(ev["image"], r"^/scene-image/the-flooded-crypt\.png\?v=\d+$")
        self.assertNotIn("look", ev)

        self.assertEqual(self._png_path().read_bytes(), PNG1)
        entry = self._manifest().get(SLUG)
        self.assertIsInstance(entry, dict, "no manifest entry for the slug")
        self.assertEqual(entry.get("sha256"), _sha(PNG1))
        self.assertIn("made", entry)
        self.assertIn("style", entry)

        # persisted: current stats and stats.json carry the image
        cur = self.app._current_stats.get("place")
        self.assertEqual(cur["image"], ev["image"])
        self.assertIs(cur["pending"], False)
        stored = json.loads(self.stats_file.read_text(encoding="utf-8"))
        self.assertEqual(stored["place"]["image"], ev["image"])
        self.assertIs(stored["place"]["pending"], False)

        r = self.client.get(ev["image"])
        try:
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.data, PNG1)
        finally:
            r.close()

        for e in self.sent:
            self.assertNotIn("SECRET-LOOK-7731", json.dumps(e, default=str))

    # D17 / plan "Needs a spec decision" 3: generation on, the first picture
    # creates scenes/ in a campaign that had none.
    def test_first_picture_creates_scenes_folder(self):
        self.assertFalse((self.camp / "scenes").exists())
        self._generate_once()
        self.assertTrue((self.camp / "scenes").is_dir())
        self.assertEqual(self._png_path().read_bytes(), PNG1)

    # Atomic write: no temp files left beside the picture and manifest.
    def test_no_temp_files_left(self):
        self._generate_once()
        self._settle()
        names = sorted(p.name for p in (self.camp / "scenes").iterdir())
        self.assertEqual(names, ["generated.json", f"{SLUG}.png"])

    # De-duplicating queue: the same place named twice while pending is one job.
    def test_same_place_twice_while_pending_is_one_call(self):
        self._arrive()
        self._wait(self.stub.started.is_set, msg="generation never started")
        self._arrive(look="the same crypt, described again")
        place = self.app._current_stats.get("place")
        self.assertIs(place["pending"], True)
        self.assertIsNone(place["image"])
        self.stub.gate.set()
        self._wait(lambda: self._place_events(SLUG), msg="no ready broadcast")
        self._settle()
        self.assertEqual(len(self.stub.calls), 1, self.stub.calls)

    # A single worker: a second new place waits for the first job, then runs.
    def test_single_worker_one_job_at_a_time(self):
        self._arrive()
        self._wait(self.stub.started.is_set, msg="generation never started")
        self._arrive(name="The Old Mill", look="a broken waterwheel over a dry race")
        self._settle()
        self.assertEqual(len(self.stub.calls), 1, "a second job ran while the first was still running")
        self.stub.gate.set()
        self._wait(lambda: len(self.stub.calls) == 2 and self.stub.done == 2,
                   msg="the queued second place was never generated")
        self._wait(lambda: self._place_events("the-old-mill"), msg="no ready broadcast for the current place")
        self._settle()
        self.assertEqual(self._place_events(SLUG), [], "a place the party left was broadcast")
        self.assertEqual(self.app._current_stats["place"]["slug"], "the-old-mill")
        self.assertTrue(self._png_path().is_file(), "the left place's picture was not saved for later")


# ─── AC4: saved, reused, the user's file wins, renew ─────────────────────────

class TestReuseAndRenew(_GenCase):
    BLOCK = False

    # AC4: a second arrival makes zero calls and shows the saved picture.
    def test_second_arrival_makes_no_call(self):
        ev = self._generate_once()
        self._settle()
        self._post({"place": None})
        self._arrive(look="different words this time")
        self._settle()
        self.assertEqual(len(self.stub.calls), 1)
        place = self.app._current_stats["place"]
        self.assertEqual(place["image"], ev["image"])
        self.assertIs(place["pending"], False)
        self.assertIs(self._last_place()["pending"], False)

    # AC4: the user's file makes zero calls.
    def test_users_file_makes_no_call(self):
        self._scene_file("The Flooded Crypt.jpg", JPEG)
        self._arrive()
        self._settle()
        self.assertEqual(self.stub.calls, [])
        place = self._last_place()
        self.assertIs(place["pending"], False)
        self.assertIn("The%20Flooded%20Crypt.jpg", place["image"])

    # AC4: renew makes one call, replaces the file and changes ?v=.
    def test_renew_generated_picture(self):
        self._generate_once()
        self._settle()
        old = self._png_path()
        os.utime(old, (time.time() - 1000, time.time() - 1000))
        self._arrive()                                   # re-read: old ?v=
        self._settle()
        self.assertEqual(len(self.stub.calls), 1)
        url_old = self.app._current_stats["place"]["image"]
        self.assertIsNotNone(url_old)
        n_events = len(self._place_events(SLUG))

        self.stub.gate.clear()
        self.stub.started.clear()
        self._arrive(look="the same hall, now scorched by fire", renew=True)
        self._wait(self.stub.started.is_set, msg="renew did not start a new picture")
        place = self.app._current_stats["place"]
        self.assertEqual(place["image"], url_old, "the old picture must show until the new one is ready")
        self.assertIs(place["pending"], True)

        self.stub.gate.set()
        self._wait(lambda: len(self._place_events(SLUG)) > n_events, msg="no ready broadcast after renew")
        self._settle()
        self.assertEqual(len(self.stub.calls), 2)
        self.assertIn("scorched", self.stub.calls[1]["look"] or "")
        new = self._place_events(SLUG)[-1]
        self.assertIs(new["pending"], False)
        self.assertRegex(new["image"], r"^/scene-image/the-flooded-crypt\.png\?v=\d+$")
        self.assertNotEqual(new["image"], url_old, "?v= did not change after renew")
        self.assertEqual(self._png_path().read_bytes(), PNG2)
        self.assertEqual(self._manifest()[SLUG]["sha256"], _sha(PNG2))
        self.assertEqual(self.app._current_stats["place"]["image"], new["image"])

    # AC4: renew against the user's file makes zero calls.
    def test_renew_users_file_makes_no_call(self):
        for fname, data in (("The Flooded Crypt.jpg", JPEG), (f"{SLUG}.png", PNG3)):
            with self.subTest(file=fname):
                for p in list((self.camp / "scenes").glob("*")) if (self.camp / "scenes").exists() else []:
                    p.unlink()
                f = self._scene_file(fname, data)
                self._arrive(renew=True)
                self._settle()
                self.assertEqual(self.stub.calls, [], f"renew regenerated the user's {fname}")
                place = self._last_place()
                self.assertIs(place["pending"], False)
                self.assertIsNotNone(place["image"])
                self.assertEqual(f.read_bytes(), data, "the user's file was changed")

    # Plan Shape: a generated file the user has since replaced (hash no longer
    # recorded) counts as the user's, so renew leaves it alone.
    def test_renew_after_user_replaced_generated_file(self):
        self._generate_once()
        self._settle()
        self._png_path().write_bytes(PNG3)
        self._arrive(renew=True)
        self._settle()
        self.assertEqual(len(self.stub.calls), 1, "renew regenerated a file the user replaced")
        self.assertEqual(self._png_path().read_bytes(), PNG3)


# ─── D5: moving on before the picture is ready ───────────────────────────────

class TestMovingOn(_GenCase):

    def test_leave_before_done_saves_but_does_not_show(self):
        self._scene_file("the-old-mill.png", PNG3)
        self._arrive()
        self._wait(self.stub.started.is_set, msg="generation never started")
        self._arrive(name="The Old Mill", look="a broken waterwheel")
        mill = self.app._current_stats["place"]
        self.assertEqual(mill["slug"], "the-old-mill")
        self.stub.gate.set()
        self._wait(lambda: self._png_path().is_file() and
                   (self.camp / "scenes" / "generated.json").is_file(),
                   msg="the left place's picture was not saved for later")
        self._wait(lambda: SLUG in self._manifest(), msg="no manifest entry for the left place")
        self._settle()
        self.assertEqual(self._place_events(SLUG), [], "broadcast a picture for a place the party left")
        self.assertEqual(self.app._current_stats["place"], mill, "the current place changed")
        for ev in self._stats_events():
            pl = ev.get("place")
            if isinstance(pl, dict) and pl.get("slug") == SLUG:
                self.assertIsNone(pl["image"], "a stats broadcast showed the left place's picture")
        stored = json.loads(self.stats_file.read_text(encoding="utf-8"))
        self.assertEqual(stored["place"]["slug"], "the-old-mill")

    def test_leave_to_no_place(self):
        self._arrive()
        self._wait(self.stub.started.is_set, msg="generation never started")
        self._post({"place": None})
        self.stub.gate.set()
        self._wait(self._png_path().is_file, msg="the picture was not saved for later")
        self._settle()
        self.assertEqual(self._place_events(SLUG), [])
        self.assertIsNone(self.app._current_stats.get("place"))


# ─── AC5: every request has the campaign's style and the rules ───────────────

class TestRequestContents(_GenCase):
    BLOCK = False

    def _assert_body(self, body, look):
        self.assertIsInstance(body, str, f"build_request failed in the worker's call: {body!r}")
        low = body.lower()
        self.assertIn(_style_prompt().lower(), low, "the campaign's style prompt is missing")
        self.assertIn("no people", low)
        self.assertIn("no creatures", low)
        self.assertIn("no text", low)
        self.assertIn(look.lower(), low)

    def test_every_captured_request_has_style_and_rules(self):
        places = [(NAME, LOOK), ("The Old Mill", "a broken waterwheel over a dry race"),
                  ("Town Square", "cobbles around a dry fountain at dusk")]
        for name, look in places:
            self._arrive(name=name, look=look)
            self._wait(lambda: self.stub.done >= len(self.stub.calls) and self.stub.calls,
                       msg=f"no request for {name}")
        self._wait(lambda: len(self.stub.calls) == 3 and self.stub.done == 3, msg="not every place was requested")
        for call, body, (_, look) in zip(self.stub.calls, self.stub.bodies, places):
            self.assertIn(_style_prompt(), str(call["style"] or call["kwargs"].get("style")),
                          f"the worker did not pass the campaign style: {call!r}")
            self._assert_body(body, look)

    # D6: an explicitly chosen style is used (the one preset).
    def test_chosen_style_used(self):
        self._write_display_json({"generate": True, "style": "painterly"})
        self._arrive()
        self._wait(lambda: self.stub.done == 1, msg="no request")
        self._assert_body(self.stub.bodies[0], LOOK)

    # Plan S9: scene_gen.build_request -- the campaign style becomes required.
    def test_build_request_requires_style(self):
        with self.assertRaises(TypeError):
            scene_gen.build_request("a cave")                 # pylint: disable=no-value-for-parameter
        for empty in ("", "   ", None):
            with self.subTest(style=empty):
                with self.assertRaises((TypeError, ValueError)):
                    scene_gen.build_request("a cave", empty)

    def test_build_request_has_style_and_rules(self):
        body = json.dumps(scene_gen.build_request("a dripping cave", D17_STYLE)).lower()
        for phrase in (D17_STYLE.lower(), "no people", "no creatures", "no text", "a dripping cave"):
            self.assertIn(phrase, body)


# ─── No key / generation off: zero calls, mood only ──────────────────────────

class TestNoKey(_GenCase):
    KEY = False

    def test_no_key_zero_calls(self):
        self._arrive()
        self._settle()
        self.assertEqual(self.stub.calls, [])
        place = self._last_place()
        self.assertIs(place["pending"], False)
        self.assertIsNone(place["image"])
        self.assertFalse((self.camp / "scenes").exists(), "D14: no picture, no scenes/ folder")


class TestGenerationOff(_GenCase):

    def test_generation_off_zero_calls(self):
        self._write_display_json({"generate": False, "style": "painterly"})
        self._arrive()
        self._settle()
        self.assertEqual(self.stub.calls, [])
        place = self._last_place()
        self.assertIs(place["pending"], False)
        self.assertIsNone(place["image"])
        self.assertFalse((self.camp / "scenes").exists(), "D14: generation off, no scenes/ folder")

    def test_generation_on_explicitly(self):
        self._write_display_json({"generate": True, "style": "painterly"})
        self._arrive()
        self._wait(self.stub.started.is_set, msg="generation on with a key, but no request")
        self.assertIs(self.app._current_stats["place"]["pending"], True)


if __name__ == "__main__":
    unittest.main()


# ─── Review follow-ups ───────────────────────────────────────────────────────

class TestUsersFileBeatsGenerated(_GenCase):
    """D5 / review B1: a picture the user drops in after we made one wins,
    whatever its extension, and renew never touches it."""

    def test_user_jpg_beats_generated_png(self):
        self._generate_once()
        self._settle()
        self._scene_file("The Flooded Crypt.jpg", JPEG)
        import scene_images
        path, source = scene_images.find_image(self.camp, SLUG)
        self.assertEqual(pathlib.Path(path).name, "The Flooded Crypt.jpg")
        self.assertEqual(source, "user")
        self._arrive(renew=True)
        self._settle()
        self.assertEqual(len(self.stub.calls), 1, "renew remade a picture while the user's own was there")
        self.assertIn("The%20Flooded%20Crypt.jpg", self._last_place()["image"])


class TestNoCampaignFolder(_GenCase):
    """Review S5: a place in a campaign folder that isn't there makes nothing."""

    def test_missing_campaign_makes_no_request(self):
        self._post({"campaign": "no-such-campaign-xyz"})
        self._arrive()
        self._settle()
        self.assertEqual(self.stub.calls, [])
        root = pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"]) / "campaigns" / "no-such-campaign-xyz"
        self.assertFalse(root.exists(), "a folder was made for a campaign that doesn't exist")
