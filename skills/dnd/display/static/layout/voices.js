// The laptop page's Voices section (display foundation D17, D18, AC14):
// narrator picker with preview, NPC engine, volume, speed, "Read player
// actions aloud", the Gemini quota note, and the cast list.
//
// Settings go to POST /display-config as a partial {"voice": {...}} (the
// server keeps everything else); voices go to POST /cast, which writes them
// with the characters (npcs.md, the character files) and keeps every NPC off
// the narrator's and the player characters' voices.
(function (root) {
  'use strict';

  const ENGINES = [['kokoro', 'Kokoro only'], ['gemini', 'Gemini, falling back to Kokoro']];
  const PREVIEW_LINE = 'The lantern gutters, and something in the dark takes a breath.';

  // "am_onyx+am_adam" -> "Onyx + Adam"; a Gemini name stays as it is.
  function voiceLabel(id, voices) {
    if (!id) return '—';
    const one = (v) => {
      const hit = (voices || []).find((x) => x.id === v);
      return hit ? hit.label : v;
    };
    return String(id).split('+').map(one).join(' + ');
  }

  // Options for a voice menu: single Kokoro voices grouped by kind, plus the
  // current voice when it isn't one of them (a blend, a Gemini name).
  function voiceOptions(voices, current) {
    const groups = new Map();
    for (const v of voices || []) {
      if (!groups.has(v.kind)) groups.set(v.kind, []);
      groups.get(v.kind).push(v);
    }
    const out = [];
    if (current && !(voices || []).some((v) => v.id === current)) {
      out.push({ group: 'Current', id: current, label: voiceLabel(current, voices) });
    }
    for (const [kind, list] of groups) {
      for (const v of list) out.push({ group: kind, id: v.id, label: v.label });
    }
    return out;
  }

  function mount(section) {
    const token = (document.querySelector('meta[name="dnd-token"]') || {}).content || '';
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['X-DND-Token'] = token;
    const status = document.getElementById('lp-status');
    const $ = (id) => section.querySelector('#' + id);
    let cast = null, voice = null, busy = 0, drawn = '';

    // The page's status line is at the top, far above the cast list, so the
    // section repeats each message beside its own controls.
    const near = section.querySelector('#lp-cast-status');
    function say(text, kind) {
      for (const el of [status, near]) {
        if (!el) continue;
        el.textContent = text;
        el.dataset.kind = kind || '';
      }
    }

    function post(url, body) {
      busy++;
      say('Saving…');
      return fetch(url, { method: 'POST', headers, body: JSON.stringify(body) })
        .then((r) => r.json().catch(() => ({})).then((j) => {
          if (!r.ok) throw new Error(j.error || `The server said ${r.status}.`);
          return j;
        }))
        .finally(() => { busy--; });
    }

    function saveVoice(patch) {
      voice = Object.assign({}, voice, patch);
      post('/display-config', { voice: patch })
        .then((j) => {
          voice = (j.config && j.config.voice) || voice;
          say('Saved — it applies from the next line.', 'ok');
          render(true);
        })
        .catch((err) => { say(err.message, 'error'); load(); });
    }

    function setCast(kind, name, id) {
      post('/cast', { kind, name, voice: id })
        .then((j) => {
          cast = Object.assign({}, cast, j);
          say(kind === 'narrator' ? 'Narrator saved. Any NPC that had this voice got another.'
                                  : `${name} now speaks as ${voiceLabel(id, cast.voices)}.`, 'ok');
          render(true);
        })
        .catch((err) => { say(err.message, 'error'); render(true); });
    }

    // ── preview through this laptop's speakers ──
    let ctx = null, playing = null;
    function preview(id, btn) {
      if (playing) { try { playing.stop(); } catch (_) { /* ended */ } playing = null; }
      btn.disabled = true;
      const body = { text: PREVIEW_LINE, voice: id, speed: (voice && voice.speed) || 1 };
      fetch('/tts', { method: 'POST', headers, body: JSON.stringify(body) })
        .then((r) => { if (!r.ok) throw new Error(`No preview: the voice server said ${r.status}.`); return r.arrayBuffer(); })
        .then((buf) => {
          ctx = ctx || new (root.AudioContext || root.webkitAudioContext)();
          const pcm = new Int16Array(buf.byteLength >> 1);
          const view = new DataView(buf);
          for (let i = 0; i < pcm.length; i++) pcm[i] = view.getInt16(i * 2, true);
          const clip = ctx.createBuffer(1, pcm.length || 1, 24000);
          const ch = clip.getChannelData(0);
          for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
          const src = ctx.createBufferSource();
          const gain = ctx.createGain();
          gain.gain.value = voice && typeof voice.volume === 'number' ? voice.volume : 1;
          src.buffer = clip;
          src.connect(gain).connect(ctx.destination);
          playing = src;
          return ctx.resume().then(() => src.start());
        })
        .catch((err) => say(err.message, 'error'))
        .finally(() => { btn.disabled = false; });
    }

    function fillSelect(sel, current, opts) {
      sel.replaceChildren();
      let group = null, label = '';
      for (const o of opts) {
        if (o.group !== label) {
          label = o.group;
          group = document.createElement('optgroup');
          group.label = label;
          sel.appendChild(group);
        }
        group.appendChild(new Option(o.label, o.id, false, o.id === current));
      }
      sel.value = current || '';
    }

    function renderSettings() {
      const v = voice || {};
      const narrator = (cast && (cast.narrator_heard || cast.narrator)) || v.narrator || 'bm_lewis';
      fillSelect($('lp-narrator'), narrator, voiceOptions(cast ? cast.voices : [], narrator));
      const eng = $('lp-engine');
      eng.replaceChildren();
      for (const [id, text] of ENGINES) {
        const b = document.createElement('button');
        b.type = 'button';
        b.textContent = text;
        b.setAttribute('aria-pressed', String((v.npc_engine || 'kokoro') === id));
        b.addEventListener('click', () => saveVoice({ npc_engine: id }));
        eng.appendChild(b);
      }
      $('lp-volume').value = String(v.volume == null ? 1 : v.volume);
      $('lp-volume-out').textContent = Math.round((v.volume == null ? 1 : v.volume) * 100) + '%';
      $('lp-speed').value = String(v.speed == null ? 1 : v.speed);
      $('lp-speed-out').textContent = (v.speed == null ? 1 : v.speed).toFixed(2) + '×';
      $('lp-read-actions').checked = !!v.read_player_actions;
    }

    function castRow(kind, who, name, id) {
      const tr = document.createElement('tr');
      const th = document.createElement('th');
      th.scope = 'row';
      th.innerHTML = '';
      const n = document.createElement('span');
      n.textContent = name;
      const k = document.createElement('small');
      k.textContent = who;
      th.append(n, k);
      const td = document.createElement('td');
      const sel = document.createElement('select');
      sel.setAttribute('aria-label', `Voice for ${name}`);
      fillSelect(sel, id, voiceOptions(cast.voices, id));
      if (!id) sel.prepend(new Option('No voice yet', '', true, true));
      sel.addEventListener('change', () => { if (sel.value) setCast(kind, name, sel.value); });
      td.appendChild(sel);
      const tdp = document.createElement('td');
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = 'Preview';
      b.disabled = !id;
      b.setAttribute('aria-label', `Preview ${name}'s voice`);
      b.addEventListener('click', () => preview(id, b));
      tdp.appendChild(b);
      tr.append(th, td, tdp);
      return tr;
    }

    function renderCast() {
      const body = section.querySelector('#lp-cast tbody');
      body.replaceChildren();
      if (!cast) return;
      for (const p of cast.pcs || []) body.appendChild(castRow('pc', 'Player character', p.name, p.voice));
      for (const n of cast.npcs || []) body.appendChild(castRow('npc', 'NPC', n.name, n.voice));
      if (!(cast.pcs || []).length && !(cast.npcs || []).length) {
        const tr = document.createElement('tr');
        tr.innerHTML = '<td colspan="3" class="lp-note">No characters yet. NPCs appear here once they are in npcs.md.</td>';
        body.appendChild(tr);
      }
    }

    function renderNote(note) {
      const el = $('lp-voice-note');
      el.textContent = note || '';
      el.hidden = !note;
    }

    // Rebuild only when something shown changed, so the poll never steals
    // focus or closes an open menu.
    function render(force) {
      const sig = JSON.stringify([voice, cast]);
      if (!force && sig === drawn) return;
      if (section.contains(document.activeElement) && document.activeElement.tagName === 'SELECT' && !force) return;
      drawn = sig;
      renderSettings();
      renderCast();
    }

    $('lp-narrator').addEventListener('change', (ev) => setCast('narrator', null, ev.target.value));
    $('lp-narrator-preview').addEventListener('click', (ev) => preview($('lp-narrator').value, ev.currentTarget));
    $('lp-volume').addEventListener('input', (ev) => {
      $('lp-volume-out').textContent = Math.round(Number(ev.target.value) * 100) + '%';
    });
    $('lp-volume').addEventListener('change', (ev) => saveVoice({ volume: Number(ev.target.value) }));
    $('lp-speed').addEventListener('input', (ev) => {
      $('lp-speed-out').textContent = Number(ev.target.value).toFixed(2) + '×';
    });
    $('lp-speed').addEventListener('change', (ev) => saveVoice({ speed: Number(ev.target.value) }));
    $('lp-read-actions').addEventListener('change', (ev) => saveVoice({ read_player_actions: ev.target.checked }));

    function load() {
      if (busy) return Promise.resolve();
      return Promise.all([
        fetch('/display-config', { headers }).then((r) => r.json()),
        fetch('/cast', { headers }).then((r) => r.json()),
      ]).then(([cfg, c]) => {
        if (busy) return;
        voice = (cfg.config && cfg.config.voice) || {};
        renderNote(cfg.status && cfg.status.voice_note);
        cast = c && !c.error ? c : cast;
        render(false);
      }).catch(() => { /* layout.js reports the server being away */ });
    }
    load();
    setInterval(load, 5000);
  }

  const api = { voiceLabel, voiceOptions, mount };
  root.LAYOUT_VOICES = api;
  if (typeof module !== 'undefined') module.exports = api;
  if (typeof document !== 'undefined' && document.getElementById('lp-voice')) {
    mount(document.getElementById('lp-voice'));
  }
})(typeof window !== 'undefined' ? window : globalThis);
