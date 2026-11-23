// The place picture under the mood layer (scene backgrounds spec D1, D5,
// D7, D8, AC6): image geometry, the contrast maths behind the automatic
// dimming, and the model that decides which picture is up.
//
// Pure and DOM-free so node can test it; bg.js drives the page with it.
//   coverRect(iw, ih, vw, vh)       object-fit: cover, centred
//   relLum(r, g, b)                 WCAG relative luminance (channels 0-255)
//   contrastRatio(l1, l2)           WCAG contrast ratio, either order
//   percentile(arr, p)              p on 0-100; arr is left alone
//   scrimFor(lums, opts)            the black scrim alpha that brings the
//                                   95th-percentile sample to the target
//   makePlaceModel()                set(place) / loaded(url) / failed(url)
(function (root) {
  'use strict';

  function coverRect(iw, ih, vw, vh) {
    const s = Math.max(vw / iw, vh / ih);
    const w = iw * s, h = ih * s;
    return { x: (vw - w) / 2, y: (vh - h) / 2, w, h };
  }

  // sRGB channel (0-1) <-> linear light.
  function toLinear(v) { return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }
  function toSrgb(l) { return l <= 0.0031308 ? l * 12.92 : 1.055 * Math.pow(l, 1 / 2.4) - 0.055; }

  function relLum(r, g, b) {
    return 0.2126 * toLinear(r / 255) + 0.7152 * toLinear(g / 255) + 0.0722 * toLinear(b / 255);
  }

  function contrastRatio(l1, l2) {
    const hi = Math.max(l1, l2), lo = Math.min(l1, l2);
    return (hi + 0.05) / (lo + 0.05);
  }

  function percentile(arr, p) {
    if (!arr || !arr.length) return 0;
    const s = Array.from(arr).sort((a, b) => a - b);
    const q = Math.max(0, Math.min(100, Number(p) || 0)) / 100;
    return s[Math.min(s.length - 1, Math.max(0, Math.ceil(q * s.length) - 1))];
  }

  // A black scrim at alpha a scales each sRGB channel by (1 - a), as the
  // browser composites it. Solve for the a that brings the 95th-percentile
  // background luminance down to the brightest one `target` allows.
  function scrimFor(lums, opts) {
    const o = opts || {};
    const ink = Number.isFinite(o.ink) ? o.ink : relLum(0xf1, 0xeb, 0xe1);
    const target = Number(o.target) > 0 ? Number(o.target) : 4.5;
    const min = Number.isFinite(o.min) ? o.min : 0;
    const max = Number.isFinite(o.max) ? o.max : 1;
    const p95 = percentile(lums || [], 95);
    const allowed = (ink + 0.05) / target - 0.05;   // brightest background that passes
    let a = 0;
    if (allowed <= 0) a = 1;
    else if (p95 > allowed) a = 1 - toSrgb(allowed) / toSrgb(p95);
    a += 0.005;   // a hair over, so rounding never lands just under the target
    return Math.max(min, Math.min(max, a));
  }

  // Which picture is up. A picture is keyed by its URL, not the place's name.
  //   set(place)   a stats.place {name, slug, image, pending} or null
  //   loaded(url)  the browser finished loading url
  //   failed(url)  it couldn't
  // Each returns {action:'show', url}, {action:'keep'}, {action:'clear'} or
  // null (nothing to do). A place with no picture yet clears to its mood
  // layer, whether it is being made (D17) or not (D7, AC9); a renewal keeps
  // its old picture's URL until the new one is ready.
  function makePlaceModel() {
    let wanted = null;   // the url we want up (loading or up)
    function set(place) {
      const url = place && typeof place.image === 'string' && place.image ? place.image : null;
      if (url) {
        if (url === wanted) return null;
        wanted = url;
        return { action: 'show', url };
      }
      // No picture yet (still being made, D17) or none at all (D7): the new
      // place shows its mood alone; the picture fades in when it arrives.
      wanted = null;
      return { action: 'clear' };
    }
    function loaded(url) {
      return url && url === wanted ? { action: 'show', url } : null;
    }
    function failed(url) {
      if (!url || url !== wanted) return null;
      wanted = null;
      return { action: 'clear' };
    }
    return { set, loaded, failed };
  }

  // Where the picture may show in each layout (D9, AC7). rects are measured
  // boxes {x, y, w, h} keyed party, feed, inputbar, map; a zero-size box is a
  // hidden element and is left out. In combat the picture shows only behind
  // the narration column (clip), further dimmed; the map's area is plain.
  const COMBAT_EXTRA_DIM = 0.15;
  function box(r) {
    return r && r.w > 0 && r.h > 0 ? r : null;
  }
  function unionOf(list) {
    const rs = list.map(box).filter(Boolean);
    if (!rs.length) return null;
    const x0 = Math.min(...rs.map(r => r.x)), y0 = Math.min(...rs.map(r => r.y));
    const x1 = Math.max(...rs.map(r => r.x + r.w)), y1 = Math.max(...rs.map(r => r.y + r.h));
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }
  function regions(mode, rects) {
    const r = rects || {};
    if (mode === 'combat-map') {
      return { clip: unionOf([r.party, r.feed, r.inputbar]), plain: box(r.map), extraDim: COMBAT_EXTRA_DIM };
    }
    if (mode === 'combat-nomap') {
      return { clip: unionOf([r.feed, r.inputbar]), plain: null, extraDim: COMBAT_EXTRA_DIM };
    }
    return { clip: null, plain: null, extraDim: 0 };
  }

  const api = { regions, unionOf, coverRect, relLum, contrastRatio, percentile, scrimFor, makePlaceModel, toLinear, toSrgb };
  root.TV = root.TV || {};
  root.TV.place = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
