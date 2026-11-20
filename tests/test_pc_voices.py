"""Player-character voices and "Read player actions aloud" (plan step S14).

AC18 — Importing or creating a player character stores a voice in its file
       that no NPC is ever given; "Read player actions aloud" is off by
       default; when on, typed actions (not Talk) are read in that voice
       before the DM's response.
D22  — Player characters have voices: stored in the character file, never
       given to an NPC. Off by default, per campaign. Spoken (Talk) actions
       are not read back.

Nothing here reaches Gemini, Kokoro or Whisper.

Assumed interface (from the plan's S14 and the code on the branch at S13).
The builder should match it or tell QA what changed:

  Character file: <campaign>/characters/<name>.md, `# Name` heading,
      `- **Voice:** <kokoro>` under `## Identity` (as S11 reads and writes it).
      skills/dnd/templates/character-sheet.md carries a `- **Voice:**` line
      under `## Identity`.

  cast.py (already there from S11):
      python cast.py set -c <campaign> --pc <name> <voice>
      cast.npc_voices(campaign, name) never returns a player character's voice.

  Back-fill (server). A player character whose file has no voice (no Voice:
      line, or a placeholder) is given one, and it is written into the
      file under `## Identity`:
        - when the cast list loads: GET /cast;
        - when that character's action is about to be read (read_actions).
      The voice is a single Kokoro voice (no "a+b" blend), free: not the
      narrator's, not one any NPC in npcs.md holds, not another PC's.
      ("Fitting" — matched to the character — is not checked here.)
      Expected home: cast.py (e.g. cast.pc_voice(campaign, name) -> str that
      assigns and writes when missing); the tests below go through the app.

  display.json voice.read_player_actions: bool, default False
      (display_config.default()). Read at action time via _display_config().

  /stt (POST, mic holder): on success, the server remembers that character's
      transcript text (as returned to the phone) and the time it was made.

  _check_auto_trigger(): when actions are taken from the staging area into
      the DM's inbox (.input_queue) and read_player_actions is on, the server
      broadcasts once, before the request that triggered it returns:
          {"read_actions": [{"character": str, "text": str, "voice": str}, ...]}
      one entry per taken action, in the order they were taken, where
      "voice" is the character's Kokoro voice from the character file (back-
      filled first when missing). An action is left out when its text
      contains that same character's /stt transcript from the last 10
      minutes (Talk actions are not read back). A transcript older than 10
      minutes, or another character's transcript, does not suppress it.
      When no action is left, no read_actions broadcast. When the setting is
      off, never a read_actions broadcast.

  Player actions arrive here through POST /player-input/stage (solo mode,
  or the awaited character, triggers at once) and POST /player-input/ready.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import types
import unittest
import uuid
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
CAST = DISPLAY / "cast.py"
TEMPLATE = REPO / "skills" / "dnd" / "templates" / "character-sheet.md"
for _p in (str(DISPLAY), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tts  # noqa: E402
import display_config  # noqa: E402

KOKORO = set(tts.KOKORO_VOICES)
NARRATOR = "bm_lewis"


# ── helpers ──────────────────────────────────────────────────────────────

def _load_cast():
    spec = importlib.util.spec_from_file_location(f"cast_pc_{uuid.uuid4().hex[:6]}", str(CAST))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_app():
    spec = importlib.util.spec_from_file_location(f"dnd_display_app_pc_{uuid.uuid4().hex[:6]}",
                                                  str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError as exc:
        raise unittest.SkipTest(f"display app not importable: {exc}")
    if mod._cast is None:
        raise unittest.SkipTest("cast module unavailable")
    return mod


_VOICE_LINE = re.compile(r"^\s*-\s*\*\*Voice:\*\*[ \t]*(\S+)", re.M)


def _voices_in(text: str) -> list:
    return [v for v in _VOICE_LINE.findall(text.replace("\r\n", "\n"))
            if re.fullmatch(r"[ab][fm]_[a-z]+(\+[ab][fm]_[a-z]+)?", v)]


def _identity(sheet: str) -> str:
    m = re.search(r"^## Identity[ \t]*\n(.*?)(?=^## |\Z)", sheet.replace("\r\n", "\n"), re.M | re.S)
    return m.group(1) if m else ""


def _npc(name, voice=None):
    v = f"- **Voice:** {voice}\n" if voice else ""
    return f"### {name}\n- **Role:** villager\n{v}\n"


class _CampaignCase(unittest.TestCase):
    """A real campaign folder under the sandboxed campaign root."""

    def setUp(self):
        self.name = f"pcv-{uuid.uuid4().hex[:8]}"
        self.camp = pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"]) / "campaigns" / self.name
        (self.camp / "characters").mkdir(parents=True)
        (self.camp / "state.md").write_text("# State\n\n## Session Flags\n", encoding="utf-8")
        self.addCleanup(shutil.rmtree, str(self.camp), True)

    def npcs(self, *sections):
        (self.camp / "npcs.md").write_bytes(("# NPCs — test\n\n" + "".join(sections)).encode("utf-8"))

    def pc(self, name, voice=None, voice_line=None):
        if voice_line is None:
            voice_line = f"- **Voice:** {voice}\n" if voice else ""
        (self.camp / "characters" / f"{name.lower()}.md").write_bytes((
            f"# {name}\n**Player:** p  **Campaign:** {self.name}\n\n## Identity\n"
            f"- **Race:** Human | **Class:** Fighter | **Level:** 3\n{voice_line}\n"
            f"## Character Pillar\n- x\n"
        ).encode("utf-8"))

    def sheet(self, name) -> str:
        return (self.camp / "characters" / f"{name.lower()}.md").read_bytes().decode("utf-8")

    def display(self, read_actions=None, narrator=NARRATOR):
        voice = {"narrator": narrator, "npc_engine": "kokoro", "volume": 1.0, "speed": 1.0}
        if read_actions is not None:
            voice["read_player_actions"] = read_actions
        (self.camp / "display.json").write_text(json.dumps(
            {"version": 1, "layout": {"preset": "story", "left": ["party"], "right": ["quests", "clock"]},
             "voice": voice}), encoding="utf-8")


# ── Guards from S11: cast.py set --pc, and no NPC gets a PC's voice ──────

class TestPcVoiceGuards(_CampaignCase):

    def test_set_pc_writes_voice_into_identity(self):
        self.display()
        self.pc("Aldric")
        r = subprocess.run([sys.executable, str(CAST), "set", "-c", self.name, "--pc", "Aldric", "am_liam"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        sheet = self.sheet("Aldric")
        self.assertEqual(_voices_in(_identity(sheet)), ["am_liam"], sheet)
        self.assertEqual(len(_voices_in(sheet)), 1, sheet)

    def test_npc_assignment_never_returns_a_pc_voice(self):
        self.display()
        self.pc("Aldric", "am_onyx")
        self.pc("Brenna", "bf_emma")
        self.npcs()
        cast = _load_cast()
        for i in range(len(KOKORO) + 5):
            v = cast.npc_voices(self.name, f"Stranger{i:02d}")["voice"]
            parts = set(v.split("+"))
            self.assertFalse(parts & {"am_onyx", "bf_emma"}, f"NPC {i} got {v}, a player character's voice")

    def test_template_has_a_voice_line_under_identity(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertRegex(_identity(text), r"(?m)^\s*-\s*\*\*Voice:\*\*",
                         "character-sheet.md has no `- **Voice:**` line under ## Identity")

    def test_setting_defaults_to_off(self):
        self.assertIs(display_config.default()["voice"].get("read_player_actions"), False)
        # A display.json without the key reads as off too.
        self.display(read_actions=None)
        self.assertIs(display_config.load(str(self.camp))["voice"].get("read_player_actions"), False)


# ── App harness ──────────────────────────────────────────────────────────

class _AppCase(_CampaignCase):

    DEVICE = {"X-DND-Device": "pc-voices-test-device"}

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.app._rate_ok = lambda *a, **k: True
        cls.client = cls.app.app.test_client()

    def setUp(self):
        super().setUp()
        app = self.app
        self.sent = []
        self.events = []      # broadcasts and inbox writes, in order
        real_append = app._queue_append

        def _bc(payload):
            self.sent.append(payload)
            self.events.append(("broadcast", payload))

        def _qa(content):
            self.events.append(("queue", content))
            return real_append(content)

        for p in (mock.patch.object(app, "_broadcast", side_effect=_bc),
                  mock.patch.object(app, "_queue_append", side_effect=_qa),
                  mock.patch.object(app, "_rewind_mark"),
                  mock.patch.object(app, "_persist_log"),
                  mock.patch.object(app, "_persist_tail"),
                  mock.patch.object(app, "_active_campaign_name", return_value=self.name)):
            p.start()
            self.addCleanup(p.stop)
        # Staging state is module-global: start each test clean.
        saved = (app._current_stats, app._expected_count, app._autorun_threshold, app._awaiting)
        self.addCleanup(self._restore, saved)
        app._current_stats = {"players": []}
        app._expected_count = 1
        app._autorun_threshold = None
        app._awaiting = None
        with app._staged_lock:
            app._staged.clear()
        app._mic.update(holder="", device="", since=0.0)
        getattr(app, "_stt_said", {}).clear()   # Talk transcripts (S14)
        try:
            os.remove(app.QUEUE_FILE)
        except OSError:
            pass
        app._display_reload()

    def _restore(self, saved):
        app = self.app
        app._current_stats, app._expected_count, app._autorun_threshold, app._awaiting = saved
        with app._staged_lock:
            app._staged.clear()
        app._mic.update(holder="", device="", since=0.0)
        getattr(app, "_stt_said", {}).clear()   # Talk transcripts (S14)
        app._display_reload()
        try:
            os.remove(app.QUEUE_FILE)
        except OSError:
            pass

    # party
    def party(self, *names):
        self.app._current_stats = {"players": [{"name": n} for n in names]}
        self.app._expected_count = len(names)

    # actions
    def stage(self, who, text):
        r = self.client.post("/player-input/stage", json={"character": who, "text": text},
                             headers=self.DEVICE)
        self.assertEqual(r.status_code, 204, r.get_data(as_text=True)[:300])

    def ready(self, who):
        r = self.client.post("/player-input/ready", json={"character": who, "ready": True},
                             headers=self.DEVICE)
        self.assertEqual(r.status_code, 204, r.get_data(as_text=True)[:300])

    def read_actions(self) -> list:
        return [p["read_actions"] for p in self.sent if isinstance(p, dict) and "read_actions" in p]

    def queued(self) -> bool:
        return any(k == "queue" for k, _ in self.events)

    # talk: a phone's clip through /stt, transcribed as `words`
    def talk(self, who, words):
        fake = types.SimpleNamespace(
            MAX_CLIP_BYTES=8 * 1024 * 1024,
            state=lambda: {"on": True, "reason": ""},
            transcribe=lambda audio, hints=None: words,
            load=lambda: None,
        )
        self.app._mic.update(holder=who, device="", since=time.time())
        with mock.patch.object(self.app, "_stt", fake):
            r = self.client.post("/stt", data={"character": who, "audio": (io.BytesIO(b"\0" * 512), "clip.webm")},
                                 content_type="multipart/form-data", headers=self.DEVICE)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        self.assertEqual(r.get_json().get("text"), words)


# ── Back-fill: a PC with no voice gets a free single Kokoro voice ────────

class TestBackfill(_AppCase):

    def _tight_pool(self):
        """NPCs hold every Kokoro voice but the narrator's, Aldric's and one."""
        self.display(read_actions=True)
        self.pc("Aldric", "am_onyx")
        self.pc("Brenna")
        left = sorted(KOKORO - {NARRATOR, "am_onyx"})
        self.free = left[-1]
        self.npcs(*[_npc(f"Npc{i:02d}", v) for i, v in enumerate(left[:-1])])
        return left[:-1]

    def _check_brenna(self, expect=None):
        sheet = self.sheet("Brenna")
        got = _voices_in(_identity(sheet))
        self.assertEqual(len(got), 1, f"no voice written under ## Identity:\n{sheet}")
        v = got[0]
        self.assertIn(v, KOKORO, f"{v} is not a single Kokoro voice")
        self.assertNotIn(v, {NARRATOR, "am_onyx"}, f"Brenna got a reserved voice {v}")
        if expect:
            self.assertEqual(v, expect, "Brenna was not given the one free voice")
        return v

    def test_get_cast_backfills_pc_with_no_voice(self):
        self.display()
        self.pc("Aldric", "am_onyx")
        self.pc("Brenna")
        self.npcs(_npc("Mira Ashgrove", "af_bella"))
        r = self.client.get("/cast")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        v = self._check_brenna()
        self.assertNotEqual(v, "af_bella", "Brenna was given an NPC's voice")
        pcs = {p["name"]: p["voice"] for p in r.get_json()["pcs"]}
        self.assertEqual(pcs.get("Brenna"), v, "GET /cast does not show the back-filled voice")
        self.assertEqual(pcs.get("Aldric"), "am_onyx", "an existing PC voice changed")

    def test_get_cast_backfill_takes_the_free_voice_not_an_npcs(self):
        self._tight_pool()
        self.client.get("/cast")
        self._check_brenna(expect=self.free)

    def test_get_cast_replaces_placeholder_voice(self):
        self.display()
        self.pc("Brenna", voice_line="- **Voice:** <read-aloud voice>\n")
        self.npcs()
        self.client.get("/cast")
        sheet = self.sheet("Brenna")
        self._check_brenna()
        self.assertNotIn("read-aloud voice", sheet)
        self.assertEqual(len(_VOICE_LINE.findall(sheet)), 1, f"two Voice: lines:\n{sheet}")

    def test_backfilled_voice_is_kept_from_npcs(self):
        self.display()
        self.pc("Brenna")
        self.npcs()
        self.client.get("/cast")
        v = self._check_brenna()
        cast = _load_cast()
        for i in range(len(KOKORO) + 5):
            got = cast.npc_voices(self.name, f"Stranger{i:02d}")["voice"]
            self.assertNotIn(v, got.split("+"), f"NPC {i} got Brenna's back-filled voice")

    def test_reading_an_action_backfills_the_voice(self):
        self._tight_pool()
        self.party("Brenna")
        self.stage("Brenna", "I check the door for traps")
        v = self._check_brenna(expect=self.free)
        batches = self.read_actions()
        self.assertEqual(len(batches), 1, f"expected one read_actions broadcast: {self.sent}")
        self.assertEqual(batches[0], [{"character": "Brenna", "text": "I check the door for traps",
                                       "voice": v}])


# ── Read player actions aloud ────────────────────────────────────────────

class TestReadActions(_AppCase):

    def setUp(self):
        super().setUp()
        self.pc("Aldric", "am_onyx")
        self.pc("Brenna", "bf_emma")
        self.pc("Corwin", "am_liam")
        self.npcs(_npc("Mira Ashgrove", "af_bella"))

    def test_off_by_default_no_broadcast(self):
        # No display.json at all: the default is off.
        self.party("Aldric")
        self.stage("Aldric", "I draw my sword")
        self.assertTrue(self.queued(), "the action never reached the DM's inbox")
        self.assertEqual(self.read_actions(), [], "read_actions sent with the setting off")

    def test_off_explicitly_no_broadcast(self):
        self.display(read_actions=False)
        self.party("Aldric")
        self.stage("Aldric", "I draw my sword")
        self.assertTrue(self.queued(), "the action never reached the DM's inbox")
        self.assertEqual(self.read_actions(), [], "read_actions sent with the setting off")

    def test_on_typed_action_read_in_character_voice(self):
        self.display(read_actions=True)
        self.party("Aldric")
        self.stage("Aldric", "I draw my sword")
        batches = self.read_actions()
        self.assertEqual(len(batches), 1, f"expected one read_actions broadcast: {self.sent}")
        self.assertEqual(batches[0], [{"character": "Aldric", "text": "I draw my sword", "voice": "am_onyx"}])

    def test_on_read_before_dm_can_respond(self):
        # The broadcast goes out in the same request that hands the action to
        # the DM, so it is never behind the DM's reply.
        self.display(read_actions=True)
        self.party("Aldric")
        self.stage("Aldric", "I draw my sword")
        kinds = [k if k == "queue" else ("read" if "read_actions" in v else "other")
                 for k, v in self.events]
        self.assertIn("queue", kinds)
        self.assertIn("read", kinds, f"no read_actions by the time /player-input/stage returned: {self.events}")

    def test_on_batch_of_three_each_in_own_voice(self):
        self.display(read_actions=True)
        self.party("Aldric", "Brenna", "Corwin")
        self.stage("Aldric", "I hold the line")
        self.stage("Brenna", "I cast bless")
        self.stage("Corwin", "I sneak around the back")
        self.assertEqual(self.read_actions(), [], "read before the batch was ready")
        for who in ("Aldric", "Brenna", "Corwin"):
            self.ready(who)
        batches = self.read_actions()
        self.assertEqual(len(batches), 1, f"expected one read_actions broadcast: {self.sent}")
        got = {e["character"]: (e["text"], e["voice"]) for e in batches[0]}
        self.assertEqual(got, {"Aldric": ("I hold the line", "am_onyx"),
                               "Brenna": ("I cast bless", "bf_emma"),
                               "Corwin": ("I sneak around the back", "am_liam")})

    def test_talk_action_not_read_back(self):
        self.display(read_actions=True)
        self.party("Aldric")
        self.talk("Aldric", "I shout a warning to the others")
        self.stage("Aldric", "I shout a warning to the others")
        self.assertTrue(self.queued(), "the action never reached the DM's inbox")
        self.assertEqual(self.read_actions(), [], "a Talk action was read back")

    def test_talk_then_edited_still_not_read_back(self):
        # The player adds a few words to the transcript before sending.
        self.display(read_actions=True)
        self.party("Aldric")
        self.talk("Aldric", "I shout a warning to the others")
        self.stage("Aldric", "I shout a warning to the others and raise my shield")
        self.assertEqual(self.read_actions(), [], "an edited Talk action was read back")

    def test_mixed_batch_reads_only_the_typed_action(self):
        self.display(read_actions=True)
        self.party("Aldric", "Brenna")
        self.talk("Brenna", "I cast bless on everyone")
        self.stage("Aldric", "I hold the line")
        self.stage("Brenna", "I cast bless on everyone")
        self.ready("Aldric")
        self.ready("Brenna")
        batches = self.read_actions()
        self.assertEqual(len(batches), 1, f"expected one read_actions broadcast: {self.sent}")
        self.assertEqual(batches[0], [{"character": "Aldric", "text": "I hold the line", "voice": "am_onyx"}])

    def test_other_characters_transcript_does_not_suppress(self):
        self.display(read_actions=True)
        self.party("Aldric")
        self.app._current_stats = {"players": [{"name": "Aldric"}, {"name": "Brenna"}]}
        self.app._expected_count = 1
        self.talk("Brenna", "I draw my sword")
        self.stage("Aldric", "I draw my sword")
        batches = self.read_actions()
        self.assertEqual(len(batches), 1, f"Aldric's typed action was suppressed by Brenna's talk: {self.sent}")
        self.assertEqual(batches[0], [{"character": "Aldric", "text": "I draw my sword", "voice": "am_onyx"}])

    def test_transcript_older_than_ten_minutes_does_not_suppress(self):
        self.display(read_actions=True)
        self.party("Aldric")
        self.talk("Aldric", "I draw my sword")
        later = time.time() + 11 * 60
        with mock.patch.object(self.app._time, "time", return_value=later):
            self.stage("Aldric", "I draw my sword")
        batches = self.read_actions()
        self.assertEqual(len(batches), 1, f"a transcript from 11 minutes ago still suppressed it: {self.sent}")
        self.assertEqual(batches[0], [{"character": "Aldric", "text": "I draw my sword", "voice": "am_onyx"}])

    def test_transcript_within_ten_minutes_suppresses(self):
        self.display(read_actions=True)
        self.party("Aldric")
        self.talk("Aldric", "I draw my sword")
        later = time.time() + 9 * 60
        with mock.patch.object(self.app._time, "time", return_value=later):
            self.stage("Aldric", "I draw my sword")
        self.assertEqual(self.read_actions(), [], "a transcript from 9 minutes ago did not suppress it")

    def test_turned_on_mid_session_applies_to_next_action(self):
        self.party("Aldric")
        self.display(read_actions=False)
        self.app._display_reload()
        self.stage("Aldric", "I wait")
        self.assertEqual(self.read_actions(), [])
        r = self.client.post("/display-config", json={"voice": {"read_player_actions": True}},
                             environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        self.stage("Aldric", "I draw my sword")
        batches = self.read_actions()
        self.assertEqual(len(batches), 1, f"setting turned on did not apply: {self.sent}")
        self.assertEqual(batches[0][0]["voice"], "am_onyx")


if __name__ == "__main__":
    unittest.main()
