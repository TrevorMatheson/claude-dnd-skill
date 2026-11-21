// The battle-map board (battle-map spec D5-D8, D16): themed tiles drawn from
// the text layout, tokens with an HP ring, initials or a portrait, condition
// icons and the current-turn highlight, and an SVG overlay for spell areas
// and attack sprites. Shared by /tv now and the phone's Map tab later.
//
// fit(), tileKind() and tokenView() are pure so node can test them; render()
// and the overlay helpers need a DOM. Positions come from the caller (the
// TV's map.js adapter decides what is shown and when, fx.js how it moves).
(function (root) {
  'use strict';

  // ── pure ─────────────────────────────────────────────────────────────────

  // The whole w×h grid in a boxW×boxH box (D7: never panned or zoomed): an
  // integer square size in CSS px (so square edges land on whole device
  // pixels at an integer pixel ratio), the grid size, and the offsets that
  // centre it. `back*` are the canvas backing sizes at `dpr`.
  function fit(w, h, boxW, boxH, dpr) {
    const r = typeof dpr === 'number' && isFinite(dpr) && dpr > 0 ? dpr : 1;
    const cols = Math.max(1, w | 0), rows = Math.max(1, h | 0);
    const size = Math.max(8, Math.floor(Math.min(boxW / cols, boxH / rows)));
    const width = cols * size, height = rows * size;
    return {
      size, width, height,
      x: Math.max(0, Math.floor((boxW - width) / 2)),
      y: Math.max(0, Math.floor((boxH - height) / 2)),
      dpr: r, backW: Math.round(width * r), backH: Math.round(height * r),
    };
  }

  const KINDS = { '#': 'wall', '.': 'floor', '~': 'rough', 'T': 'obstacle', 'D': 'door', ' ': 'void' };
  function tileKind(ch) {
    if (ch === undefined || ch === null || ch === '') return 'void';
    return KINDS[ch] || 'floor';     // a token letter left in a layout is floor
  }

  // A monster's ring shows a rough state in fixed steps, never its numbers (D8).
  const STEPS = { healthy: 1, bloodied: 0.5, near_death: 0.2 };
  function hpState(cur, max) {
    if (typeof cur !== 'number' || typeof max !== 'number' || !(max > 0)) return null;
    const f = cur / max;
    return f > 0.5 ? 'healthy' : f > 0.25 ? 'bloodied' : 'near_death';
  }

  function initials(name) {
    return String(name || '').trim().split(/\s+/).map((w) => w[0] || '').join('').slice(0, 2).toUpperCase();
  }

  // A small glyph per 5e condition; anything else shows its first letter.
  const ICONS = {
    blinded: '◌', charmed: '♥', deafened: '◍', exhausted: '≈', frightened: '!', grappled: '✊',
    incapacitated: '✕', invisible: '◇', paralyzed: 'ϟ', paralysed: 'ϟ', petrified: '▣', poisoned: '☠',
    prone: '↧', restrained: '⛓', stunned: '✶', unconscious: 'z', concentrating: '◎', hidden: '◐',
    blessed: '✚', hasted: '»', raging: '♨',
  };
  function conditionIcon(c) {
    const k = String(c || '').trim().toLowerCase();
    return { name: k, glyph: ICONS[k] || (k[0] || '?').toUpperCase() };
  }

  function findPlayer(players, name) {
    if (!players || name == null) return null;
    const key = String(name).toLowerCase();
    if (!Array.isArray(players) && typeof players === 'object' && players[key]) return players[key];
    if (Array.isArray(players)) {
      for (const p of players) if (p && p.name != null && String(p.name).toLowerCase() === key) return p;
    }
    return null;
  }

  function hpPair(hp) {
    if (!hp) return null;
    const cur = Array.isArray(hp) ? hp[0] : hp.current;
    const max = Array.isArray(hp) ? hp[1] : hp.max;
    return typeof cur === 'number' && typeof max === 'number' && max > 0 ? [cur, max] : null;
  }

  // What one token looks like: initials, ring fraction (exact for a PC,
  // stepped for a monster), the PC's "cur/max" label, condition icons and
  // the highlight flags. Monsters never carry a number, even from an old
  // payload that still has `hp` (D8).
  function tokenView(t, players, current, mine) {
    t = t || {};
    const name = String(t.name == null ? '' : t.name);
    const pc = t.kind === 'pc';
    const p = findPlayer(players, name);
    let ring = null, hpLabel = '', state = null;
    if (pc) {
      const pair = hpPair(t.hp) || hpPair(p && p.hp);
      if (pair) {
        ring = Math.max(0, Math.min(1, pair[0] / pair[1]));
        hpLabel = `${pair[0]}/${pair[1]}`;
      }
    } else {
      state = STEPS[t.hp_state] !== undefined ? t.hp_state : null;
      if (!state) { const pair = hpPair(t.hp); state = pair ? hpState(pair[0], pair[1]) : null; }
      ring = state ? STEPS[state] : null;
    }
    let conds = Array.isArray(t.conditions) && t.conditions.length ? t.conditions
      : (p && Array.isArray(p.conditions) ? p.conditions : []);
    const icons = conds.slice(0, 6).map(conditionIcon);
    const isCurrent = !!current && name.toLowerCase() === String(current).toLowerCase();
    const isMine = !!mine && (mine === true || name.toLowerCase() === String(mine).toLowerCase());
    const down = pc ? ring === 0 : false;
    const classes = [pc ? 'pc' : 'foe'];
    if (isCurrent) classes.push('now');
    if (isMine) classes.push('mine');
    if (down) classes.push('down');
    if (state) classes.push(state.replace('_', '-'));
    return {
      name, pc, initials: initials(name), ring, state, hpLabel,
      icons, current: isCurrent, mine: isMine, down, classes,
      portrait: typeof t.portrait === 'string' && t.portrait ? t.portrait : null,
      size: Math.max(1, Math.min(4, t.size | 0 || 1)),
      title: name + (icons.length ? ' — ' + icons.map((i) => i.name).join(', ') : ''),
    };
  }

  // ── tiles ────────────────────────────────────────────────────────────────

  // A stable pseudo-random number per square, so the stone texture doesn't
  // shimmer between redraws.
  function rand(x, y, k) {
    let h = (x * 374761393 + y * 668265263 + (k || 0) * 2147483647) | 0;
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
  }

  function shade(hex, amt) {
    const n = parseInt(hex.slice(1), 16);
    const c = (v) => Math.max(0, Math.min(255, Math.round(v + amt)));
    return `rgb(${c(n >> 16)},${c((n >> 8) & 255)},${c(n & 255)})`;
  }

  const PAL = {
    floor: '#4a4036', seam: '#2c251e', wall: '#2e251d', wallTop: '#5a4b3c', wallDark: '#120d09',
    door: '#7a5431', doorDark: '#3f2a16', iron: '#2a2622', rough: '#3d453a', pebble: '#262b24',
    block: '#64503a', blockTop: '#86704f', voidFill: '#080604',
  };

  function drawFloor(ctx, x, y, s) {
    const n = rand(x, y, 1);
    ctx.fillStyle = shade(PAL.floor, (n - 0.5) * 18);
    ctx.fillRect(x * s, y * s, s, s);
    // flagstone seams: one split per square, alternating direction
    ctx.strokeStyle = PAL.seam; ctx.lineWidth = Math.max(1, s / 28);
    ctx.beginPath();
    if (n < 0.5) { const sy = y * s + s * (0.35 + rand(x, y, 2) * 0.3); ctx.moveTo(x * s, sy); ctx.lineTo(x * s + s, sy); }
    else { const sx = x * s + s * (0.35 + rand(x, y, 3) * 0.3); ctx.moveTo(sx, y * s); ctx.lineTo(sx, y * s + s); }
    ctx.stroke();
    // a few specks of grit
    ctx.fillStyle = 'rgba(0,0,0,.18)';
    for (let i = 0; i < 3; i++) {
      const px = x * s + rand(x, y, 10 + i) * s, py = y * s + rand(x, y, 20 + i) * s;
      ctx.fillRect(px, py, Math.max(1, s / 24), Math.max(1, s / 24));
    }
  }

  function drawWall(ctx, x, y, s, cells) {
    ctx.fillStyle = shade(PAL.wall, (rand(x, y, 4) - 0.5) * 10);
    ctx.fillRect(x * s, y * s, s, s);
    const b = Math.max(2, s / 7);
    const open = (dx, dy) => { const ch = (cells[y + dy] || '')[x + dx]; return ch !== undefined && ch !== '#' && ch !== ' '; };
    // a lit top edge where the wall faces open floor, a shadow below it
    ctx.fillStyle = PAL.wallTop;
    if (open(0, 1) || open(1, 0) || open(-1, 0) || open(0, -1)) ctx.fillRect(x * s, y * s, s, b);
    ctx.fillStyle = PAL.wallDark;
    ctx.fillRect(x * s, y * s + s - b * 0.6, s, b * 0.6);
    // brick courses
    ctx.strokeStyle = 'rgba(0,0,0,.35)'; ctx.lineWidth = Math.max(1, s / 32);
    ctx.beginPath();
    const mid = y * s + s / 2;
    ctx.moveTo(x * s, mid); ctx.lineTo(x * s + s, mid);
    const off = (y % 2) ? s / 2 : 0;
    ctx.moveTo(x * s + (off || s / 2), y * s + b); ctx.lineTo(x * s + (off || s / 2), mid);
    ctx.stroke();
  }

  function drawDoor(ctx, x, y, s) {
    drawFloor(ctx, x, y, s);
    const i = s * 0.12;
    ctx.fillStyle = PAL.door;
    ctx.fillRect(x * s + i, y * s + i, s - 2 * i, s - 2 * i);
    ctx.strokeStyle = PAL.doorDark; ctx.lineWidth = Math.max(1, s / 26);
    ctx.beginPath();
    for (let k = 1; k < 4; k++) { const px = x * s + i + (s - 2 * i) * k / 4; ctx.moveTo(px, y * s + i); ctx.lineTo(px, y * s + s - i); }
    ctx.stroke();
    ctx.fillStyle = PAL.iron;
    ctx.fillRect(x * s + i, y * s + s * 0.3, s - 2 * i, Math.max(1, s / 14));
    ctx.fillRect(x * s + i, y * s + s * 0.66, s - 2 * i, Math.max(1, s / 14));
  }

  function drawRough(ctx, x, y, s) {
    ctx.fillStyle = shade(PAL.rough, (rand(x, y, 5) - 0.5) * 14);
    ctx.fillRect(x * s, y * s, s, s);
    ctx.fillStyle = PAL.pebble;
    for (let k = 0; k < 5; k++) {
      const px = x * s + (0.15 + rand(x, y, 30 + k) * 0.7) * s, py = y * s + (0.15 + rand(x, y, 40 + k) * 0.7) * s;
      ctx.beginPath(); ctx.arc(px, py, s * (0.04 + rand(x, y, 50 + k) * 0.05), 0, Math.PI * 2); ctx.fill();
    }
    ctx.strokeStyle = 'rgba(160,190,170,.22)'; ctx.lineWidth = Math.max(1, s / 30);
    ctx.beginPath();
    const wy = y * s + s * (0.3 + rand(x, y, 6) * 0.4);
    ctx.moveTo(x * s + s * 0.15, wy);
    ctx.quadraticCurveTo(x * s + s * 0.5, wy - s * 0.12, x * s + s * 0.85, wy);
    ctx.stroke();
  }

  function drawObstacle(ctx, x, y, s) {
    drawFloor(ctx, x, y, s);
    const i = s * 0.12;
    ctx.fillStyle = 'rgba(0,0,0,.45)';
    ctx.fillRect(x * s + i + s * 0.05, y * s + i + s * 0.06, s - 2 * i, s - 2 * i);
    ctx.fillStyle = PAL.block;
    ctx.fillRect(x * s + i, y * s + i, s - 2 * i, s - 2 * i);
    ctx.fillStyle = PAL.blockTop;
    ctx.fillRect(x * s + i, y * s + i, s - 2 * i, Math.max(2, s / 9));
    ctx.strokeStyle = 'rgba(0,0,0,.4)'; ctx.lineWidth = Math.max(1, s / 26);
    ctx.strokeRect(x * s + i, y * s + i, s - 2 * i, s - 2 * i);
    ctx.beginPath();
    ctx.moveTo(x * s + i, y * s + i); ctx.lineTo(x * s + s - i, y * s + s - i);
    ctx.moveTo(x * s + s - i, y * s + i); ctx.lineTo(x * s + i, y * s + s - i);
    ctx.stroke();
  }

  // Over a background image the tiles turn into translucent tints, so the
  // picture stays visible (as the old page did).
  const TINT = {
    wall: 'rgba(20,14,8,.55)', obstacle: 'rgba(90,60,30,.45)', rough: 'rgba(60,90,120,.30)',
    door: 'rgba(122,84,49,.35)', floor: null, void: 'rgba(8,6,4,.55)',
  };

  function drawTiles(canvas, m, s, dpr) {
    const ctx = canvas.getContext('2d');
    const W = m.grid.w, H = m.grid.h;
    const cells = Array.isArray(m.cells) ? m.cells.map(String) : [];
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W * s, H * s);
    const img = !!m.image;
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const kind = tileKind((cells[y] || '')[x] === undefined ? ' ' : cells[y][x]);
        if (img) {
          if (TINT[kind]) { ctx.fillStyle = TINT[kind]; ctx.fillRect(x * s, y * s, s, s); }
          continue;
        }
        if (kind === 'void') continue;
        if (kind === 'wall') drawWall(ctx, x, y, s, cells);
        else if (kind === 'door') drawDoor(ctx, x, y, s);
        else if (kind === 'rough') drawRough(ctx, x, y, s);
        else if (kind === 'obstacle') drawObstacle(ctx, x, y, s);
        else drawFloor(ctx, x, y, s);
      }
    }
    // faint grid over open squares
    ctx.strokeStyle = 'rgba(241,235,225,.10)';
    ctx.lineWidth = 1 / dpr;
    const half = 0.5 / dpr;
    ctx.beginPath();
    for (let x = 0; x <= W; x++) { ctx.moveTo(x * s + half, 0); ctx.lineTo(x * s + half, H * s); }
    for (let y = 0; y <= H; y++) { ctx.moveTo(0, y * s + half); ctx.lineTo(W * s, y * s + half); }
    ctx.stroke();
  }

  // ── DOM ──────────────────────────────────────────────────────────────────

  const SVGNS = 'http://www.w3.org/2000/svg';

  function tokenId(name) { return 'bm-tok-' + String(name).toLowerCase().replace(/[^a-z0-9]+/g, '-'); }

  function ensureBoard(box) {
    let board = box.querySelector(':scope > .map-board');
    if (!board) {
      box.innerHTML = '';
      board = document.createElement('div');
      board.className = 'map-board';
      board.innerHTML = '<canvas class="map-tiles"></canvas><canvas class="map-fog"></canvas>'
        + '<canvas class="map-fog map-fog-b"></canvas><div class="map-tokens"></div><svg class="map-fx" xmlns="' + SVGNS + '"></svg>';
      box.appendChild(board);
    }
    return board;
  }

  function ringColor(v) {
    if (v.ring === null) return 'var(--accent)';
    if (v.pc) return v.ring > 0.5 ? 'var(--hp)' : v.ring > 0.25 ? '#e0b44a' : 'var(--hp-low)';
    return v.state === 'healthy' ? 'var(--hp)' : v.state === 'bloodied' ? '#e0b44a' : 'var(--hp-low)';
  }

  function updateToken(el, v, s) {
    const px = v.size * s;
    el.className = 'bm-token ' + v.classes.join(' ');
    el.style.width = px + 'px'; el.style.height = px + 'px';
    el.title = v.title;
    const face = el.querySelector('.face');
    const want = v.portrait ? '/map-image/' + encodeURI(v.portrait) : '';
    const img = face.querySelector('img');
    if (want) {
      if (!img || img.getAttribute('src') !== want) {
        face.textContent = '';
        const im = document.createElement('img');
        im.alt = ''; im.src = want;
        // A missing picture falls back to the initials.
        im.onerror = () => { im.remove(); face.textContent = v.initials; };
        face.appendChild(im);
      }
    } else if (img || face.textContent !== v.initials) {
      face.textContent = v.initials;
    }
    face.style.fontSize = Math.max(9, Math.floor(px * 0.34)) + 'px';
    const fill = el.querySelector('.ring .fill');
    const frac = v.ring === null ? 1 : v.ring;
    fill.setAttribute('stroke-dasharray', `${(frac * 100).toFixed(1)} 100`);
    fill.style.stroke = ringColor(v);
    const icons = el.querySelector('.icons');
    const iconHtml = v.icons.map((i) => `<span class="ic" data-cond="${i.name.replace(/[^a-z_-]/g, '')}">${i.glyph}</span>`).join('');
    if (icons.innerHTML !== iconHtml) icons.innerHTML = iconHtml;
    icons.style.fontSize = Math.max(9, Math.floor(s * 0.26)) + 'px';
    const hp = el.querySelector('.hp');
    hp.textContent = v.hpLabel || '';
    hp.hidden = !v.hpLabel;
    hp.style.fontSize = Math.max(10, Math.floor(s * 0.3)) + 'px';
  }

  function newToken(name) {
    const el = document.createElement('div');
    el.id = tokenId(name);
    el.dataset.name = name;
    el.innerHTML = '<svg class="ring" viewBox="0 0 36 36" aria-hidden="true">'
      + '<circle class="track" cx="18" cy="18" r="16"/>'
      + '<circle class="fill" cx="18" cy="18" r="16" pathLength="100" transform="rotate(-90 18 18)"/></svg>'
      + '<div class="face"></div><div class="icons"></div><div class="hp" hidden></div>';
    return el;
  }

  function place(el, x, y, s) { el.style.transform = `translate(${x * s}px, ${y * s}px)`; }

  // Draw `view` into `box`:
  //   view.map      the stats.map payload (grid, cells, image)
  //   view.tokens   [{name, x, y, kind, hp?, hp_state?, conditions?, portrait?, size?}]
  //                 — already filtered by the caller (fog, hidden)
  //   view.players  stats.players, view.current the turn holder, view.mine a name
  //   view.slides   {name: path [[x,y],...]} tokens that slide along a path now
  //   view.fog      {visible:Set, seen:Set} of "x,y" keys, or null (S8)
  // Cheap to call again: tiles are redrawn only when the map or box changes.
  // Returns the fit used, or null when there is nothing to draw.
  function render(box, view) {
    if (!box) return null;
    const m = view && view.map;
    if (!m || !m.grid || !m.grid.w || !m.grid.h) {
      box.innerHTML = ''; delete box.dataset.key; return null;
    }
    const W = m.grid.w, H = m.grid.h;
    const cssW = box.clientWidth, cssH = box.clientHeight;
    if (!cssW || !cssH) return null;
    const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
    const f = fit(W, H, cssW, cssH, dpr);
    const s = f.size;
    const board = ensureBoard(box);
    const key = JSON.stringify([m.grid, m.cells, m.image, s, dpr, cssW, cssH]);
    if (box.dataset.key !== key) {
      box.dataset.key = key;
      board.style.left = f.x + 'px'; board.style.top = f.y + 'px';
      board.style.width = f.width + 'px'; board.style.height = f.height + 'px';
      board.style.setProperty('--sq', s + 'px');
      let bg = board.querySelector('img.map-bg');
      if (m.image) {
        const src = '/map-image/' + encodeURI(m.image);
        if (!bg) { bg = document.createElement('img'); bg.className = 'map-bg'; bg.alt = ''; board.prepend(bg); }
        if (bg.getAttribute('src') !== src) bg.src = src;
      } else if (bg) bg.remove();
      for (const c of board.querySelectorAll('canvas')) { c.width = f.backW; c.height = f.backH; }
      drawTiles(board.querySelector('canvas.map-tiles'), m, s, dpr);
      const svg = board.querySelector('svg.map-fx');
      svg.setAttribute('viewBox', `0 0 ${f.width} ${f.height}`);
      board.dataset.fogKey = '';
    }
    drawFog(board, m, f, view.fog || null);

    const layer = board.querySelector('.map-tokens');
    const slides = view.slides || {};
    const keep = new Set();
    for (const t of Array.isArray(view.tokens) ? view.tokens : []) {
      if (!t || t.name == null) continue;
      const id = tokenId(t.name);
      keep.add(id);
      let el = layer.querySelector('#' + id);
      const fresh = !el;
      if (fresh) { el = newToken(String(t.name)); layer.appendChild(el); }
      updateToken(el, tokenView(t, view.players, view.current, view.mine), s);
      const path = slides[t.name];
      if (!fresh && Array.isArray(path) && path.length > 1 && typeof el.animate === 'function') {
        slide(el, path, s, view.slideMs || 600);
      } else {
        if (el._anim) { try { el._anim.cancel(); } catch (_) {} el._anim = null; }
        place(el, t.x || 0, t.y || 0, s);
      }
    }
    for (const el of [...layer.children]) if (!keep.has(el.id)) el.remove();
    return f;
  }

  // Fog (D9): dark where no PC can see, dim where one saw earlier, clear
  // where one sees now. Two canvases take turns so a change crossfades
  // (400 ms, map.css) instead of popping.
  function drawFog(board, m, f, fogv) {
    const sig = fogv ? [...fogv.visible].sort().join(';') + '|' + [...fogv.seen].sort().join(';') + '|' + f.size : 'none|' + f.size;
    if (board.dataset.fogKey === sig) return;
    // A fresh board (first draw, new map, resize) shows its fog at once: a
    // crossfade from nothing would flash the whole map, monsters' rooms too.
    const instant = !board.dataset.fogKey;
    board.dataset.fogKey = sig;
    const canvases = board.querySelectorAll('canvas.map-fog');
    if (canvases.length < 2) return;
    const front = board.dataset.fogFront === 'b' ? canvases[1] : canvases[0];
    const back = front === canvases[0] ? canvases[1] : canvases[0];
    const ctx = back.getContext('2d');
    const s = f.size, W = m.grid.w, H = m.grid.h;
    ctx.setTransform(f.dpr, 0, 0, f.dpr, 0, 0);
    ctx.clearRect(0, 0, W * s, H * s);
    if (fogv) {
      // A soft edge: each fogged square is drawn a little larger and blurred.
      try { ctx.filter = `blur(${Math.max(1, Math.round(s * 0.12))}px)`; } catch (_) {}
      for (let y = 0; y < H; y++) {
        for (let x = 0; x < W; x++) {
          const k = x + ',' + y;
          if (fogv.visible.has(k)) continue;
          ctx.fillStyle = fogv.seen.has(k) ? 'rgba(6,4,3,.6)' : 'rgb(6,4,3)';
          ctx.fillRect(x * s - 1, y * s - 1, s + 2, s + 2);
        }
      }
      try { ctx.filter = 'none'; } catch (_) {}
    }
    if (instant) { back.style.transition = 'none'; front.style.transition = 'none'; }
    back.style.opacity = '1';
    front.style.opacity = '0';
    if (instant) { void back.offsetWidth; back.style.transition = ''; front.style.transition = ''; }
    board.dataset.fogFront = back === canvases[1] ? 'b' : 'a';
  }

  // Slide a token square by square along `path` (Web Animations), ending at
  // its last square. Never longer than `ms`.
  function slide(el, path, s, ms) {
    if (el._anim) { try { el._anim.cancel(); } catch (_) {} }
    const last = path[path.length - 1];
    place(el, last[0], last[1], s);
    const frames = path.map((p) => ({ transform: `translate(${p[0] * s}px, ${p[1] * s}px)` }));
    el._anim = el.animate(frames, { duration: ms, easing: 'cubic-bezier(.3,.7,.2,1)' });
    el._anim.onfinish = () => { el._anim = null; };
  }

  const api = {
    fit, tileKind, tokenView, hpState, initials, conditionIcon, STEPS,
    drawTiles, drawFog, render, slide, tokenId, ensureBoard,
  };
  root.TV = root.TV || {};
  root.TV.board = Object.assign(root.TV.board || {}, api);
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
