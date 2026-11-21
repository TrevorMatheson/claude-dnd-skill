"""Scene backgrounds S2: place images from <campaign>/scenes/, served and stored in stats.

Covers AC4 (a file the user supplies is used), AC9 (no scenes/ folder: mood
only, no errors), D5 (saved and reused; the user's file wins) and D14.

Two layers:
  A) skills/dnd/display/scene_images.py, the pure stdlib module:
       slug(name) -> str | None
       clean_place(obj) -> {"name", "look", "renew"} | None
       find_image(camp_dir, slug) -> (path, source) | None
       image_url(path) -> "/scene-image/<file>?v=<mtime>"
  B) dnd-display-app.py through the Flask test client, with a real campaign
     under the sandboxed DND_CAMPAIGN_ROOT (tests/__init__.py):
       POST /stats {"place": {...}} -> broadcasts stats.place
            = {"name", "slug", "image", "pending": False}, never "look"
       GET  /scene-image/<name>     -> the file, content type from magic bytes

Interface assumptions beyond the plan's wording (flagged in the QA report):
  - find_image's `source` is a string; its value is not pinned here.
  - image_url's ?v= is the file's mtime as an integer (int(st_mtime)).
  - clean_place returns None or a name of at most 80 characters (send.py's
    limit) for an over-long name.
  - Re-sending the same campaign name keeps the place (the plan pops it only
    "when the name changes").

No network: urllib.request.urlopen is patched to raise in every test.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import unittest
import urllib.parse
import uuid
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
SCENE_IMAGES_PY = DISPLAY / "scene_images.py"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SCRIPTS))

# Smallest believable headers for each format (the server sniffs magic bytes).
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 64 + b"\xff\xd9"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 64

LOOK = "a half-submerged stone hall, pillars in black water SECRET-LOOK-7731"
WINDOWS_RESERVED = {"con", "prn", "aux", "nul",
                    *(f"com{i}" for i in range(1, 10)),
                    *(f"lpt{i}" for i in range(1, 10))}

_si_cache: dict = {}


def _no_network(*_a, **_k):
    raise AssertionError("network call attempted in a scene test")


def _load_scene_images(case: unittest.TestCase):
    """Import scene_images.py by path; a missing module is a test failure."""
    if "mod" in _si_cache:
        return _si_cache["mod"]
    if not SCENE_IMAGES_PY.exists():
        case.fail(f"{SCENE_IMAGES_PY} does not exist (S2 not built)")
    spec = importlib.util.spec_from_file_location("scene_images_s2", str(SCENE_IMAGES_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _si_cache["mod"] = mod
    return mod


class _NoNetCase(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("DND_TTS_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        net = mock.patch("urllib.request.urlopen", side_effect=_no_network)
        net.start()
        self.addCleanup(net.stop)


# ─── A) the pure module ──────────────────────────────────────────────────────

class TestSlug(_NoNetCase):
    def setUp(self):
        super().setUp()
        self.si = _load_scene_images(self)

    def test_plain_name(self):
        self.assertEqual(self.si.slug("The Flooded Crypt"), "the-flooded-crypt")

    def test_no_edge_hyphens(self):
        s = self.si.slug("  The Crypt!  ")
        self.assertEqual(s, "the-crypt")

    def test_non_latin_letters_kept(self):
        s = self.si.slug("Крипта Тьмы")
        self.assertIsNotNone(s)
        self.assertIn("крипта", s)
        self.assertIn("тьмы", s)
        s2 = self.si.slug("東の塔")
        self.assertIsNotNone(s2)
        self.assertIn("東", s2)

    def test_windows_reserved_names_made_safe(self):
        for name in ("CON", "con", "PRN", "AUX", "NUL", "COM1", "LPT1"):
            s = self.si.slug(name)
            if s is None:
                continue
            stem = s.split(".")[0].lower()
            self.assertNotIn(stem, WINDOWS_RESERVED, f"slug({name!r}) = {s!r}")

    def test_path_parts_removed(self):
        for name in ("a/../b", "..\\..\\evil", "../../etc/passwd", 'a<b>c:d"e|f?g*h'):
            s = self.si.slug(name)
            if s is None:
                continue
            for bad in ("/", "\\", "..", "<", ">", ":", '"', "|", "?", "*"):
                self.assertNotIn(bad, s, f"slug({name!r}) = {s!r}")
            self.assertFalse(s.startswith("."), s)

    def test_nothing_left_is_none(self):
        for name in ("!!!", "", "   ", "---", "..."):
            self.assertIsNone(self.si.slug(name), repr(name))

    def test_at_most_60(self):
        s = self.si.slug("The Very " + "Long " * 40 + "Hall")
        self.assertIsNotNone(s)
        self.assertLessEqual(len(s), 60)
        self.assertFalse(s.endswith("-"), s)

    def test_lowercase_hyphenated(self):
        s = self.si.slug("Old Mill_Road")
        self.assertEqual(s, s.lower())
        self.assertNotIn(" ", s)


class TestCleanPlace(_NoNetCase):
    def setUp(self):
        super().setUp()
        self.si = _load_scene_images(self)

    def test_good_place(self):
        p = self.si.clean_place({"name": "The Flooded Crypt", "look": LOOK})
        self.assertEqual(p["name"], "The Flooded Crypt")
        self.assertEqual(p["look"], LOOK)
        self.assertIn("renew", p)
        self.assertFalse(p["renew"])

    def test_name_trimmed(self):
        p = self.si.clean_place({"name": "  The Crypt  ", "look": "x"})
        self.assertEqual(p["name"], "The Crypt")

    def test_look_optional(self):
        p = self.si.clean_place({"name": "The Crypt"})
        self.assertIsNotNone(p)
        self.assertIn(p["look"], ("", None))

    def test_bad_shapes_are_none(self):
        for bad in (None, "The Crypt", 42, [], ["The Crypt"], {}, {"name": ""},
                    {"name": "   "}, {"name": 5}, {"name": None}, {"look": "x"},
                    {"name": "!!!"}):
            self.assertIsNone(self.si.clean_place(bad), repr(bad))

    def test_over_long_name(self):
        p = self.si.clean_place({"name": "x" * 5000, "look": "y"})
        if p is not None:
            self.assertLessEqual(len(p["name"]), 80)


class TestFindImage(_NoNetCase):
    def setUp(self):
        super().setUp()
        self.si = _load_scene_images(self)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.camp = pathlib.Path(self._tmp.name) / "camp"
        self.camp.mkdir()
        self.scenes = self.camp / "scenes"

    def _put(self, fname, data=PNG):
        self.scenes.mkdir(exist_ok=True)
        p = self.scenes / fname
        p.write_bytes(data)
        return p

    def _found(self, slug):
        got = self.si.find_image(self.camp, slug)
        if got is None:
            return None
        path, source = got
        self.assertIsInstance(source, str)
        return pathlib.Path(path).name

    def test_missing_folder_is_none(self):
        self.assertIsNone(self.si.find_image(self.camp, "the-flooded-crypt"))
        self.assertIsNone(self.si.find_image(str(self.camp), "the-flooded-crypt"))
        self.assertIsNone(self.si.find_image(self.camp / "nope", "x"))

    def test_scenes_is_a_file_is_none(self):
        self.scenes.write_bytes(b"not a folder")
        self.assertIsNone(self.si.find_image(self.camp, "the-flooded-crypt"))

    def test_exact_slug_file(self):
        self._put("the-flooded-crypt.png")
        self.assertEqual(self._found("the-flooded-crypt"), "the-flooded-crypt.png")

    def test_lenient_user_file(self):
        self._put("The Flooded Crypt.jpg", JPEG)
        self.assertEqual(self._found("the-flooded-crypt"), "The Flooded Crypt.jpg")

    def test_lenient_variants(self):
        self._put("THE_FLOODED_CRYPT.webp", WEBP)
        self.assertEqual(self._found("the-flooded-crypt"), "THE_FLOODED_CRYPT.webp")

    def test_extension_order(self):
        for ext in ("webp", "jpeg", "jpg", "png"):
            self._put(f"crypt.{ext}")
        self.assertEqual(self._found("crypt"), "crypt.png")
        (self.scenes / "crypt.png").unlink()
        self.assertEqual(self._found("crypt"), "crypt.jpg")
        (self.scenes / "crypt.jpg").unlink()
        self.assertEqual(self._found("crypt"), "crypt.jpeg")
        (self.scenes / "crypt.jpeg").unlink()
        self.assertEqual(self._found("crypt"), "crypt.webp")

    def test_extension_order_across_user_spellings(self):
        self._put("The Crypt.webp", WEBP)
        self._put("the crypt.jpg", JPEG)
        self.assertEqual(self._found("the-crypt"), "the crypt.jpg")

    def test_other_extensions_ignored(self):
        self._put("the-crypt.txt", b"hello")
        self._put("the-crypt.gif", b"GIF89a")
        self._put("the-crypt.json", b"{}")
        self.assertIsNone(self._found("the-crypt"))

    def test_different_place_not_matched(self):
        self._put("the-flooded-crypt-2.png")
        self._put("flooded-crypt.png")
        self.assertIsNone(self._found("the-flooded-crypt"))

    def test_subfolders_not_searched(self):
        (self.scenes / "old").mkdir(parents=True)
        (self.scenes / "old" / "the-crypt.png").write_bytes(PNG)
        self.assertIsNone(self._found("the-crypt"))


class TestImageUrl(_NoNetCase):
    def setUp(self):
        super().setUp()
        self.si = _load_scene_images(self)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def _split(self, url):
        self.assertTrue(url.startswith("/scene-image/"), url)
        parts = urllib.parse.urlsplit(url)
        fname = urllib.parse.unquote(parts.path[len("/scene-image/"):])
        q = urllib.parse.parse_qs(parts.query)
        return fname, q

    def test_plain_file(self):
        p = self.dir / "the-crypt.png"
        p.write_bytes(PNG)
        os.utime(p, (1700000000, 1700000000))
        fname, q = self._split(self.si.image_url(p))
        self.assertEqual(fname, "the-crypt.png")
        self.assertEqual(q.get("v"), ["1700000000"])

    def test_spaces_encoded(self):
        p = self.dir / "The Flooded Crypt.jpg"
        p.write_bytes(JPEG)
        os.utime(p, (1700000123, 1700000123))
        url = self.si.image_url(str(p))
        self.assertNotIn(" ", url)
        fname, q = self._split(url)
        self.assertEqual(fname, "The Flooded Crypt.jpg")
        self.assertEqual(q.get("v"), ["1700000123"])

    def test_version_changes_with_file(self):
        p = self.dir / "a.png"
        p.write_bytes(PNG)
        os.utime(p, (1700000000, 1700000000))
        u1 = self.si.image_url(p)
        os.utime(p, (1700009999, 1700009999))
        self.assertNotEqual(u1, self.si.image_url(p))


# ─── B) the app ──────────────────────────────────────────────────────────────

def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_scene", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError as exc:                    # flask/deps absent
        raise unittest.SkipTest(f"display app not importable: {exc}")
    return mod


class _AppCase(_NoNetCase):
    """Fresh app; broadcasts captured; a real campaign under the sandboxed root.

    CAMP_FILE and STATS_FILE point at a temp dir, so the active campaign follows
    what the test writes (and what POST /stats {"campaign"} writes) and
    stats.json can be read back."""

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def setUp(self):
        super().setUp()
        a = self.app
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = pathlib.Path(self._tmp.name)
        self.camps = pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"]) / "campaigns"
        self.name = f"scene-{uuid.uuid4().hex[:8]}"
        self.camp = self._make_campaign(self.name)
        self.camp_file = root / ".campaign"
        self.camp_file.write_text(self.name, encoding="utf-8")
        self.stats_file = root / "stats.json"
        self.sent = []
        patches = [mock.patch.object(a, "_broadcast", side_effect=self.sent.append),
                   mock.patch.object(a, "_persist_log"),
                   mock.patch.object(a, "_persist_tail"),
                   mock.patch.object(a, "CAMP_FILE", str(self.camp_file)),
                   mock.patch.object(a, "STATS_FILE", str(self.stats_file))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        saved = dict(a._current_stats)
        a._current_stats.pop("place", None)

        def _restore():
            a._current_stats.clear()
            a._current_stats.update(saved)
        self.addCleanup(_restore)

    def _make_campaign(self, name):
        camp = self.camps / name
        camp.mkdir(parents=True, exist_ok=True)
        (camp / "state.md").write_text("# State\n", encoding="utf-8")
        self.addCleanup(shutil.rmtree, str(camp), True)
        return camp

    def _scene_file(self, fname, data):
        d = self.camp / "scenes"
        d.mkdir(exist_ok=True)
        p = d / fname
        p.write_bytes(data)
        return p

    def _post(self, body):
        return self.client.post("/stats", data=json.dumps(body), content_type="application/json")

    def _stats_events(self):
        return [e["stats"] for e in self.sent if isinstance(e, dict) and "stats" in e]

    def _last_place(self):
        evs = self._stats_events()
        self.assertTrue(evs, f"no stats broadcast; sent = {self.sent!r}")
        self.assertIn("place", evs[-1], f"stats broadcast has no place: {evs[-1]!r}")
        return evs[-1]["place"]

    def _assert_place_shape(self, place, name):
        self.assertIsInstance(place, dict)
        self.assertEqual(set(place), {"name", "slug", "image", "pending"}, place)
        self.assertEqual(place["name"], name)
        self.assertIs(place["pending"], False)
        self.assertNotIn("look", place)


class TestPlaceThroughStats(_AppCase):

    # AC4, D5: a file the user supplies is used
    def test_user_file_broadcast(self):
        f = self._scene_file("The Flooded Crypt.jpg", JPEG)
        os.utime(f, (1700000500, 1700000500))
        r = self._post({"place": {"name": "The Flooded Crypt", "look": LOOK}})
        self.assertEqual(r.status_code, 204)
        place = self._last_place()
        self._assert_place_shape(place, "The Flooded Crypt")
        self.assertEqual(place["slug"], "the-flooded-crypt")
        img = place["image"]
        self.assertIsInstance(img, str)
        self.assertRegex(img, r"^/scene-image/[^?]+\?v=\d+$")
        parts = urllib.parse.urlsplit(img)
        self.assertEqual(urllib.parse.unquote(parts.path[len("/scene-image/"):]),
                         "The Flooded Crypt.jpg")
        self.assertEqual(urllib.parse.parse_qs(parts.query)["v"], ["1700000500"])

    # AC4: GET on the broadcast URL returns the file
    def test_user_file_served(self):
        self._scene_file("The Flooded Crypt.jpg", JPEG)
        self._post({"place": {"name": "The Flooded Crypt", "look": LOOK}})
        img = self._last_place()["image"]
        r = self.client.get(img)
        try:
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.mimetype, "image/jpeg")
            self.assertEqual(r.data, JPEG)
        finally:
            r.close()

    # look never reaches phones: not in the broadcast, not in stats.json
    def test_look_not_broadcast_or_stored(self):
        self._scene_file("the-flooded-crypt.png", PNG)
        self._post({"place": {"name": "The Flooded Crypt", "look": LOOK}})
        for ev in self.sent:
            self.assertNotIn("SECRET-LOOK-7731", json.dumps(ev, default=str), ev)
        self.assertTrue(self.stats_file.exists(), "stats.json was not written")
        text = self.stats_file.read_text(encoding="utf-8")
        self.assertNotIn("SECRET-LOOK-7731", text)
        stored = json.loads(text)
        self._assert_place_shape(stored.get("place"), "The Flooded Crypt")
        self.assertNotIn("look", self.app._current_stats.get("place") or {})

    def test_stored_in_current_stats(self):
        self._scene_file("the-flooded-crypt.png", PNG)
        self._post({"place": {"name": "The Flooded Crypt", "look": LOOK}})
        self._assert_place_shape(self.app._current_stats.get("place"), "The Flooded Crypt")

    # D5: reused on return, same picture
    def test_return_reuses_image(self):
        self._scene_file("the-flooded-crypt.png", PNG)
        self._post({"place": {"name": "The Flooded Crypt", "look": LOOK}})
        first = self._last_place()["image"]
        self._post({"place": {"name": "the flooded crypt", "look": "different words"}})
        self.assertEqual(self._last_place()["image"], first)

    def test_place_with_other_keys(self):
        self._scene_file("the-flooded-crypt.png", PNG)
        self._post({"world_time": {"day": 2, "hour": 9},
                    "place": {"name": "The Flooded Crypt", "look": LOOK}})
        self._assert_place_shape(self._last_place(), "The Flooded Crypt")
        self.assertEqual(self.app._current_stats.get("world_time"), {"day": 2, "hour": 9})

    def test_place_with_no_matching_file(self):
        self._scene_file("somewhere-else.png", PNG)
        r = self._post({"place": {"name": "The Flooded Crypt", "look": LOOK}})
        self.assertEqual(r.status_code, 204)
        place = self._last_place()
        self._assert_place_shape(place, "The Flooded Crypt")
        self.assertIsNone(place["image"])

    def test_place_null_clears(self):
        self._scene_file("the-flooded-crypt.png", PNG)
        self._post({"place": {"name": "The Flooded Crypt", "look": LOOK}})
        self.assertIsNotNone(self._last_place())
        r = self._post({"place": None})
        self.assertEqual(r.status_code, 204)
        evs = self._stats_events()
        self.assertGreaterEqual(len(evs), 2, "place: null did not broadcast stats")
        self.assertIsNone(evs[-1].get("place"))
        self.assertIsNone(self.app._current_stats.get("place"))
        stored = json.loads(self.stats_file.read_text(encoding="utf-8"))
        self.assertIsNone(stored.get("place"))

    # Bad shapes never give a 500 and never store garbage
    def test_bad_shapes_never_500(self):
        bads = ["The Crypt", 42, 3.5, True, [], ["The Crypt"], {}, {"name": ""},
                {"name": "   "}, {"name": "x" * 5000, "look": "y"}, {"name": 5},
                {"name": None}, {"look": "only a look"}, {"name": ["a"]},
                {"name": "!!!"}, {"name": "../../state"}, {"name": "CON"},
                {"name": "The Crypt", "look": 12}, {"name": "The Crypt", "look": "z" * 20000}]
        for bad in bads:
            r = self._post({"place": bad})
            self.assertLess(r.status_code, 500, f"place={bad!r} -> {r.status_code}")
            cur = self.app._current_stats.get("place")
            if cur is not None:
                self.assertIsInstance(cur, dict, f"place={bad!r} stored {cur!r}")
                self.assertNotIn("look", cur)
                self.assertIsInstance(cur.get("name"), str, f"place={bad!r} stored {cur!r}")
                self.assertLessEqual(len(cur["name"]), 80, f"place={bad!r} stored {cur!r}")

    def test_bad_shape_after_good_does_not_crash_later_posts(self):
        self._scene_file("the-flooded-crypt.png", PNG)
        self._post({"place": 42})
        r = self._post({"place": {"name": "The Flooded Crypt", "look": LOOK}})
        self.assertEqual(r.status_code, 204)
        self._assert_place_shape(self._last_place(), "The Flooded Crypt")

    # The place is cleared when the campaign changes
    def test_campaign_switch_clears_place(self):
        self._scene_file("the-flooded-crypt.png", PNG)
        self._post({"place": {"name": "The Flooded Crypt", "look": LOOK}})
        self.assertIsNotNone(self.app._current_stats.get("place"))
        other = f"scene-other-{uuid.uuid4().hex[:6]}"
        self._make_campaign(other)
        r = self._post({"campaign": other})
        self.assertEqual(r.status_code, 204)
        self.assertIsNone(self.app._current_stats.get("place"))
        evs = self._stats_events()
        self.assertIsNone(evs[-1].get("place"), evs[-1])
        stored = json.loads(self.stats_file.read_text(encoding="utf-8"))
        self.assertIsNone(stored.get("place"))

    # Plan S2 Files: pop only "when the name changes"
    def test_same_campaign_resent_keeps_place(self):
        self._scene_file("the-flooded-crypt.png", PNG)
        self._post({"place": {"name": "The Flooded Crypt", "look": LOOK}})
        self._post({"campaign": self.name})
        self._assert_place_shape(self.app._current_stats.get("place"), "The Flooded Crypt")


class TestNoScenesFolder(_AppCase):
    """AC9 / D14: an older campaign with no scenes/ folder."""

    def test_no_folder_image_none_no_stderr(self):
        self.assertFalse((self.camp / "scenes").exists())
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertNoLogs(level="WARNING"):
            r = self._post({"place": {"name": "The Flooded Crypt", "look": LOOK}})
        self.assertEqual(r.status_code, 204)
        place = self._last_place()
        self._assert_place_shape(place, "The Flooded Crypt")
        self.assertIsNone(place["image"])
        self.assertEqual(err.getvalue(), "")

    def test_no_folder_image_route_404(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = self.client.get("/scene-image/x.png")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(err.getvalue(), "")

    def test_scenes_is_a_file(self):
        (self.camp / "scenes").write_bytes(b"oops")
        r = self._post({"place": {"name": "The Flooded Crypt", "look": LOOK}})
        self.assertEqual(r.status_code, 204)
        self.assertIsNone(self._last_place()["image"])
        self.assertEqual(self.client.get("/scene-image/x.png").status_code, 404)

    def test_no_active_campaign(self):
        self.camp_file.unlink()
        r = self._post({"place": {"name": "The Flooded Crypt", "look": LOOK}})
        self.assertEqual(r.status_code, 204)
        place = self._last_place()
        self._assert_place_shape(place, "The Flooded Crypt")
        self.assertIsNone(place["image"])
        self.assertEqual(self.client.get("/scene-image/x.png").status_code, 404)


class TestSceneImageRoute(_AppCase):

    def _get(self, url):
        r = self.client.get(url)
        try:
            return r.status_code, r.mimetype, r.data
        finally:
            r.close()

    def test_serves_each_format(self):
        self._scene_file("a.png", PNG)
        self._scene_file("b.jpg", JPEG)
        self._scene_file("c.jpeg", JPEG)
        self._scene_file("d.webp", WEBP)
        for fname, mime, data in (("a.png", "image/png", PNG), ("b.jpg", "image/jpeg", JPEG),
                                  ("c.jpeg", "image/jpeg", JPEG), ("d.webp", "image/webp", WEBP)):
            code, got, body = self._get(f"/scene-image/{fname}")
            self.assertEqual(code, 200, fname)
            self.assertEqual(got, mime, fname)
            self.assertEqual(body, data, fname)

    def test_query_string_ignored(self):
        self._scene_file("a.png", PNG)
        code, mime, _ = self._get("/scene-image/a.png?v=123")
        self.assertEqual((code, mime), (200, "image/png"))

    # Content type from magic bytes, not the extension
    def test_content_type_from_magic_bytes(self):
        self._scene_file("jpeg-named.png", JPEG)
        self._scene_file("webp-named.jpg", WEBP)
        self._scene_file("png-named.webp", PNG)
        self.assertEqual(self._get("/scene-image/jpeg-named.png")[:2], (200, "image/jpeg"))
        self.assertEqual(self._get("/scene-image/webp-named.jpg")[:2], (200, "image/webp"))
        self.assertEqual(self._get("/scene-image/png-named.webp")[:2], (200, "image/png"))

    def test_missing_file_404(self):
        self._scene_file("a.png", PNG)
        self.assertEqual(self._get("/scene-image/nothere.png")[0], 404)

    # Unsafe requests: .., %2e%2e, .txt, an absolute path
    def test_unsafe_requests_404(self):
        self._scene_file("a.png", PNG)
        (self.camp / "scenes" / "notes.txt").write_text("secret", encoding="utf-8")
        (self.camp / "outside.png").write_bytes(PNG)
        (self.camp / "maps").mkdir()
        (self.camp / "maps" / "m.png").write_bytes(PNG)
        abs_png = (self.camp / "outside.png").resolve().as_posix()
        urls = [
            "/scene-image/../outside.png",
            "/scene-image/..%2Foutside.png",
            "/scene-image/%2e%2e/outside.png",
            "/scene-image/%2e%2e%2foutside.png",
            "/scene-image/%2E%2E%2Fmaps%2Fm.png",
            "/scene-image/..%5Coutside.png",
            "/scene-image/../state.md",
            "/scene-image/../maps/m.png",
            "/scene-image/notes.txt",
            "/scene-image/a.png.txt",
            "/scene-image/" + abs_png,
            "/scene-image/" + urllib.parse.quote(abs_png, safe=""),
            "/scene-image//etc/passwd",
            "/scene-image/C:/Windows/win.ini",
            "/scene-image/C:%5CWindows%5Cwin.ini",
        ]
        for url in urls:
            code, _, body = self._get(url)
            self.assertEqual(code, 404, f"{url} -> {code}")
            self.assertNotIn(b"secret", body, url)

    def test_route_exists(self):
        """Distinguishes 'route missing' from 'file refused': a real file is served."""
        self._scene_file("present.png", PNG)
        self.assertEqual(self._get("/scene-image/present.png")[0], 200)


if __name__ == "__main__":
    unittest.main()
