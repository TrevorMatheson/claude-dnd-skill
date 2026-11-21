// Attack sprites for the battle map (battle-map spec D15, D16, D17).
//
// A sword swing for melee, an arrow for a ranged weapon, a glowing bolt for
// a spell. A hit flashes the target; a miss shows a whiff (melee) or a
// glance-off (arrow, bolt). Everything is SVG and CSS drawn in code (D16):
// no sprite sheets, downloads or build step. Each attack lasts under a
// second and several in one block play back to back for at most 2 s; the
// rest snap (D17: animations never hold up the story).
//
// plan(), schedule() and svg() are pure so node can test them.
(function (root) {
  'use strict';

  const SPRITE = { melee: 'sword', ranged: 'arrow', spell: 'bolt' };
  const CLASSES = { hit: 'atk-hit', whiff: 'atk-whiff', deflect: 'atk-deflect' };
  const MAX_ATTACK_MS = 900;
  const MAX_TOTAL_MS = 2000;

  const isSquare = (p) => Array.isArray(p) && p.length === 2 && Number.isFinite(p[0]) && Number.isFinite(p[1]);

  // One attack as it can be shown now. `ctx.pos` holds the squares of the
  // tokens on screen and `ctx.visible(name)` says whether fog lets the
  // table see one. A hidden or unseen attacker leaves only the target's hit
  // or miss; an unseen target leaves only the swing or launch.
  function plan(attack, ctx) {
    if (!attack || typeof attack !== 'object') return null;
    const kind = attack.kind;
    if (!SPRITE[kind] || !attack.to) return null;
    const pos = (ctx && ctx.pos) || {};
    const seen = (n) => n != null && isSquare(pos[n]) && (!ctx || typeof ctx.visible !== 'function' || !!ctx.visible(n));
    const aOk = !!attack.from && seen(attack.from);
    const tOk = seen(attack.to);
    if (!aOk && !tOk) return null;
    const from = aOk ? [pos[attack.from][0], pos[attack.from][1]] : null;
    const to = tOk ? [pos[attack.to][0], pos[attack.to][1]] : null;
    const outcome = tOk ? (attack.hit === true ? 'hit' : (kind === 'melee' ? 'whiff' : 'deflect')) : null;
    let duration;
    if (!aOk) duration = 450;                         // the target's flash or whiff alone
    else if (kind === 'melee') duration = 650;
    else {
      // flight time grows with distance, never past the cap
      const d = to ? Math.hypot(to[0] - from[0], to[1] - from[1]) : 4;
      duration = Math.min(MAX_ATTACK_MS, Math.round(420 + 30 * d));
    }
    return {
      kind, sprite: aOk ? SPRITE[kind] : null, outcome,
      targetClass: outcome ? CLASSES[outcome] : null,
      from, to, duration,
      attacker: aOk ? String(attack.from) : null, target: tOk ? String(attack.to) : null,
    };
  }

  // A block's attacks, back to back from delay 0; whatever would end after
  // 2 s snaps (no sprite, no flash).
  function schedule(attacks, ctx) {
    const out = [];
    let t = 0;
    for (const a of Array.isArray(attacks) ? attacks : []) {
      const item = plan(a, ctx);
      if (!item) continue;
      if (t + item.duration > MAX_TOTAL_MS) {
        out.push(Object.assign({}, item, { delay: t, snapped: true, sprite: null, targetClass: null, outcome: null }));
        continue;
      }
      out.push(Object.assign({}, item, { delay: t }));
      t += item.duration;
    }
    return out;
  }

  const n2 = (v) => Math.round(v * 10) / 10;

  // The SVG for one scheduled attack, in board pixels (`s` = square size).
  // Motion comes from map.css keyframes; distances ride CSS variables.
  function svg(item, s) {
    if (!item || item.snapped) return '';
    const c = (p) => [n2((p[0] + 0.5) * s), n2((p[1] + 0.5) * s)];
    const cls = ['atk', item.sprite || 'target-only', item.targetClass || ''].filter(Boolean).join(' ');
    const style = `animation-delay:${item.delay || 0}ms;--atk-ms:${item.duration}ms;--atk-delay:${item.delay || 0}ms`;
    let body = '';
    const F = item.from && c(item.from), T = item.to && c(item.to);
    if (item.sprite === 'sword') {
      // The blade sweeps an arc over the target, from the attacker's side.
      const at = T || F;
      const ang = F && T ? Math.atan2(T[1] - F[1], T[0] - F[0]) * 180 / Math.PI : 0;
      const L = s * 0.95;
      body += `<g transform="translate(${at[0]} ${at[1]}) rotate(${n2(ang)})">`
        + `<g class="swing" style="${style}">`
        + `<path class="blade" d="M${n2(-s * 0.9)} ${n2(-s * 0.06)} L${n2(-s * 0.9 + L)} 0 L${n2(-s * 0.9)} ${n2(s * 0.06)} Z"/>`
        + `<rect class="hilt" x="${n2(-s * 0.98)}" y="${n2(-s * 0.18)}" width="${n2(s * 0.08)}" height="${n2(s * 0.36)}"/>`
        + `<path class="trail" d="M${n2(-s * 0.2)} ${n2(-s * 0.75)} A${n2(s * 0.8)} ${n2(s * 0.8)} 0 0 1 ${n2(-s * 0.2)} ${n2(s * 0.75)}"/>`
        + '</g></g>';
    } else if (item.sprite === 'arrow' || item.sprite === 'bolt') {
      let end = T || [F[0] + s * 3, F[1]];
      // A shot that misses glances off short of the target's disc.
      if (T && item.outcome === 'deflect') {
        const L = Math.hypot(T[0] - F[0], T[1] - F[1]) || 1;
        // the arrow's tip is drawn ahead of its origin; stop it at the disc's edge
        const short = s * (item.sprite === 'arrow' ? 0.85 : 0.5);
        const k = Math.max(0, (L - short) / L);
        end = [F[0] + (T[0] - F[0]) * k, F[1] + (T[1] - F[1]) * k];
      }
      const dx = n2(end[0] - F[0]), dy = n2(end[1] - F[1]);
      const ang = Math.atan2(dy, dx) * 180 / Math.PI;
      const fly = `${style};--dx:${dx}px;--dy:${dy}px`;
      body += `<g transform="translate(${F[0]} ${F[1]})"><g class="fly" style="${fly}">`
        + `<g transform="rotate(${n2(ang)})">`
        + (item.sprite === 'arrow'
          ? `<line class="shaft" x1="${n2(-s * 0.45)}" y1="0" x2="${n2(s * 0.2)}" y2="0"/>`
            + `<path class="head" d="M${n2(s * 0.32)} 0 L${n2(s * 0.14)} ${n2(-s * 0.09)} L${n2(s * 0.14)} ${n2(s * 0.09)} Z"/>`
            + `<path class="fletch" d="M${n2(-s * 0.45)} 0 l${n2(-s * 0.1)} ${n2(-s * 0.08)} M${n2(-s * 0.45)} 0 l${n2(-s * 0.1)} ${n2(s * 0.08)}"/>`
          : `<ellipse class="tail" cx="${n2(-s * 0.22)}" cy="0" rx="${n2(s * 0.34)}" ry="${n2(s * 0.08)}"/>`
            + `<circle class="core" r="${n2(s * 0.15)}"/>`)
        + '</g></g></g>';
    }
    if (T && item.outcome) {
      const r = n2(s * 0.55);
      const o = `${style}`;
      if (item.outcome === 'hit') {
        body += `<g transform="translate(${T[0]} ${T[1]})"><g class="flash" style="${o}">`
          + `<circle r="${r}"/>`
          + [0, 60, 120, 180, 240, 300].map((a) => `<line x1="0" y1="${n2(-r * 0.55)}" x2="0" y2="${n2(-r * 1.15)}" transform="rotate(${a})"/>`).join('')
          + '</g></g>';
      } else if (item.outcome === 'whiff') {
        body += `<g transform="translate(${T[0]} ${T[1]})"><g class="whiff" style="${o}">`
          + `<path d="M${n2(-r)} ${n2(-r * 0.2)} q${r} ${n2(-r * 0.9)} ${n2(r * 2)} 0"/>`
          + `<path d="M${n2(-r * 0.8)} ${n2(r * 0.15)} q${n2(r * 0.8)} ${n2(-r * 0.7)} ${n2(r * 1.6)} 0"/>`
          + '</g></g>';
      } else {
        // cold sparks where the shot stopped, and the shot skittering away
        const ang = F ? Math.atan2(T[1] - F[1], T[0] - F[0]) * 180 / Math.PI : 0;
        body += `<g transform="translate(${T[0]} ${T[1]}) rotate(${n2(ang)})"><g class="deflect" style="${o}">`
          + [150, 180, 210].map((a) => `<line x1="${n2(-r * 0.95)}" y1="0" x2="${n2(-r * 1.45)}" y2="0" transform="rotate(${a - 180} ${n2(-r * 0.9)} 0)"/>`).join('')
          + `<path class="ricochet" d="M${n2(-r * 0.9)} 0 l${n2(r * 0.5)} ${n2(-r * 0.9)}"/>`
          + '</g></g>';
      }
    }
    return `<g class="${cls}" data-sprite="${item.sprite || ''}">${body}</g>`;
  }

  const api = { plan, schedule, svg, CLASSES, SPRITE, MAX_ATTACK_MS, MAX_TOTAL_MS };
  root.TV = root.TV || {};
  root.TV.sprites = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
