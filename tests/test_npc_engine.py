"""NPC voice engine: Gemini with each NPC's own Kokoro fallback (plan step S13).

AC14 (server side) — Voice settings save to display.json and apply from the
       next line; a Gemini daily-quota error shows the note on the laptop
       page and nothing on the TV.
AC17 — With Gemini on and the quota used up, each NPC is read in its own
       Kokoro voice.
D17  — NPC voices: "Kokoro only" (default) or "Gemini, falling back to Kokoro".
D21  — Each NPC's Gemini voice falls back to its own Kokoro voice, not a
       shared mapping.

Nothing here reaches Gemini or a real Kokoro helper.

Assumed interface (from the plan's "Cast and voices" section and S13). The
builder should match it or tell QA what changed:

  display.json  voice.npc_engine: "kokoro" (default) | "gemini". Read when
      /chunk handles a block (via _display_config()), so a change POSTed to
      /display-config applies from the next line.

  cast.npc_voices(campaign, name, engine) -> {"voice": <kokoro>, "gemini": <str>}
      engine="gemini": an NPC with no `- **Gemini voice:**` line is given the
          least-used voice of tts.VALID_VOICES (counted over the NPCs in
          npcs.md) and the line is written back under its Voice: line. The
          Kokoro `- **Voice:**` line is left as it is.
          Never the narrator's voice: when the narrator (display.json
          voice.narrator / state.md tts_voice) is a Gemini voice, no NPC is
          given it, and an NPC that holds it is given another (as an NPC's
          Kokoro voice is moved off the narrator's in S11). No NPC sounds like
          the narrator.
      engine="kokoro": no Gemini voice is assigned or written.

  /chunk {"npc": name, "text": ...}
      Kokoro mode: block "voice" = the NPC's Kokoro voice; no "voice_fallback".
      Gemini mode: block "voice" = the NPC's Gemini voice and
          "voice_fallback" = its Kokoro voice. The text_log entry keeps both
          (so a replay after reconnect has the fallback too).

  /tts {"text", "voice", "fallback"?}
      "fallback" (a Kokoro voice or blend; anything else is ignored) is what
      every local stand-in path reads with: Gemini 429 with Kokoro up, the
      quota window, Gemini off/no key, and a Gemini upstream failure.
      X-Audio-Voice names it. Without "fallback", tts.kokoro_standin(voice)
      as today.

  Daily quota (429) in Gemini mode with Kokoro up: the server stores a
      voice note "NPCs moved to Kokoro — Gemini quota used up, back in about
      N h". GET /display-config returns it as status["voice_note"]. No
      {"tts_quota": ...} and no voice-state {"voice": {...}} broadcast goes out.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shutil
import sys
import unittest
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
GEMINI = set(tts.VALID_VOICES)
DAILY_429 = dict(status=429, quota_id="GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                 retry_after=7200)
NOTE = re.compile(r"NPCs moved to Kokoro\s*[—–-]+\s*Gemini quota used up, back in about 2 h")


# ── helpers ──────────────────────────────────────────────────────────────

def _load_cast():
    spec = importlib.util.spec_from_file_location(f"cast_eng_{uuid.uuid4().hex[:6]}", str(CAST))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_app():
    spec = importlib.util.spec_from_file_location(f"dnd_display_app_eng_{uuid.uuid4().hex[:6]}",
                                                  str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError as exc:
        raise unittest.SkipTest(f"display app not importable: {exc}")
    if mod._tts is None or mod._cast is None:
        raise unittest.SkipTest("tts or cast module unavailable")
    return mod


def _section(text: str, name: str) -> str:
    text = text.replace("\r\n", "\n")
    m = re.search(rf"^###[ \t]+{re.escape(name)}[ \t]*\n(.*?)(?=^##|\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


_VOICE_LINE = re.compile(r"^\s*-\s*\*\*Voice:\*\*\s*(\S+)", re.M)
_GEMINI_LINE = re.compile(r"^\s*-\s*\*\*Gemini voice:\*\*\s*(\S+)", re.M)


def _npc(name, voice=None, gemini=None):
    v = f"- **Voice:** {voice}\n" if voice else ""
    g = f"- **Gemini voice:** {gemini}\n" if gemini else ""
    return f"### {name}\n- **Role:** villager\n{v}{g}\n"


class _CampaignCase(unittest.TestCase):
    """A real campaign folder under the sandboxed campaign root."""

    def setUp(self):
        self.name = f"eng-{uuid.uuid4().hex[:8]}"
        self.camp = pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"]) / "campaigns" / self.name
        (self.camp / "characters").mkdir(parents=True)
        (self.camp / "state.md").write_text("# State\n\n## Session Flags\n", encoding="utf-8")
        self.addCleanup(shutil.rmtree, str(self.camp), True)

    def npcs(self, *sections):
        (self.camp / "npcs.md").write_bytes(("# NPCs — test\n\n" + "".join(sections)).encode("utf-8"))

    def npcs_text(self) -> str:
        return (self.camp / "npcs.md").read_bytes().decode("utf-8")

    def display(self, engine="kokoro", narrator="bm_lewis"):
        voice = {"npc_engine": engine, "volume": 1.0, "speed": 1.0, "read_player_actions": False}
        if narrator:
            voice["narrator"] = narrator
        (self.camp / "display.json").write_text(json.dumps(
            {"version": 1, "layout": {"preset": "story", "left": ["party"], "right": ["quests", "clock"]},
             "voice": voice}), encoding="utf-8")

    def gemini_of(self, name) -> list:
        return _GEMINI_LINE.findall(_section(self.npcs_text(), name))

    def kokoro_of(self, name) -> list:
        return _VOICE_LINE.findall(_section(self.npcs_text(), name))


# ── cast.npc_voices in Gemini mode (D21) ─────────────────────────────────

class TestCastGeminiVoices(_CampaignCase):

    def setUp(self):
        super().setUp()
        self.cast = _load_cast()

    def test_gemini_mode_assigns_and_writes_a_gemini_voice(self):
        self.display("gemini")
        self.npcs(_npc("Mira Ashgrove", "af_bella"))
        got = self.cast.npc_voices(self.name, "Mira Ashgrove", engine="gemini")
        self.assertIn(got.get("gemini"), GEMINI, "no Gemini voice returned in Gemini mode")
        self.assertEqual(got.get("voice"), "af_bella", "the Kokoro voice changed")
        self.assertEqual(self.gemini_of("Mira Ashgrove"), [got["gemini"]],
                         "the Gemini voice was not written back to npcs.md")
        self.assertEqual(self.kokoro_of("Mira Ashgrove"), ["af_bella"])

    def test_existing_gemini_voice_kept_and_nothing_written(self):
        self.display("gemini")
        self.npcs(_npc("Mira Ashgrove", "af_bella", "Zephyr"))
        before = self.npcs_text()
        got = self.cast.npc_voices(self.name, "Mira Ashgrove", engine="gemini")
        self.assertEqual(got, {"voice": "af_bella", "gemini": "Zephyr"})
        self.assertEqual(self.npcs_text(), before)

    def test_new_npc_in_gemini_mode_gets_both_voices(self):
        self.display("gemini")
        self.npcs()
        got = self.cast.npc_voices(self.name, "Garrick", engine="gemini")
        self.assertIn(got.get("voice"), KOKORO)
        self.assertIn(got.get("gemini"), GEMINI)
        self.assertEqual(self.kokoro_of("Garrick"), [got["voice"]])
        self.assertEqual(self.gemini_of("Garrick"), [got["gemini"]])

    def test_gemini_voices_least_used_so_distinct_until_used_up(self):
        self.display("gemini")
        self.npcs(_npc("Old One", "af_bella", "Kore"), _npc("Old Two", "bf_lily", "Charon"))
        # Not tts.DEFAULT_VOICE: it reads the narration when Kokoro is down (D19).
        pool = GEMINI - {tts.DEFAULT_VOICE}
        seen = ["Kore", "Charon"]
        for i in range(len(pool) - 2):
            seen.append(self.cast.npc_voices(self.name, f"Npc{i}", engine="gemini").get("gemini"))
        self.assertEqual(sorted(seen), sorted(pool),
                         f"Gemini voices were not spread least-used first: {seen}")
        extra = self.cast.npc_voices(self.name, "Npc Extra", engine="gemini").get("gemini")
        self.assertIn(extra, GEMINI)

    def test_never_the_gemini_narrators_voice(self):
        # No NPC sounds like the narrator: a Gemini narrator's voice is out.
        self.display("gemini", narrator="Kore")
        self.npcs()
        got = [self.cast.npc_voices(self.name, f"Npc{i}", engine="gemini").get("gemini")
               for i in range(len(GEMINI) + 3)]
        self.assertTrue(all(g in GEMINI for g in got), got)
        self.assertNotIn("Kore", got, "an NPC was given the narrator's Gemini voice")

    def test_npc_holding_the_gemini_narrators_voice_is_moved(self):
        self.display("gemini", narrator="Kore")
        self.npcs(_npc("Mira Ashgrove", "af_bella", "Kore"))
        got = self.cast.npc_voices(self.name, "Mira Ashgrove", engine="gemini")
        self.assertIn(got.get("gemini"), GEMINI - {"Kore"})
        self.assertEqual(self.gemini_of("Mira Ashgrove"), [got["gemini"]])

    def test_kokoro_mode_writes_no_gemini_voice(self):
        self.display("kokoro")
        self.npcs(_npc("Mira Ashgrove", "af_bella"))
        before = self.npcs_text()
        got = self.cast.npc_voices(self.name, "Mira Ashgrove", engine="kokoro")
        self.assertEqual(got.get("voice"), "af_bella")
        self.assertFalse(got.get("gemini"))
        self.assertEqual(self.npcs_text(), before)


# ── the app ──────────────────────────────────────────────────────────────

class _AppCase(_CampaignCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def setUp(self):
        super().setUp()
        a = self.app
        a._tts_quota_until = 0.0
        a._rate_buckets.clear()
        a._kokoro_ok = True
        a._gemini_state.update(on=True, reason="")
        a._voice.update(on=True, reason="")
        a._display_reload()
        self.sent = []
        self.logged = []
        for p in (mock.patch.object(a, "_broadcast", side_effect=self.sent.append),
                  mock.patch.object(a, "_persist_log"),
                  mock.patch.object(a, "_persist_tail"),
                  mock.patch.object(a, "_active_campaign_name", return_value=self.name),
                  mock.patch.object(a._tts, "key_source", return_value="env:GEMINI_API_KEY"),
                  mock.patch.object(a._tts, "kokoro_synthesize", return_value=b"\x01\x00" * 8)):
            p.start()
            self.addCleanup(p.stop)
        real_append = a._log_append

        def _capture(entry, payload, *rest, **kw):
            self.logged.append(dict(entry))
            return real_append(entry, payload, *rest, **kw)
        p = mock.patch.object(a, "_log_append", side_effect=_capture)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(setattr, a, "_kokoro_ok", False)
        self.addCleanup(setattr, a, "_tts_quota_until", 0.0)
        self.addCleanup(a._display_reload)

    def block(self, npc, text="Well met."):
        self.sent.clear()
        r = self.client.post("/chunk", json={"npc": npc, "text": text})
        self.assertIn(r.status_code, (200, 204))
        blocks = [b for b in self.sent if isinstance(b, dict) and b.get("text") and b.get("npc")]
        self.assertTrue(blocks, "no NPC block broadcast")
        return blocks[-1]

    def tts(self, block, text="A line."):
        body = {"text": text, "voice": block.get("voice")}
        if block.get("voice_fallback"):
            body["fallback"] = block["voice_fallback"]
        return self.client.post("/tts", json=body)

    def quota_broadcasts(self):
        return [m for m in self.sent if isinstance(m, dict)
                and ("tts_quota" in m or isinstance(m.get("voice"), dict))]


class TestChunkEngine(_AppCase):
    """D17/D21: what an NPC block carries in each mode."""

    def test_gemini_mode_block_carries_gemini_voice_and_kokoro_fallback(self):
        self.display("gemini")
        self.npcs(_npc("Mira Ashgrove", "af_bella", "Zephyr"))
        b = self.block("Mira Ashgrove")
        self.assertEqual(b.get("voice"), "Zephyr", "Gemini mode: the block should carry the Gemini voice")
        self.assertEqual(b.get("voice_fallback"), "af_bella",
                         "Gemini mode: voice_fallback should be the NPC's own Kokoro voice")

    def test_gemini_mode_assigns_missing_gemini_voice_on_chunk(self):
        self.display("gemini")
        self.npcs(_npc("Mira Ashgrove", "af_bella"))
        b = self.block("Mira Ashgrove")
        self.assertIn(b.get("voice"), GEMINI)
        self.assertEqual(b.get("voice_fallback"), "af_bella")
        self.assertEqual(self.gemini_of("Mira Ashgrove"), [b["voice"]],
                         "the assigned Gemini voice was not written to npcs.md")

    def test_gemini_mode_new_npc_gets_both_written(self):
        self.display("gemini")
        self.npcs()
        b = self.block("Garrick")
        self.assertIn(b.get("voice"), GEMINI)
        self.assertIn(b.get("voice_fallback"), KOKORO)
        self.assertEqual(self.gemini_of("Garrick"), [b["voice"]])
        self.assertEqual(self.kokoro_of("Garrick"), [b["voice_fallback"]])

    def test_gemini_mode_log_entry_keeps_the_fallback(self):
        # A TV that reconnects replays from text_log; its ▶ replay needs the fallback too.
        self.display("gemini")
        self.npcs(_npc("Mira Ashgrove", "af_bella", "Zephyr"))
        self.block("Mira Ashgrove")
        entry = [e for e in self.logged if e.get("npc")][-1]
        self.assertEqual((entry.get("voice"), entry.get("voice_fallback")), ("Zephyr", "af_bella"))

    def test_kokoro_mode_block_carries_kokoro_voice_only(self):
        self.display("kokoro")
        self.npcs(_npc("Mira Ashgrove", "af_bella", "Zephyr"))
        b = self.block("Mira Ashgrove")
        self.assertEqual(b.get("voice"), "af_bella")
        self.assertNotIn("voice_fallback", b)

    def test_kokoro_mode_never_calls_gemini_for_an_npc_block(self):
        self.display("kokoro")
        self.npcs(_npc("Mira Ashgrove", "af_bella", "Zephyr"), _npc("Bram", None, "Charon"))
        with mock.patch.object(self.app._tts, "synthesize_strict") as gem:
            for npc in ("Mira Ashgrove", "Bram", "Garrick"):
                r = self.tts(self.block(npc))
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.headers["X-Audio-Engine"], "kokoro")
        gem.assert_not_called()

    def test_default_mode_is_kokoro(self):
        self.npcs(_npc("Mira Ashgrove", "af_bella", "Zephyr"))   # no display.json
        b = self.block("Mira Ashgrove")
        self.assertEqual(b.get("voice"), "af_bella")
        self.assertNotIn("voice_fallback", b)

    def test_setting_applies_from_the_next_line(self):
        # AC14: the host flips the engine on the laptop page; the next NPC line follows.
        self.display("kokoro")
        self.npcs(_npc("Mira Ashgrove", "af_bella", "Zephyr"))
        self.assertEqual(self.block("Mira Ashgrove").get("voice"), "af_bella")
        r = self.client.post("/display-config", json={"voice": {"npc_engine": "gemini"}})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        b = self.block("Mira Ashgrove")
        self.assertEqual((b.get("voice"), b.get("voice_fallback")), ("Zephyr", "af_bella"))
        r = self.client.post("/display-config", json={"voice": {"npc_engine": "kokoro"}})
        self.assertEqual(r.status_code, 200)
        b = self.block("Mira Ashgrove")
        self.assertEqual(b.get("voice"), "af_bella")
        self.assertNotIn("voice_fallback", b)


class TestQuotaFallback(_AppCase):
    """AC17: with the quota used up, each NPC reads in its own Kokoro voice.

    Both NPCs share the Gemini voice Kore (least-used reuse past nine NPCs),
    so the old shared table would read both as af_kore."""

    # /tts bodies as the TV sends them for these two NPC blocks in Gemini mode.
    mira = {"voice": "Kore", "voice_fallback": "af_bella"}
    bram = {"voice": "Kore", "voice_fallback": "bf_lily"}

    def setUp(self):
        super().setUp()
        self.display("gemini")
        self.npcs(_npc("Mira Ashgrove", "af_bella", "Kore"), _npc("Bram", "bf_lily", "Kore"))

    def test_end_to_end_chunk_then_429(self):
        # The blocks /chunk sends, read through /tts after the quota is gone.
        mira, bram = self.block("Mira Ashgrove"), self.block("Bram")
        self.assertEqual((mira.get("voice"), mira.get("voice_fallback")), ("Kore", "af_bella"))
        self.assertEqual((bram.get("voice"), bram.get("voice_fallback")), ("Kore", "bf_lily"))
        self.sent.clear()
        err = tts.TtsError("http 429", **DAILY_429)
        with mock.patch.object(self.app._tts, "synthesize_strict", side_effect=err) as gem:
            r1, r2 = self.tts(mira, "first"), self.tts(bram, "second")
        self._assert_own_voices(r1, r2)
        self.assertEqual(gem.call_count, 1)
        self.assertEqual(self.quota_broadcasts(), [])

    def _assert_own_voices(self, r1, r2):
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertEqual(r1.headers["X-Audio-Engine"], "kokoro")
        self.assertEqual((r1.headers["X-Audio-Voice"], r2.headers["X-Audio-Voice"]),
                         ("af_bella", "bf_lily"), "an NPC was not read in its own Kokoro voice")

    def test_429_two_npcs_each_in_their_own_kokoro_voice(self):
        err = tts.TtsError("http 429", **DAILY_429)
        with mock.patch.object(self.app._tts, "synthesize_strict", side_effect=err) as gem:
            r1 = self.tts(self.mira, "first")       # the 429 itself
            r2 = self.tts(self.bram, "second")      # inside the quota window
        self._assert_own_voices(r1, r2)
        self.assertEqual(gem.call_count, 1)

    def test_kokoro_reads_with_the_fallback_it_was_given(self):
        seen = []

        def _kokoro(text, voice, **kw):
            seen.append(voice)
            return b"\x01\x00" * 8
        err = tts.TtsError("http 429", **DAILY_429)
        with mock.patch.object(self.app._tts, "synthesize_strict", side_effect=err), \
             mock.patch.object(self.app._tts, "kokoro_synthesize", side_effect=_kokoro):
            self.tts(self.mira, "first")
            self.tts(self.bram, "second")
        self.assertEqual(seen, ["af_bella", "bf_lily"])

    def test_gemini_off_uses_each_fallback(self):
        self.app._gemini_state.update(on=False, reason="x")
        with mock.patch.object(self.app._tts, "synthesize_strict") as gem:
            self._assert_own_voices(self.tts(self.mira), self.tts(self.bram))
        gem.assert_not_called()

    def test_gemini_failure_uses_each_fallback(self):
        err = tts.TtsError("http 500", status=500)
        with mock.patch.object(self.app._tts, "synthesize_strict", side_effect=err):
            self._assert_own_voices(self.tts(self.mira), self.tts(self.bram))

    def test_fallback_that_is_not_kokoro_is_ignored(self):
        err = tts.TtsError("http 429", **DAILY_429)
        with mock.patch.object(self.app._tts, "synthesize_strict", side_effect=err):
            for bad in ("Zephyr", "nobody"):
                r = self.client.post("/tts", json={"text": f"x {bad}", "voice": "Kore", "fallback": bad})
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.headers["X-Audio-Voice"], "af_kore")

    def test_no_fallback_still_uses_the_shared_standin(self):
        # tests/test_voice_kokoro_and_rewind.py:86-97 (the old page sends no fallback).
        err = tts.TtsError("http 429", **DAILY_429)
        with mock.patch.object(self.app._tts, "synthesize_strict", side_effect=err):
            r1 = self.client.post("/tts", json={"text": "first", "voice": "Kore"})
            r2 = self.client.post("/tts", json={"text": "second", "voice": "Kore"})
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertEqual(r2.headers["X-Audio-Voice"], "af_kore")

    def test_quota_note_on_display_config_and_nothing_to_the_tv(self):
        before = self.client.get("/display-config").get_json()
        self.assertFalse((before.get("status") or {}).get("voice_note"))
        err = tts.TtsError("http 429", **DAILY_429)
        with mock.patch.object(self.app._tts, "synthesize_strict", side_effect=err):
            self.tts(self.mira, "first")
            self.tts(self.bram, "second")
        status = self.client.get("/display-config").get_json().get("status") or {}
        self.assertRegex(str(status.get("voice_note", "")), NOTE,
                         f"no quota note under /display-config status: {status!r}")
        self.assertEqual(self.quota_broadcasts(), [],
                         "a tts_quota or voice-state broadcast went out to the TV")
        self.assertTrue(self.app._voice_payload()["on"])


if __name__ == "__main__":
    unittest.main()
