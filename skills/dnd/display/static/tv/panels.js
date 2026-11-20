// The side-panel modules on the TV (display foundation spec D7): Party,
// Turn order, Battle map, Quests, and World clock & effects, plus the
// "Waiting on …" input bar under the narration (port of index.html:8385-8445).
//
// The *View functions and makeWaitModel() have no DOM so node can test them;
// mount() draws them. Panels are thin rules, never boxes (mockup "ink" look).
(function (root) {
  'use strict';

  const list = (v) => (Array.isArray(v) ? v : []);

  // ── View models ─────────────────────────────────────────────────────────

  function partyView(stats) {
    return list(stats && stats.players).filter((p) => p && typeof p === 'object').map((p) => {
      const hp = p.hp && typeof p.hp === 'object' ? p.hp : {};
      const hasBoth = hp.max && hp.current != null;
      const hpPct = hasBoth ? Math.max(0, Math.min(100, (hp.current / hp.max) * 100)) : 100;
      const meta = [p.class ? p.class + (p.level ? ' ' + p.level : '') : '', p.ac != null ? 'AC ' + p.ac : '']
        .filter(Boolean).join(' · ');
      return {
        name: String(p.name || '—'),
        hp: { current: hp.current, max: hp.max },
        hpPct,
        low: hasBoth && hpPct <= 35,
        conditions: list(p.conditions).map(String),
        meta,
      };
    });
  }

  function turnsView(stats) {
    const to = stats && stats.turn_order;
    if (!to || typeof to !== 'object') return null;
    const order = list(to.order);
    if (!order.length) return null;
    return {
      round: to.round != null ? to.round : 1,
      items: order.map((name) => ({ name: String(name), current: name === to.current })),
    };
  }

  // The combat turn bar (D10): round, then a token per combatant; a name
  // that isn't in the party is a foe. Null while the order names nobody.
  function turnBarView(stats) {
    const t = turnsView(stats);
    if (!t) return null;
    const party = new Set(list(stats && stats.players).filter((p) => p && p.name)
      .map((p) => String(p.name).toLowerCase()));
    return {
      round: t.round,
      items: t.items.map((i) => ({ name: i.name, current: i.current, foe: !party.has(i.name.toLowerCase()) })),
    };
  }

  function questsView(stats) {
    return list(stats && stats.quests).filter((q) => q && typeof q === 'object').map((q) =>
      ({ name: String(q.name || '—'), status: q.status != null ? String(q.status) : '' }));
  }

  function fmtLeft(ms) {
    const min = Math.ceil(ms / 60000);
    if (min >= 60) {
      const h = Math.floor(min / 60), m = min % 60;
      return m ? `${h} h ${m} min` : `${h} h`;
    }
    return `${Math.max(0, min)} min`;
  }

  function effectLeft(eff, now) {
    if (eff.duration_type === 'rounds') {
      const r = eff.duration_remaining || 0;
      return `${r} rnd`;
    }
    if ((eff.duration_type === 'minutes' || eff.duration_type === 'hours')
        && eff.started_at != null && eff.duration_seconds != null) {
      return fmtLeft((eff.started_at + eff.duration_seconds) * 1000 - now);
    }
    return '';
  }

  function clockView(stats, now) {
    now = now == null ? Date.now() : now;
    const wt = stats && stats.world_time && typeof stats.world_time === 'object' ? stats.world_time : {};
    const effects = [];
    for (const p of list(stats && stats.players)) {
      if (!p || typeof p !== 'object') continue;
      for (const eff of list(p.effects)) {
        if (!eff || typeof eff !== 'object') continue;
        effects.push({ owner: String(p.name || ''), name: String(eff.name || ''), left: effectLeft(eff, now) });
      }
    }
    const sub = [wt.season, wt.day_name, wt.weather].filter(Boolean).join(' · ');
    return { date: wt.date, time: wt.time, sub, effects };
  }

  // ── "Waiting on …" ──────────────────────────────────────────────────────
  // Who the table is waiting on: the DM's awaiting list, else whoever's turn
  // it is, else the whole party — minus anyone who has already sent. Once
  // everything is in it reads "The DM is responding…" until the reply stops.

  function joinNames(names) {
    if (names.length <= 1) return names.join('');
    return names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1];
  }

  function makeWaitModel(opts) {
    opts = opts || {};
    const setTimer = opts.setTimeout || ((f, ms) => setTimeout(f, ms));
    const clearTimer = opts.clearTimeout || ((t) => clearTimeout(t));
    const onChange = opts.onChange || (() => {});
    let awaiting = null, staged = {}, queued = [], busy = false, timer = null, party = [], current = null;

    function idleIn(ms) {
      clearTimer(timer);
      timer = setTimer(() => { busy = false; onChange(); }, ms);
    }

    function names() {
      if (awaiting && awaiting.length) return awaiting.slice();
      if (current && party.some((n) => n.toLowerCase() === String(current).toLowerCase())) return [current];
      return party.slice();
    }

    function view() {
      if (busy || queued.length) return { text: 'The DM is responding…', cls: 'dm', names: [] };
      const sent = new Set(Object.entries(staged || {})
        .filter(([, v]) => v && v.ready).map(([c]) => c.toLowerCase()));
      const left = sent.has('everybody') ? [] : names().filter((n) => !sent.has(String(n).toLowerCase()));
      if (left.length === 1) return { text: `Waiting on ${left[0]}`, cls: '', names: left };
      if (left.length) return { text: `Waiting on ${joinNames(left)}`, cls: '', names: left };
      if (sent.size) return { text: 'Sent — the DM is up next', cls: 'dm', names: [] };
      return { text: '', cls: '', names: [] };
    }

    function apply(p) {
      if (!p || typeof p !== 'object') return;
      let changed = false;
      if (p.stats && typeof p.stats === 'object') {
        party = list(p.stats.players).map((x) => x && x.name).filter(Boolean).map(String);
        const to = p.stats.turn_order;
        current = to && typeof to === 'object' ? to.current || null : null;
        changed = true;
      }
      if ('awaiting' in p) { awaiting = Array.isArray(p.awaiting) ? p.awaiting.map(String) : null; changed = true; }
      if (p.staged_inputs !== undefined) { staged = p.staged_inputs || {}; changed = true; }
      if (p.queue_status !== undefined) {
        queued = list(p.queue_status).slice();
        if (queued.length) { busy = true; idleIn(90000); }
        changed = true;
      }
      if (p.dm_processing) { busy = true; idleIn(90000); changed = true; }
      // Each piece of the DM's reply pushes "responding" back; 12 s of quiet
      // after the last one means the players are up.
      if (p.text && !p.player && !p.action && (busy || queued.length)) idleIn(12000);
      if (changed) onChange();
    }

    return { apply, view, party: () => party.slice() };
  }

  // ── Drawing ─────────────────────────────────────────────────────────────

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  const TITLES = { party: 'Party', turns: 'Turn order', map: 'Battle map', quests: 'Quests', clock: 'World clock' };

  function partyHtml(stats) {
    const pcs = partyView(stats);
    if (!pcs.length) return '<p class="mod-empty">No one has joined yet</p>';
    return pcs.map((pc) => {
      const hp = pc.hp.current != null || pc.hp.max != null
        ? `<span class="pc-hp"><b>${esc(pc.hp.current ?? '—')}</b> / ${esc(pc.hp.max ?? '—')}</span>` : '';
      const chips = pc.conditions.map((c) => `<span class="chip">${esc(c)}</span>`).join('');
      const cls = pc.meta ? `<span class="pc-class">${esc(pc.meta)}</span>` : '';
      const meta = cls || chips ? `<div class="pc-meta${chips ? '' : ' plain'}">${cls}${chips}</div>` : '';
      return `<div class="pc"><div class="pc-top"><span class="pc-name">${esc(pc.name)}</span>${hp}</div>`
        + `<div class="bar"><i${pc.low ? ' class="low"' : ''} style="width:${pc.hpPct.toFixed(1)}%"></i></div>${meta}</div>`;
    }).join('');
  }

  function turnsHtml(stats) {
    const t = turnsView(stats);
    if (!t) return '<p class="mod-empty">Not in combat</p>';
    return `<div class="turn-round">Round ${esc(t.round)}</div><ol class="turns">`
      + t.items.map((i) => `<li${i.current ? ' class="now"' : ''}>${esc(i.name)}</li>`).join('') + '</ol>';
  }

  function questsHtml(stats) {
    const qs = questsView(stats);
    if (!qs.length) return '<p class="mod-empty">No quests yet</p>';
    return qs.map((q) => {
      const s = q.status.toLowerCase();
      const done = s === 'resolved' || s === 'failed';
      const st = s && s !== 'active' ? `<small${done ? ' class="q-done"' : ''}>${esc(q.status)}</small>` : '';
      return `<div class="quest${done ? ' done' : ''}">${esc(q.name)}${st}</div>`;
    }).join('');
  }

  function clockHtml(stats) {
    const c = clockView(stats);
    const when = [c.date, c.time].filter(Boolean).join(' · ');
    let html = when
      ? `<div class="clock">${esc(when)}${c.sub ? `<small>${esc(c.sub)}</small>` : ''}</div>`
      : '<p class="mod-empty">Time unknown</p>';
    if (c.effects.length) {
      html += '</section><section class="mod" data-module="effects"><h3 class="mod-title">Effects</h3>'
        + c.effects.map((e) => `<div class="effect"><span>${esc(e.name)}${e.owner ? ' · ' + esc(e.owner) : ''}</span>`
          + `<b>${esc(e.left)}</b></div>`).join('');
    }
    return html;
  }

  function mapHtml(stats) {
    // map.js draws into the slot after the panel is on the page.
    return stats && stats.map ? '<div class="map-slot" data-aspect="grid"></div>' : '<p class="mod-empty">No map yet</p>';
  }

  function initials(name) {
    return String(name).split(/\s+/).map((w) => w[0] || '').join('').slice(0, 2).toUpperCase();
  }

  function turnBarHtml(stats) {
    const t = turnBarView(stats);
    if (!t) return '';
    return `<span class="round">Round ${esc(t.round)}</span><div class="toks">`
      + t.items.map((i) => `<span class="tok${i.foe ? ' foe' : ''}${i.current ? ' now' : ''}">`
        + `<span class="dot">${esc(initials(i.name))}</span>${esc(i.name)}</span>`).join('') + '</div>';
  }

  // A long fight can't fit one row: slide the tokens so the current one
  // shows, with the one before it still in view.
  function showCurrentTurn(bar) {
    const toks = bar && bar.querySelector('.toks');
    if (!toks) return;
    const now = toks.querySelector('.tok.now');
    if (!now || toks.scrollWidth <= toks.clientWidth) { toks.scrollLeft = 0; toks.classList.remove('more'); return; }
    const prev = now.previousElementSibling;
    toks.scrollLeft = Math.max(0, (prev || now).offsetLeft - toks.offsetLeft);
    const nowRight = now.offsetLeft - toks.offsetLeft + now.offsetWidth;
    if (nowRight > toks.scrollLeft + toks.clientWidth) toks.scrollLeft = nowRight - toks.clientWidth;
    toks.classList.toggle('more', toks.scrollLeft + toks.clientWidth < toks.scrollWidth - 1);
  }

  const BODY = { party: partyHtml, turns: turnsHtml, map: mapHtml, quests: questsHtml, clock: clockHtml };

  function panelHtml(ids, stats) {
    return ids.map((id) => {
      let body = '';
      try { body = BODY[id](stats || {}); } catch (err) { console.error('tv: panel ' + id + ' failed', err); }
      return `<section class="mod" data-module="${id}"><h3 class="mod-title">${TITLES[id]}</h3>${body}</section>`;
    }).join('');
  }

  // Draw the side panels, the combat turn bar, party header and map, and the
  // input bar for a resolved layout. Redraws only what changed.
  function mount(els) {
    let last = { left: null, right: null, bar: null, turnbar: null, party: null };
    const set = (key, html) => {
      if (els[key] && html !== last[key]) { els[key].innerHTML = html; last[key] = html; }
    };
    const drawMap = (box, stats) => {
      if (box && root.TV && root.TV.map) {
        try { root.TV.map.draw(box, stats); } catch (err) { console.error('tv: map failed', err); }
      }
    };

    function render(resolved, stats, wait) {
      const combat = resolved.mode === 'combat-map' || resolved.mode === 'combat-nomap';
      const left = combat ? [] : resolved.left, right = combat ? [] : resolved.right;
      set('left', panelHtml(left, stats));
      set('right', panelHtml(right, stats));
      if (els.left) { els.left.hidden = !left.length; els.left.dataset.count = left.length; }
      if (els.right) { els.right.hidden = !right.length; els.right.dataset.count = right.length; }
      set('turnbar', combat ? turnBarHtml(stats || {}) : '');
      set('party', combat ? partyHtml(stats || {}) : '');
      if (els.turnbar) { els.turnbar.hidden = !combat; if (combat) showCurrentTurn(els.turnbar); }
      if (els.party) els.party.hidden = !combat;
      if (els.map) {
        els.map.hidden = resolved.mode !== 'combat-map';
        if (resolved.mode === 'combat-map') drawMap(els.map, stats);
        else { els.map.innerHTML = ''; delete els.map.dataset.key; }
      }
      for (const side of [els.left, els.right]) {
        const slot = side && side.querySelector('.map-slot');
        if (slot) drawMap(slot, stats);
      }
      if (els.bar && wait) {
        const v = wait.view();
        const pips = wait.party().map((n) =>
          `<span class="pip${v.names.some((w) => w.toLowerCase() === n.toLowerCase()) ? ' on' : ''}">${esc(n)}</span>`).join('');
        const text = v.names.length
          ? 'Waiting on ' + joinNames(v.names.map((n) => `<b>${esc(n)}</b>`))
          : esc(v.text);
        const bar = `<span class="wait ${v.cls}">${text}</span><span class="pips">${pips}</span>`;
        if (bar !== last.bar) { els.bar.innerHTML = bar; last.bar = bar; }
      }
    }

    return { render };
  }

  const api = { partyView, turnsView, turnBarView, questsView, clockView, makeWaitModel, panelHtml, mount };
  root.TV = root.TV || {};
  root.TV.panels = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
