"""Scene backgrounds S1: the image trial script and the request builder.

Spec: docs/specs/2026-11-18-scene-backgrounds.md (AC1, AC5, D3, D4, D10, D12).
Plan: docs/specs/2026-11-18-scene-backgrounds.plan.md, step S1.

The trial spends the user's free Gemini image quota, so it is a dry run by
default and only calls Google with --send. Nothing here may reach the
network: every test clears DND_TTS_KEY / GEMINI_API_KEY, points the key file
at nowhere, and replaces urllib.request.urlopen (and socket connects) with
something that raises, unless the test installs a stub that answers like
Gemini's generateContent.

Interface these tests assume (from the plan):
  scene_gen.GEMINI_IMAGE_MODEL            default "gemini-2.5-flash-image"
  scene_gen.build_request(look, style)    JSON-able generateContent body
  scene_trial.main(argv) -> int           exit code
     dry run (default)    0, prints each request and
                          "Would make N image requests — none made (add --send)"
     --send               prints "Making N image requests on your Gemini key"
                          before the first call; writes <out>/<slug>.png and
                          <out>/index.json
     N > --max (8)        exit 2, no calls
     no key with --send   exit 2, no calls
     429                  exit 3 at once, names the quota id and how many made
     list-models          GET only, network only when named
  --locations FILE is a JSON list of {"name": ..., "look": ...}.
"""
import base64
import contextlib
import importlib
import io
import json
import os
import pathlib
import re
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
if str(DISPLAY) not in sys.path:
    sys.path.insert(0, str(DISPLAY))

import tts  # noqa: E402

FAKE_KEY = "AIzaFAKE-scene-trial-key-9f3c2b"
QUOTA_ID = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"

# A real 1x1 PNG, so a writer that checks magic bytes is happy.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

BODY_429 = json.dumps({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota", "details": [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
        {"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
         "quotaId": QUOTA_ID, "quotaValue": "10"}]},
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "33703s"}]}})

SIX = [
    {"name": "The Rusty Flagon", "look": "a smoky tavern common room, low beams, a fire in a stone hearth"},
    {"name": "Glimmer Cave", "look": "a wet limestone cave mouth, pale crystals catching torchlight"},
    {"name": "The Old Mill Road", "look": "a muddy forest road under tall oaks, a leaning milestone"},
    {"name": "The Flooded Crypt", "look": "a half-submerged stone hall, pillars in black water"},
    {"name": "Harrow Square", "look": "a cobbled town square with a dry fountain and shuttered stalls"},
    {"name": "Sunken Abbey", "look": "a roofless ruined abbey, ivy over broken arches, grey sky"},
]
SIX_SLUGS = ["the-rusty-flagon", "glimmer-cave", "the-old-mill-road",
             "the-flooded-crypt", "harrow-square", "sunken-abbey"]


def _never(*a, **k):
    raise AssertionError("a scene test tried to reach the network")


class _Resp:
    """Enough of an http.client.HTTPResponse for urllib callers."""

    def __init__(self, payload: bytes, status: int = 200):
        self._payload = payload
        self.status = status
        self.code = status
        self.headers = {"Content-Type": "application/json"}

    def read(self, *a):
        return self._payload

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


def _image_payload(png: bytes = PNG_BYTES) -> bytes:
    return json.dumps({"candidates": [{"content": {"role": "model", "parts": [
        {"text": "Here is the scene."},
        {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(png).decode("ascii")}},
    ]}, "finishReason": "STOP"}]}).encode("utf-8")


def _http_429():
    return urllib.error.HTTPError("https://generativelanguage.googleapis.com/x", 429,
                                  "Too Many Requests", {}, io.BytesIO(BODY_429.encode("utf-8")))


class _Stub:
    """Stands in for urllib.request.urlopen. Records every call."""

    def __init__(self, fail_on: int = 0, handler=None, out_buf=None):
        self.calls = []          # (method, url, body-or-None)
        self.fail_on = fail_on   # 1-based call number that answers 429
        self.handler = handler
        self.out_buf = out_buf
        self.stdout_at_first_call = None

    def __call__(self, req, *a, **k):
        if isinstance(req, urllib.request.Request):
            url, data, method = req.full_url, req.data, req.get_method()
        else:
            url, data = str(req), k.get("data") or (a[0] if a else None)
            method = "POST" if data else "GET"
        self.calls.append((method, url, data))
        if len(self.calls) == 1 and self.out_buf is not None:
            self.stdout_at_first_call = self.out_buf.getvalue()
        if self.fail_on and len(self.calls) == self.fail_on:
            raise _http_429()
        if self.handler:
            return self.handler(method, url, data)
        return _Resp(_image_payload())


def _prompt_text(body) -> str:
    """Every text part in a generateContent body, joined."""
    texts = []
    for c in body.get("contents", []) or []:
        for p in c.get("parts", []) or []:
            if isinstance(p, dict) and isinstance(p.get("text"), str):
                texts.append(p["text"])
    si = body.get("systemInstruction") or body.get("system_instruction") or {}
    for p in si.get("parts", []) or []:
        if isinstance(p, dict) and isinstance(p.get("text"), str):
            texts.append(p["text"])
    return "\n".join(texts)


class SceneTestBase(unittest.TestCase):
    """No key, no key file, no network, a temp output folder."""

    def setUp(self):
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for k in ("DND_TTS_KEY", "GEMINI_API_KEY"):
            os.environ.pop(k, None)

        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="scene-trial-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.out = self.tmp / "out"

        for p in (mock.patch.object(tts, "KEY_FILE", self.tmp / "no-such" / "tts.key"),
                  mock.patch("urllib.request.urlopen", side_effect=_never),
                  mock.patch.object(socket, "create_connection", side_effect=_never),
                  mock.patch.object(socket.socket, "connect", _never)):
            p.start()
            self.addCleanup(p.stop)

        self.scene_gen = importlib.import_module("scene_gen")
        self.scene_trial = importlib.import_module("scene_trial")

    # helpers

    def write_locations(self, locs) -> str:
        path = self.tmp / "locations.json"
        path.write_text(json.dumps(locs), encoding="utf-8")
        return str(path)

    def run_main(self, argv, stub=None):
        """main(argv) with stdout/stderr captured; returns (code, out, err)."""
        out, err = io.StringIO(), io.StringIO()
        if stub is not None:
            stub.out_buf = out
        ctx = mock.patch("urllib.request.urlopen", stub) if stub is not None else contextlib.nullcontext()
        with ctx, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.scene_trial.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def files(self, folder=None):
        folder = pathlib.Path(folder or self.out)
        if not folder.exists():
            return []
        return sorted(p.name for p in folder.iterdir())

    def pngs(self, folder=None):
        return [n for n in self.files(folder) if n.endswith(".png")]


class TestBuildRequest(SceneTestBase):
    """AC5, D3, D10: every request says no people / creatures / text, 16:9."""

    def test_default_model(self):
        self.assertEqual(self.scene_gen.GEMINI_IMAGE_MODEL, "gemini-2.5-flash-image")

    def test_body_is_json_and_prompt_has_rules(self):
        look = "a half-submerged stone hall, pillars in black water"
        style = "muted oil painting, dramatic light"
        body = self.scene_gen.build_request(look, style)
        self.assertIsInstance(body, dict)
        dumped = json.dumps(body)  # must be JSON-able as-is
        prompt = _prompt_text(body)
        self.assertTrue(prompt, f"no text part found in {dumped[:300]}")
        low = prompt.lower()
        self.assertIn(look, prompt)
        self.assertIn(style, prompt)
        for phrase in ("no people", "no creatures", "no text"):
            self.assertIn(phrase, low, f"prompt lacks {phrase!r}: {prompt!r}")
        self.assertIn("16:9", dumped, "the request must ask for the 16:9 aspect ratio")

    def test_rules_hold_for_any_look_and_style(self):
        for look, style in (("", ""), ("a crowded market full of merchants", "anime"),
                            ("x" * 400, "watercolour")):
            prompt = _prompt_text(self.scene_gen.build_request(look, style)).lower()
            for phrase in ("no people", "no creatures", "no text"):
                self.assertIn(phrase, prompt, (look[:20], style, phrase))

    def test_build_request_never_calls_network(self):
        self.scene_gen.build_request("a cave", "ink")  # urlopen raises if touched


class TestDryRun(SceneTestBase):
    """D12: the default run prints the requests and makes none."""

    def test_default_run_prints_six_requests_and_makes_none(self):
        stub = _Stub()
        code, out, err = self.run_main(["--out", str(self.out)], stub)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(stub.calls, [], "a dry run must not call Gemini")
        self.assertIn("Would make 6 image requests", out)
        self.assertIn("none made", out)
        self.assertIn("--send", out)
        self.assertEqual(self.files(), [], "a dry run must write no files")

    def test_dry_run_shows_model_aspect_and_every_prompt_rule(self):
        os.environ["GEMINI_API_KEY"] = FAKE_KEY
        code, out, err = self.run_main(["--out", str(self.out)], _Stub())
        self.assertEqual(code, 0, out + err)
        low = out.lower()
        self.assertGreaterEqual(out.count(self.scene_gen.GEMINI_IMAGE_MODEL), 1)
        self.assertGreaterEqual(out.count("16:9"), 1)
        for phrase in ("no people", "no creatures", "no text"):
            self.assertGreaterEqual(low.count(phrase), 6, f"each of 6 prompts must say {phrase!r}")
        self.assertIn("***", out, "the key is shown as ***")
        self.assertNotIn(FAKE_KEY, out + err)

    def test_locations_file_and_styles_multiply(self):
        locs = self.write_locations(SIX[:2])
        code, out, err = self.run_main(
            ["--locations", locs, "--style", "oil painting", "--style", "ink wash",
             "--style", "pixel art", "--out", str(self.out)], _Stub())
        self.assertEqual(code, 0, out + err)
        self.assertIn("Would make 6 image requests", out)
        for s in ("oil painting", "ink wash", "pixel art"):
            self.assertIn(s, out)
        for loc in SIX[:2]:
            self.assertIn(loc["look"], out)

    def test_model_override_is_shown(self):
        code, out, err = self.run_main(["--model", "imagen-test-model", "--out", str(self.out)], _Stub())
        self.assertEqual(code, 0, out + err)
        self.assertIn("imagen-test-model", out)


class TestSend(SceneTestBase):
    """--send with a stubbed Gemini: files, index, count first."""

    def setUp(self):
        super().setUp()
        os.environ["GEMINI_API_KEY"] = FAKE_KEY
        self.locs = self.write_locations(SIX)

    def test_send_writes_six_slug_files_and_index(self):
        stub = _Stub()
        code, out, err = self.run_main(["--locations", self.locs, "--send", "--out", str(self.out)], stub)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(stub.calls), 6)
        self.assertTrue(all(m == "POST" for m, _, _ in stub.calls), stub.calls)
        self.assertEqual(sorted(self.pngs()), sorted(s + ".png" for s in SIX_SLUGS))
        for s in SIX_SLUGS:
            self.assertEqual((self.out / f"{s}.png").read_bytes(), PNG_BYTES)
        index = json.loads((self.out / "index.json").read_text(encoding="utf-8"))
        text = json.dumps(index)
        for s in SIX_SLUGS:
            self.assertIn(s, text)

    def test_count_printed_before_first_call(self):
        stub = _Stub()
        code, out, err = self.run_main(["--locations", self.locs, "--send", "--out", str(self.out)], stub)
        self.assertEqual(code, 0, out + err)
        self.assertIsNotNone(stub.stdout_at_first_call)
        self.assertIn("Making 6 image requests on your Gemini key", stub.stdout_at_first_call)

    def test_request_body_carries_rules(self):
        stub = _Stub()
        self.run_main(["--locations", self.locs, "--send", "--out", str(self.out)], stub)
        self.assertEqual(len(stub.calls), 6)
        for _, url, data in stub.calls:
            self.assertIn(self.scene_gen.GEMINI_IMAGE_MODEL, url)
            body = json.loads(data.decode("utf-8"))
            low = _prompt_text(body).lower()
            for phrase in ("no people", "no creatures", "no text"):
                self.assertIn(phrase, low)
            self.assertIn("16:9", json.dumps(body))

    def test_default_out_is_tempdir_mydm_scene_trial(self):
        fake_tmp = self.tmp / "systemp"
        fake_tmp.mkdir()
        stub = _Stub()
        with mock.patch.object(tempfile, "tempdir", str(fake_tmp)):
            code, out, err = self.run_main(["--locations", self.locs, "--send"], stub)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(sorted(self.pngs(fake_tmp / "mydm-scene-trial")),
                         sorted(s + ".png" for s in SIX_SLUGS))

    def test_429_on_third_call_stops_with_exit_3(self):
        stub = _Stub(fail_on=3)
        code, out, err = self.run_main(["--locations", self.locs, "--send", "--out", str(self.out)], stub)
        self.assertEqual(code, 3, out + err)
        self.assertEqual(len(stub.calls), 3, "no request after the first 429")
        self.assertEqual(len(self.pngs()), 2)
        msg = out + err
        self.assertIn(QUOTA_ID, msg)
        self.assertRegex(msg, r"\b2\b", "the message says how many images were made")
        self.assertNotIn(FAKE_KEY, msg)

    def test_rerun_skips_existing_files(self):
        self.out.mkdir(parents=True)
        for s in SIX_SLUGS[:2]:
            (self.out / f"{s}.png").write_bytes(b"user kept this")
        stub = _Stub()
        code, out, err = self.run_main(["--locations", self.locs, "--send", "--out", str(self.out)], stub)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(stub.calls), 4)
        self.assertIn("Making 4 image requests", out)
        for s in SIX_SLUGS[:2]:
            self.assertEqual((self.out / f"{s}.png").read_bytes(), b"user kept this")
        self.assertEqual(len(self.pngs()), 6)

    def test_rerun_dry_count_drops(self):
        self.out.mkdir(parents=True)
        for s in SIX_SLUGS[:2]:
            (self.out / f"{s}.png").write_bytes(PNG_BYTES)
        code, out, err = self.run_main(["--locations", self.locs, "--out", str(self.out)], _Stub())
        self.assertEqual(code, 0, out + err)
        self.assertIn("Would make 4 image requests", out)

    def test_second_full_run_makes_none(self):
        self.run_main(["--locations", self.locs, "--send", "--out", str(self.out)], _Stub())
        stub = _Stub()
        code, out, err = self.run_main(["--locations", self.locs, "--send", "--out", str(self.out)], stub)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(stub.calls, [])

    def test_again_remakes_existing(self):
        self.out.mkdir(parents=True)
        for s in SIX_SLUGS:
            (self.out / f"{s}.png").write_bytes(b"old")
        stub = _Stub()
        code, out, err = self.run_main(
            ["--locations", self.locs, "--send", "--again", "--out", str(self.out)], stub)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(stub.calls), 6)
        self.assertEqual((self.out / "the-flooded-crypt.png").read_bytes(), PNG_BYTES)

    def test_key_never_in_output(self):
        code, out, err = self.run_main(["--locations", self.locs, "--send", "--out", str(self.out)], _Stub())
        self.assertEqual(code, 0)
        self.assertNotIn(FAKE_KEY, out + err)
        self.assertNotIn(FAKE_KEY, (self.out / "index.json").read_text(encoding="utf-8"))


class TestLimits(SceneTestBase):
    """Refusals: exit 2 and zero calls."""

    def test_no_key_refuses(self):
        stub = _Stub()
        code, out, err = self.run_main(["--send", "--out", str(self.out)], stub)
        self.assertEqual(code, 2, out + err)
        self.assertEqual(stub.calls, [])
        self.assertEqual(self.pngs(), [])

    def test_nine_locations_over_default_max_refuses(self):
        os.environ["GEMINI_API_KEY"] = FAKE_KEY
        nine = SIX + [{"name": f"Extra Place {i}", "look": f"an empty hall number {i}"} for i in range(3)]
        locs = self.write_locations(nine)
        stub = _Stub()
        code, out, err = self.run_main(["--locations", locs, "--send", "--out", str(self.out)], stub)
        self.assertEqual(code, 2, out + err)
        self.assertEqual(stub.calls, [])
        self.assertEqual(self.pngs(), [])

    def test_nine_locations_with_max_nine_sends(self):
        os.environ["GEMINI_API_KEY"] = FAKE_KEY
        nine = SIX + [{"name": f"Extra Place {i}", "look": f"an empty hall number {i}"} for i in range(3)]
        locs = self.write_locations(nine)
        stub = _Stub()
        code, out, err = self.run_main(
            ["--locations", locs, "--max", "9", "--send", "--out", str(self.out)], stub)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(stub.calls), 9)
        self.assertEqual(len(self.pngs()), 9)

    def test_styles_count_toward_max(self):
        os.environ["GEMINI_API_KEY"] = FAKE_KEY
        stub = _Stub()
        code, out, err = self.run_main(
            ["--style", "a", "--style", "b", "--send", "--out", str(self.out)], stub)  # 6 x 2 = 12 > 8
        self.assertEqual(code, 2, out + err)
        self.assertEqual(stub.calls, [])


class TestListModels(SceneTestBase):
    """list-models is a GET that costs no quota, and only runs when named."""

    MODELS = {"models": [
        {"name": "models/gemini-2.5-flash-image", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-2.5-flash-preview-tts", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
    ]}

    def _handler(self, method, url, data):
        return _Resp(json.dumps(self.MODELS).encode("utf-8"))

    def test_list_models_is_get_only(self):
        os.environ["GEMINI_API_KEY"] = FAKE_KEY
        stub = _Stub(handler=self._handler)
        code, out, err = self.run_main(["list-models"], stub)
        self.assertEqual(code, 0, out + err)
        self.assertGreaterEqual(len(stub.calls), 1)
        for method, _, data in stub.calls:
            self.assertEqual(method, "GET")
            self.assertFalse(data)
        self.assertIn("gemini-2.5-flash-image", out)
        self.assertNotIn(FAKE_KEY, out + err)

    def test_list_models_not_run_by_default(self):
        os.environ["GEMINI_API_KEY"] = FAKE_KEY
        stub = _Stub(handler=self._handler)
        code, out, err = self.run_main(["--out", str(self.out)], stub)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(stub.calls, [])

    def test_scene_gen_list_image_models_uses_get(self):
        os.environ["GEMINI_API_KEY"] = FAKE_KEY
        stub = _Stub(handler=self._handler)
        with mock.patch("urllib.request.urlopen", stub):
            models = self.scene_gen.list_image_models()
        self.assertTrue(stub.calls)
        self.assertTrue(all(m == "GET" for m, _, _ in stub.calls))
        self.assertIn("gemini-2.5-flash-image", json.dumps(models))


class TestGenerate(SceneTestBase):
    """scene_gen.generate: stdlib call, SceneGenError on a 429 (D4, D7)."""

    def test_generate_returns_image_bytes(self):
        os.environ["GEMINI_API_KEY"] = FAKE_KEY
        stub = _Stub()
        with mock.patch("urllib.request.urlopen", stub):
            result = self.scene_gen.generate("a cave", "ink")
        self.assertEqual(len(stub.calls), 1)
        data = result if isinstance(result, (bytes, bytearray)) else result[0]
        self.assertEqual(bytes(data), PNG_BYTES)

    def test_generate_429_raises_scene_gen_error_with_quota(self):
        os.environ["GEMINI_API_KEY"] = FAKE_KEY
        stub = _Stub(fail_on=1)
        with mock.patch("urllib.request.urlopen", stub):
            with self.assertRaises(self.scene_gen.SceneGenError) as cm:
                self.scene_gen.generate("a cave", "ink")
        self.assertEqual(cm.exception.status, 429)
        self.assertEqual(cm.exception.quota_id, QUOTA_ID)
        self.assertEqual(cm.exception.retry_after, 33703.0)
        self.assertNotIn(FAKE_KEY, str(cm.exception))


if __name__ == "__main__":
    unittest.main()
