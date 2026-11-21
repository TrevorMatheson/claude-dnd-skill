// The battle map on the TV: the adapter between the page and the shared
// board (static/map/board.js). It picks the container (the combat column or
// a Tactical side panel), works out what the table may see, and hands the
// board the tokens to draw.
//
// canvasSize() is pure so node can test it (foundation S8); draw() needs a DOM.
(function (root) {
  'use strict';

  // The cell size that fits a cols×rows grid into a cssW×cssH box (never
  // under 8 px), and the canvas backing size at that pixel ratio.
  function canvasSize(cssW, cssH, cols, rows, dpr) {
    const r = typeof dpr === 'number' && isFinite(dpr) && dpr > 0 ? dpr : 1;
    const cell = Math.max(8, Math.floor(Math.min(cssW / cols, cssH / rows)));
    return { width: Math.round(cols * cell * r), height: Math.round(rows * cell * r), cell };
  }

  function board() { return root.TV && root.TV.board; }

  // The square a sliding token has reached `frac` of the way along `path`.
  function alongPath(path, frac) {
    if (!Array.isArray(path) || !path.length) return null;
    const f = Math.max(0, Math.min(1, Number(frac) || 0));
    const p = path[Math.min(path.length - 1, Math.floor(f * (path.length - 1) + 1e-9))];
    return [p[0], p[1]];
  }

  // When each slide began (by its key), so fog can follow a sliding PC
  // square by square instead of jumping to where it ends.
  const slideStarts = new Map();
  const SLIDE_MS = 600;
  let fogTimer = null;
  function los() { return root.TV && root.TV.los; }

  // Per box: the layout it shows (a stable cells array, so line of sight is
  // memoised across payloads), the dim "seen earlier" squares, and how much
  // of fog.trail they already include.
  const boxes = new WeakMap();

  // What the table may see of `m` with tokens at `tokens` (D9): the fog and
  // the tokens to draw. `seen` is rebuilt from fog.trail when a map loads
  // (first draw, reconnect, a new layout: D18), then grows with what the PCs
  // see; `absorbTrail` adds squares seen from trail squares that arrived
  // since (false while a move is still to play, so dim squares never run
  // ahead of the story).
  function sight(box, m, tokens, absorbTrail) {
    const L = los();
    if (!L) return { fog: null, tokens };
    const ck = JSON.stringify([m.grid, m.cells]);
    let st = boxes.get(box);
    if (!st || st.ck !== ck) { st = { ck, cells: m.cells, seen: null, trailN: 0 }; boxes.set(box, st); }
    const mm = Object.assign({}, m, { cells: st.cells });
    const trail = (m.fog && Array.isArray(m.fog.trail)) ? m.fog.trail : [];
    if (st.seen && absorbTrail && trail.length > st.trailN) {
      for (const p of trail.slice(st.trailN)) {
        if (Array.isArray(p) && p.length === 2) for (const k of L.visibleFrom(st.cells, p[0], p[1])) st.seen.add(k);
      }
    }
    if (!st.seen || absorbTrail) st.trailN = trail.length;
    const fog = L.fog(mm, tokens, st.seen);
    st.seen = fog.seen;
    return { fog, tokens: tokens.filter((t) => L.tokenShown(t, fog)) };
  }

  // The effect timeline (static/map/fx.js): moves and spell areas play when
  // their narration block starts (D12, D14), skipped blocks snap (D17).
  let fx = null, lastMap, onChange = null, drawnNames = null;
  function timeline() {
    if (!fx && root.TV && root.TV.fx) {
      fx = root.TV.fx.makeFx({
        onChange: () => { if (onChange) { try { onChange(); } catch (_) {} } },
        // An attack's unseen side doesn't play (fog, D9 + D15): what the
        // board drew last is what the table can see.
        isVisible: (name) => !drawnNames || drawnNames.has(String(name)),
      });
    }
    return fx;
  }
  function call(name, a, b) {
    const t = timeline();
    if (t) { try { t[name](a, b); } catch (err) { console.error('tv: map ' + name + ' failed', err); } }
  }
  // Called by the feed (fire-and-forget; feed.js wraps each call too).
  function blockArrived(seq, mapFx) { call('arrive', seq, mapFx); }
  function blockStarted(seq) { call('start', seq); }
  function blockSkipped(seq) { call('skip', seq); }
  // A backgrounded tab returns, rewind, clear or a reconnect: jump to now.
  function flushAll() { call('flushAll'); }
  function setOnChange(cb) { onChange = cb; }

  // Draw stats.map into `box`. Cheap to call again: tiles are redrawn only
  // when the map or the box size changes.
  function draw(box, stats) {
    if (!box) return;
    const m = stats && stats.map;
    const B = board();
    if (!m || !m.grid || !m.grid.w || !m.grid.h || !B) { box.innerHTML = ''; delete box.dataset.key; return; }
    if (box.dataset.aspect === 'grid') box.style.aspectRatio = `${m.grid.w} / ${m.grid.h}`;
    const to = (stats && stats.turn_order) || {};
    let tokens = Array.isArray(m.tokens) ? m.tokens : [], slides = {}, area = null, attacks = [], settled = true;
    const t = timeline();
    if (t) {
      if (m !== lastMap) { lastMap = m; t.setTarget(m); }
      const st = t.shownState();
      tokens = st.tokens; slides = st.slides || {}; area = st.area || null; attacks = st.attacks || [];
      settled = !t.pendingCount();
    }
    // Fog follows a sliding PC along its path (D9 + D12): the eyes are
    // where the token has got to, and the map redraws as it goes.
    const nowMs = (typeof performance !== 'undefined' ? performance.now() : Date.now());
    let sliding = false;
    const eyes = tokens.map((tok) => {
      const sl = slides[tok.name];
      if (!sl || tok.kind !== 'pc') return tok;
      const k = sl.key != null ? sl.key : JSON.stringify(sl.path || sl);
      if (!slideStarts.has(k)) slideStarts.set(k, nowMs);
      const frac = (nowMs - slideStarts.get(k)) / SLIDE_MS;
      if (frac < 1) sliding = true;
      const sq = alongPath(sl.path || sl, frac);
      return sq ? Object.assign({}, tok, { x: sq[0], y: sq[1] }) : tok;
    });
    if (slideStarts.size > 50) slideStarts.clear();
    if (sliding && !fogTimer && onChange) {
      fogTimer = setTimeout(() => { fogTimer = null; if (onChange) onChange(); }, 90);
    }
    const seenAt = sight(box, m, eyes, settled);
    const shownNames = new Set(seenAt.tokens.map((x) => String(x.name)));
    const seen = { fog: seenAt.fog, tokens: tokens.filter((x) => shownNames.has(String(x.name))) };
    drawnNames = new Set(seen.tokens.map((x) => String(x.name)));
    B.render(box, {
      map: m,
      tokens: seen.tokens,
      fog: seen.fog,
      slides,
      area,
      attacks,
      players: Array.isArray(stats.players) ? stats.players : [],
      current: to.current || null,
      mine: null,
    });
  }

  const api = { canvasSize, alongPath, draw, sight, blockArrived, blockStarted, blockSkipped, flushAll, setOnChange };
  root.TV = root.TV || {};
  root.TV.map = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
