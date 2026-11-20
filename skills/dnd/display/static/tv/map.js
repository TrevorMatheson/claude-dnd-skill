// The battle map on the TV (display foundation spec D10, AC13). A port of the
// old page's _bmRender/_bmDrawCells (index.html:8499-8624) with three
// changes: the canvas is backed at devicePixelRatio so it stays sharp at 4K,
// token labels are sized in rem, and it draws into whichever container holds
// it — the combat column or a Tactical side panel.
//
// canvasSize() is pure so node can test it; draw() needs a DOM.
(function (root) {
  'use strict';

  // The cell size that fits a cols×rows grid into a cssW×cssH box (never
  // under 8 px, as before), and the canvas backing size at that pixel ratio.
  function canvasSize(cssW, cssH, cols, rows, dpr) {
    const r = typeof dpr === 'number' && isFinite(dpr) && dpr > 0 ? dpr : 1;
    const cell = Math.max(8, Math.floor(Math.min(cssW / cols, cssH / rows)));
    return { width: Math.round(cols * cell * r), height: Math.round(rows * cell * r), cell };
  }

  function cssVar(name, fallback) {
    try {
      const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch (_) { return fallback; }
  }

  function drawCells(canvas, m, cs, dpr) {
    const ctx = canvas.getContext('2d');
    const W = m.grid.w, H = m.grid.h;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W * cs, H * cs);
    const img = !!m.image;
    const floor = cssVar('--map-floor', '#201914'), wall = cssVar('--map-wall', '#0a0705');
    for (let y = 0; y < H; y++) {
      const row = (m.cells && m.cells[y]) || '';
      for (let x = 0; x < W; x++) {
        const c = row[x] || ' ';
        let fill = null;
        if (c === '#') fill = img ? 'rgba(20,14,8,.55)' : '#2a2118';
        else if (c === ' ') fill = img ? null : wall;
        else if (c === 'T') fill = img ? 'rgba(90,60,30,.45)' : '#5a4128';
        else if (c === '~') fill = img ? 'rgba(60,90,120,.30)' : '#2f3f4a';
        else if (!img) fill = c === 'D' ? '#6b4a22' : floor;
        if (fill) { ctx.fillStyle = fill; ctx.fillRect(x * cs, y * cs, cs, cs); }
      }
    }
    ctx.strokeStyle = cssVar('--map-line', 'rgba(241,235,225,.08)');
    ctx.lineWidth = 1 / dpr;
    const half = 0.5 / dpr;
    for (let x = 0; x <= W; x++) { ctx.beginPath(); ctx.moveTo(x * cs + half, 0); ctx.lineTo(x * cs + half, H * cs); ctx.stroke(); }
    for (let y = 0; y <= H; y++) { ctx.beginPath(); ctx.moveTo(0, y * cs + half); ctx.lineTo(W * cs, y * cs + half); ctx.stroke(); }
  }

  function hpFrac(t, players) {
    const p = players[String(t.name).toLowerCase()];
    const hp = t.hp || (p && p.hp);
    if (!hp) return null;
    const cur = Array.isArray(hp) ? hp[0] : hp.current;
    const max = Array.isArray(hp) ? hp[1] : hp.max;
    if (typeof cur !== 'number' || !max) return null;
    return Math.max(0, Math.min(1, cur / max));
  }

  function tokenId(name) { return 'tv-tok-' + String(name).toLowerCase().replace(/[^a-z0-9]+/g, '-'); }

  // Draw stats.map into `box`. Cheap to call again: the grid is redrawn only
  // when the map or the box size changes; tokens move with a transition.
  function draw(box, stats) {
    if (!box) return;
    const m = stats && stats.map;
    if (!m || !m.grid || !m.grid.w || !m.grid.h) { box.innerHTML = ''; delete box.dataset.key; return; }
    const W = m.grid.w, H = m.grid.h;
    if (box.dataset.aspect === 'grid') box.style.aspectRatio = `${W} / ${H}`;
    const cssW = box.clientWidth, cssH = box.clientHeight;
    if (!cssW || !cssH) return;
    const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
    const size = canvasSize(cssW, cssH, W, H, dpr);
    const cs = size.cell;

    let board = box.querySelector('.map-board');
    if (!board) { box.innerHTML = '<div class="map-board"></div>'; board = box.firstChild; }
    const key = JSON.stringify([m.grid, m.cells, m.image, cs, dpr]);
    if (box.dataset.key !== key) {
      box.dataset.key = key;
      board.querySelectorAll('canvas, img.map-bg').forEach((el) => el.remove());
      board.style.width = W * cs + 'px';
      board.style.height = H * cs + 'px';
      if (m.image) {
        const bg = document.createElement('img');
        bg.className = 'map-bg'; bg.alt = '';
        bg.src = '/map-image/' + encodeURI(m.image);
        board.prepend(bg);
      }
      const canvas = document.createElement('canvas');
      canvas.width = size.width; canvas.height = size.height;
      drawCells(canvas, m, cs, dpr);
      if (m.image) board.querySelector('img.map-bg').after(canvas); else board.prepend(canvas);
    }

    const players = {};
    for (const p of Array.isArray(stats.players) ? stats.players : []) if (p && p.name) players[String(p.name).toLowerCase()] = p;
    const to = stats.turn_order || {};
    const current = String(to.current || '').toLowerCase();
    const seen = new Set();
    for (let t of Array.isArray(m.tokens) ? m.tokens : []) {
      if (!t || t.name == null) continue;
      const id = tokenId(t.name);
      seen.add(id);
      let el = board.querySelector('#' + id);
      if (!el) {
        el = document.createElement('div');
        el.id = id; el.className = 'map-token';
        el.innerHTML = '<div class="face"></div><div class="label"></div>';
        board.appendChild(el);
      }
      const px = (t.size || 1) * cs;
      el.classList.toggle('foe', t.kind !== 'pc');
      el.classList.toggle('now', !!current && String(t.name).toLowerCase() === current);
      el.style.width = px + 'px'; el.style.height = px + 'px';
      el.style.left = (t.x || 0) * cs + 'px'; el.style.top = (t.y || 0) * cs + 'px';
      const frac = hpFrac(t, players);
      const ring = frac === null ? 'var(--accent)' : frac > 0.5 ? 'var(--hp)' : frac > 0.25 ? '#e0b44a' : 'var(--hp-low)';
      const deg = frac === null ? 360 : Math.round(frac * 360);
      el.style.background = `conic-gradient(${ring} ${deg}deg, rgba(40,30,20,.9) ${deg}deg)`;
      el.classList.toggle('down', frac === 0);
      const face = el.querySelector('.face');
      face.textContent = String(t.name).split(/\s+/).map((w) => w[0] || '').join('').slice(0, 2).toUpperCase();
      face.style.fontSize = Math.max(9, Math.floor(px * 0.36)) + 'px';
      el.querySelector('.label').textContent = cs >= 26 ? t.name : '';
      const pl = players[String(t.name).toLowerCase()];
      if ((!t.conditions || !t.conditions.length) && pl && Array.isArray(pl.conditions)) t = Object.assign({}, t, { conditions: pl.conditions });
      let cond = el.querySelector('.cond');
      if (t.conditions && t.conditions.length) {
        if (!cond) { cond = document.createElement('div'); cond.className = 'cond'; el.appendChild(cond); }
        cond.textContent = t.conditions.length;
        el.title = t.name + ' — ' + t.conditions.join(', ');
      } else {
        if (cond) cond.remove();
        el.title = t.name;
      }
    }
    board.querySelectorAll('.map-token').forEach((el) => { if (!seen.has(el.id)) el.remove(); });
  }

  const api = { canvasSize, draw };
  root.TV = root.TV || {};
  root.TV.map = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
