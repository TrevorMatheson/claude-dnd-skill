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

  // The layout as a blocker grid (1 = wall or outside), built once per cells.
  let gridCells = null, grid = null;
  function blockGrid(cells) {
    if (gridCells === cells && grid) return grid;
    const H = cells.length;
    let W = 0;
    for (const r of cells) W = Math.max(W, String(r).length);
    const b = new Uint8Array(W * H), door = new Uint8Array(W * H);
    for (let y = 0; y < H; y++) {
      const row = String(cells[y]);
      for (let x = 0; x < W; x++) { b[y * W + x] = BLOCKS(row[x]) ? 1 : 0; door[y * W + x] = row[x] === 'D' ? 1 : 0; }
    }
    gridCells = cells; grid = { W, H, b, door };
    return grid;
  }
  function blk(g, x, y) { return x < 0 || y < 0 || x >= g.W || y >= g.H ? 1 : g.b[y * g.W + x]; }

  // Walk the squares a ray from point (ax,ay) to point (bx,by) passes through
  // (Amanatides & Woo), starting in the square holding (ax,ay). True when it
  // reaches square (x1,y1) with no blocker on the way. A ray through the
  // exact corner of two diagonal blockers is stopped, as movement is
  // (battlemap.py: no squeezing between two wall squares).
  function clear(g, ax, ay, bx, by, x1, y1) {
    const dx = bx - ax, dy = by - ay;
    const sx = dx > 0 ? 1 : dx < 0 ? -1 : 0, sy = dy > 0 ? 1 : dy < 0 ? -1 : 0;
    let x = Math.floor(ax), y = Math.floor(ay);
    const tdx = dx ? Math.abs(1 / dx) : Infinity, tdy = dy ? Math.abs(1 / dy) : Infinity;
    let tmx = dx ? ((sx > 0 ? x + 1 - ax : ax - x) * tdx) : Infinity;
    let tmy = dy ? ((sy > 0 ? y + 1 - ay : ay - y) * tdy) : Infinity;
    const EPS = 1e-9;
    for (let guard = 0; guard < 400; guard++) {
      if (x === x1 && y === y1) return true;
      if ((tmx < tmy ? tmx : tmy) > 1 + EPS) return false;     // ends short of the target
      const d = tmx - tmy;
      if (d < EPS && d > -EPS) {
        if (blk(g, x + sx, y) && blk(g, x, y + sy)) return false;
        x += sx; y += sy; tmx += tdx; tmy += tdy;
      } else if (d < 0) {
        x += sx; tmx += tdx;
      } else {
        y += sy; tmy += tdy;
      }
      if (x === x1 && y === y1) return true;
      if (blk(g, x, y)) return false;
    }
    return false;
  }

  // Points a PC can look from inside its square: the centre, then near each
  // corner (a PC in a doorway leans to see into the room). Lines end at the
  // target square's centre, as the plan's centre-to-centre rule has it.
  const SAMPLES = [[0.5, 0.5], [0.12, 0.12], [0.88, 0.12], [0.12, 0.88], [0.88, 0.88]];
  const NB8 = [[-1, -1], [0, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [0, 1], [1, 1]];

  // Every square visible from (x, y): squares with a clear line from some
  // point of the PC's square to their centre, and the first blocker such a
  // line meets (so a room's walls show). Centre-to-centre rays find most of
  // it at once; the rest is grown from the edge of what is visible, so
  // far-off dark squares never cost the slower test.
  function visibleFrom(cells, x, y) {
    if (!Array.isArray(cells)) return new Set();
    if (memoCells !== cells) { memoCells = cells; memo = new Map(); }
    const k = key(x, y);
    if (memo.has(k)) return memo.get(k);
    const g = blockGrid(cells);
    const { W, H } = g;
    const out = new Set();
    if (y >= 0 && y < H && x >= 0 && x < W) {
      const vis = new Uint8Array(W * H);      // 1 visible, 2 tried and not
      vis[y * W + x] = 1;
      const cx0 = x + 0.5, cy0 = y + 0.5;
      const queue = [];
      for (let ty = 0; ty < H; ty++) {
        for (let tx = 0; tx < W; tx++) {
          const i = ty * W + tx;
          if (i === y * W + x) { queue.push(i); continue; }
          if (clear(g, cx0, cy0, tx + 0.5, ty + 0.5, tx, ty)) { vis[i] = 1; queue.push(i); }
        }
      }
      while (queue.length) {
        const i = queue.pop();
        const qx = i % W, qy = (i - qx) / W;
        if (g.b[i] && i !== y * W + x) continue;          // look past open squares only
        for (const [dx, dy] of NB8) {
          const nx = qx + dx, ny = qy + dy;
          if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
          const ni = ny * W + nx;
          if (vis[ni]) continue;
          vis[ni] = 2;
          for (let s = 1; s < SAMPLES.length; s++) {
            if (clear(g, x + SAMPLES[s][0], y + SAMPLES[s][1], nx + 0.5, ny + 0.5, nx, ny)) {
              vis[ni] = 1; queue.push(ni); break;
            }
          }
        }
      }
      // A wall, a doorway or the map's edge next to a visible open square
      // shows too, so a lit room shows all its walls, doors and corners.
      for (let i = 0; i < W * H; i++) {
        if (vis[i] !== 1 || g.b[i]) continue;
        const qx = i % W, qy = (i - qx) / W;
        for (const [dx, dy] of NB8) {
          const nx = qx + dx, ny = qy + dy;
          if (nx >= 0 && ny >= 0 && nx < W && ny < H && (g.b[ny * W + nx] || g.door[ny * W + nx])) vis[ny * W + nx] = 1;
        }
      }
      for (let i = 0; i < W * H; i++) if (vis[i] === 1) out.add((i % W) + ',' + ((i - (i % W)) / W));
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
