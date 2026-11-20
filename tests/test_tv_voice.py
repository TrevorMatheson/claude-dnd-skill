"""Voice on the TV (display foundation, plan step S9).

AC8  — in combat (and everywhere) narration types in step with the voice.
AC11 — D13 parity: Kokoro narration typed in step, blocks in order, NPC voice
       blocks, the "Tap for audio" prompt when audio is locked.
D17  — /tts carries the narrator voice, speed and volume from the display
       settings; the TV shows no voice toasts.
D22  — read_actions items join the same audio timeline, ahead of any later
       narration.

The voice logic lives in skills/dnd/display/static/tv/voice.js, DOM-free,
ending with `if (typeof module !== 'undefined') module.exports = {...}`:

  makeVoice(deps) -> {enqueue(block), readActions(items), setSettings(s),
                      reset(), idle()}
  stripRolls(text) -> text

deps (all injected, so node can drive it with fakes):

  fetchTts(body) -> Promise<clip>
      body = {text, voice?, fallback?, speed}. `text` is the spoken text
      (rolls and markdown stripped, whitespace collapsed). `fallback` only
      when the block has `voice_fallback`. A clip has `clip.ms` (its length);
      a rejection is a failed gate.
  play(clip, {gain}) -> Promise   starts the clip now; resolves when it ends.
      `gain` is the volume setting (0..1) at the moment the clip starts.
  stop()                          stops whatever is playing (reset() calls it).
  type(seg) -> Promise|void       reveal one segment of a block; resolves when
      typed. seg = {block, text, spoken, voiced, msPerChar, instant}.
      The `text`s of one block's segments join to exactly block.text.
      voiced: true -> called at the moment its clip starts, msPerChar paces the
      typing over the clip (clip.ms / characters, at least 8).
      voiced: false -> msPerChar 0 means "your normal speed".
      instant: true -> a history block, shown whole.
  isUnlocked() -> bool            is the audio context running?
  onLocked()                      a voiced block is due but audio is locked:
                                  show "Tap for audio".
  gateWait = {narration, npc}     ms a gate waits for its clip (default
                                  4000 / 14000, as in index.html:7404-7405).

The old page's behaviour this ports: paced reveal, gates and prefetch
index.html:7404-7516, auto-narrate queue :7594-7636, roll stripping :7698,
"Tap for audio" :7881-7897, deferral queue :5523-5583. Driven with real
timers, like tests/test_tts_autoqueue.py: the queue is timing behaviour.

Skipped when node is not installed.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
VOICE_JS = ROOT / "skills" / "dnd" / "display" / "static" / "tv" / "voice.js"
NODE = shutil.which("node")

_PRELUDE = r"""
globalThis.window = globalThis.window || { TV: {} };
const mod = require(%(voice)s);
if (!mod || typeof mod.makeVoice !== 'function') throw new Error('voice.js does not export makeVoice()');
const makeVoice = mod.makeVoice;
const t0 = Date.now();
const now = () => Date.now() - t0;
const ev = { fetch: [], play: [], end: [], type: [], locked: 0 };
let playing = 0, maxPlaying = 0;
const live = new Set();
let v = null;
const later = (ms, fn) => setTimeout(fn, ms);

// Fake TTS: a clip lasts 12 ms per spoken character, arrives after 10 ms.
// `rule(body)` may return {fail, hang, delay, ms} for one request.
function deps(o) {
  o = o || {};
  return {
    fetchTts(body) {
      const r = (o.rule && o.rule(body)) || {};
      ev.fetch.push({ t: now(), body: JSON.parse(JSON.stringify(body)) });
      return new Promise((res, rej) => {
        if (r.hang) return;
        setTimeout(() => {
          if (r.fail) rej(new Error('TTS 500'));
          else res({ ms: r.ms != null ? r.ms : Math.max(60, 12 * String(body.text).length), text: body.text });
        }, r.delay != null ? r.delay : 10);
      });
    },
    play(clip, opts) {
      playing++; maxPlaying = Math.max(maxPlaying, playing);
      const p = { t: now(), text: clip.text, ms: clip.ms, gain: opts ? opts.gain : undefined };
      ev.play.push(p);
      if (o.onPlay) o.onPlay(p, ev.play.length);
      return new Promise((res) => {
        let done = false;
        const fin = () => {
          if (done) return;
          done = true; playing--; live.delete(fin);
          ev.end.push({ t: now(), text: clip.text });
          res();
        };
        live.add(fin);
        setTimeout(fin, clip.ms);
      });
    },
    stop() { [...live].forEach((f) => f()); },
    type(seg) {
      ev.type.push({ t: now(), text: seg.text, spoken: seg.spoken, voiced: !!seg.voiced,
                     msPerChar: seg.msPerChar, instant: !!seg.instant,
                     seq: seg.block ? seg.block.seq : null });
      const ms = seg.instant ? 0 : (seg.msPerChar > 0 ? seg.msPerChar * seg.text.length : 2 * seg.text.length);
      return new Promise((r) => setTimeout(r, ms));
    },
    isUnlocked: () => !o.locked,
    onLocked: () => { ev.locked++; },
    gateWait: o.gateWait || { narration: 400, npc: 400 },
  };
}

async function finish(extraWaitMs) {
  const to = new Promise((r) => setTimeout(() => r('stalled'), 15000));
  const result = await Promise.race([Promise.resolve(v.idle()).then(() => 'idle'), to]);
  if (extraWaitMs) await new Promise((r) => setTimeout(r, extraWaitMs));
  console.log(JSON.stringify(Object.assign({ result, maxPlaying, playingAtEnd: playing }, ev)));
  process.exit(0);   // don't wait out the stall timer or a hung fake request
}
"""

S1 = "The door creaks open slowly. "
S2 = "A cold wind blows through."
NARR = S1 + S2                       # two sentences
NPC = "Mind your step, traveller. The floor is rotten."
LAST = "The hall falls silent again."


def _narr(seq, text):
    return json.dumps({"seq": seq, "text": text})


@unittest.skipUnless(NODE, "node is not installed")
class _VoiceCase(unittest.TestCase):

    def run_js(self, body: str) -> dict:
        if not VOICE_JS.exists():
            self.fail("not built yet: static/tv/voice.js")
        script = _PRELUDE % {"voice": json.dumps(str(VOICE_JS))} + body
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=40)
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        if r.returncode != 0:
            self.fail("node failed:\n" + (r.stderr or r.stdout)[-1500:])
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        self.assertTrue(lines, "scenario printed nothing")
        res = json.loads(lines[-1])
        self.assertEqual(res["result"], "idle", f"the voice queue stalled: {res}")
        return res

    def plays(self, res):
        return [p["text"] for p in res["play"]]

    def assert_no_overlap(self, res):
        self.assertEqual(res["maxPlaying"], 1, "two clips played at once")
        ends = {e["text"]: e["t"] for e in res["end"]}
        for a, b in zip(res["play"], res["play"][1:]):
            self.assertGreaterEqual(b["t"], ends[a["text"]] - 5,
                                    f"{b['text']!r} started before {a['text']!r} ended")


# ── (1) One audio timeline: a burst plays in order, never overlapping ─────

class TestBurst(_VoiceCase):

    # AC11/D13 — narration, an NPC line and more narration, sent as a burst,
    # are all heard, in order, one at a time (single audio timeline).
    def test_burst_plays_every_block_in_order_without_overlap(self):
        res = self.run_js(f"""
v = makeVoice(deps());
v.setSettings({{ narrator: 'bm_lewis', speed: 1, volume: 1 }});
v.enqueue({_narr(1, NARR)});
v.enqueue({{ seq: 2, text: {json.dumps(NPC)}, npc: 'Mira', voice: 'af_bella' }});
v.enqueue({_narr(3, LAST)});
finish();
""")
        self.assertEqual(self.plays(res), [S1.strip(), S2, NPC, LAST])
        self.assert_no_overlap(res)
        # typed in the same order, each block's segments rebuild its text
        self.assertEqual([t["seq"] for t in res["type"]], [1, 1, 2, 3])
        self.assertEqual("".join(t["text"] for t in res["type"] if t["seq"] == 1), NARR)
        self.assertEqual("".join(t["text"] for t in res["type"] if t["seq"] == 2), NPC)

    # AC8 — each sentence starts typing when its clip starts, paced to the clip.
    def test_each_sentence_types_in_step_with_its_clip(self):
        res = self.run_js(f"""
v = makeVoice(deps());
v.setSettings({{ narrator: 'bm_lewis' }});
v.enqueue({_narr(1, NARR)});
finish();
""")
        voiced = [t for t in res["type"] if t["voiced"]]
        self.assertEqual(len(voiced), 2, res["type"])
        for seg, clip in zip(voiced, res["play"]):
            self.assertLess(abs(seg["t"] - clip["t"]), 40,
                            f"{seg['text']!r} typed at {seg['t']} ms, its clip started at {clip['t']} ms")
            typed_ms = seg["msPerChar"] * len(seg["text"].strip())
            self.assertLess(abs(typed_ms - clip["ms"]) / clip["ms"], 0.25,
                            f"{seg['text']!r} types over {typed_ms} ms, its clip is {clip['ms']} ms")

    # The next sentence and the next block are fetched while the current one
    # plays, so there is no gap between them (prefetch, index.html:7427, :7626).
    def test_next_clips_are_fetched_while_the_current_one_plays(self):
        res = self.run_js(f"""
v = makeVoice(deps());
v.enqueue({_narr(1, NARR)});
v.enqueue({_narr(2, LAST)});
finish();
""")
        fetched = {f["body"]["text"]: f["t"] for f in res["fetch"]}
        ends = {e["text"]: e["t"] for e in res["end"]}
        self.assertLess(fetched[S2], ends[S1.strip()], "sentence 2 was not fetched while sentence 1 played")
        self.assertLess(fetched[LAST], ends[S2], "the next block was not fetched while the last sentence played")

    # D13 — a dice line, a player line or a tutor hint arriving mid-narration is
    # held until the narration has finished typing (deferral queue :5523-5583),
    # and is never spoken.
    def test_unspoken_blocks_wait_their_turn_and_are_not_voiced(self):
        res = self.run_js(f"""
v = makeVoice(deps());
v.enqueue({_narr(1, NARR)});
later(20, () => {{
  v.enqueue({{ seq: 2, text: 'Bram rolls Stealth: 17', dice: true }});
  v.enqueue({{ seq: 3, text: 'I slip behind the pillar.', player: 'Bram' }});
  v.enqueue({{ seq: 4, text: 'Tip: hiding needs cover.', tutor: true }});
}});
finish();
""")
        self.assertEqual([f["body"]["text"] for f in res["fetch"]], [S1.strip(), S2])
        self.assertEqual([t["seq"] for t in res["type"]], [1, 1, 2, 3, 4])
        narr_end = max(e["t"] for e in res["end"])
        dice = next(t for t in res["type"] if t["seq"] == 2)
        self.assertGreaterEqual(dice["t"], narr_end - 30, "the dice line landed in the middle of the narration")
        for t in res["type"]:
            if t["seq"] != 1:
                self.assertFalse(t["voiced"], t)

    # D13 — the replayed history after a reconnect is shown, not read aloud.
    def test_history_blocks_are_shown_whole_and_not_spoken(self):
        res = self.run_js(f"""
v = makeVoice(deps());
v.enqueue({{ seq: 1, text: {json.dumps(NARR)}, history: true }});
v.enqueue({{ seq: 2, text: {json.dumps(NPC)}, npc: 'Mira', voice: 'af_bella', history: true }});
finish();
""")
        self.assertEqual(res["fetch"], [])
        self.assertEqual(res["play"], [])
        self.assertEqual([(t["seq"], t["text"], t["instant"]) for t in res["type"]],
                         [(1, NARR, True), (2, NPC, True)])

    # A rewind or clear drops what was waiting; the queue still works after.
    def test_reset_drops_the_queue_and_does_not_stall_it(self):
        res = self.run_js(f"""
v = makeVoice(deps({{ onPlay: (p, n) => {{ if (n === 1) later(30, () => {{
  v.reset();
  v.enqueue({_narr(9, LAST)});
}}); }} }}));
v.enqueue({_narr(1, NARR)});
v.enqueue({_narr(2, NPC)});
finish(300);
""")
        self.assertEqual(self.plays(res), [S1.strip(), LAST])
        self.assertNotIn(2, [t["seq"] for t in res["type"]])
        self.assertEqual(res["playingAtEnd"], 0)


# ── (2) A gate that fails or times out types without voice ───────────────

class TestGates(_VoiceCase):

    def test_a_failed_gate_types_without_voice_and_the_queue_continues(self):
        res = self.run_js(f"""
v = makeVoice(deps({{ rule: (b) => b.text === {json.dumps(S2)} ? {{ fail: true }} : null }}));
v.enqueue({_narr(1, NARR + " " + LAST)});
v.enqueue({{ seq: 2, text: {json.dumps(NPC)}, npc: 'Mira', voice: 'af_bella' }});
finish();
""")
        self.assertEqual(self.plays(res), [S1.strip(), LAST, NPC])
        broken = next(t for t in res["type"] if t["text"].strip() == S2)
        self.assertFalse(broken["voiced"])
        self.assertEqual(broken["msPerChar"], 0)
        self.assertEqual([t["seq"] for t in res["type"]], [1, 1, 1, 2])
        self.assert_no_overlap(res)

    def test_a_gate_that_times_out_types_without_voice_and_a_late_clip_is_dropped(self):
        res = self.run_js(f"""
v = makeVoice(deps({{
  gateWait: {{ narration: 200, npc: 200 }},
  rule: (b) => b.text === {json.dumps(S2)} ? {{ delay: 700 }} : (b.text === {json.dumps(LAST)} ? {{ hang: true }} : null),
}}));
v.enqueue({_narr(1, NARR)});
v.enqueue({_narr(2, LAST)});
v.enqueue({{ seq: 3, text: {json.dumps(NPC)}, npc: 'Mira', voice: 'af_bella' }});
finish(900);
""")
        # S2 arrived after its gate gave up: never played, not even later.
        self.assertEqual(self.plays(res), [S1.strip(), NPC])
        by_text = {t["text"].strip(): t for t in res["type"]}
        self.assertFalse(by_text[S2]["voiced"])
        self.assertFalse(by_text[LAST]["voiced"])
        self.assertTrue(by_text[NPC]["voiced"])
        self.assertEqual([t["seq"] for t in res["type"]], [1, 1, 2, 3])

    # An NPC line gets one gate for the whole line, with the longer NPC wait
    # (Gemini, then Kokoro, on the server).
    def test_npc_line_is_one_gate_with_the_npc_wait(self):
        res = self.run_js(f"""
v = makeVoice(deps({{ gateWait: {{ narration: 100, npc: 900 }}, rule: (b) => b.voice === 'af_bella' ? {{ delay: 350 }} : null }}));
v.enqueue({{ seq: 1, text: {json.dumps(NPC)}, npc: 'Mira', voice: 'af_bella' }});
finish();
""")
        self.assertEqual([f["body"]["text"] for f in res["fetch"]], [NPC])
        self.assertEqual(len(res["type"]), 1)
        self.assertTrue(res["type"][0]["voiced"], "the NPC line gave up before the NPC wait")

    # D13 — audio locked (no tap yet): text types at normal speed, nothing is
    # played, and the TV is asked to show "Tap for audio".
    def test_locked_audio_types_text_only_and_asks_for_a_tap(self):
        res = self.run_js(f"""
v = makeVoice(deps({{ locked: true }}));
v.enqueue({_narr(1, NARR)});
v.enqueue({{ seq: 2, text: {json.dumps(NPC)}, npc: 'Mira', voice: 'af_bella' }});
finish();
""")
        self.assertEqual(res["play"], [])
        self.assertTrue(res["type"])
        self.assertTrue(all(not t["voiced"] and t["msPerChar"] == 0 for t in res["type"]), res["type"])
        self.assertGreaterEqual(res["locked"], 1, "onLocked() was never called")


# ── (3) read_actions share the timeline ──────────────────────────────────

class TestReadActions(_VoiceCase):

    # D22 — narration that arrives while a player's action is being read waits for it.
    def test_narration_arriving_during_a_read_action_waits_for_it(self):
        res = self.run_js(f"""
v = makeVoice(deps());
v.setSettings({{ narrator: 'bm_lewis' }});
v.readActions([{{ character: 'Bram', text: 'I kick the door down.', voice: 'am_adam' }}]);
later(40, () => v.enqueue({_narr(1, NARR)}));
finish();
""")
        self.assertEqual(self.plays(res), ["I kick the door down.", S1.strip(), S2])
        self.assert_no_overlap(res)
        action_end = res["end"][0]["t"]
        first_type = min(t["t"] for t in res["type"])
        self.assertGreaterEqual(first_type, action_end - 5, "narration typed while the action was being read")
        action_req = res["fetch"][0]["body"]
        self.assertEqual(action_req["voice"], "am_adam")
        # the action is already on screen as the player's block: audio only
        self.assertNotIn("I kick the door down.", [t["text"].strip() for t in res["type"]])

    # D22 — read_actions items are queued behind narration already playing,
    # and ahead of narration that arrives after them.
    def test_read_actions_queue_on_the_same_timeline(self):
        res = self.run_js(f"""
v = makeVoice(deps());
v.enqueue({_narr(1, NARR)});
later(20, () => {{
  v.readActions([{{ character: 'Bram', text: 'I hide.', voice: 'am_adam' }},
                 {{ character: 'Lia', text: 'I cast light.', voice: 'bf_emma' }}]);
  v.enqueue({_narr(2, LAST)});
}});
finish();
""")
        self.assertEqual(self.plays(res), [S1.strip(), S2, "I hide.", "I cast light.", LAST])
        self.assert_no_overlap(res)

    def test_a_failed_read_action_does_not_stall_the_queue(self):
        res = self.run_js(f"""
v = makeVoice(deps({{ rule: (b) => b.text === 'I hide.' ? {{ fail: true }} : null }}));
v.readActions([{{ character: 'Bram', text: 'I hide.', voice: 'am_adam' }}]);
v.enqueue({_narr(1, LAST)});
finish();
""")
        self.assertEqual(self.plays(res), [LAST])


# ── (4) Voices, fallback, speed and volume from the settings ─────────────

class TestRequests(_VoiceCase):

    # D17 — narration in the narrator voice, at the set speed, no fallback.
    def test_narration_request_carries_narrator_voice_and_speed(self):
        res = self.run_js(f"""
v = makeVoice(deps());
v.setSettings({{ narrator: 'bm_george', speed: 1.25, volume: 0.4, npc_engine: 'kokoro', read_player_actions: false }});
v.enqueue({_narr(1, LAST)});
finish();
""")
        body = res["fetch"][0]["body"]
        self.assertEqual(body["voice"], "bm_george")
        self.assertEqual(body["speed"], 1.25)
        self.assertIsNone(body.get("fallback"))
        self.assertEqual(res["play"][0]["gain"], 0.4)

    # D19/D21 — an NPC is read in its own voice, with its own fallback.
    def test_npc_request_carries_its_own_voice_and_fallback(self):
        res = self.run_js(f"""
v = makeVoice(deps());
v.setSettings({{ narrator: 'bm_lewis', speed: 0.9 }});
v.enqueue({{ seq: 1, text: {json.dumps(NPC)}, npc: 'Mira', voice: 'Kore', voice_fallback: 'bf_emma' }});
v.enqueue({{ seq: 2, text: 'Hold the line!', npc: 'Torv', voice: 'am_onyx' }});
finish();
""")
        a, b = (f["body"] for f in res["fetch"])
        self.assertEqual((a["voice"], a.get("fallback"), a["speed"]), ("Kore", "bf_emma", 0.9))
        self.assertEqual(b["voice"], "am_onyx")
        self.assertIsNone(b.get("fallback"))
        self.assertNotIn("bm_lewis", (a["voice"], b["voice"]), "an NPC was read in the narrator voice")

    # A narration block that names its own voice keeps it.
    def test_block_voice_wins_over_the_narrator(self):
        res = self.run_js(f"""
v = makeVoice(deps());
v.setSettings({{ narrator: 'bm_lewis' }});
v.enqueue({{ seq: 1, text: {json.dumps(LAST)}, voice: 'bf_alice' }});
finish();
""")
        self.assertEqual(res["fetch"][0]["body"]["voice"], "bf_alice")

    # With no narrator saved, the TV sends none and the server picks it
    # (display.json, then state.md, then the default).
    def test_no_narrator_setting_sends_no_voice(self):
        res = self.run_js(f"""
v = makeVoice(deps());
v.enqueue({_narr(1, LAST)});
finish();
""")
        self.assertFalse(res["fetch"][0]["body"].get("voice"))
        self.assertEqual(res["fetch"][0]["body"]["speed"], 1)

    # D17 — volume goes through a gain; default 1; a change applies from the next clip.
    def test_volume_change_applies_from_the_next_clip(self):
        res = self.run_js(f"""
v = makeVoice(deps({{ onPlay: (p, n) => {{ if (n === 1) v.setSettings({{ volume: 0.3 }}); }} }}));
v.enqueue({_narr(1, NARR)});
finish();
""")
        self.assertEqual([p["gain"] for p in res["play"]], [1, 0.3])


# ── (5) Roll stripping ───────────────────────────────────────────────────

class TestRollStripping(_VoiceCase):

    def test_strip_rolls(self):
        cases = {
            "You swing (d20+5 = 17) and hit.": "You swing and hit.",
            "The arrow flies [Attack: 18 vs AC 15] and misses.": "The arrow flies and misses.",
            "🎲 1d8+3 → 9 damage to the goblin.": "damage to the goblin.",
            "A natural twenty (nat 20!)!": "A natural twenty!",
            "Six doors, three keys.": "Six doors, three keys.",
        }
        res = self.run_js(f"""
const cases = {json.dumps(list(cases))};
if (typeof mod.stripRolls !== 'function') throw new Error('voice.js does not export stripRolls()');
v = {{ idle: () => Promise.resolve() }};
ev.stripped = cases.map((c) => mod.stripRolls(c).replace(/\\s+/g, ' ').trim());
finish();
""")
        self.assertEqual(res["stripped"], list(cases.values()))

    def test_spoken_text_has_no_rolls_or_markdown(self):
        text = "**Bram** swings (d20+5 = 17) and the blade bites deep. The troll roars 🎲 2d6+3 = 11 in pain."
        res = self.run_js(f"""
v = makeVoice(deps());
v.enqueue({{ seq: 1, text: {json.dumps(text)} }});
finish();
""")
        spoken = [f["body"]["text"] for f in res["fetch"]]
        self.assertEqual(spoken, ["Bram swings and the blade bites deep.", "The troll roars in pain."])
        # the screen still shows the roll
        self.assertEqual("".join(t["text"] for t in res["type"]), text)

    def test_a_block_that_is_only_a_roll_is_not_fetched(self):
        res = self.run_js(f"""
v = makeVoice(deps());
v.enqueue({{ seq: 1, text: '🎲 1d8+3 → 9' }});
finish();
""")
        self.assertEqual(res["fetch"], [])
        self.assertEqual([t["voiced"] for t in res["type"]], [False])


if __name__ == "__main__":
    unittest.main()
