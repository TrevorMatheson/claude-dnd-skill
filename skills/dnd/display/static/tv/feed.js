// The story feed on the TV.
//
// makeFeedModel() holds what the feed shows and has no DOM, so node can test
// it: the blocks in order, and the roll card for a pending roll request. It
// takes the server's stream payloads as they come (see the shapes in
// tests/test_tv_feed.py). mount() draws a model into the page.
(function (root) {
  'use strict';

  const streamApi = (typeof module !== 'undefined' && typeof require === 'function')
    ? require('./stream.js')
    : (root && root.TV && root.TV.stream);

  // Payload keys that make a block even without text.
  const BLOCK_KEYS = ['inspiration_award', 'milestone_award', 'xp_award', 'effect_expired'];
  const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

  function isBlock(p) {
    if (!isObj(p) || 'milestone_spend' in p) return false;
    if (BLOCK_KEYS.some((k) => k in p)) return true;
    return typeof p.text === 'string';
  }

  function makeFeedModel() {
    const filter = streamApi.makeSeqFilter();
    let blocks = [];
    let requests = new Map();   // request_id -> the dice_request payload
    let pending = new Map();    // request_id -> {pending, label} from dice_pending

    const addBlock = (entry, history) => {
      if (!isObj(entry) || !filter.keep(entry)) return;
      blocks.push(Object.assign({}, entry, { history: !!history }));
    };

    const forgetRolls = () => { requests = new Map(); pending = new Map(); };

    function setPending(list) {
      pending = new Map();
      if (Array.isArray(list)) {
        for (const p of list) {
          if (isObj(p) && p.request_id != null) pending.set(p.request_id, p);
        }
      }
      // A request no longer pending is done (or was never tracked, like an
      // open "anyone roll"): it never gets a card.
      for (const rid of [...requests.keys()]) if (!pending.has(rid)) requests.delete(rid);
    }

    function applyOne(p) {
      if (!isObj(p)) return;

      if (isObj(p.hello)) {
        // The server re-sends what is still pending after the hello, so the
        // roll card is rebuilt from that; anything resolved while away is gone.
        forgetRolls();
        if (filter.hello(p.hello.epoch) !== 'same') blocks = [];
        return;
      }
      if ('replay_batch' in p) {
        if (Array.isArray(p.replay_batch)) p.replay_batch.forEach((e) => addBlock(e, true));
        return;
      }
      if ('rewind' in p) {
        const r = p.rewind;
        if (!isObj(r)) return;
        blocks = (Array.isArray(r.log) ? r.log : [])
          .filter(isObj)
          .map((e) => Object.assign({}, e, { history: true }));
        forgetRolls();
        return;
      }
      if (p.clear) { blocks = []; forgetRolls(); return; }

      if ('dice_pending' in p) setPending(p.dice_pending);
      if (p.dice_request_cancelled != null) requests.delete(p.dice_request_cancelled);
      if ('dice_request' in p) {
        const req = p.dice_request;
        if (isObj(req) && req.request_id != null) requests.set(req.request_id, req);
      }

      if (isBlock(p)) addBlock(p, false);
    }

    function apply(payload) {
      try { applyOne(payload); } catch (err) {
        if (typeof console !== 'undefined') console.error('tv feed: bad payload', err);
      }
    }

    function rollCard() {
      // The oldest request that is still waiting on someone.
      for (const [rid, req] of requests) {
        const p = pending.get(rid);
        if (p) return Object.assign({}, req, { pending: p.pending || [] });
      }
      return null;
    }

    return {
      apply,
      view() { return { blocks: blocks.slice(), rollCard: rollCard() }; },
    };
  }

  // ── Drawing ──────────────────────────────────────────────────────────────

  const MAX_BLOCKS = 80;

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // **bold**, *italic* and paragraph breaks.
  function inline(text) {
    return esc(text)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\n]+?)\*/g, '$1<em>$2</em>')
      .split(/\n{2,}/).map((para) => para.replace(/\n/g, '<br>')).join('</p><p>');
  }

  function blockHTML(b) {
    if (b.npc) {
      return `<div class="who">${esc(b.npc)}</div><q>${inline(b.text || '')}</q>`;
    }
    if (b.player || b.action) {
      return `<span class="who">${esc(b.player || b.action)}</span>${inline(b.text || '')}`;
    }
    if (b.effect_expired) {
      const e = b.effect_expired;
      return `${esc(e.name || 'An effect')} on ${esc(e.owner || 'someone')} has ended.`;
    }
    if (b.inspiration_award) return `<strong>${esc(b.inspiration_award)}</strong> gains Inspiration.${b.reason ? ` <span class="why">${esc(b.reason)}</span>` : ''}`;
    if (b.milestone_award) return `<strong>${esc(b.milestone_award)}</strong>: ${esc(b.label || 'Milestone')}.${b.reason ? ` <span class="why">${esc(b.reason)}</span>` : ''}`;
    return `<p>${inline(b.text || '')}</p>`;
  }

  function blockClass(b) {
    if (b.npc) return 'npc';
    if (b.player || b.action) return 'you';
    if (b.dice) return 'dice';
    if (b.tutor) return 'tutor';
    if (b.effect_expired || b.inspiration_award || b.milestone_award || b.xp_award) return 'note';
    return 'narr';
  }

  // What the feed shows, as a string: the feed is re-drawn only when this
  // changes, so stats, mic and queue updates don't rebuild it.
  function signature(v) {
    const last = v.blocks[v.blocks.length - 1];
    const c = v.rollCard;
    return [v.blocks.length, v.blocks.length ? v.blocks[0].seq : '', last ? last.seq : '',
            last ? String(last.text || '').length : '',
            c ? `${c.request_id}:${(c.pending || []).join(',')}` : ''].join('|');
  }

  // Draw `model` into `el` after every payload that changes it. `opts.token`
  // signs the rewind request.
  function mount(el, model, opts) {
    opts = opts || {};
    const headers = { 'Content-Type': 'application/json' };
    if (opts.token) headers['X-DND-Token'] = opts.token;
    let drawn = null;
    // The armed ↶ survives a re-draw: which block it was on, and until when.
    let armed = { seq: null, until: 0 };

    function rewindButton(seq) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'rewind-btn';
      b.title = 'Rewind this turn';
      b.setAttribute('aria-label', 'Rewind this turn');
      const isArmed = () => armed.seq === seq && Date.now() < armed.until;
      const show = () => {
        b.classList.toggle('armed', isArmed());
        b.textContent = isArmed() ? '↶ Rewind?' : '↶';
      };
      show();
      if (isArmed()) setTimeout(show, armed.until - Date.now());
      b.addEventListener('click', (ev) => {
        ev.stopPropagation();
        if (!isArmed()) {
          armed = { seq, until: Date.now() + 4000 };
          show();
          setTimeout(show, 4000);
          return;
        }
        armed = { seq: null, until: 0 };
        b.disabled = true;
        b.textContent = '↶ Rewinding…';
        fetch('/rewind', { method: 'POST', headers })
          .then((r) => { if (!r.ok) throw new Error(String(r.status)); })
          .catch(() => {
            b.disabled = false;
            b.classList.remove('armed');
            b.textContent = '↶ Rewind failed';
            setTimeout(show, 4000);
          });
      });
      return b;
    }

    function render(force) {
      const v = model.view();
      const sig = signature(v);
      if (!force && sig === drawn) return;
      drawn = sig;
      const shown = v.blocks.slice(-MAX_BLOCKS);
      const frag = document.createDocumentFragment();
      let lastTurn = null;
      let lastTurnSeq = null;
      shown.forEach((b, i) => {
        const div = document.createElement('div');
        // Every block but the newest is "old" (narration dims; NPC and
        // player lines keep their colours). `history` only means "not spoken".
        div.className = `block ${blockClass(b)}${i < shown.length - 1 ? ' old' : ''}`;
        if (typeof b.seq === 'number') div.dataset.seq = String(b.seq);
        div.innerHTML = blockHTML(b);
        if (b.player || b.action) { lastTurn = div; lastTurnSeq = b.seq; }
        frag.appendChild(div);
      });
      if (lastTurn) lastTurn.appendChild(rewindButton(lastTurnSeq));
      if (v.rollCard) {
        const c = v.rollCard;
        const card = document.createElement('div');
        card.className = 'roll-card';
        card.dataset.requestId = String(c.request_id);
        const who = (c.pending && c.pending.length) ? c.pending.join(', ') : (c.characters || []).join(', ');
        const mod = typeof c.modifier === 'number' && c.modifier ? (c.modifier > 0 ? ` +${c.modifier}` : ` ${c.modifier}`) : '';
        card.innerHTML = `<span class="die" aria-hidden="true">d20</span><div><div>${esc(who)}, roll <strong>${esc(c.label || c.spec || 'the dice')}</strong></div>`
          + `<small>${esc((c.spec || '') + mod)}${c.dc ? ` · DC ${esc(c.dc)}` : ''} · roll on your phone, or roll real dice</small></div>`;
        frag.appendChild(card);
      }
      el.replaceChildren(frag);
    }

    render(true);
    return {
      apply(p) { model.apply(p); render(false); },
      render: () => render(true),
    };
  }

  const api = { makeFeedModel, mount };
  if (root) { root.TV = root.TV || {}; root.TV.feed = api; }
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : null);
