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
  let fx = null, lastMap, onChange = null;
  function timeline() {
    if (!fx && root.TV && root.TV.fx) {
      fx = root.TV.fx.makeFx({ onChange: () => { if (onChange) { try { onChange(); } catch (_) {} } } });
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
    let tokens = Array.isArray(m.tokens) ? m.tokens : [], slides = {}, area = null, settled = true;
    const t = timeline();
    if (t) {
      if (m !== lastMap) { lastMap = m; t.setTarget(m); }
      const st = t.shownState();
      tokens = st.tokens; slides = st.slides || {}; area = st.area || null;
      settled = !t.pendingCount();
    }
    const seen = sight(box, m, tokens, settled);
    B.render(box, {
      map: m,
      tokens: seen.tokens,
      fog: seen.fog,
      slides,
      area,
      players: Array.isArray(stats.players) ? stats.players : [],
      current: to.current || null,
      mine: null,
    });
  }

  const api = { canvasSize, draw, sight, blockArrived, blockStarted, blockSkipped, flushAll, setOnChange };
  root.TV = root.TV || {};
  root.TV.map = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
