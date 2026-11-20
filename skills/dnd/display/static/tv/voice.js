// Voice on the TV (display foundation spec D13, D17, D22): narration types in
// step with its voice, sentence by sentence; an NPC line types with its own
// voice; every block of a burst waits its turn on one timeline. A port of the
// old page's paced reveal (index.html:7404-7516), auto-narrate queue
// (:7594-7636), /tts fetch (:7665) and roll stripping (:7698).
//
// makeVoice() has no DOM — audio, fetching and typing are injected — so node
// can test it (tests/test_tv_voice.py). makeWebAudio() is the browser side:
// an AudioContext with a GainNode for volume.
(function (root) {
  'use strict';

  // Roll mechanics the DM writes inline ("(d20+5 = 17)", "🎲 1d8+3 → 9",
  // "[Attack: 18 vs AC 15]") are for the screen; the voice reads only the story.
  function stripRolls(text) {
    const ASIDE = /[(\[][^)\]\n]*(?:\b\d*d(?:4|6|8|10|12|20|100)\b|\bvs\.?\s*(?:AC|DC)\b|\bnat(?:ural)?\s*(?:1|20)\b)[^)\]\n]*[)\]]/gi;
    const BARE = /(?:🎲\s*)?\b\d*d(?:4|6|8|10|12|20|100)\b(?:\s*[+\-−]\s*\d+)*\s*(?:=|→|->|:)\s*\*{0,2}\d+\*{0,2}/gi;
    return String(text)
      .replace(ASIDE, '')
      .replace(BARE, '')
      .replace(/🎲/g, '')
      .replace(/\s+([,.;:!?])/g, '$1');
  }

  // What the voice says for a piece of screen text: no markdown, no rolls.
  function spokenOf(text) {
    const plain = String(text).replace(/\*\*(.+?)\*\*/g, '$1').replace(/\*([^*\n]+?)\*/g, '$1');
    return stripRolls(plain).replace(/\s+/g, ' ').trim();
  }

  // Narration in sentences: one ends at . ! ? or … (and any closing quote or
  // bracket) before whitespace, once it is at least 12 characters long. The
  // pieces join back to exactly the original text.
  function sentences(text) {
    const out = [];
    const re = /[.!?…]["”’'\)\]]*\s+/g;
    let start = 0, m;
    while ((m = re.exec(text))) {
      const end = m.index + m[0].length;
      if (end - start >= 12) { out.push(text.slice(start, end)); start = end; }
    }
    if (start < text.length || !out.length) out.push(text.slice(start));
    return out;
  }

  // Blocks that are shown but never read aloud.
  const UNSPOKEN = ['player', 'action', 'dice', 'tutor', 'effect_expired',
                    'inspiration_award', 'milestone_award', 'xp_award'];
  const SAYABLE = /[A-Za-z0-9]/;

  function makeVoice(d) {
    const settings = { narrator: '', speed: 1, volume: 1 };
    const wait = Object.assign({ narration: 4000, npc: 14000 }, d.gateWait || {});
    const canVoice = d.canVoice || (() => true);
    let queue = [];        // items, each a list of steps
    let running = false;
    let gen = 0;           // bumped by reset(): anything older is dropped
    let waiters = [];

    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

    function step(block, text, kind, voice, fallback, maxWait, audioOnly) {
      const spoken = spokenOf(text);
      return { block, text, spoken, kind, voice, fallback, maxWait, audioOnly,
               gate: SAYABLE.test(spoken) && canVoice(kind), p: null, done: false };
    }

    function plan(b) {
      const text = String(b.text || '');
      if (b.history) return [{ block: b, text, spoken: '', instant: true }];
      if (UNSPOKEN.some((k) => b[k])) return [{ block: b, text, spoken: '' }];
      if (b.npc) return [step(b, text, 'npc', b.voice, b.voice_fallback, wait.npc, false)];
      return sentences(text).map((s) => step(b, s, 'narration', b.voice || settings.narrator, null, wait.narration, false));
    }

    // Start fetching a step's audio once; resolves to a clip or null.
    function fetchStep(s) {
      if (s.p) return;
      const body = { text: s.spoken, speed: settings.speed };
      if (s.voice) body.voice = s.voice;
      if (s.fallback) body.fallback = s.fallback;
      s.p = Promise.resolve()
        .then(() => d.fetchTts(body, { kind: s.kind }))
        .then((c) => (c && c.ms > 0 ? c : null), () => null);
    }

    // Fetch the next two gates ahead, in this item or ones queued after it.
    function prefetchAhead(cur) {
      let k = 0;
      for (const item of queue) {
        for (const s of item) {
          if (s !== cur && !s.done && s.gate) { fetchStep(s); if (++k >= 2) return; }
        }
      }
    }

    async function run(s, g) {
      const seg = (voiced, msPerChar, instant) =>
        ({ block: s.block, text: s.text, spoken: s.spoken, voiced, msPerChar, instant: !!instant });
      if (s.instant) return d.type(seg(false, 0, true));
      if (!s.gate) return s.audioOnly ? null : d.type(seg(false, 0));
      fetchStep(s);
      prefetchAhead(s);
      if (!d.isUnlocked()) {
        if (d.onLocked) d.onLocked();
        return s.audioOnly ? null : d.type(seg(false, 0));
      }
      // The gate: wait for the clip, but never longer than maxWait.
      const clip = await Promise.race([s.p, sleep(s.maxWait).then(() => null)]);
      if (g !== gen) return null;
      if (!clip) return s.audioOnly ? null : d.type(seg(false, 0));
      const played = d.play(clip, { gain: settings.volume });
      // Type the sentence over the time it takes to say it.
      const typed = s.audioOnly ? null
        : d.type(seg(true, Math.max(8, clip.ms / Math.max(1, s.text.length))));
      return Promise.all([played, typed]);
    }

    async function pump() {
      if (running) return;
      running = true;
      const g = gen;
      while (queue.length && g === gen) {
        for (const s of queue[0]) {
          if (g !== gen) break;
          try { await run(s, g); } catch (err) {
            if (typeof console !== 'undefined') console.error('tv voice: step failed', err);
          }
          s.done = true;
        }
        if (g === gen) queue.shift();
      }
      running = false;
      if (queue.length) { pump(); return; }
      const w = waiters; waiters = [];
      w.forEach((f) => f());
    }

    return {
      enqueue(block) { if (block) { queue.push(plan(block)); pump(); } },
      // D22: player actions read aloud join the same timeline, audio only.
      readActions(items) {
        for (const it of Array.isArray(items) ? items : []) {
          if (it && it.text) queue.push([step(null, String(it.text), 'action', it.voice, null, wait.npc, true)]);
        }
        pump();
      },
      setSettings(s) {
        if (!s || typeof s !== 'object') return;
        if (typeof s.narrator === 'string') settings.narrator = s.narrator;
        if (typeof s.speed === 'number' && isFinite(s.speed)) settings.speed = s.speed;
        if (typeof s.volume === 'number' && isFinite(s.volume)) settings.volume = s.volume;
      },
      // Rewind and clear: drop everything waiting and stop what is playing.
      reset() { gen++; queue = []; if (d.stop) d.stop(); },
      idle() {
        return !running && !queue.length ? Promise.resolve() : new Promise((r) => waiters.push(r));
      },
    };
  }

  // ── Browser audio ──────────────────────────────────────────────────────
  // /tts returns 24 kHz 16-bit mono PCM. Everything plays through one
  // GainNode, so volume applies without touching each clip.
  function makeWebAudio() {
    let ctx = null, gain = null, sources = [];
    function context() {
      if (!ctx || ctx.state === 'closed') {
        ctx = new (window.AudioContext || window.webkitAudioContext)();
        gain = ctx.createGain();
        gain.connect(ctx.destination);
      }
      return ctx;
    }
    return {
      context,
      gainNode() { context(); return gain; },
      isUnlocked() { return context().state === 'running'; },
      unlock() { return context().resume().catch(() => {}); },
      decode(buf) {
        const c = context();
        const int16 = new Int16Array(buf);
        const ab = c.createBuffer(1, Math.max(1, int16.length), 24000);
        const ch = ab.getChannelData(0);
        for (let i = 0; i < int16.length; i++) ch[i] = int16[i] / 32768;
        return { buffer: ab, ms: int16.length ? ab.duration * 1000 : 0 };
      },
      play(clip, opts) {
        const c = context();
        gain.gain.value = opts && typeof opts.gain === 'number' ? opts.gain : 1;
        return new Promise((resolve) => {
          const src = c.createBufferSource();
          src.buffer = clip.buffer;
          src.connect(gain);
          src.onended = () => { sources = sources.filter((x) => x !== src); resolve(); };
          sources.push(src);
          src.start();
        });
      },
      stop() {
        sources.forEach((s) => { try { s.stop(); } catch (_) { /* already ended */ } });
        sources = [];
      },
    };
  }

  const api = { makeVoice, stripRolls, spokenOf, sentences, makeWebAudio };
  root.TV = root.TV || {};
  root.TV.voice = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
