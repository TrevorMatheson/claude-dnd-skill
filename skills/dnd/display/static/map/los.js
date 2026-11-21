// Line of sight and fog for the battle map (battle-map spec D9, D11, D18).
//
// Squares no PC can see are dark, squares seen earlier are dim (the room's
// shape, never its monsters), squares a PC can see now are fully shown, and a
// monster is drawn only when one of its squares is visible. Everything is
// lit (D11); only walls (#) and the outside (space) block sight, using the
// layout's own walls. Claude's manual overrides (fog.show / fog.hide) win.
//
// All pure, so node can test it. Squares are "x,y" strings in Sets.
(function (root) {
  'use strict';

  const BLOCKS = (ch) => ch === '#' || ch === ' ' || ch === undefined;
  const key = (x, y) => x + ',' + y;

  // Memo: one result per origin for each cells array (a new map, a new array).
  let memoCells = null, memo = new Map();

  function cellAt(cells, x, y) {
    const row = cells[y];
    if (row === undefined || x < 0) return undefined;
    const ch = row[x];
    return ch === undefined ? undefined : ch;
  }

  // Walk the squares a centre-to-centre ray from (x0,y0) to (x1,y1) passes
  // through (Amanatides & Woo). True when no blocker stands strictly between
  // them. A ray through the exact corner of two diagonal blockers is stopped,
  // as movement is (battlemap.py: no squeezing between two wall squares).
  function clear(cells, x0, y0, x1, y1) {
    const dx = x1 - x0, dy = y1 - y0;
    const sx = Math.sign(dx), sy = Math.sign(dy);
    const adx = Math.abs(dx), ady = Math.abs(dy);
    // Parametric distance to the next vertical / horizontal grid line,
    // in units of the whole ray (0..1), from the square's centre.
    const tdx = adx ? 1 / adx : Infinity, tdy = ady ? 1 / ady : Infinity;
    let tmx = adx ? 0.5 / adx : Infinity, tmy = ady ? 0.5 / ady : Infinity;
    let x = x0, y = y0;
    const EPS = 1e-9;
    while (true) {
      if (Math.abs(tmx - tmy) < EPS) {
        // Through a corner: both side squares blocking stops the ray.
        if (BLOCKS(cellAt(cells, x + sx, y)) && BLOCKS(cellAt(cells, x, y + sy))) return false;
        x += sx; y += sy; tmx += tdx; tmy += tdy;
      } else if (tmx < tmy) {
        x += sx; tmx += tdx;
      } else {
        y += sy; tmy += tdy;
      }
      if (x === x1 && y === y1) return true;
      if (BLOCKS(cellAt(cells, x, y))) return false;
    }
  }

  // Every square visible from (x, y): open squares with a clear ray, and the
  // first blocker a ray meets (so a room's walls show).
  function visibleFrom(cells, x, y) {
    if (!Array.isArray(cells)) return new Set();
    if (memoCells !== cells) { memoCells = cells; memo = new Map(); }
    const k = key(x, y);
    if (memo.has(k)) return memo.get(k);
    const out = new Set();
    const H = cells.length;
    let W = 0;
    for (const r of cells) W = Math.max(W, String(r).length);
    if (y >= 0 && y < H && x >= 0 && x < W) {
      out.add(k);
      for (let ty = 0; ty < H; ty++) {
        for (let tx = 0; tx < W; tx++) {
          if (tx === x && ty === y) continue;
          if (clear(cells, x, y, tx, ty)) out.add(key(tx, ty));
        }
      }
    }
    memo.set(k, out);
    return out;
  }

  function pairs(list) {
    const out = [];
    for (const p of Array.isArray(list) ? list : []) {
      if (Array.isArray(p) && p.length === 2 && Number.isInteger(p[0]) && Number.isInteger(p[1])) out.push(p);
    }
    return out;
  }

  // D18: the dim squares after a load, rebuilt from where the PCs have been.
  function seenFromTrail(map) {
    const seen = new Set();
    const cells = map && map.cells;
    const trail = pairs(map && map.fog && map.fog.trail);
    for (const [x, y] of trail) for (const k of visibleFrom(cells, x, y)) seen.add(k);
    return seen;
  }

  function squaresOf(t) {
    const n = Math.max(1, Math.min(4, (t && t.size) | 0 || 1));
    const out = [];
    for (let dy = 0; dy < n; dy++) for (let dx = 0; dx < n; dx++) out.push(key((t.x | 0) + dx, (t.y | 0) + dy));
    return out;
  }

  // What the table sees now: {visible, seen}. `tokens` are the map's tokens
  // (PCs are the eyes); `seen` is the previous dim set, or null on a load,
  // when it is rebuilt from fog.trail. fog.show adds squares, fog.hide
  // darkens them (dark, not dim).
  function fog(map, tokens, seen) {
    const cells = map && map.cells;
    const visible = new Set();
    for (const t of Array.isArray(tokens) ? tokens : []) {
      if (!t || t.kind !== 'pc') continue;
      for (const sq of squaresOf(t)) {
        const [x, y] = sq.split(',').map(Number);
        for (const k of visibleFrom(cells, x, y)) visible.add(k);
      }
    }
    const f = (map && map.fog) || {};
    for (const [x, y] of pairs(f.show)) visible.add(key(x, y));
    const hidden = new Set(pairs(f.hide).map(([x, y]) => key(x, y)));
    for (const k of hidden) visible.delete(k);
    const out = new Set(seen instanceof Set ? seen : (seen ? seen : seenFromTrail(map)));
    for (const k of visible) out.add(k);
    for (const k of hidden) out.delete(k);
    return { visible, seen: out };
  }

  // PCs always show; anyone else only when one of their squares is visible.
  function tokenShown(t, fogResult) {
    if (!t) return false;
    if (t.kind === 'pc') return true;
    const vis = fogResult && fogResult.visible;
    if (!vis) return true;
    return squaresOf(t).some((k) => vis.has(k));
  }

  const api = { visibleFrom, fog, tokenShown, seenFromTrail, squaresOf, key };
  root.TV = root.TV || {};
  root.TV.los = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
