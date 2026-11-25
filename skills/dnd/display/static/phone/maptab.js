// The Map tab (phone sheet spec D13, milestone 2): the battle map with fog,
// centred on this player's token; tap a square to see the path and its cost
// ("25 of 35 ft"), then Move here. The move is checked with the battle map's
// dry run (POST /map/check) before it is sent (POST /map/move), and a move
// that can't be done shows its reason.
//
// The seam: this file draws with the battle map's own modules,
// window.TV.board (render, fit) and window.TV.los (fog, tokenShown), loaded
// only when static/map/ is installed, and asks the server through
// map_moves.py. The tab shows only in combat, with a map, and when both are
// there (tabVisible). Pure helpers are exported for node tests.
(function (root) {
  'use strict';

  const SQ = 44;            // a tappable square, CSS px

  function combatOn(stats) {
    const to = stats && stats.turn_order;
    return !!(to && typeof to === 'object' && Array.isArray(to.order) && to.order.length > 0);
  }

  function tabVisible(stats, caps) {
    return combatOn(stats) && !!(stats && stats.map && stats.map.grid)
      && !!(caps && caps.check && caps.board);
  }

  function costLabel(r) {
    if (!r || r.cost == null) return '';
    return `${r.cost} of ${r.budget} ft`;
  }

  // The square under a tap at (px, py) CSS px from the board's top-left.
  function cellAt(px, py, size, w, h) {
    const x = Math.floor(px / size), y = Math.floor(py / size);
    if (x < 0 || y < 0 || x >= w || y >= h) return null;
    return [x, y];
  }

  // Scroll offsets that put square (x, y) in the middle of a view.
  function centerOn(x, y, size, viewW, viewH, boardW, boardH) {
    const left = Math.round((x + 0.5) * size - viewW / 2);
    const top = Math.round((y + 0.5) * size - viewH / 2);
    return {
      left: Math.max(0, Math.min(left, Math.max(0, boardW - viewW))),
      top: Math.max(0, Math.min(top, Math.max(0, boardH - viewH))),
    };
  }

  function myToken(map, me) {
    const m = String(me || '').toLowerCase();
    const toks = Array.isArray(map && map.tokens) ? map.tokens : [];
    return toks.find((t) => String(t.name).toLowerCase() === m)
      || toks.find((t) => String(t.name).toLowerCase().split(' ')[0] === m.split(' ')[0]) || null;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // ── Browser ─────────────────────────────────────────────────────────────
  let app = null, view = null, box = null, overlay = null, bar = null;
  let seen = null, seenKey = '', fogNow = null, pick = null, check = null, centred = '';

  function B() { return root.TV && root.TV.board; }
  function L() { return root.TV && root.TV.los; }

  function shown() {
    const p = root.document.getElementById('pane-map');
    return p && !p.hidden && (app.layout === 'desktop' || app.tab === 'map');
  }

  function draw() {
    const m = app.stats && app.stats.map;
    if (!m || !m.grid || !B() || !shown()) return;
    const w = m.grid.w, h = m.grid.h;
    box.style.width = (w * SQ) + 'px';
    box.style.height = (h * SQ) + 'px';
    let tokens = Array.isArray(m.tokens) ? m.tokens : [];
    let fog = null;
    if (L()) {
      const ck = JSON.stringify([m.grid, m.cells]);
      if (ck !== seenKey) { seenKey = ck; seen = null; }
      fog = L().fog(m, tokens, seen);
      seen = fog.seen;
      tokens = tokens.filter((t) => L().tokenShown(t, fog));
    }
    fogNow = fog;
    const to = app.stats.turn_order || {};
    B().render(box, { map: m, tokens, fog, players: app.stats.players || [], current: to.current || null, mine: app.me });
    drawPick();
    const mine = myToken(m, app.me);
    const key = mine ? `${mine.x},${mine.y},${w},${h}` : '';
    if (mine && key !== centred) {
      centred = key;
      const c = centerOn(mine.x, mine.y, SQ, view.clientWidth, view.clientHeight, w * SQ, h * SQ);
      view.scrollLeft = c.left; view.scrollTop = c.top;
    }
  }

  // The tapped square, its path and the bar under the map.
  function drawPick() {
    if (!overlay) return;
    const m = app.stats.map;
    overlay.setAttribute('viewBox', `0 0 ${m.grid.w * SQ} ${m.grid.h * SQ}`);
    overlay.style.width = (m.grid.w * SQ) + 'px';
    overlay.style.height = (m.grid.h * SQ) + 'px';
    let svg = '';
    if (pick) {
      if (check && Array.isArray(check.path) && check.path.length > 1) {
        const pts = check.path.map(([x, y]) => `${(x + 0.5) * SQ},${(y + 0.5) * SQ}`).join(' ');
        svg += `<polyline points="${pts}" class="mt-path${check.ok ? '' : ' bad'}"/>`;
      }
      svg += `<rect x="${pick[0] * SQ + 2}" y="${pick[1] * SQ + 2}" width="${SQ - 4}" height="${SQ - 4}" class="mt-pick${check && !check.ok ? ' bad' : ''}"/>`;
    }
    overlay.innerHTML = svg;
    if (!pick) { bar.innerHTML = '<span class="muted">Tap a square to see the way there</span>'; return; }
    if (!check) { bar.innerHTML = '<span class="muted">Checking…</span>'; return; }
    if (check.ok) {
      bar.innerHTML = `<span class="mt-cost">${esc(costLabel(check))}</span>
        <button type="button" class="btn primary" id="mt-go">Move here</button>
        <button type="button" class="btn" id="mt-x">Cancel</button>`;
    } else {
      bar.innerHTML = `<span class="mt-why">${esc(check.message || "Can't move there")}</span>
        <button type="button" class="btn" id="mt-x">OK</button>`;
    }
  }

  function tap(ev) {
    const m = app.stats.map;
    if (!m) return;
    const r = box.getBoundingClientRect();
    const sq = cellAt(ev.clientX - r.left, ev.clientY - r.top, SQ, m.grid.w, m.grid.h);
    if (!sq) return;
    // Dark squares (never seen) can't be picked: nothing there is known.
    if (fogNow && !fogNow.visible.has(sq.join(',')) && !fogNow.seen.has(sq.join(','))) return;
    pick = sq; check = null; drawPick();
    const at = sq.join(',');
    fetch('/map/check', { method: 'POST', headers: app.headers(),
      body: JSON.stringify({ character: app.me, x: sq[0], y: sq[1] }) })
      .then((res) => res.json().catch(() => ({})))
      .then((j) => { if (pick && pick.join(',') === at) { check = j; drawPick(); } })
      .catch(() => { if (pick && pick.join(',') === at) { check = { ok: false, message: 'No connection' }; drawPick(); } });
  }

  function go() {
    if (!pick || !check || !check.ok) return;        // never without a passing check
    const [x, y] = pick;
    const label = `Move to ${x},${y} (${costLabel(check)})`;
    const sent = pick;
    root.PH.net.send('move', '/map/move', { character: app.me, x, y }, label).then((res) => {
      if (!res || res.notSent) return;
      if (res.ok) {
        pick = null; check = null; drawPick();
        bar.innerHTML = `<span class="mt-sent">Sent to the DM: ${esc(res.body && res.body.sent || label)}</span>`;
        return;
      }
      if (pick === sent) { check = Object.assign({ ok: false }, res.body || {}); drawPick(); }
    });
  }

  function start(a) {
    app = a;
    const body = root.document.getElementById('map-body');
    if (!body) return;
    body.innerHTML = `<div class="mt-view" id="mt-view"><div class="mt-box" id="mt-box"></div>
      <svg class="mt-overlay" id="mt-overlay"></svg></div>
      <div class="mt-bar" id="mt-bar"></div>`;
    view = root.document.getElementById('mt-view');
    box = root.document.getElementById('mt-box');
    overlay = root.document.getElementById('mt-overlay');
    bar = root.document.getElementById('mt-bar');
    view.addEventListener('click', tap);
    bar.addEventListener('click', (ev) => {
      if (ev.target.id === 'mt-go') go();
      if (ev.target.id === 'mt-x') { pick = null; check = null; drawPick(); }
    });
    app.on((p) => {
      if (p.stats || p._tab || p.hello) {
        root.requestAnimationFrame ? root.requestAnimationFrame(draw) : draw();
      }
    });
    root.addEventListener('resize', () => { centred = ''; draw(); });
  }

  const api = { tabVisible, costLabel, cellAt, centerOn, myToken, combatOn, start, SQ };
  if (root) { root.PH = root.PH || {}; root.PH.maptab = api; }
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : null);
