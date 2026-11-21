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

  // Draw stats.map into `box`. Cheap to call again: tiles are redrawn only
  // when the map or the box size changes.
  function draw(box, stats) {
    if (!box) return;
    const m = stats && stats.map;
    const B = board();
    if (!m || !m.grid || !m.grid.w || !m.grid.h || !B) { box.innerHTML = ''; delete box.dataset.key; return; }
    if (box.dataset.aspect === 'grid') box.style.aspectRatio = `${m.grid.w} / ${m.grid.h}`;
    const to = (stats && stats.turn_order) || {};
    B.render(box, {
      map: m,
      tokens: Array.isArray(m.tokens) ? m.tokens : [],
      players: Array.isArray(stats.players) ? stats.players : [],
      current: to.current || null,
      mine: null,
    });
  }

  const api = { canvasSize, draw };
  root.TV = root.TV || {};
  root.TV.map = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
