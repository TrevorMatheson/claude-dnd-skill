"""The cast: every NPC has its own voice (display foundation, plan step S11).

AC15 — An NPC that speaks with no voice is given one, written to npcs.md,
       never the narrator's; it has the same voice next session.
AC16 — No two NPCs share a voice until the pool is used up (blends are S12).
D18  — Voices are stored with the characters (npcs.md, characters/*.md).
D19  — The server assigns the least-used free voice and writes it back. No NPC
       is read in the narrator's voice.
D21  — Each NPC has a Kokoro voice; an old Gemini-named Voice: line is
       migrated to a Kokoro voice plus a `Gemini voice:` line.

Assumed interface (from the plan's "Cast and voices" section and S11). The
builder should match it or tell QA what changed:

  skills/dnd/display/cast.py  — a library and a CLI. `campaign` is a campaign
  NAME, resolved with paths.find_campaign (the sandboxed DND_CAMPAIGN_ROOT).

    npc_voices(campaign, name, engine="kokoro") -> {"voice": str, ...}
        Ensures the NPC has a Kokoro voice and returns it under "voice" (in
        Kokoro mode; Gemini mode is S13). Name matching: a case-insensitive
        exact heading match first, then a UNIQUE heading whose first word
        matches; otherwise a stub `### <name>` + `- **Voice:** <v>` is
        appended to npcs.md. A placeholder or Gemini-named Voice: line is
        replaced (Gemini: the old stand-in tts.GEMINI_TO_KOKORO[name] when it
        is free, plus a new `- **Gemini voice:** <name>` line). Nothing is
        written when the NPC already has a Kokoro voice. Writes keep the
        file's line endings.
    narrator_voice(campaign) -> str
        display.json voice.narrator, else state.md `tts_voice:`, else
        tts.DEFAULT_NARRATOR ("bm_lewis").
    free_voices(campaign) -> list[str]
        tts.KOKORO_VOICES minus the narrator, player-character voices and
        voices NPCs hold.
    set_voice(campaign, kind, name, voice)
        kind "npc" | "pc" | "narrator" (name ignored for narrator). Raises
        ValueError on an invalid voice, or an NPC given the narrator's or a
        player character's voice. Setting the narrator to a voice an NPC holds
        re-assigns that NPC.
    cast_list(campaign) -> {"narrator": str,
                            "pcs":  [{"name": str, "voice": str}, ...],
                            "npcs": [{"name": str, "voice": str}, ...]}

  Player characters: <campaign>/characters/<name>.md, `# Name` heading,
  `- **Voice:** <kokoro>` under `## Identity`.

  CLI:
    python cast.py voices --free -c <campaign>
        exit 0; prints each free voice id, grouped by accent (British /
        American) and sex (male / female).
    python cast.py set -c <campaign> --npc <name> <voice>
    python cast.py set -c <campaign> --pc <name> <voice>
        exit 0 and the voice written; non-zero and nothing written when the
        voice is invalid (not Kokoro), or an NPC would get the narrator's or
        a player character's voice.

  App (dnd-display-app.py):
    /chunk with an `npc` block -> the block's `voice` comes from
        cast.npc_voices (assigned and written when missing).
    GET /cast  -> 200, cast.cast_list(active campaign) as JSON.
    POST /cast {"kind": "npc"|"pc"|"narrator", "name": str, "voice": str}
        token plus loopback, else 403 and nothing written; invalid voice 400.
    POST /voice {"voice": v} -> narrator_voice() becomes v and an NPC that
        held v is re-assigned.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
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
NARRATOR_DEFAULT = "bm_lewis"


# ── helpers ──────────────────────────────────────────────────────────────

def _load_cast():
    """A fresh instance of cast.py (a new import = a new session)."""
    if not CAST.exists():
        raise AssertionError("skills/dnd/display/cast.py does not exist yet (S11)")
    spec = importlib.util.spec_from_file_location(f"cast_{uuid.uuid4().hex[:6]}", str(CAST))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_app():
    spec = importlib.util.spec_from_file_location(f"dnd_display_app_cast_{uuid.uuid4().hex[:6]}",
                                                  str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError as exc:
        raise unittest.SkipTest(f"display app not importable: {exc}")
    if mod._tts is None:
        raise unittest.SkipTest("tts module unavailable")
    return mod


def _section(text: str, name: str) -> str:
    """The body of `### name` (up to the next ### or ## heading); '' if none."""
    text = text.replace("\r\n", "\n")
    m = re.search(rf"^###[ \t]+{re.escape(name)}[ \t]*\n(.*?)(?=^##|\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


_VOICE_LINE = re.compile(r"^\s*-\s*\*\*Voice:\*\*\s*(\S+)", re.M)
_GEMINI_LINE = re.compile(r"^\s*-\s*\*\*Gemini voice:\*\*\s*(\S+)", re.M)


def _voices_in(sec: str) -> list:
    return _VOICE_LINE.findall(sec)


def _npc(name, voice=None, extra="- **Role:** villager\n"):
    v = f"- **Voice:** {voice}\n" if voice else ""
    return f"### {name}\n{extra}{v}\n"


class _CampaignCase(unittest.TestCase):
    """A real campaign folder under the sandboxed campaign root."""

    def setUp(self):
        self.name = f"cast-{uuid.uuid4().hex[:8]}"
        self.camp = pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"]) / "campaigns" / self.name
        (self.camp / "characters").mkdir(parents=True)
        (self.camp / "state.md").write_text("# State\n\n## Session Flags\n", encoding="utf-8")
        self.addCleanup(shutil.rmtree, str(self.camp), True)

    # files
    def npcs(self, *sections, header="# NPCs — test\n\n"):
        (self.camp / "npcs.md").write_bytes((header + "".join(sections)).encode("utf-8"))

    def npcs_text(self) -> str:
        return (self.camp / "npcs.md").read_bytes().decode("utf-8")

    def pc(self, name, voice=None):
        v = f"- **Voice:** {voice}\n" if voice else ""
        (self.camp / "characters" / f"{name.lower()}.md").write_bytes((
            f"# {name}\n**Player:** p  **Campaign:** {self.name}\n\n## Identity\n"
            f"- **Race:** Human | **Class:** Fighter | **Level:** 3\n{v}\n## Character Pillar\n- x\n"
        ).encode("utf-8"))

    def display_narrator(self, voice):
        (self.camp / "display.json").write_text(json.dumps(
            {"version": 1, "layout": {"preset": "story", "left": ["party"], "right": ["quests", "clock"]},
             "voice": {"narrator": voice, "npc_engine": "kokoro", "volume": 1.0, "speed": 1.0,
                       "read_player_actions": False}}), encoding="utf-8")

    def state_narrator(self, voice):
        (self.camp / "state.md").write_text(f"# State\n\n## Session Flags\ntts_voice: {voice}\n",
                                            encoding="utf-8")


# ── AC15 / D19: an NPC that speaks with no voice gets one, written back ──

class TestAssign(_CampaignCase):

    def setUp(self):
        super().setUp()
        self.cast = _load_cast()

    def test_new_npc_gets_stub_section_with_voice(self):
        self.npcs(_npc("Mira Ashgrove", "af_bella"))
        v = self.cast.npc_voices(self.name, "Garrick")["voice"]
        self.assertIn(v, KOKORO)
        sec = _section(self.npcs_text(), "Garrick")
        self.assertEqual(_voices_in(sec), [v], f"stub section for Garrick:\n{self.npcs_text()}")
        self.assertIn("### Mira Ashgrove", self.npcs_text(), "existing NPC lost")

    def test_npc_section_without_voice_line_gets_one(self):
        self.npcs(_npc("Old Tam"), _npc("Mira Ashgrove", "af_bella"))
        v = self.cast.npc_voices(self.name, "Old Tam")["voice"]
        self.assertIn(v, KOKORO)
        self.assertEqual(_voices_in(_section(self.npcs_text(), "Old Tam")), [v])
        self.assertEqual(self.npcs_text().count("### Old Tam"), 1, "a duplicate section was added")

    def test_placeholder_voice_is_replaced(self):
        self.npcs(_npc("Old Tam", "<read-aloud voice — pick one>"))
        v = self.cast.npc_voices(self.name, "Old Tam")["voice"]
        self.assertIn(v, KOKORO)
        sec = _section(self.npcs_text(), "Old Tam")
        self.assertEqual(_voices_in(sec), [v], f"one Voice: line expected:\n{sec}")
        self.assertNotIn("read-aloud voice", sec)

    def test_existing_kokoro_voice_kept_and_nothing_written(self):
        self.npcs(_npc("Mira Ashgrove", "af_bella"))
        before = (self.camp / "npcs.md").read_bytes()
        self.assertEqual(self.cast.npc_voices(self.name, "Mira Ashgrove")["voice"], "af_bella")
        self.assertEqual((self.camp / "npcs.md").read_bytes(), before)

    def test_never_the_narrator_or_a_pc_voice(self):
        # Narrator from display.json, two PCs; assign NPCs until the pool is gone.
        self.display_narrator("am_adam")
        self.pc("Aldric", "am_onyx")
        self.pc("Brenna", "bf_emma")
        self.npcs()
        forbidden = {"am_adam", "am_onyx", "bf_emma"}
        for i in range(len(KOKORO) + 3):
            v = self.cast.npc_voices(self.name, f"Stranger{i:02d}")["voice"]
            self.assertNotIn(v, forbidden, f"NPC {i} got a reserved voice")

    def test_same_voice_next_session(self):
        self.npcs(_npc("Mira Ashgrove", "af_bella"))
        v = self.cast.npc_voices(self.name, "Garrick")["voice"]
        after_first = (self.camp / "npcs.md").read_bytes()
        fresh = _load_cast()   # a new import: nothing cached in memory
        self.assertEqual(fresh.npc_voices(self.name, "garrick")["voice"], v)
        self.assertEqual((self.camp / "npcs.md").read_bytes(), after_first,
                         "resolving a known NPC rewrote npcs.md")


# ── AC16: no two NPCs share a voice until the pool is used up ────────────

class TestDistinct(_CampaignCase):

    def setUp(self):
        super().setUp()
        self.cast = _load_cast()

    def test_twenty_plus_new_npcs_all_distinct(self):
        self.display_narrator("bm_lewis")
        self.pc("Aldric", "am_onyx")
        self.pc("Brenna", "bf_emma")
        self.npcs(_npc("Mira Ashgrove", "af_bella"))
        pool = KOKORO - {"bm_lewis", "am_onyx", "bf_emma", "af_bella"}
        self.assertGreaterEqual(len(pool), 20)
        self.assertEqual(set(self.cast.free_voices(self.name)), pool)
        got = []
        for i in range(len(pool)):
            got.append(self.cast.npc_voices(self.name, f"Villager{i:02d}")["voice"])
        self.assertEqual(len(set(got)), len(got), f"shared voices: {got}")
        self.assertEqual(set(got), pool)
        self.assertEqual(self.cast.free_voices(self.name), [])
        text = self.npcs_text()
        for i, v in enumerate(got):
            self.assertEqual(_voices_in(_section(text, f"Villager{i:02d}")), [v])


# ── Line endings ─────────────────────────────────────────────────────────

class TestLineEndings(_CampaignCase):

    def setUp(self):
        super().setUp()
        self.cast = _load_cast()

    def test_crlf_npcs_md_keeps_crlf(self):
        body = ("# NPCs\r\n\r\n### Mira Ashgrove\r\n- **Role:** innkeeper\r\n- **Voice:** Kore\r\n\r\n"
                "### Old Tam\r\n- **Voice:** <read-aloud voice — pick one>\r\n\r\n")
        (self.camp / "npcs.md").write_bytes(body.encode("utf-8"))
        self.cast.npc_voices(self.name, "Mira Ashgrove")   # migration: rewrite + add a line
        self.cast.npc_voices(self.name, "Old Tam")          # placeholder replaced
        self.cast.npc_voices(self.name, "Garrick")          # stub appended
        data = (self.camp / "npcs.md").read_bytes()
        self.assertNotIn(b"\r\r", data)
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), f"bare LF in a CRLF file:\n{data!r}")
        self.assertIn(b"### Garrick", data)

    def test_lf_npcs_md_stays_lf(self):
        self.npcs(_npc("Mira Ashgrove", "Kore"))
        self.cast.npc_voices(self.name, "Mira Ashgrove")
        self.cast.npc_voices(self.name, "Garrick")
        self.assertNotIn(b"\r", (self.camp / "npcs.md").read_bytes())


# ── D21: a Gemini-named Voice: line is migrated ──────────────────────────

class TestGeminiMigration(_CampaignCase):

    def setUp(self):
        super().setUp()
        self.cast = _load_cast()

    def test_gemini_voice_migrated_to_stand_in(self):
        self.npcs(_npc("Brother Hale", "Charon"))
        v = self.cast.npc_voices(self.name, "Brother Hale")["voice"]
        self.assertEqual(v, tts.GEMINI_TO_KOKORO["Charon"])   # bm_george, free
        sec = _section(self.npcs_text(), "Brother Hale")
        self.assertEqual(_voices_in(sec), [v])
        self.assertEqual(_GEMINI_LINE.findall(sec), ["Charon"])

    def test_gemini_voice_migrated_elsewhere_when_stand_in_taken(self):
        self.npcs(_npc("Sela", "af_kore"), _npc("Mira Ashgrove", "Kore"))
        v = self.cast.npc_voices(self.name, "Mira Ashgrove")["voice"]
        self.assertIn(v, KOKORO)
        self.assertNotEqual(v, "af_kore", "took a voice another NPC holds")
        sec = _section(self.npcs_text(), "Mira Ashgrove")
        self.assertEqual(_voices_in(sec), [v])
        self.assertEqual(_GEMINI_LINE.findall(sec), ["Kore"])
        self.assertEqual(_voices_in(_section(self.npcs_text(), "Sela")), ["af_kore"])

    def test_migration_happens_once(self):
        self.npcs(_npc("Mira Ashgrove", "Kore"))
        v = self.cast.npc_voices(self.name, "Mira Ashgrove")["voice"]
        once = (self.camp / "npcs.md").read_bytes()
        self.assertEqual(_load_cast().npc_voices(self.name, "Mira Ashgrove")["voice"], v)
        self.assertEqual((self.camp / "npcs.md").read_bytes(), once)


# ── Name matching ────────────────────────────────────────────────────────

class TestNameMatching(_CampaignCase):

    def setUp(self):
        super().setUp()
        self.cast = _load_cast()

    def test_exact_match_case_insensitive(self):
        self.npcs(_npc("Mira Ashgrove", "af_bella"))
        self.assertEqual(self.cast.npc_voices(self.name, "mIRA ashgrove")["voice"], "af_bella")
        self.assertNotIn("### mIRA", self.npcs_text())

    def test_exact_match_beats_first_word(self):
        self.npcs(_npc("Odessa Varn", "bf_alice"), _npc("Odessa", "bf_lily"))
        self.assertEqual(self.cast.npc_voices(self.name, "ODESSA")["voice"], "bf_lily")

    def test_unique_first_word_matches(self):
        self.npcs(_npc("Odessa Varn", "bf_alice"))
        self.assertEqual(self.cast.npc_voices(self.name, "odessa")["voice"], "bf_alice")
        self.assertEqual(len(re.findall(r"^### ", self.npcs_text(), re.M)), 1, "a stub was added")

    def test_ambiguous_first_word_gets_a_stub(self):
        self.npcs(_npc("Tam Hollow", "am_liam"), _npc("Tam Brightwater", "am_eric"))
        v = self.cast.npc_voices(self.name, "Tam")["voice"]
        self.assertNotIn(v, {"am_liam", "am_eric"})
        self.assertEqual(_voices_in(_section(self.npcs_text(), "Tam")), [v])

    def test_template_subheadings_are_not_npcs(self):
        # templates/npcs.md puts ### Personality and ### Relationships under
        # each NPC; they are not cast members and must not get voices.
        self.npcs("### Mira Ashgrove\n- **Voice:** af_bella\n\n"
                  "### Personality\n- **Brave ↔ Cowardly:** brave\n\n"
                  "### Relationships\n- **Knows:** Tam\n\n")
        before = (self.camp / "npcs.md").read_bytes()
        names = [n["name"] for n in self.cast.cast_list(self.name)["npcs"]]
        self.assertEqual(names, ["Mira Ashgrove"])
        self.assertEqual((self.camp / "npcs.md").read_bytes(), before, "cast_list wrote npcs.md")


# ── Narrator resolution: display.json -> state.md -> default ─────────────

class TestNarrator(_CampaignCase):

    def setUp(self):
        super().setUp()
        self.cast = _load_cast()

    def test_display_json_first(self):
        self.display_narrator("bm_george")
        self.state_narrator("bm_daniel")
        self.assertEqual(self.cast.narrator_voice(self.name), "bm_george")

    def test_state_md_second(self):
        self.state_narrator("bm_daniel")
        self.assertEqual(self.cast.narrator_voice(self.name), "bm_daniel")
        self.assertNotIn("bm_daniel", self.cast.free_voices(self.name))

    def test_default_last(self):
        self.assertEqual(self.cast.narrator_voice(self.name), NARRATOR_DEFAULT)
        self.assertNotIn(NARRATOR_DEFAULT, self.cast.free_voices(self.name))

    def test_gemini_narrator_still_reserves_the_voice_that_reads(self):
        # A campaign saved before Kokoro keeps a Gemini narrator (Charon) in
        # state.md, but bm_lewis reads the narration: no NPC may get it.
        self.state_narrator("Charon")
        self.npcs()
        self.assertNotIn(NARRATOR_DEFAULT, self.cast.free_voices(self.name))
        for i in range(len(KOKORO) + 2):
            self.assertNotEqual(self.cast.npc_voices(self.name, f"Guard{i:02d}")["voice"],
                                NARRATOR_DEFAULT, f"NPC {i} got the voice that reads the narration")

    def test_saving_narrator_held_by_npc_reassigns_npc(self):
        self.npcs(_npc("Brin", "am_adam"), _npc("Mira Ashgrove", "af_bella"))
        self.cast.set_voice(self.name, "narrator", None, "am_adam")
        self.assertEqual(self.cast.narrator_voice(self.name), "am_adam")
        new = _voices_in(_section(self.npcs_text(), "Brin"))
        self.assertEqual(len(new), 1, self.npcs_text())
        self.assertIn(new[0], KOKORO)
        self.assertNotIn(new[0], {"am_adam", "af_bella"})
        self.assertEqual(_voices_in(_section(self.npcs_text(), "Mira Ashgrove")), ["af_bella"])


# ── CLI ──────────────────────────────────────────────────────────────────

class TestCli(_CampaignCase):

    def setUp(self):
        super().setUp()
        if not CAST.exists():
            self.fail("skills/dnd/display/cast.py does not exist yet (S11)")
        self.display_narrator("bm_lewis")
        self.pc("Aldric", "am_onyx")
        self.npcs(_npc("Mira Ashgrove", "af_bella"), _npc("Odessa Varn", "bf_alice"))

    def run_cast(self, *args):
        return subprocess.run([sys.executable, str(CAST), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)

    def test_voices_free_lists_only_free_voices(self):
        r = self.run_cast("voices", "--free", "-c", self.name)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        ids = set(re.findall(r"\b[ab][fm]_[a-z]+\b", out))
        self.assertEqual(ids, KOKORO - {"bm_lewis", "am_onyx", "af_bella", "bf_alice"}, out)
        low = out.lower()
        for word in ("british", "american", "female", "male"):
            self.assertIn(word, low, f"not grouped by accent and sex:\n{out}")

    def test_set_npc_writes_voice(self):
        r = self.run_cast("set", "-c", self.name, "--npc", "Mira Ashgrove", "af_sky")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_voices_in(_section(self.npcs_text(), "Mira Ashgrove")), ["af_sky"])

    def test_set_npc_first_word_match(self):
        r = self.run_cast("set", "-c", self.name, "--npc", "Odessa", "bf_lily")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_voices_in(_section(self.npcs_text(), "Odessa Varn")), ["bf_lily"])
        self.assertEqual(len(re.findall(r"^### ", self.npcs_text(), re.M)), 2)

    def test_set_npc_refuses_bad_voices(self):
        before = (self.camp / "npcs.md").read_bytes()
        for voice, why in (("robot", "unknown"), ("Kore", "a Gemini name"),
                           ("bm_lewis", "the narrator's"), ("am_onyx", "a player character's")):
            r = self.run_cast("set", "-c", self.name, "--npc", "Mira Ashgrove", voice)
            self.assertNotEqual(r.returncode, 0, f"accepted {why} voice {voice}")
            self.assertEqual((self.camp / "npcs.md").read_bytes(), before, f"{voice} was written")

    def test_set_pc_writes_identity_voice(self):
        r = self.run_cast("set", "-c", self.name, "--pc", "Aldric", "am_liam")
        self.assertEqual(r.returncode, 0, r.stderr)
        sheet = (self.camp / "characters" / "aldric.md").read_text(encoding="utf-8")
        identity = re.search(r"^## Identity\n(.*?)(?=^## |\Z)", sheet, re.M | re.S).group(1)
        self.assertEqual(_voices_in(identity), ["am_liam"], sheet)
        self.assertEqual(len(_voices_in(sheet)), 1, sheet)

    def test_set_pc_refuses_unknown_voice(self):
        before = (self.camp / "characters" / "aldric.md").read_bytes()
        r = self.run_cast("set", "-c", self.name, "--pc", "Aldric", "robot")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((self.camp / "characters" / "aldric.md").read_bytes(), before)


# ── App: /chunk, /voice, GET/POST /cast ──────────────────────────────────

class _AppCase(_CampaignCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def setUp(self):
        super().setUp()
        self.sent = []
        for p in (mock.patch.object(self.app, "_broadcast", side_effect=self.sent.append),
                  mock.patch.object(self.app, "_persist_log"),
                  mock.patch.object(self.app, "_persist_tail"),
                  mock.patch.object(self.app, "_active_campaign_name", return_value=self.name)):
            p.start()
            self.addCleanup(p.stop)


class TestChunkAssigns(_AppCase):

    def _npc_block(self, app, client, npc, text):
        self.sent.clear()
        client.post("/chunk", json={"npc": npc, "text": text})
        blocks = [b for b in self.sent if isinstance(b, dict) and b.get("text")]
        self.assertTrue(blocks, "no block broadcast")
        return blocks[-1]

    def test_npc_block_without_voice_gets_one_written(self):
        self.display_narrator("bm_lewis")
        self.pc("Aldric", "am_onyx")
        self.npcs(_npc("Mira Ashgrove", "af_bella"))
        v = self._npc_block(self.app, self.client, "Garrick", "Well met.").get("voice")
        self.assertIn(v, KOKORO, "the NPC block carries no Kokoro voice")
        self.assertNotIn(v, {"bm_lewis", "am_onyx", "af_bella"})
        self.assertEqual(_voices_in(_section(self.npcs_text(), "Garrick")), [v])

    def test_same_voice_after_app_reload(self):
        self.display_narrator("bm_lewis")
        self.npcs(_npc("Mira Ashgrove", "af_bella"))
        v = self._npc_block(self.app, self.client, "Garrick", "Well met.").get("voice")
        self.assertIn(v, KOKORO, "the NPC block carries no Kokoro voice")
        app2 = _import_app()   # next session
        app2._token_ok = lambda: True
        with mock.patch.object(app2, "_broadcast", side_effect=self.sent.append), \
             mock.patch.object(app2, "_persist_log"), mock.patch.object(app2, "_persist_tail"), \
             mock.patch.object(app2, "_active_campaign_name", return_value=self.name):
            v2 = self._npc_block(app2, app2.app.test_client(), "Garrick", "Again.").get("voice")
        self.assertEqual(v2, v)


class TestNarratorSave(_AppCase):

    def test_voice_post_reassigns_npc_holding_it(self):
        self.npcs(_npc("Brin", "am_adam"))
        r = self.client.post("/voice", json={"voice": "am_adam"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(_load_cast().narrator_voice(self.name), "am_adam")
        new = _voices_in(_section(self.npcs_text(), "Brin"))
        self.assertEqual(len(new), 1)
        self.assertNotEqual(new[0], "am_adam", "the NPC still holds the narrator's voice")
        self.assertIn(new[0], KOKORO)


class TestCastEndpoints(_AppCase):

    def setUp(self):
        super().setUp()
        self.display_narrator("bm_lewis")
        self.pc("Aldric", "am_onyx")
        self.npcs(_npc("Mira Ashgrove", "af_bella"), _npc("Brin", "am_adam"))

    def test_get_cast(self):
        r = self.client.get("/cast")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        data = r.get_json()
        self.assertEqual(data["narrator"], "bm_lewis")
        self.assertIn({"name": "Aldric", "voice": "am_onyx"},
                      [{"name": p["name"], "voice": p["voice"]} for p in data["pcs"]])
        npcs = {n["name"]: n["voice"] for n in data["npcs"]}
        self.assertEqual(npcs.get("Mira Ashgrove"), "af_bella")
        self.assertEqual(npcs.get("Brin"), "am_adam")

    def test_post_npc_voice(self):
        r = self.client.post("/cast", json={"kind": "npc", "name": "Mira Ashgrove", "voice": "af_sky"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        self.assertEqual(_voices_in(_section(self.npcs_text(), "Mira Ashgrove")), ["af_sky"])

    def test_post_pc_voice(self):
        r = self.client.post("/cast", json={"kind": "pc", "name": "Aldric", "voice": "am_liam"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        sheet = (self.camp / "characters" / "aldric.md").read_text(encoding="utf-8")
        self.assertEqual(_voices_in(sheet), ["am_liam"])

    def test_post_narrator_reassigns_npc(self):
        r = self.client.post("/cast", json={"kind": "narrator", "voice": "am_adam"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        self.assertEqual(_load_cast().narrator_voice(self.name), "am_adam")
        self.assertNotEqual(_voices_in(_section(self.npcs_text(), "Brin")), ["am_adam"])

    def test_post_invalid_voice_400(self):
        before = (self.camp / "npcs.md").read_bytes()
        r = self.client.post("/cast", json={"kind": "npc", "name": "Mira Ashgrove", "voice": "robot"})
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True)[:300])
        self.assertEqual((self.camp / "npcs.md").read_bytes(), before)

    def test_post_from_lan_is_403(self):
        before = (self.camp / "npcs.md").read_bytes()
        r = self.client.post("/cast", json={"kind": "npc", "name": "Mira Ashgrove", "voice": "af_sky"},
                             environ_base={"REMOTE_ADDR": "192.168.1.50"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual((self.camp / "npcs.md").read_bytes(), before)

    def test_post_without_token_is_403(self):
        before = (self.camp / "npcs.md").read_bytes()
        with mock.patch.object(self.app, "_token_ok", return_value=False):
            r = self.client.post("/cast", json={"kind": "npc", "name": "Mira Ashgrove", "voice": "af_sky"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual((self.camp / "npcs.md").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
