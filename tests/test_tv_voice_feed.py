"""The feed and the voice together on the TV (display foundation, plan step S9).

Regression tests for two failures found in S9 verify:

FAIL 1 — the server turned the voice off ({"voice": {"on": false}}) while a
         burst was being read: the voice queue was dropped but the feed was
         not told, so the rest of the burst never appeared and the current
         block stayed half typed. Every block must end up shown whole.
FAIL 2 — any {"hello"} (a reconnect) settled the feed and reset the voice,
         so a Wi-Fi blip cut the narrator off mid-sentence. Only a hello with
         a NEW epoch (the server restarted) replaces the feed; a hello on the
         same epoch leaves the voice running.

AC8 / AC11 (D13 parity: Kokoro narration typed in step, blocks in order,
catching up after a reconnect).

These drive the real feed.mount() (skills/dnd/display/static/tv/feed.js) with
the real makeFeedModel() and the real makeVoice() (voice.js), wired the way
tv.js wires them, in node with a small fake DOM. Audio and /tts are fakes:
a clip lasts 15 ms per character and arrives after 10 ms.

Skipped when node is not installed.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TV_STATIC = ROOT / "skills" / "dnd" / "display" / "static" / "tv"
FEED_JS = TV_STATIC / "feed.js"
VOICE_JS = TV_STATIC / "voice.js"
TV_JS = TV_STATIC / "tv.js"
NODE = shutil.which("node")

_PRELUDE = r"""
// ── A fake DOM: just what feed.mount() uses ──
function cls(el) {
  return {
    contains: (c) => el.className.split(/\s+/).includes(c),
    toggle(c, on) {
      const has = this.contains(c);
      if (on === undefined) on = !has;
      if (on && !has) el.className = (el.className + ' ' + c).trim();
      if (!on && has) el.className = el.className.split(/\s+/).filter((x) => x !== c).join(' ');
    },
    add(c) { this.toggle(c, true); },
    remove(c) { this.toggle(c, false); },
  };
}
function El(tag) {
  const el = {
    tagName: String(tag).toUpperCase(), children: [], className: '', dataset: {}, attrs: {},
    _html: '', _text: null, hidden: false, disabled: false, type: '', title: '',
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); this._text = null; this.children = []; },
    get textContent() {
      const own = this._text != null ? this._text : this._html.replace(/<[^>]*>/g, '')
        .replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
      return own + this.children.map((c) => c.textContent).join('');
    },
    set textContent(v) { this._text = String(v); this._html = ''; this.children = []; },
    appendChild(c) {
      if (c && c.isFragment) { this.children.push(...c.children); return c; }
      this.children.push(c); return c;
    },
    replaceChildren(...cs) {
      this.children = [];
      cs.forEach((c) => this.appendChild(c));
    },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    addEventListener() {},
    all() { return this.children.flatMap((c) => [c, ...c.all()]); },
    querySelector(sel) {
      let m = /^\[data-key="([^"]+)"\]$/.exec(sel);
      if (m) return this.all().find((e) => e.dataset.key === m[1]) || null;
      m = /^\.([\w-]+)$/.exec(sel);
      if (m) return this.all().find((e) => e.classList.contains(m[1])) || null;
      throw new Error('fake DOM: selector not supported: ' + sel);
    },
  };
  el.classList = cls(el);
  return el;
}
globalThis.document = {
  createElement: (t) => El(t),
  createDocumentFragment: () => Object.assign(El('#fragment'), { isFragment: true }),
};

const { makeFeedModel, mount } = require(%(feed)s);
const { makeVoice } = require(%(voice)s);
const out = (v) => console.log(JSON.stringify(v));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const T = (seq, text, extra) => Object.assign({ text, seq }, extra || {});

// ── Wiring as in tv.js, with fake audio and /tts ──
const ev = { fetch: [], play: [], end: [], stop: 0, reset: 0 };
let voiceOn = true;
let playing = new Set();
let feed = null;
const voice = makeVoice({
  fetchTts(body) {
    if (!voiceOn) return Promise.reject(new Error('voice off'));
    ev.fetch.push(body.text);
    return new Promise((r) => setTimeout(() => r({ ms: 15 * body.text.length, text: body.text }), 10));
  },
  play(clip) {
    ev.play.push(clip.text);
    return new Promise((res) => {
      const fin = () => { if (!playing.has(fin)) return; playing.delete(fin); ev.end.push(clip.text); res(); };
      playing.add(fin);
      setTimeout(fin, clip.ms);
    });
  },
  stop() { ev.stop++; [...playing].forEach((f) => f()); },
  type: (seg) => (feed ? feed.type(seg) : undefined),
  isUnlocked: () => true,
  onLocked() {},
  canVoice: () => voiceOn,
  gateWait: { narration: 400, npc: 400 },
});
const realReset = voice.reset;
voice.reset = () => { ev.reset++; return realReset(); };

const el = El('div');
feed = mount(el, makeFeedModel(), { voice });

// What the TV shows: [seq, typing?, text] per block.
const shown = () => el.children.filter((c) => c.classList.contains('block'))
  .map((c) => [c.dataset.seq ? Number(c.dataset.seq) : null, c.classList.contains('typing'), c.textContent]);
async function until(cond, ms) {
  const t0 = Date.now();
  while (!cond()) { if (Date.now() - t0 > ms) return false; await sleep(5); }
  return true;
}
"""

N1 = "The golem raises one enormous fist. Chips of stone rain from its knuckles. "
N1 += "Havoc's shield takes the blow. Anony is already moving."
NPC = "Golem. Do not let it reach the vault door."
N3 = "Its single eye opens, glowing blue."
N4 = "The golem takes another step."


@unittest.skipUnless(NODE, "node is not installed")
class _FeedVoiceCase(unittest.TestCase):

    def run_js(self, body: str) -> dict:
        missing = [p.name for p in (FEED_JS, VOICE_JS) if not p.exists()]
        if missing:
            self.fail("not built yet: static/tv/" + ", ".join(missing))
        script = _PRELUDE % {"feed": json.dumps(str(FEED_JS)), "voice": json.dumps(str(VOICE_JS))}
        script += "\n(async () => {\n" + body + "\nprocess.exit(0);\n})().catch((e) => { console.error(e); process.exit(1); });\n"
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
        return json.loads(lines[-1])


# ── FAIL 1: the voice goes off in the middle of a burst ──────────────────

class TestVoiceOffMidBurst(_FeedVoiceCase):

    # AC11 / D13 — every block of the burst ends up on screen, whole and in
    # order, none left typing; a block sent afterwards still types out.
    def test_every_block_of_the_burst_is_shown_whole(self):
        res = self.run_js(f"""
feed.apply({{hello: {{epoch: 'E1'}}}});
feed.apply(T(71, {json.dumps(N1)}));
feed.apply(T(72, {json.dumps(NPC)}, {{npc: 'Vesna', voice: 'af_kore'}}));
feed.apply(T(73, {json.dumps(N3)}));
await until(() => ev.play.length >= 1, 3000);
await sleep(100);                       // the first sentence is typing
const during = shown();
// tv.js on {{"voice": {{"on": false}}}}
voiceOn = false;
feed.settle();
const right = shown();
await sleep(800);
const later = shown();
feed.apply(T(74, {json.dumps(N4)}));
await until(() => shown().some((b) => b[0] === 74 && !b[1]), 5000);
out({{during, right, later, end: shown(), playsAfter: ev.play.length}});
""")
        self.assertTrue(any(b[0] == 71 and b[1] for b in res["during"]),
                        f"setup: block 71 should be typing when the voice goes off: {res['during']}")
        want = [[71, False, N1], [72, False, "Vesna" + NPC], [73, False, N3]]
        self.assertEqual(res["right"], want, "settle() must show the whole burst at once")
        self.assertEqual(res["later"], want, "the burst must stay whole afterwards")
        self.assertEqual(res["end"], want + [[74, False, N4]],
                         "a block sent after the voice went off must still appear, typed unvoiced")
        self.assertEqual(res["playsAfter"], 1, "nothing may be voiced after the voice went off")

    # tv.js wires the voice-off branch to feed.settle(), not voice.reset()
    # alone (a reset without settling the feed is FAIL 1).
    def test_tv_js_settles_the_feed_when_the_voice_goes_off(self):
        src = TV_JS.read_text(encoding="utf-8")
        m = re.search(r"if\s*\(\s*p\.voice\s*&&[^\n]*\{(.*?)\n\s*\}", src, re.S)
        self.assertIsNotNone(m, "tv.js: no handler for the server's {voice: {on, reason}} payload")
        branch = m.group(1)
        self.assertIn("feed.settle()", branch, "tv.js must settle the feed when the voice goes off")
        self.assertNotRegex(branch, r"voice\.reset\(\)",
                            "voice.reset() on its own leaves queued blocks hidden (FAIL 1)")


# ── FAIL 2: a reconnect in the middle of narration ───────────────────────

class TestReconnectMidNarration(_FeedVoiceCase):

    # AC12 + D13 — same server run: the voice carries on, every sentence is
    # spoken, the narration types to the end, nothing is duplicated.
    def test_same_epoch_hello_leaves_the_voice_running(self):
        res = self.run_js(f"""
feed.apply({{hello: {{epoch: 'E1'}}}});
feed.apply({{replay_batch: [T(1, 'Earlier.')]}});
feed.apply(T(2, {json.dumps(N1)}));
await until(() => ev.play.length >= 2, 5000);  // second sentence speaking
const before = {{reset: ev.reset, stop: ev.stop}};
// Wi-Fi blip: the stream reopens on the same server run and replays.
feed.apply({{hello: {{epoch: 'E1'}}}});
feed.apply({{replay_batch: [T(1, 'Earlier.'), T(2, {json.dumps(N1)})]}});
const right = shown();
await until(() => shown().every((b) => !b[1]), 8000);
out({{before, reset: ev.reset, stop: ev.stop, right, end: shown(), plays: ev.play.length, ends: ev.end.length}});
""")
        self.assertEqual(res["reset"], 0, "a same-epoch hello must not reset the voice")
        self.assertEqual(res["stop"], 0, "a same-epoch hello must not stop the audio")
        self.assertTrue(any(b[0] == 2 and b[1] for b in res["right"]),
                        f"block 2 should still be typing right after the reconnect: {res['right']}")
        self.assertEqual(res["plays"], 4, "all four sentences must be spoken")
        self.assertEqual(res["end"], [[1, False, "Earlier."], [2, False, N1]])

    # AC12 — the server restarted (new epoch): the feed is rebuilt from the
    # replay, the voice is reset and nothing is left typing.
    def test_new_epoch_hello_resets_the_voice_and_settles(self):
        res = self.run_js(f"""
feed.apply({{hello: {{epoch: 'E1'}}}});
feed.apply(T(2, {json.dumps(N1)}));
await until(() => ev.play.length >= 1, 3000);
await sleep(50);
const during = shown();
feed.apply({{hello: {{epoch: 'E2'}}}});
feed.apply({{replay_batch: [T(1, 'Earlier.'), T(2, {json.dumps(N1)})]}});
const right = shown();
await sleep(600);
out({{during, reset: ev.reset, stop: ev.stop, right, end: shown(), plays: ev.play.length}});
""")
        self.assertTrue(any(b[0] == 2 and b[1] for b in res["during"]), f"setup: {res['during']}")
        self.assertGreaterEqual(res["reset"], 1, "a new epoch must reset the voice")
        self.assertGreaterEqual(res["stop"], 1, "a new epoch must stop the audio")
        self.assertEqual(res["right"], [[1, False, "Earlier."], [2, False, N1]])
        self.assertEqual(res["end"], res["right"])
        self.assertEqual(res["plays"], 1, "nothing may be spoken after the restart")

    # The very first hello (page load) is not a restart: a live block sent
    # right after it is still spoken.
    def test_first_hello_is_not_a_restart(self):
        res = self.run_js(f"""
feed.apply({{hello: {{epoch: 'E1'}}}});
feed.apply(T(5, {json.dumps(N3)}));
await until(() => shown().some((b) => b[0] === 5 && !b[1]), 5000);
out({{reset: ev.reset, plays: ev.play.length, end: shown()}});
""")
        self.assertEqual(res["plays"], 1)
        self.assertEqual(res["end"], [[5, False, N3]])


if __name__ == "__main__":
    unittest.main()
