// The Talk button (phone sheet spec D15, AC12): today's speech-to-text,
// unchanged from the old page (templates/index.html, "Pass the mic").
//
// One speaker at a time: Talk claims the mic on the server, records, uploads
// the clip, and the PC transcribes it (local Whisper); the words land in the
// Send box to check and send. Hidden when speech-to-text is off.
//
// This is the only file on /phone that touches /mic/* or /stt. The Pass the
// mic spec replaces this file and the #talk-slot it mounts in:
//   PH.talk.mount(el, {character, headers: () => {...}, onText: (text) => {}})
// The page feeds it the server's `mic` stream payloads through PH.app.on().
(function (root) {
  'use strict';

  const MAX_MS = 45000;
  let btn = null, opts = null, state = { holder: '', on: false, reason: '' };
  let rec = null, stream = null, chunks = [], tick = null, t0 = 0, busy = false;

  function can() {
    const n = root.navigator;
    return !!(n && n.mediaDevices && n.mediaDevices.getUserMedia && root.MediaRecorder);
  }

  function toast(text) {
    const el = root.document.getElementById('talk-note');
    if (!el) return;
    el.textContent = text;
    el.hidden = !text;
    clearTimeout(toast.t);
    if (text) toast.t = setTimeout(() => { el.hidden = true; }, 6000);
  }

  function who() { return (opts && opts.character) || ''; }
  function mine() { return !!state.holder && state.holder.toLowerCase() === who().toLowerCase(); }

  function headers(json) {
    const h = Object.assign({}, opts.headers());
    if (!json) delete h['Content-Type'];
    else h['Content-Type'] = 'application/json';
    return h;
  }

  function render() {
    if (!btn) return;
    btn.hidden = !state.on;
    btn.classList.remove('recording', 'busy', 'taken');
    btn.disabled = false;
    if (rec) {
      const secs = Math.floor((Date.now() - t0) / 1000);
      btn.textContent = `Stop · ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
      btn.classList.add('recording');
    } else if (busy) {
      btn.textContent = 'Writing it down…';
      btn.classList.add('busy');
      btn.disabled = true;
    } else if (state.holder && !mine()) {
      btn.textContent = `${state.holder} is talking`;
      btn.classList.add('taken');
      btn.disabled = true;
    } else {
      btn.textContent = 'Talk';
    }
  }

  function releaseQuiet() {
    root.fetch('/mic/release', { method: 'POST', headers: headers(true),
      body: JSON.stringify({ character: who() }) }).catch(() => {});
  }

  async function startTalk() {
    if (!can()) { toast('The mic needs a secure (https) link to this page. Typing still works.'); return; }
    if (!who()) { toast('Pick your character first, then tap Talk.'); return; }
    let res;
    try {
      res = await root.fetch('/mic/claim', { method: 'POST', headers: headers(true),
        body: JSON.stringify({ character: who() }) });
    } catch (_) { toast("Couldn't reach the table to take the mic."); return; }
    if (res.status === 409) {
      const d = await res.json().catch(() => ({}));
      toast(`${d.holder || 'Someone'} has the mic — wait for them to finish.`);
      return;
    }
    if (res.status === 202) { toast('This phone is waiting for the DM to approve it.'); return; }
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      toast('Mic unavailable: ' + (d.error || ('error ' + res.status)));
      return;
    }
    try {
      stream = await root.navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (_) {
      releaseQuiet();
      toast('This browser blocked the microphone. Allow it in the site settings and try again.');
      return;
    }
    const MR = root.MediaRecorder;
    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
      .find((t) => MR.isTypeSupported && MR.isTypeSupported(t)) || '';
    chunks = [];
    rec = new MR(stream, mime ? { mimeType: mime } : undefined);
    rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    rec.onstop = send;
    rec.start(250);
    t0 = Date.now();
    tick = setInterval(() => { if (Date.now() - t0 >= MAX_MS) stopTalk(); else render(); }, 250);
    render();
  }

  function stopTalk() {
    clearInterval(tick); tick = null;
    if (rec && rec.state !== 'inactive') rec.stop();
  }

  async function send() {
    const r = rec;
    rec = null;
    if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
    const type = (r && r.mimeType) || 'audio/webm';
    const blob = new Blob(chunks, { type });
    chunks = [];
    if (!blob.size) { releaseQuiet(); render(); return; }
    busy = true; render();
    try {
      const fd = new FormData();
      fd.append('character', who());
      fd.append('audio', blob, 'clip.' + (type.includes('mp4') ? 'mp4' : 'webm'));
      const res = await root.fetch('/stt', { method: 'POST', headers: headers(false), body: fd });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { toast("Didn't catch that: " + (d.error || ('error ' + res.status))); return; }
      const said = String(d.text || '').trim();
      if (!said) { toast("Didn't hear any words — try again a little closer."); return; }
      if (opts.onText) opts.onText(said);
    } catch (_) {
      toast("Couldn't send the recording to the table.");
    } finally {
      busy = false; render();
    }
  }

  function mount(el, o) {
    opts = o || {};
    el.innerHTML = '<button type="button" id="talk-btn" class="talk-btn" hidden>Talk</button>'
      + '<div id="talk-note" class="talk-note" hidden role="status"></div>';
    btn = el.querySelector('#talk-btn');
    btn.addEventListener('click', () => { if (rec) stopTalk(); else if (!busy) startTalk(); });
    root.addEventListener('pagehide', () => { if (rec || mine()) releaseQuiet(); });
    if (root.PH && root.PH.app) {
      root.PH.app.on((p) => { if (p && p.mic) { state = p.mic; render(); } });
    }
    render();
  }

  const api = { mount };
  if (root) { root.PH = root.PH || {}; root.PH.talk = api; }
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : null);
