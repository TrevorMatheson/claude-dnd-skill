// The Play tab (phone sheet spec D3, D11, D14): the Send box, the
// "Waiting on …" banner, the DM's roll request and the dice pad.
//
// Every write goes through PH.net (one attempt, then Not sent + Retry).
// A roll request for this character jumps the page to Play from any tab
// (AC3). Pure helpers (requestIsMine, stillOpen, faceSpec) are exported for
// node tests. PH.play.roll() and PH.play.act() are what the Sheet tab calls
// for tap-to-roll, Cast and Use.
(function (root) {
  'use strict';

  const DICE = ['d4', 'd6', 'd8', 'd10', 'd12', 'd20', 'd100'];
  const REAL_DICE_KEY = 'dnd-phone-real-dice';

  // A request is this phone's when it names this character or "any".
  function requestIsMine(req, me) {
    if (!req || !me) return false;
    const m = String(me).toLowerCase();
    const names = (Array.isArray(req.characters) && req.characters.length ? req.characters : [req.character || 'any'])
      .map((n) => String(n || '').toLowerCase());
    return names.includes(m) || names.includes('any') || names.includes('everybody');
  }

  // Is the request still waiting on this character, per a dice_pending
  // snapshot ([{request_id, pending:[names]}])?
  function stillOpen(req, pending, me) {
    if (!req) return false;
    // The server tracks only named targets; an "any" request is
    // fire-and-forget, so a snapshot says nothing about it. It closes when
    // this phone rolls it, the DM cancels it, or the page reconnects.
    const named = (Array.isArray(req.characters) && req.characters.length ? req.characters : [req.character || 'any'])
      .filter((n) => !['any', 'everybody', ''].includes(String(n || '').toLowerCase()));
    if (!named.length) return true;
    const list = Array.isArray(pending) ? pending : [];
    const e = list.find((x) => x && x.request_id === req.request_id);
    if (!e) return false;
    const m = String(me || '').toLowerCase();
    return (e.pending || []).some((n) => ['any', 'everybody', m].includes(String(n).toLowerCase()));
  }

  // How many faces a physical roll needs: 2 for a d20 with advantage or
  // disadvantage, else the number of dice.
  function faceCount(spec, advantage) {
    const m = /^(\d+)d(\d+)$/.exec(String(spec || '1d20'));
    const n = m ? Number(m[1]) : 1;
    if (spec === '1d20' && (advantage === 'advantage' || advantage === 'disadvantage')) return 2;
    return n;
  }

  function sign(n) { n = Number(n) || 0; return n >= 0 ? '+' + n : '−' + Math.abs(n); }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // ── Browser ─────────────────────────────────────────────────────────────
  let app = null, req = null, wait = null, lastResult = null;
  const $ = (id) => root.document.getElementById(id);

  function realDice() { return app.store.get(REAL_DICE_KEY) === '1'; }

  function note(text, cls) {
    const el = $('play-note');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'play-note' + (cls ? ' ' + cls : '');
    el.hidden = !text;
  }

  function handle(res, okText) {
    if (!res) return res;
    if (res.notSent) { note(''); return res; }
    if (res.status === 202) { note('Waiting for the DM to approve this phone…', 'warn'); return res; }
    if (!res.ok) {
      const b = res.body || {};
      note(b.reason || b.error || b.text || `Refused (${res.status})`, 'warn');
      return res;
    }
    if (okText) note(okText);
    return res;
  }

  // ── Send an action (D11's "goes to Claude as an action", D12, Send box) ──
  function act(text, label) {
    text = String(text || '').trim();
    if (!text) return Promise.resolve(null);
    return PH().net.send('action', '/player-input/stage',
      { character: app.me, text, ready: true, append: true }, label || `“${text.slice(0, 60)}”`)
      .then((res) => handle(res, 'Sent'));
  }

  // ── Rolls ────────────────────────────────────────────────────────────────
  // opts: {spec, modifier, label, advantage, request_id, faces}
  function roll(opts) {
    opts = Object.assign({ spec: '1d20', modifier: 0, advantage: 'normal' }, opts || {});
    if (opts.faces == null && realDice()) return askFaces(opts);
    const body = {
      character: app.me, spec: opts.spec, modifier: Number(opts.modifier) || 0,
      advantage: opts.advantage || 'normal', label: opts.label || '',
    };
    if (opts.request_id) body.request_id = opts.request_id;
    if (opts.faces != null) body.faces = opts.faces;
    const what = `${opts.label || opts.spec} roll`;
    return PH().net.send('roll', '/player-input/dice', body, what).then((res) => {
      if (res && res.ok) {
        lastResult = res.body;
        showResult(res.body);
        if (opts.request_id && req && req.request_id === opts.request_id) { req = null; drawCard(); }
      }
      return handle(res);
    });
  }

  function showResult(r) {
    const el = $('roll-result');
    if (!el || !r) return;
    el.hidden = false;
    el.innerHTML = `<span class="rr-total">${esc(r.total)}</span>
      <span class="rr-text">${esc(r.text || '')}</span>
      <span class="rr-tag">${r.asked ? 'answered the DM' : 'sent to the DM'}</span>`;
  }

  // Physical dice: ask for the face(s), then roll with them.
  function askFaces(opts) {
    const n = faceCount(opts.spec, opts.advantage);
    const el = $('faces');
    el.hidden = false;
    el.innerHTML = `<div class="faces-h">${esc(opts.label || opts.spec)}: type what you rolled
      (${n === 2 ? 'both d20s' : n + ' × ' + esc(String(opts.spec).replace(/^\d+/, ''))})</div>
      <div class="faces-in">${Array.from({ length: n }, (_, i) =>
        `<input type="number" inputmode="numeric" min="1" class="die-face" aria-label="Die ${i + 1}">`).join('')}
      <button type="button" class="btn primary" id="faces-go">Send</button>
      <button type="button" class="btn" id="faces-x">Cancel</button></div>`;
    const first = el.querySelector('.die-face');
    if (first) first.focus();
    return new Promise((resolve) => {
      el.querySelector('#faces-x').onclick = () => { el.hidden = true; resolve(null); };
      el.querySelector('#faces-go').onclick = () => {
        const faces = [...el.querySelectorAll('.die-face')].map((x) => parseInt(x.value, 10));
        if (faces.some((f) => !(f >= 1))) { note('Type each die you rolled', 'warn'); return; }
        el.hidden = true;
        resolve(roll(Object.assign({}, opts, { faces })));
      };
    });
  }

  // ── The DM's roll request (AC3, AC9) ────────────────────────────────────
  function drawCard() {
    const el = $('roll-card');
    if (!req) { el.hidden = true; el.innerHTML = ''; return; }
    el.hidden = false;
    const adv = req.advantage && req.advantage !== 'normal' ? ` · ${esc(req.advantage)}` : '';
    el.innerHTML = `<div class="rc-h">The DM asks for a roll</div>
      <div class="rc-label">${esc(req.label || req.spec || 'Roll')}</div>
      <div class="rc-meta">${esc(req.spec || '1d20')} ${sign(req.modifier)}${adv}${req.dc != null ? ` · DC ${esc(req.dc)}` : ''}</div>
      <div class="rc-btns"><button type="button" class="btn primary" id="rc-roll">Roll</button>
      <button type="button" class="btn" id="rc-real">I rolled a real die</button></div>`;
    const opts = () => ({ spec: req.spec || '1d20', modifier: req.modifier || 0, label: req.label || '',
                          advantage: req.advantage || 'normal', request_id: req.request_id });
    el.querySelector('#rc-roll').onclick = () => roll(opts());
    el.querySelector('#rc-real').onclick = () => askFaces(opts());
  }

  // The request the Sheet tab's tap answers, if it matches (S12).
  function openRequest() { return req; }

  // ── Dice pad ────────────────────────────────────────────────────────────
  function drawPad() {
    const el = $('dice-pad');
    el.innerHTML = `<div class="pad-h">Dice</div>
      <div class="pad-row">
        <select id="pad-n" aria-label="How many">${[1, 2, 3, 4, 5, 6, 8, 10].map((n) => `<option>${n}</option>`).join('')}</select>
        <div class="pad-dice">${DICE.map((d) => `<button type="button" class="die${d === 'd20' ? ' on' : ''}" data-die="${d}">${d}</button>`).join('')}</div>
      </div>
      <div class="pad-row">
        <label class="pad-mod">Mod <input id="pad-mod" type="number" inputmode="numeric" value="0"></label>
        <div class="seg" id="pad-adv">
          <button type="button" data-adv="disadvantage">Dis</button><button type="button" data-adv="normal" class="on">Normal</button><button type="button" data-adv="advantage">Adv</button>
        </div>
      </div>
      <div class="pad-row">
        <input id="pad-label" type="text" maxlength="60" placeholder="What is it for? (optional)">
        <button type="button" class="btn primary" id="pad-roll">Roll</button>
      </div>
      <label class="real"><input type="checkbox" id="real-dice"${realDice() ? ' checked' : ''}> I roll real dice (type what I rolled)</label>`;
    let die = 'd20', adv = 'normal';
    el.querySelectorAll('.die').forEach((b) => b.onclick = () => {
      die = b.dataset.die; el.querySelectorAll('.die').forEach((x) => x.classList.toggle('on', x === b));
    });
    el.querySelectorAll('#pad-adv button').forEach((b) => b.onclick = () => {
      adv = b.dataset.adv; el.querySelectorAll('#pad-adv button').forEach((x) => x.classList.toggle('on', x === b));
      api.adv = adv;
    });
    el.querySelector('#real-dice').onchange = (ev) => app.store.set(REAL_DICE_KEY, ev.target.checked ? '1' : '0');
    el.querySelector('#pad-roll').onclick = () => {
      const n = Number(el.querySelector('#pad-n').value) || 1;
      roll({ spec: `${n}${die}`, modifier: Number(el.querySelector('#pad-mod').value) || 0,
             advantage: `${n}${die}` === '1d20' ? adv : 'normal',
             label: el.querySelector('#pad-label').value.trim() });
    };
  }

  function PH() { return root.PH; }

  function start(a) {
    app = a;
    const body = $('play-body');
    body.innerHTML = `<form id="send-form" class="send" autocomplete="off">
        <label for="send-text" class="send-h">What do you do?</label>
        <textarea id="send-text" rows="3" maxlength="500" placeholder="Describe your action, or say something in character"></textarea>
        <div class="send-row"><span id="send-count" class="muted"></span><button type="submit" class="btn primary" id="send-btn">Send</button></div>
      </form>
      <div id="play-note" class="play-note" hidden></div>
      <div id="faces" class="faces" hidden></div>
      <div id="roll-result" class="roll-result" hidden></div>
      <div id="dice-pad" class="dice-pad"></div>`;
    const form = $('send-form'), ta = $('send-text');
    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      const text = ta.value.trim();
      if (!text) return;
      ta.value = '';
      // Put the text back when nothing was staged: refused, or held while
      // the DM approves this phone (202). A Not sent item keeps its own copy.
      act(text).then((res) => { if (res && !res.notSent && (!res.ok || res.status === 202)) ta.value = text; });
    });
    drawPad();

    // Waiting on … (the TV's own model, so phone and TV agree).
    const banner = $('wait-banner');
    wait = root.TV.panels.makeWaitModel({ onChange: () => {
      const v = wait.view();
      banner.hidden = !v.text;
      banner.textContent = v.text;
      banner.className = 'wait' + (v.cls ? ' ' + v.cls : '')
        + (v.names.some((n) => app.isMe(n)) ? ' me' : '');
    } });

    app.on((p) => {
      wait.apply(p);
      if (p.hello) {
        // Reconnected: forget the request on screen; the server replays every
        // request still open right after hello (D14, AC11).
        req = null; drawCard();
        return;
      }
      if (p.dice_request && requestIsMine(p.dice_request, app.me)) {
        req = p.dice_request;
        drawCard();
        app.setTab('play');            // AC3: from any tab
      }
      if (p.dice_pending !== undefined && req && !stillOpen(req, p.dice_pending, app.me)) {
        req = null; drawCard();
      }
      if (p.dice_request_cancelled && req && req.request_id === p.dice_request_cancelled) {
        req = null; drawCard();
      }
      if (p.rewind && p.rewind.inputs) {
        const mine = p.rewind.inputs.find((x) => x && app.isMe(x.character));
        if (mine && mine.text && !$('send-text').value) $('send-text').value = mine.text;
      }
    });

    if (PH().talk && typeof PH().talk.mount === 'function') {
      PH().talk.mount($('talk-slot'), { character: app.me, headers: () => app.headers(),
        onText: (t) => { ta.value = (ta.value ? ta.value + ' ' : '') + t; ta.focus(); } });
    }
  }

  const api = { requestIsMine, stillOpen, faceCount, start, roll, act, openRequest,
                get last() { return lastResult; }, adv: 'normal', REAL_DICE_KEY };
  if (root) { root.PH = root.PH || {}; root.PH.play = api; }
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : null);
