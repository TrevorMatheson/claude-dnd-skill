// The mood layer's recipes (scene backgrounds spec D1, D2, AC2).
//
// Each scene type the server picks (SCENES in dnd-display-app.py) gets a
// recipe of 2-4 animated layers, drawn by bg.js: the existing particle kinds
// plus four new ones —
//   fogbank    wide, slow banks of fog low in the frame          (soft)
//   shafts     pale beams of light falling from above            (soft)
//   flicker    a warm glow that wavers like firelight            (soft)
//   glowmotes  slow, softly pulsing motes of light               (particle)
// Soft layers are drawn on a half-resolution canvas; particles stay sharp.
//
// Pure and DOM-free so node can test it: MOODS, recipeFor(scene),
// makeMixer(fadeMs) for the crossfade, budget(recipe, w, h) and PARTICLE_CAP.
(function (root) {
  'use strict';

  const PARTICLE_KINDS = ['embers', 'fireflies', 'smoke', 'dust', 'snow', 'stars', 'bubbles',
    'sparks', 'sand', 'rain', 'drips', 'mist', 'leaves', 'ripples', 'glowmotes'];
  const SOFT_KINDS = ['fogbank', 'shafts', 'flicker'];

  // Soft layers: `count` is the number of banks/beams (flicker has none);
  // `alpha` their strength; flicker's `x`/`y` place the light (0-1).
  const MOODS = {
    tavern: { layers: [
      { kind: 'flicker', color: '#ff8a3a', x: 0.5, y: 0.95, alpha: 0.16 },
      { kind: 'shafts', count: 2, color: '#ffcf8a', alpha: 0.045 },
      { kind: 'embers', count: 45, color: '#ff8a3a' },
      { kind: 'dust', count: 60, color: '#d8b07a' },
    ] },
    dungeon: { layers: [
      { kind: 'flicker', color: '#d9752e', x: 0.08, y: 0.4, alpha: 0.12 },
      { kind: 'dust', count: 70, color: '#8a80a8' },
      { kind: 'drips', count: 18, color: '#6a7aa0' },
    ] },
    mine: { layers: [
      { kind: 'smoke', count: 12, color: '#3a3430' },
      { kind: 'dust', count: 90, color: '#a08060' },
      { kind: 'glowmotes', count: 6, color: '#ffb050' },
    ] },
    cave: { layers: [
      { kind: 'fogbank', count: 3, color: '#4a6a8a', alpha: 0.10 },
      { kind: 'mist', count: 8, color: '#7888a0' },
      { kind: 'drips', count: 30, color: '#6090b0' },
    ] },
    forest: { layers: [
      { kind: 'shafts', count: 3, color: '#d8f0a0', alpha: 0.05 },
      { kind: 'leaves', count: 40 },
      { kind: 'fireflies', count: 20, color: '#c0ff70' },
    ] },
    castle: { layers: [
      { kind: 'shafts', count: 2, color: '#c8c8f0', alpha: 0.05 },
      { kind: 'flicker', color: '#e08a40', x: 0.92, y: 0.5, alpha: 0.10 },
      { kind: 'dust', count: 70, color: '#a0a0c8' },
    ] },
    mountain: { layers: [
      { kind: 'fogbank', count: 2, color: '#a0b8d8', alpha: 0.10 },
      { kind: 'snow', count: 110, color: '#e0ecff' },
    ] },
    ocean: { layers: [
      { kind: 'mist', count: 8, color: '#6080a0' },
      { kind: 'ripples', count: 18, color: '#4888c0' },
      { kind: 'bubbles', count: 20, color: '#60a0d0' },
    ] },
    desert: { layers: [
      { kind: 'shafts', count: 3, color: '#ffe0a0', alpha: 0.06 },
      { kind: 'sand', count: 90, color: '#c8a060' },
    ] },
    ruins: { layers: [
      { kind: 'fogbank', count: 2, color: '#8a8060', alpha: 0.08 },
      { kind: 'dust', count: 60, color: '#a09060' },
      { kind: 'leaves', count: 14, color: '#8a7a40' },
    ] },
    swamp: { layers: [
      { kind: 'fogbank', count: 4, color: '#5a7040', alpha: 0.11 },
      { kind: 'mist', count: 10, color: '#6a8050' },
      { kind: 'fireflies', count: 22, color: '#b0e060' },
      { kind: 'bubbles', count: 10, color: '#708050' },
    ] },
    crypt: { layers: [
      { kind: 'fogbank', count: 3, color: '#4a3a5a', alpha: 0.12 },
      { kind: 'drips', count: 16, color: '#6a7090' },
      { kind: 'glowmotes', count: 10, color: '#80e0b0' },
    ] },
    fire: { layers: [
      { kind: 'flicker', color: '#ff5010', x: 0.5, y: 1.0, alpha: 0.22 },
      { kind: 'smoke', count: 18, color: '#403028' },
      { kind: 'embers', count: 80, color: '#ff6a20' },
      { kind: 'sparks', count: 30, color: '#ffe060' },
    ] },
    arcane: { layers: [
      { kind: 'shafts', count: 2, color: '#b090ff', alpha: 0.05 },
      { kind: 'glowmotes', count: 24, color: '#a080ff' },
      { kind: 'sparks', count: 30, color: '#d0b0ff' },
    ] },
    city: { layers: [
      { kind: 'mist', count: 8, color: '#607088' },
      { kind: 'rain', count: 120, color: '#8090b0' },
      { kind: 'glowmotes', count: 8, color: '#ffc070' },
    ] },
    night: { layers: [
      { kind: 'fogbank', count: 2, color: '#203050', alpha: 0.09 },
      { kind: 'stars', count: 150, color: '#c0d0ff' },
    ] },
    temple: { layers: [
      { kind: 'shafts', count: 3, color: '#ffe6b0', alpha: 0.06 },
      { kind: 'smoke', count: 10, color: '#5a5048' },
      { kind: 'dust', count: 50, color: '#d0b880' },
      { kind: 'glowmotes', count: 8, color: '#ffd080' },
    ] },
  };

  const FALLBACK_KIND = 'dust';

  // The recipe for a scene the server sent. A scene type this file doesn't
  // know (a campaign's own) uses the server's particle kind over a fog bank.
  function recipeFor(scene) {
    const name = scene && typeof scene.name === 'string' ? scene.name : '';
    if (name && Object.prototype.hasOwnProperty.call(MOODS, name)) return MOODS[name];
    const kind = scene && PARTICLE_KINDS.includes(scene.particles) ? scene.particles : FALLBACK_KIND;
    const accent = scene && typeof scene.accent === 'string' ? scene.accent : undefined;
    const layer = { kind, count: 60 };
    if (accent && kind === 'embers') layer.color = accent;
    return { layers: [{ kind: 'fogbank', count: 2, color: '#506070', alpha: 0.08 }, layer] };
  }

  // The crossfade between moods: weights(now) -> [{name, w}], at most two.
  // Setting the mood already showing changes nothing (the server repeats the
  // scene with every chunk). A switch during a fade drops the oldest, and the
  // one it was fading from leaves from where it had got to.
  function makeMixer(fadeMs) {
    const fade = Number(fadeMs) > 0 ? Number(fadeMs) : 2500;
    let entries = [];   // [{name, start, base}] — last is the newest

    function progress(now, start) {
      return Math.max(0, Math.min(1, (now - start) / fade));
    }
    function settle(now) {
      if (entries.length === 2 && progress(now, entries[1].start) >= 1) entries = [entries[1]];
    }
    // Reading the weights changes nothing, so any time may be asked about.
    function weights(now) {
      if (!entries.length) return [];
      if (entries.length === 1) return [{ name: entries[0].name, w: 1 }];
      const p = progress(now, entries[1].start);
      if (p >= 1) return [{ name: entries[1].name, w: 1 }];
      return [{ name: entries[0].name, w: entries[0].base * (1 - p) }, { name: entries[1].name, w: p }];
    }
    function set(name, now) {
      settle(now);
      const newest = entries[entries.length - 1];
      if (newest && newest.name === name) return;
      if (!newest) { entries = [{ name, start: now - fade, base: 1 }]; return; }
      // The one now leaving keeps the weight it had reached.
      const ws = weights(now);
      const cur = ws.find(e => e.name === newest.name);
      entries = [{ name: newest.name, start: newest.start, base: cur ? cur.w : 1 }, { name, start: now, base: 1 }];
    }
    return { set, weights };
  }

  // Particle counts for a w x h screen (CSS px), capped so a 4K TV with a
  // device pixel ratio of 1 doesn't draw four times as many.
  const PARTICLE_CAP = 360;
  const BASE_AREA = 1920 * 1080;
  function budget(recipe, w, h) {
    const layers = (recipe && Array.isArray(recipe.layers)) ? recipe.layers : [];
    const area = Math.max(1, (Number(w) || 1920) * (Number(h) || 1080));
    let scale = Math.min(1.5, Math.max(0.5, Math.sqrt(area / BASE_AREA)));
    const counted = l => !SOFT_KINDS.includes(l.kind) || l.count != null;
    const raw = layers.reduce((s, l) => s + (counted(l) ? (Number(l.count) || 0) : 0), 0);
    if (raw * scale > PARTICLE_CAP) scale = PARTICLE_CAP / raw;
    const out = layers.map(l => {
      const c = Object.assign({}, l);
      if (SOFT_KINDS.includes(l.kind)) return c;          // a few banks/beams: not scaled
      c.count = Math.max(1, Math.floor((Number(l.count) || 0) * scale));
      return c;
    });
    // Rounding up to 1 can't push a big recipe past the cap, but check anyway.
    let total = out.reduce((s, l) => s + (Number(l.count) || 0), 0);
    for (let i = out.length - 1; total > PARTICLE_CAP && i >= 0; i--) {
      if (SOFT_KINDS.includes(out[i].kind) || !out[i].count) continue;
      const cut = Math.min(out[i].count - 1, total - PARTICLE_CAP);
      out[i].count -= cut; total -= cut;
    }
    return Object.assign({}, recipe, { layers: out });
  }

  const api = { MOODS, recipeFor, makeMixer, budget, PARTICLE_CAP, PARTICLE_KINDS, SOFT_KINDS };
  root.TV = root.TV || {};
  root.TV.mood = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
