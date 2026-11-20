"""Kokoro voice blends once the pool runs out (display foundation, plan step S12).

AC16 — No two NPCs share a voice until the pool is used up; after that,
       blended voices are saved per NPC (and sound distinct: listening test
       by the user, not covered here).
D20  — Past the English Kokoro voices, a new NPC gets a saved blend of two
       Kokoro voices. If blending is not supported, reuse the least-used
       voice instead, never the narrator's or a player character's.

Assumed interface (from the plan's "Cast and voices" section and S12). The
builder should match it or tell QA what changed:

  skills/dnd/display/tts.py
    is_kokoro(v) / valid_voice(v)
        Also accept a blend "a+b" whose two parts are both KOKORO_VOICES
        ("am_onyx+am_adam" -> True; "am_onyx+nobody" -> False; a blend with
        a Gemini part such as "Kore+am_adam" is not Kokoro).
    kokoro_health()
        As today, and it caches the blend flag from /health's "blend" key
        (missing key -> False).
    kokoro_blend_supported() -> bool | None
        NEW. The cached flag: True / False from the last successful
        kokoro_health(), None when unknown. Tests patch this function, so
        cast.py must look it up on the tts module at call time
        (`tts.kokoro_blend_supported()`), not copy it at import.
    kokoro_standin / kokoro_synthesize
        A blend is a Kokoro voice: it reaches the helper's /synth unchanged.

  skills/dnd/display/cast.py
    npc_voices(campaign, name) once the free pool is empty:
      * kokoro_blend_supported() is True -> a NEW blend "a+b" (50/50, saved
        as exactly "a+b" with no weights), a and b different voices in the
        same voice group (same two-letter prefix: af / am / bf / bm), the
        unordered pair not used by any other NPC, and neither part the
        narrator's (narrator_voice or heard_narrator) or a PC's voice. It is
        written to npcs.md and resolves to the same blend next session.
      * False or None -> the least-used allowed single voice (as S11).
    While free voices remain, single voices are assigned even with blending on.

  skills/dnd/display/kokoro_server.py
    Engine.synth(text, "a+b") -> self.k.create(text, voice=<style array>,
        speed=..., lang=<lang of a>) with style
        0.5*self.k.get_voice_style(a) + 0.5*self.k.get_voice_style(b).
        A blend with an unknown part falls back to "bm_lewis", as an unknown
        single voice does today.
    GET /health -> includes "blend": hasattr(engine.k, "get_voice_style").
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import sys
import threading
import unittest
import urllib.request
import uuid
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
CAST = DISPLAY / "cast.py"
for _p in (str(DISPLAY), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tts  # noqa: E402

KOKORO = set(tts.KOKORO_VOICES)
BLEND = re.compile(r"^([a-z]{2}_[a-z]+)\+([a-z]{2}_[a-z]+)$")


# ── helpers ──────────────────────────────────────────────────────────────

def _load(path: pathlib.Path, prefix: str):
    spec = importlib.util.spec_from_file_location(f"{prefix}_{uuid.uuid4().hex[:6]}", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_cast():
    """A fresh instance of cast.py (a new import = a new session)."""
    return _load(CAST, "cast_blend")


def _section(text: str, name: str) -> str:
    text = text.replace("\r\n", "\n")
    m = re.search(rf"^###[ \t]+{re.escape(name)}[ \t]*\n(.*?)(?=^##|\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


_VOICE_LINE = re.compile(r"^\s*-\s*\*\*Voice:\*\*\s*(\S+)", re.M)


def _voices_in(sec: str) -> list:
    return _VOICE_LINE.findall(sec)


def _parts(v: str):
    m = BLEND.match(v or "")
    return (m.group(1), m.group(2)) if m else None


class _CampaignCase(unittest.TestCase):
    """A real campaign folder under the sandboxed campaign root."""

    def setUp(self):
        self.name = f"blend-{uuid.uuid4().hex[:8]}"
        self.camp = pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"]) / "campaigns" / self.name
        (self.camp / "characters").mkdir(parents=True)
        (self.camp / "state.md").write_text("# State\n\n## Session Flags\n", encoding="utf-8")
        self.addCleanup(shutil.rmtree, str(self.camp), True)
        (self.camp / "npcs.md").write_bytes(b"# NPCs - test\n\n")

    def npcs_text(self) -> str:
        return (self.camp / "npcs.md").read_bytes().decode("utf-8")

    def pc(self, name, voice):
        (self.camp / "characters" / f"{name.lower()}.md").write_bytes((
            f"# {name}\n**Player:** p  **Campaign:** {self.name}\n\n## Identity\n"
            f"- **Race:** Human | **Class:** Fighter | **Level:** 3\n- **Voice:** {voice}\n\n"
            f"## Character Pillar\n- x\n").encode("utf-8"))

    def display_narrator(self, voice):
        (self.camp / "display.json").write_text(json.dumps(
            {"version": 1, "layout": {"preset": "story", "left": ["party"], "right": ["quests", "clock"]},
             "voice": {"narrator": voice, "npc_engine": "kokoro", "volume": 1.0, "speed": 1.0,
                       "read_player_actions": False}}), encoding="utf-8")

    def state_narrator(self, voice):
        (self.camp / "state.md").write_text(f"# State\n\n## Session Flags\ntts_voice: {voice}\n",
                                            encoding="utf-8")

    def blend_flag(self, value):
        """Pretend Kokoro's /health said blend=value (None: unknown)."""
        if not hasattr(tts, "kokoro_blend_supported"):
            self.fail("tts.kokoro_blend_supported() does not exist yet (S12)")
        p = mock.patch.object(tts, "kokoro_blend_supported", return_value=value)
        p.start()
        self.addCleanup(p.stop)


# ── AC16 / D20: blending on ──────────────────────────────────────────────

class TestBlendsWhenPoolExhausted(_CampaignCase):

    def setUp(self):
        super().setUp()
        self.cast = _load_cast()
        self.blend_flag(True)

    def _exhaust(self, reserved):
        pool = KOKORO - reserved
        singles = [self.cast.npc_voices(self.name, f"Villager{i:02d}")["voice"]
                   for i in range(len(pool))]
        self.assertEqual(set(singles), pool, "pool not handed out as single voices first")
        for v in singles:
            self.assertNotIn("+", v, f"blend {v} handed out while free voices remained")
        self.assertEqual(self.cast.free_voices(self.name), [])
        return singles

    def _check_blends(self, blends, forbidden):
        seen = set()
        for i, v in enumerate(blends):
            parts = _parts(v)
            self.assertIsNotNone(parts, f"NPC {i} past the pool got {v!r}, not an a+b blend")
            a, b = parts
            self.assertIn(a, KOKORO, v)
            self.assertIn(b, KOKORO, v)
            self.assertNotEqual(a, b, f"{v} blends a voice with itself")
            self.assertEqual(a[:2], b[:2], f"{v} mixes voice groups")
            self.assertNotIn(a, forbidden, f"{v} contains a reserved voice")
            self.assertNotIn(b, forbidden, f"{v} contains a reserved voice")
            pair = frozenset((a, b))
            self.assertNotIn(pair, seen, f"{v} repeats a pair already used")
            seen.add(pair)

    def test_gemini_narrator_heard_default_never_in_a_blend(self):
        # Saved narrator is a Gemini name, so bm_lewis is the voice heard.
        self.state_narrator("Charon")
        self.pc("Aldric", "am_onyx")
        self.pc("Brenna", "bf_emma")
        forbidden = {"bm_lewis", "am_onyx", "bf_emma"}
        self._exhaust(forbidden)
        blends = [self.cast.npc_voices(self.name, f"Stranger{i:02d}")["voice"] for i in range(12)]
        self._check_blends(blends, forbidden)

    def test_kokoro_narrator_and_pcs_never_in_a_blend(self):
        self.display_narrator("bm_george")
        self.pc("Aldric", "am_onyx")
        self.pc("Brenna", "bf_alice")
        forbidden = {"bm_george", "am_onyx", "bf_alice"}
        self._exhaust(forbidden)
        blends = [self.cast.npc_voices(self.name, f"Stranger{i:02d}")["voice"] for i in range(12)]
        self._check_blends(blends, forbidden)

    def test_blend_is_saved_and_same_next_session(self):
        self.display_narrator("bm_lewis")
        self._exhaust({"bm_lewis"})
        v = self.cast.npc_voices(self.name, "Garrick")["voice"]
        self.assertIsNotNone(_parts(v), f"expected a blend, got {v!r}")
        text = self.npcs_text()
        self.assertEqual(_voices_in(_section(text, "Garrick")), [v],
                         f"blend not written to npcs.md:\n{_section(text, 'Garrick')}")
        after = (self.camp / "npcs.md").read_bytes()
        fresh = _load_cast()   # next session
        self.assertEqual(fresh.npc_voices(self.name, "Garrick")["voice"], v)
        self.assertEqual((self.camp / "npcs.md").read_bytes(), after,
                         "resolving an NPC with a saved blend rewrote npcs.md")

    def test_cast_list_shows_the_blend(self):
        self.display_narrator("bm_lewis")
        self._exhaust({"bm_lewis"})
        v = self.cast.npc_voices(self.name, "Garrick")["voice"]
        npcs = {n["name"]: n["voice"] for n in self.cast.cast_list(self.name)["npcs"]}
        self.assertEqual(npcs.get("Garrick"), v)


# ── D20 fallback: blending off or unknown ────────────────────────────────

class TestSetRefusesReservedBlend(_CampaignCase):
    """cast.py set --npc with a blend that contains the narrator's or a PC's
    voice is refused, as the single voice is (D20)."""

    def test_blend_with_narrator_or_pc_part_refused(self):
        cast = _load_cast()
        self.display_narrator("bm_lewis")
        self.pc("Aldric", "am_onyx")
        for v in ("bm_lewis+bm_george", "am_adam+am_onyx"):
            with self.assertRaises(ValueError, msg=v):
                cast.set_voice(self.name, "npc", "Garrick", v)
        cast.set_voice(self.name, "npc", "Garrick", "am_adam+am_liam")
        self.assertEqual(cast.npc_voices(self.name, "Garrick")["voice"], "am_adam+am_liam")


class TestNoBlendFallback(_CampaignCase):

    def _run(self, flag):
        self.cast = _load_cast()
        self.blend_flag(flag)
        self.state_narrator("Charon")          # heard narrator: bm_lewis
        self.pc("Aldric", "am_onyx")
        self.pc("Brenna", "bf_emma")
        forbidden = {"bm_lewis", "am_onyx", "bf_emma"}
        allowed = KOKORO - forbidden
        got = [self.cast.npc_voices(self.name, f"Villager{i:02d}")["voice"]
               for i in range(len(allowed) + 10)]
        for i, v in enumerate(got):
            self.assertNotIn("+", v, f"NPC {i} got blend {v} with blending {flag}")
            self.assertIn(v, allowed, f"NPC {i} got {v!r}")
        counts = {v: got.count(v) for v in allowed}
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1,
                             f"reuse is not least-used: {counts}")

    def test_blend_off_reuses_least_used(self):
        self._run(False)

    def test_blend_unknown_reuses_least_used(self):
        self._run(None)


# ── tts: blends are Kokoro voices ────────────────────────────────────────

class TestTtsBlendVoices(unittest.TestCase):

    def test_valid_voice_accepts_known_blend(self):
        self.assertTrue(tts.valid_voice("am_onyx+am_adam"))

    def test_valid_voice_rejects_unknown_part(self):
        self.assertFalse(tts.valid_voice("am_onyx+nobody"))

    def test_is_kokoro_accepts_known_blend(self):
        self.assertTrue(tts.is_kokoro("am_onyx+am_adam"))

    def test_is_kokoro_rejects_unknown_or_gemini_part(self):
        self.assertFalse(tts.is_kokoro("am_onyx+nobody"))
        self.assertFalse(tts.is_kokoro("Kore+am_adam"))

    def test_single_voices_unchanged(self):
        self.assertTrue(tts.is_kokoro("am_onyx"))
        self.assertTrue(tts.valid_voice("Kore"))
        self.assertFalse(tts.is_kokoro("Kore"))

    def test_synthesize_sends_blend_unchanged(self):
        sent = {}

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            sent["body"] = json.loads(req.data.decode("utf-8"))
            return _Resp(b"\x00\x00" * 8)

        with mock.patch.object(tts.urllib.request, "urlopen", fake_urlopen):
            tts.kokoro_synthesize("Hello there.", "am_onyx+am_adam", use_cache=False)
        self.assertEqual(sent["body"]["voice"], "am_onyx+am_adam",
                         "the blend was swapped for a stand-in before reaching Kokoro")


class TestKokoroHealthCachesBlendFlag(unittest.TestCase):

    def _health(self, payload):
        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch.object(tts.urllib.request, "urlopen",
                               lambda *a, **k: _Resp(json.dumps(payload).encode())):
            return tts.kokoro_health()

    def _flag(self):
        if not hasattr(tts, "kokoro_blend_supported"):
            self.fail("tts.kokoro_blend_supported() does not exist yet (S12)")
        # No network after the health check: the flag must come from the cache.
        with mock.patch.object(tts.urllib.request, "urlopen",
                               side_effect=AssertionError("network call while reading cached flag")):
            return tts.kokoro_blend_supported()

    def test_blend_true_is_cached(self):
        self.assertTrue(self._health({"ok": True, "provider": "cpu", "voices": [], "blend": True}))
        self.assertIs(self._flag(), True)

    def test_blend_false_is_cached(self):
        self._health({"ok": True, "provider": "cpu", "voices": [], "blend": False})
        self.assertIs(self._flag(), False)

    def test_old_helper_without_blend_key_is_false(self):
        self._health({"ok": True, "provider": "cpu", "voices": [], "blend": True})
        self._health({"ok": True, "provider": "cpu", "voices": []})
        self.assertIs(self._flag(), False)


# ── kokoro_server: blend synthesis and /health ───────────────────────────

class _FakeKokoro:
    """Stands in for kokoro_onnx.Kokoro: voice styles and create()."""

    def __init__(self):
        import numpy as np
        self.np = np
        self.styles = {v: np.full((4, 1, 8), float(i + 1), dtype=np.float32)
                       for i, v in enumerate(sorted(KOKORO))}
        self.calls = []

    def get_voices(self):
        return list(self.styles)

    def get_voice_style(self, name):
        return self.styles[name]

    def create(self, text, voice, speed=1.0, lang="en-us", **kw):
        self.calls.append({"text": text, "voice": voice, "speed": speed, "lang": lang})
        return self.np.zeros(16, dtype=self.np.float32), 24000


class _FakeKokoroNoStyles(_FakeKokoro):
    get_voice_style = None

    def __getattribute__(self, item):
        if item == "get_voice_style":
            raise AttributeError(item)
        return object.__getattribute__(self, item)


def _engine(server_mod, fake):
    eng = server_mod.Engine.__new__(server_mod.Engine)
    eng.device = "cpu"
    eng.k = fake
    eng.voices = set(fake.get_voices())
    eng.lock = threading.Lock()
    return eng


class TestKokoroServerBlend(unittest.TestCase):

    def setUp(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("numpy not installed")
        self.srv = _load(DISPLAY / "kokoro_server.py", "kokoro_server_blend")
        self.fake = _FakeKokoro()
        self.eng = _engine(self.srv, self.fake)

    def test_blend_synth_uses_half_and_half_style(self):
        import numpy as np
        pcm = self.eng.synth("Well met.", "am_onyx+am_adam")
        self.assertIsInstance(pcm, bytes)
        call = self.fake.calls[-1]
        want = 0.5 * self.fake.styles["am_onyx"] + 0.5 * self.fake.styles["am_adam"]
        self.assertIsInstance(call["voice"], np.ndarray,
                              f"create() got voice={call['voice']!r}, not a style array")
        self.assertTrue(np.allclose(call["voice"], want), "style is not 0.5*a + 0.5*b")
        self.assertEqual(call["lang"], "en-us")

    def test_blend_lang_follows_first_voice(self):
        self.eng.synth("Well met.", "bm_george+bm_daniel")
        self.assertEqual(self.fake.calls[-1]["lang"], "en-gb")

    def test_unknown_blend_part_falls_back_to_lewis(self):
        self.eng.synth("Well met.", "am_onyx+nobody")
        self.assertEqual(self.fake.calls[-1]["voice"], "bm_lewis")

    def test_single_voice_unchanged(self):
        self.eng.synth("Well met.", "af_bella")
        self.assertEqual(self.fake.calls[-1]["voice"], "af_bella")

    def _serve(self, eng):
        from http.server import ThreadingHTTPServer
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), self.srv._make_handler(eng))
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    def _get_health(self, base):
        fetch = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
        with fetch(f"{base}/health", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))

    def test_health_reports_blend_true(self):
        body = self._get_health(self._serve(self.eng))
        self.assertIs(body.get("blend"), True, f"/health: {body}")

    def test_health_reports_blend_false_without_styles(self):
        eng = _engine(self.srv, _FakeKokoroNoStyles())
        body = self._get_health(self._serve(eng))
        self.assertIs(body.get("blend"), False, f"/health: {body}")

    def test_synth_endpoint_accepts_blend(self):
        import numpy as np
        base = self._serve(self.eng)
        fetch = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
        req = urllib.request.Request(f"{base}/synth", method="POST",
                                     data=json.dumps({"text": "Hi.", "voice": "af_bella+af_nova"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with fetch(req, timeout=5) as r:
            self.assertEqual(r.status, 200)
        self.assertIsInstance(self.fake.calls[-1]["voice"], np.ndarray)


if __name__ == "__main__":
    unittest.main()
