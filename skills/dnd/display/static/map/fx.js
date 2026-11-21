// The battle map's effect timeline (battle-map spec D12, D14, D17).
//
// Each narration block may carry map effects (map_fx: moves, a spell area,
// attacks). They play when that block starts on screen, not when it
// arrives: a token with an unplayed move keeps its on-screen place until
// its block starts; every other change takes the latest stats.map at once.
// A skipped block (history, voice off, rewind, a backgrounded tab) snaps to
// the current state with no animation, and nothing is ever replayed.
//
// Pure: timers and the clock are injected, so node can test it.
(function (root) {
  'use strict';

  const SLIDE_MS = 600;       // a move slides in about 0.6 s (D17: within ~1 s)
  const AREA_MS = 3000;       // a spell area outlines its squares for 3 s (D14)
  const SAFETY_MS = 90000;    // a block that never starts is flushed (no stale map)

  function makeFx(opts) {
    opts = opts || {};
    const now = opts.now || (() => Date.now());
    const setT = opts.setTimeout || ((fn, ms) => setTimeout(fn, ms));
    const clearT = opts.clearTimeout || ((id) => clearTimeout(id));
    const changed = () => { if (opts.onChange) { try { opts.onChange(); } catch (_) {} } };

    let target = null;                 // the latest stats.map
    let pending = [];                  // [{id, fx, timer}] arrived, not yet played
    const done = new Set();            // ids played, skipped or flushed: never again
    let shown = new Map();             // name -> token as last drawn
    let played = new Map();            // name -> {x, y, hp_state?} from a played move,
                                       //   until the next stats.map (it may start first)
    let slides = {};                   // name -> {path, key}
    const slideTimers = new Map();
    let area = null, areaTimer = null;
    let slideKey = 0;

    const nameKey = (n) => String(n);

    function held() {
      const s = new Set();
      for (const p of pending) for (const mv of movesOf(p.fx)) s.add(nameKey(mv.name));
      return s;
    }

    function movesOf(fx) {
      return fx && Array.isArray(fx.moves) ? fx.moves.filter((m) => m && m.name != null) : [];
    }

    // Work out what is drawn now and remember it, so a token that becomes
    // held keeps the place it was drawn at.
    function recompute() {
      const h = held();
      const next = new Map();
      const toks = target && Array.isArray(target.tokens) ? target.tokens : [];
      const inTarget = new Set();
      for (const t of toks) {
        if (!t || t.name == null) continue;
        const n = nameKey(t.name);
        inTarget.add(n);
        if (h.has(n)) {
          if (shown.has(n)) next.set(n, Object.assign({}, t, pick(shown.get(n))));
          continue;            // a token new to the board waits for its move
        }
        const p = played.get(n);
        next.set(n, p ? Object.assign({}, t, p) : t);
      }
      // A token gone from stats.map stays while it still has a move to play.
      for (const [n, t] of shown) if (!inTarget.has(n) && h.has(n)) next.set(n, t);
      shown = next;
    }

    function pick(t) {
      const o = { x: t.x, y: t.y };
      if (t.hp_state !== undefined) o.hp_state = t.hp_state;
      return o;
    }

    function find(id) { return pending.findIndex((p) => p.id === id); }

    function take(id) {
      const i = find(id);
      if (i < 0) return null;
      const p = pending[i];
      pending.splice(i, 1);
      if (p.timer != null) clearT(p.timer);
      done.add(id);
      return p;
    }

    // Put a block's moves in place; `animate` slides them along their paths.
    function applyMoves(fx, animate) {
      for (const mv of movesOf(fx)) {
        const n = nameKey(mv.name);
        if (!Number.isInteger(mv.x) || !Number.isInteger(mv.y)) continue;
        const o = { x: mv.x, y: mv.y };
        if (mv.hp_state) o.hp_state = mv.hp_state;
        played.set(n, o);
        const base = shown.get(n) || (target && (target.tokens || []).find((t) => t && nameKey(t.name) === n)) || { name: mv.name };
        shown.set(n, Object.assign({}, base, o));
        if (animate && Array.isArray(mv.path) && mv.path.length > 1) {
          slides[n] = { path: mv.path.map((p) => [p[0], p[1]]), key: ++slideKey };
          if (slideTimers.has(n)) clearT(slideTimers.get(n));
          slideTimers.set(n, setT(() => { delete slides[n]; slideTimers.delete(n); changed(); }, SLIDE_MS));
        } else {
          delete slides[n];
        }
      }
    }

    function showArea(a) {
      if (!a || !Array.isArray(a.squares) || !a.squares.length) return;
      if (areaTimer != null) clearT(areaTimer);
      area = { squares: a.squares, label: a.label || null, shape: a.shape || null, key: ++slideKey };
      areaTimer = setT(() => { area = null; areaTimer = null; changed(); }, AREA_MS);
    }

    const api = {
      // A live block was queued with its effects (or none).
      arrive(id, fx) {
        if (done.has(id) || find(id) >= 0) return;
        if (!fx || typeof fx !== 'object') return;      // nothing to hold or play
        const timer = setT(() => api.skip(id), SAFETY_MS);
        pending.push({ id, fx, at: now(), timer });
        recompute();
      },
      // The block's paced reveal began: play its effects now.
      start(id) {
        // Blocks start in order, so any earlier block still waiting was
        // skipped; snap it first.
        for (const p of pending.slice()) {
          if (typeof p.id === 'number' && typeof id === 'number' && p.id < id) {
            const q = take(p.id);
            if (q) applyMoves(q.fx, false);
          }
        }
        const p = take(id);
        if (!p) return;
        applyMoves(p.fx, true);
        showArea(p.fx.area);
        // Attacks (milestone 2) are played by the sprite layer.
        if (api.onAttacks && Array.isArray(p.fx.attacks) && p.fx.attacks.length) {
          try { api.onAttacks(p.fx.attacks); } catch (_) {}
        }
        recompute();
        changed();
      },
      // The block is history or was skipped: snap, no animation.
      skip(id) {
        const p = take(id);
        if (!p) return;
        applyMoves(p.fx, false);
        recompute();
        changed();
      },
      // The latest stats.map. It is the truth for every token not waiting
      // on a move.
      setTarget(map) {
        target = map && typeof map === 'object' ? map : null;
        played = new Map();
        recompute();
      },
      // Snap everything to the current state: no slides, no area, nothing
      // left to play (a backgrounded tab returns, rewind, clear, reconnect).
      flushAll() {
        for (const p of pending) { if (p.timer != null) clearT(p.timer); done.add(p.id); }
        pending = [];
        for (const t of slideTimers.values()) clearT(t);
        slideTimers.clear();
        slides = {};
        if (areaTimer != null) clearT(areaTimer);
        area = null; areaTimer = null;
        recompute();
        changed();
      },
      // A last-resort sweep: any block waiting longer than the safety time.
      tick() {
        const t = now();
        for (const p of pending.slice()) if (t - p.at >= SAFETY_MS) api.skip(p.id);
      },
      shownState() {
        return { tokens: [...shown.values()], slides: Object.assign({}, slides), area };
      },
      pendingCount() { return pending.length; },
      onAttacks: null,
    };
    return api;
  }

  const out = { makeFx, SLIDE_MS, AREA_MS, SAFETY_MS };
  root.TV = root.TV || {};
  root.TV.fx = out;
  if (typeof module !== 'undefined') module.exports = out;
})(typeof window !== 'undefined' ? window : globalThis);
