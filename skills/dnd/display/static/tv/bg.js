// The TV background (display foundation D14, scene backgrounds spec D1, D2,
// D10, AC2): the scene's gradient, the sky, and the mood layer — 2-4
// animated layers per scene type from mood.js, crossfading when the scene
// changes. Soft layers (fog banks, light shafts, flicker) are drawn on a
// half-resolution canvas; particles are drawn sharp on #particles.
// Exposes TV.bg.applyScene(scene) and TV.bg.setWorldTime(wt).
(function () {
'use strict';
if (typeof document === 'undefined') return;   // loaded by node tests: nothing to draw

const MOOD = (window.TV && window.TV.mood) || null;
const FADE_MS = 2500;   // matches .bg-layer's opacity transition in tv.css

// ═══════════════════════════════════════════════════════════════════
// SCENE MANAGEMENT
// ═══════════════════════════════════════════════════════════════════

const SCENE_DEFAULTS = {
  colors: ['#1a0800', '#2e1400'],
  accent: '#c8601a',
  particles: 'embers',
  label: 'The Inn',
  name: 'tavern',
};

let activeScene = { ...SCENE_DEFAULTS };
let bgActive = 'a'; // which bg layer is currently showing

function applyScene(scene) {
  if (!scene || scene.name === activeScene.name) return;
  activeScene = { ...SCENE_DEFAULTS, ...scene };

  // Update scene indicator with a fade
  const indicator = document.getElementById('scene-indicator');
  if (indicator) {
    indicator.classList.add('changing');
    setTimeout(() => {
      indicator.textContent = '— ' + (activeScene.label || activeScene.name) + ' —';
      indicator.classList.remove('changing');
    }, 400);
  }

  // Crossfade background layers
  const incoming = bgActive === 'a' ? 'b' : 'a';
  const inEl  = document.getElementById('bg-' + incoming);
  const outEl = document.getElementById('bg-' + bgActive);

  const [top, bot] = activeScene.colors;
  inEl.style.background =
    `radial-gradient(ellipse at 50% 30%, ${top} 0%, ${bot} 100%)`;
  inEl.style.opacity = '1';
  outEl.style.opacity = '0';
  bgActive = incoming;

  // Crossfade the mood layer to the new scene's recipe.
  mood.switchTo(activeScene);
  if (placeReady) place.retint();
}

// Initialise background to default scene
;(function initBg() {
  const el = document.getElementById('bg-' + bgActive);
  const [top, bot] = SCENE_DEFAULTS.colors;
  el.style.background =
    `radial-gradient(ellipse at 50% 30%, ${top} 0%, ${bot} 100%)`;
})();


// ═══════════════════════════════════════════════════════════════════
// CANVASES
// ═══════════════════════════════════════════════════════════════════

const canvas  = document.getElementById('particles');
const ctx     = canvas.getContext('2d');
// The soft layers' canvas sits just under #particles, at half resolution.
const softCanvas = document.createElement('canvas');
softCanvas.id = 'mood-soft';
softCanvas.setAttribute('aria-hidden', 'true');
canvas.parentNode.insertBefore(softCanvas, canvas);
const sctx = softCanvas.getContext('2d');
let W = 0, H = 0;
let moodReady = false;
let placeReady = false;

function resizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  W = window.innerWidth;
  H = window.innerHeight;
  canvas.width  = Math.round(W * dpr);
  canvas.height = Math.round(H * dpr);
  canvas.style.width  = W + 'px';
  canvas.style.height = H + 'px';
  // setTransform, not scale: scale would build up on every resize.
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const half = dpr / 2;
  softCanvas.width  = Math.max(1, Math.round(W * half));
  softCanvas.height = Math.max(1, Math.round(H * half));
  softCanvas.style.width  = W + 'px';
  softCanvas.style.height = H + 'px';
  sctx.setTransform(half, 0, 0, half, 0, 0);
  if (moodReady) mood.rebudget();
}
window.addEventListener('resize', resizeCanvas);

// The weight of the mood being drawn (0-1), set by the mood system per set.
let drawWeight = 1;

// ── Particle classes ──────────────────────────────────────────────

function rand(a, b) { return a + Math.random() * (b - a); }
function randInt(a, b) { return Math.floor(rand(a, b + 1)); }
function hexToRgb(hex) {
  const r = parseInt(hex.slice(1,3),16);
  const g = parseInt(hex.slice(3,5),16);
  const b = parseInt(hex.slice(5,7),16);
  return [r,g,b];
}

class Particle {
  constructor(type, accent, color) {
    this.type   = type;
    this.accent = accent;
    this.color  = color;   // the recipe's colour, if it sets one
    this.reset();
  }
  reset() {
    const T = this.type;
    this.x    = rand(0, W);
    this.y    = rand(0, H);
    this.life = 0;
    this.maxLife = 1;
    this.size = 2;
    this.alpha = 0;
    this.vx = 0; this.vy = 0;
    this._phase = rand(0, Math.PI * 2);

    if (T === 'embers') {
      this.x = rand(0, W);
      this.y = rand(H * 0.6, H);
      this.vx = rand(-0.4, 0.4);
      this.vy = rand(-0.8, -0.3);
      this.size = rand(1, 3);
      this.maxLife = rand(120, 300);
      this._color = this.accent;
    } else if (T === 'fireflies') {
      this.x = rand(0, W);
      this.y = rand(H * 0.2, H * 0.9);
      this.vx = rand(-0.3, 0.3);
      this.vy = rand(-0.2, 0.2);
      this.size = rand(1.5, 3.5);
      this.maxLife = rand(200, 500);
      this._color = '#a0ff60';
    } else if (T === 'smoke') {
      this.x = rand(0, W);
      this.y = H + 10;
      this.vx = rand(-0.2, 0.2);
      this.vy = rand(-0.4, -0.15);
      this.size = rand(6, 18);
      this.maxLife = rand(180, 350);
      this._color = '#404040';
    } else if (T === 'dust') {
      this.x = rand(0, W);
      this.y = rand(0, H);
      this.vx = rand(-0.1, 0.1);
      this.vy = rand(-0.05, 0.08);
      this.size = rand(0.5, 1.8);
      this.maxLife = rand(300, 600);
      this._color = '#a09060';
    } else if (T === 'snow') {
      this.x = rand(-10, W + 10);
      this.y = rand(-10, 0);
      this.vx = rand(-0.3, 0.3);
      this.vy = rand(0.4, 1.2);
      this.size = rand(1, 3.5);
      this.maxLife = rand(200, 450);
      this._color = '#d8e8ff';
    } else if (T === 'stars') {
      this.x = rand(0, W);
      this.y = rand(0, H * 0.8);
      this.vx = 0; this.vy = 0;
      this.size = rand(0.5, 2);
      this.maxLife = rand(400, 900);
      this._color = '#c0d0ff';
    } else if (T === 'bubbles') {
      this.x = rand(0, W);
      this.y = H + 10;
      this.vx = rand(-0.2, 0.2);
      this.vy = rand(-0.5, -0.2);
      this.size = rand(2, 8);
      this.maxLife = rand(200, 400);
      this._color = '#60a0d0';
    } else if (T === 'sparks') {
      this.x = rand(0, W);
      this.y = rand(H * 0.4, H);
      this.vx = rand(-1.5, 1.5);
      this.vy = rand(-2, -0.5);
      this.size = rand(0.8, 2.5);
      this.maxLife = rand(40, 100);
      this._color = '#ffe060';
    } else if (T === 'sand') {
      this.x = rand(-20, 0);
      this.y = rand(0, H);
      this.vx = rand(1.2, 2.8);
      this.vy = rand(-0.2, 0.4);
      this.size = rand(0.8, 2.2);
      this.maxLife = rand(150, 350);
      this._color = '#c0a060';
    } else if (T === 'rain') {
      this.x = rand(-20, W);
      this.y = rand(-20, 0);
      this.vx = rand(0.5, 1.2);
      this.vy = rand(6, 14);
      this.size = rand(1, 2);
      this.maxLife = rand(60, 120);
      this._color = '#8090b0';
    } else if (T === 'drips') {
      this.x = rand(0, W);
      this.y = rand(-20, 0);
      this.vx = 0;
      this.vy = rand(1.5, 4);
      this.size = rand(1.5, 3.5);
      this.maxLife = rand(80, 180);
      this._color = '#6090b0';
    } else if (T === 'mist') {
      this.x = rand(-120, W + 120);
      this.y = rand(H * 0.05, H * 0.85);
      this.vx = rand(0.04, 0.18);
      this.vy = rand(-0.015, 0.015);
      this.size = rand(70, 180);
      this.maxLife = rand(700, 1400);
      this._color = '#7888a0';
    } else if (T === 'leaves') {
      this.x = rand(0, W);
      this.y = rand(-20, 0);
      this.vx = rand(-0.7, 0.7);
      this.vy = rand(0.5, 1.6);
      this.size = rand(4, 9);
      this.maxLife = rand(220, 520);
      this._rot = rand(0, Math.PI * 2);
      this._rotSpeed = rand(-0.045, 0.045);
      const lc = ['#8B4513','#D2691E','#c8a020','#CD853F','#a06828'];
      this._color = lc[randInt(0, lc.length - 1)];
    } else if (T === 'ripples') {
      this.x = rand(0, W);
      this.y = rand(H * 0.55, H);
      this.vx = 0; this.vy = 0;
      this.size = rand(6, 22);
      this.maxLife = rand(100, 220);
      this._color = '#4888c0';
    } else if (T === 'glowmotes') {
      this.x = rand(0, W);
      this.y = rand(H * 0.1, H * 0.95);
      this.vx = rand(-0.12, 0.12);
      this.vy = rand(-0.18, -0.03);
      this.size = rand(1.5, 3.5);
      this.maxLife = rand(400, 900);
      this._color = '#ffd080';
    } else {
      // fallback = dust
      this.vx = 0; this.vy = 0;
      this.size = 1;
      this.maxLife = 300;
      this._color = '#808080';
    }
    if (this.color) this._color = this.color;
  }

  update() {
    this.life++;
    const T = this.type;
    const progress = this.life / this.maxLife;

    // Alpha envelope: fade in first 10%, full mid, fade out last 20%
    if (progress < 0.1)       this.alpha = progress / 0.1;
    else if (progress > 0.8)  this.alpha = (1 - progress) / 0.2;
    else                      this.alpha = 1;

    this.x += this.vx;
    this.y += this.vy;

    if (T === 'embers') {
      this.vx += rand(-0.05, 0.05);
      this.vx  = Math.max(-0.6, Math.min(0.6, this.vx));
      if (this.life % 20 === 0) this.alpha *= rand(0.5, 1.0);
    } else if (T === 'fireflies') {
      this._phase += 0.03;
      this.x += Math.sin(this._phase) * 0.5;
      this.y += Math.cos(this._phase * 0.7) * 0.3;
      this.alpha *= (0.85 + 0.15 * Math.sin(this._phase * 2));
    } else if (T === 'smoke') {
      this.size += 0.04;
      this.vx  += rand(-0.01, 0.01);
    } else if (T === 'stars') {
      this._phase += 0.02;
      this.alpha *= (0.85 + 0.15 * Math.sin(this._phase));
    } else if (T === 'sparks') {
      this.vy += 0.08; // gravity
    } else if (T === 'leaves') {
      this._rot += this._rotSpeed;
      this.vx += rand(-0.025, 0.025);
      this.vx = Math.max(-1.2, Math.min(1.2, this.vx));
    } else if (T === 'ripples') {
      this.size += 0.55;
    } else if (T === 'glowmotes') {
      this._phase += 0.02;
      this.x += Math.sin(this._phase) * 0.25;
      this.alpha *= (0.55 + 0.45 * Math.sin(this._phase * 1.7));
    }

    return this.life < this.maxLife;
  }

  draw(ctx) {
    const T = this.type;
    const [r,g,b] = hexToRgb(this._color);
    ctx.globalAlpha = Math.max(0, Math.min(1, this.alpha)) * drawWeight;

    if (T === 'smoke') {
      const grad = ctx.createRadialGradient(
        this.x, this.y, 0, this.x, this.y, this.size);
      grad.addColorStop(0, `rgba(${r},${g},${b},0.3)`);
      grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
      ctx.fill();
    } else if (T === 'glowmotes') {
      const mote = ctx.createRadialGradient(this.x, this.y, 0, this.x, this.y, this.size * 6);
      mote.addColorStop(0, `rgba(${r},${g},${b},0.55)`);
      mote.addColorStop(0.35, `rgba(${r},${g},${b},0.18)`);
      mote.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = mote;
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size * 6, 0, Math.PI * 2);
      ctx.fill();
    } else if (T === 'embers' || T === 'fireflies' || T === 'sparks') {
      // Glow effect
      const glow = ctx.createRadialGradient(
        this.x, this.y, 0, this.x, this.y, this.size * 3);
      glow.addColorStop(0, `rgba(${r},${g},${b},0.6)`);
      glow.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size * 3, 0, Math.PI * 2);
      ctx.fill();
      // Core dot
      ctx.fillStyle = `rgb(${Math.min(255,r+60)},${Math.min(255,g+40)},${Math.min(255,b+20)})`;
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size * 0.6, 0, Math.PI * 2);
      ctx.fill();
    } else if (T === 'bubbles') {
      ctx.strokeStyle = `rgba(${r},${g},${b},0.6)`;
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
      ctx.stroke();
    } else if (T === 'rain') {
      ctx.strokeStyle = `rgba(${r},${g},${b},0.5)`;
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.moveTo(this.x, this.y);
      ctx.lineTo(this.x + this.vx * 3, this.y + this.vy * 3);
      ctx.stroke();
    } else if (T === 'drips') {
      ctx.fillStyle = `rgba(${r},${g},${b},0.7)`;
      ctx.beginPath();
      ctx.ellipse(this.x, this.y, this.size * 0.4, this.size, 0, 0, Math.PI * 2);
      ctx.fill();
    } else if (T === 'mist') {
      const mg = ctx.createRadialGradient(this.x, this.y, 0, this.x, this.y, this.size);
      mg.addColorStop(0, `rgba(${r},${g},${b},0.055)`);
      mg.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = mg;
      ctx.beginPath();
      ctx.ellipse(this.x, this.y, this.size, this.size * 0.38, 0, 0, Math.PI * 2);
      ctx.fill();
    } else if (T === 'leaves') {
      ctx.save();
      ctx.translate(this.x, this.y);
      ctx.rotate(this._rot);
      ctx.fillStyle = `rgba(${r},${g},${b},0.88)`;
      ctx.beginPath();
      ctx.ellipse(0, 0, this.size * 0.42, this.size, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    } else if (T === 'ripples') {
      ctx.strokeStyle = `rgba(${r},${g},${b},0.28)`;
      ctx.lineWidth = 0.9;
      ctx.beginPath();
      ctx.ellipse(this.x, this.y, this.size, this.size * 0.28, 0, 0, Math.PI * 2);
      ctx.stroke();
    } else {
      ctx.fillStyle = `rgba(${r},${g},${b},1)`;
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.globalAlpha = 1;
  }
}

// ── Soft layers: fog banks, light shafts, flicker ────────────────────

class SoftLayer {
  constructor(layer) {
    this.kind  = layer.kind;
    this.color = layer.color || '#808080';
    this.alpha = Number(layer.alpha) > 0 ? Number(layer.alpha) : 0.08;
    this.x = Number.isFinite(layer.x) ? layer.x : 0.5;
    this.y = Number.isFinite(layer.y) ? layer.y : 0.9;
    const n = this.kind === 'flicker' ? 1 : Math.max(1, Math.round(Number(layer.count) || 2));
    this.items = Array.from({ length: n }, (_, i) => this._item(i, n));
    this.t = rand(0, 1000);
  }
  _item(i, n) {
    if (this.kind === 'fogbank') {
      return { x: rand(-0.3, 1.0), y: 0.55 + 0.4 * (i + 0.5) / n + rand(-0.05, 0.05),
               w: rand(0.5, 0.9), h: rand(0.12, 0.22), v: rand(0.00004, 0.00012) * (i % 2 ? -1 : 1) };
    }
    if (this.kind === 'shafts') {
      return { x: 0.15 + 0.7 * (i + 0.5) / n + rand(-0.06, 0.06), w: rand(0.05, 0.11),
               lean: rand(0.12, 0.22), ph: rand(0, Math.PI * 2), sp: rand(0.002, 0.005) };
    }
    return {};
  }
  update() {
    this.t += 1;
    if (this.kind === 'fogbank') {
      for (const f of this.items) {
        f.x += f.v;
        if (f.x > 1.2) f.x = -f.w - 0.1;
        if (f.x < -f.w - 0.2) f.x = 1.1;
      }
    } else if (this.kind === 'shafts') {
      for (const s of this.items) s.ph += s.sp;
    }
  }
  draw(c) {
    const [r, g, b] = hexToRgb(this.color);
    c.globalAlpha = drawWeight;
    if (this.kind === 'fogbank') {
      for (const f of this.items) {
        const cx = (f.x + f.w / 2) * W, cy = f.y * H, rx = f.w * W / 2, ry = f.h * H / 2;
        const grad = c.createRadialGradient(cx, cy, 0, cx, cy, rx);
        grad.addColorStop(0, `rgba(${r},${g},${b},${this.alpha})`);
        grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
        c.fillStyle = grad;
        c.beginPath();
        c.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
        c.fill();
      }
    } else if (this.kind === 'shafts') {
      for (const s of this.items) {
        const a = this.alpha * (0.6 + 0.4 * Math.sin(s.ph));
        const x0 = s.x * W, w = s.w * W, lean = s.lean * W;
        const grad = c.createLinearGradient(0, 0, 0, H);
        grad.addColorStop(0, `rgba(${r},${g},${b},${a})`);
        grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
        c.fillStyle = grad;
        c.beginPath();
        c.moveTo(x0, 0); c.lineTo(x0 + w, 0);
        c.lineTo(x0 + w * 2.2 + lean, H); c.lineTo(x0 + lean, H);
        c.closePath();
        c.fill();
      }
    } else if (this.kind === 'flicker') {
      // A wavering glow: two slow sines and a little jitter, like a fire.
      const k = 0.78 + 0.12 * Math.sin(this.t * 0.07) + 0.07 * Math.sin(this.t * 0.23) + rand(-0.03, 0.03);
      const cx = this.x * W, cy = this.y * H, rad = Math.max(W, H) * 0.55;
      const grad = c.createRadialGradient(cx, cy, 0, cx, cy, rad);
      grad.addColorStop(0, `rgba(${r},${g},${b},${(this.alpha * k).toFixed(3)})`);
      grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
      c.fillStyle = grad;
      c.fillRect(0, 0, W, H);
    }
    c.globalAlpha = 1;
  }
}

// ── One scene's mood: its recipe's layers, budgeted for this screen ──

const FALLBACK_RECIPE = { layers: [{ kind: 'dust', count: 60 }, { kind: 'embers', count: 40 }] };
const SOFT = MOOD ? MOOD.SOFT_KINDS : ['fogbank', 'shafts', 'flicker'];

class MoodSet {
  constructor(scene) {
    this.scene  = scene;
    this.recipe = MOOD ? MOOD.recipeFor(scene) : FALLBACK_RECIPE;
    this.layers = [];
    this.build();
  }
  // (Re)build for the current screen size, keeping what is already drawn.
  build() {
    const r = MOOD ? MOOD.budget(this.recipe, W, H) : this.recipe;
    const old = this.layers;
    this.layers = r.layers.map((l, i) => {
      if (SOFT.includes(l.kind)) return (old[i] && old[i].soft) ? old[i] : { soft: new SoftLayer(l) };
      const keep = (old[i] && old[i].particles) ? old[i].particles.slice(0, l.count) : [];
      return { kind: l.kind, color: l.color, target: l.count, particles: keep };
    });
    for (const l of this.layers) {
      if (!l.particles) continue;
      while (l.particles.length < l.target) {
        const p = new Particle(l.kind, this.scene.accent, l.color);
        p.life = randInt(0, p.maxLife);   // stagger so they don't all start together
        l.particles.push(p);
      }
    }
  }
  update() {
    for (const l of this.layers) {
      if (l.soft) { l.soft.update(); continue; }
      l.particles = l.particles.filter(p => p.update());
      while (l.particles.length < l.target) l.particles.push(new Particle(l.kind, this.scene.accent, l.color));
    }
  }
  draw(weight) {
    drawWeight = weight;
    for (const l of this.layers) {
      if (l.soft) l.soft.draw(sctx);
      else for (const p of l.particles) p.draw(ctx);
    }
    drawWeight = 1;
  }
}

// ── The mood system: at most two moods live, crossfading ─────────────

const mood = (() => {
  const mixer = MOOD ? MOOD.makeMixer(FADE_MS) : null;
  const sets = new Map();   // scene name -> MoodSet
  let current = SCENE_DEFAULTS.name;

  function switchTo(scene) {
    const name = scene.name || 'unknown';
    if (!sets.has(name)) sets.set(name, new MoodSet(scene));
    current = name;
    if (mixer) mixer.set(name, performance.now());
  }

  function frame(now) {
    ctx.clearRect(0, 0, W, H);
    sctx.clearRect(0, 0, W, H);
    const ws = mixer ? mixer.weights(now) : [{ name: current, w: 1 }];
    const live = new Set(ws.map(e => e.name));
    for (const name of [...sets.keys()]) if (!live.has(name)) sets.delete(name);
    for (const e of ws) {
      const set = sets.get(e.name);
      if (!set) continue;
      set.update();
      if (e.w > 0.001) set.draw(e.w);
    }
  }

  function rebudget() { for (const s of sets.values()) s.build(); }

  return { switchTo, frame, rebudget, weights: now => (mixer ? mixer.weights(now) : []) };
})();

resizeCanvas();
moodReady = true;
mood.switchTo(SCENE_DEFAULTS);

// ── Sky Renderer ──────────────────────────────────────────────────

const skyCanvas = document.getElementById('sky');
const skyCtx    = skyCanvas.getContext('2d');
let SW = 0, SH = 0;

function resizeSky() {
  const dpr = window.devicePixelRatio || 1;
  SW = window.innerWidth;
  SH = Math.round(window.innerHeight * 0.42);
  skyCanvas.width  = SW * dpr;
  skyCanvas.height = SH * dpr;
  skyCanvas.style.width  = SW + 'px';
  skyCanvas.style.height = SH + 'px';
  skyCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener('resize', resizeSky);
resizeSky();

const skyRenderer = (() => {
  let _time = 'morning', _weather = '';

  // Pre-computed star positions (twinkling dots, night only)
  const STARS = Array.from({length: 130}, () => ({
    x: Math.random(), y: Math.random(),
    r: 0.4 + Math.random() * 1.6,
    ph: Math.random() * Math.PI * 2,
    spd: 0.008 + Math.random() * 0.022,
  }));

  // Cloud objects — drift left, wrap to right
  const CLOUD_BLOBS = [[0,0,30],[-25,10,23],[23,8,25],[-9,-12,19],[13,-9,17],[36,14,17],[-36,14,15],[6,16,13]];
  const CLOUDS = Array.from({length: 5}, (_, i) => ({
    x: (i / 5) * (SW + 600) - 100,
    y: 0, sc: 1, spd: 0.1,
  }));
  function resetCloud(c, offscreen) {
    c.x   = offscreen ? SW + rand(100, 350) : rand(-200, SW + 200);
    c.y   = rand(SH * 0.06, SH * 0.68);
    c.sc  = rand(0.65, 1.75);
    c.spd = rand(0.07, 0.22);
  }
  CLOUDS.forEach(c => resetCloud(c, false));

  function drawCloud(c, opacity, color) {
    skyCtx.globalAlpha = opacity;
    skyCtx.fillStyle = color;
    CLOUD_BLOBS.forEach(([dx, dy, r]) => {
      skyCtx.beginPath();
      skyCtx.arc(c.x + dx * c.sc, c.y + dy * c.sc, r * c.sc, 0, Math.PI * 2);
      skyCtx.fill();
    });
    skyCtx.globalAlpha = 1;
  }

  function glow(x, y, r0, r1, c0, c1) {
    const g = skyCtx.createRadialGradient(x, y, r0, x, y, r1);
    g.addColorStop(0, c0); g.addColorStop(1, c1);
    skyCtx.fillStyle = g;
    skyCtx.beginPath();
    skyCtx.arc(x, y, r1, 0, Math.PI * 2);
    skyCtx.fill();
  }

  return {
    setState(wt) {
      if (!wt) return;
      _time    = (wt.time    || 'morning').toLowerCase();
      _weather = (wt.weather || '').toLowerCase();
    },

    draw() {
      skyCtx.clearRect(0, 0, SW, SH);

      const isNight  = /night|midnight|late/.test(_time);
      const isDusk   = /evening|dusk/.test(_time);
      const isDawn   = /dawn/.test(_time);
      const isRain   = /rain|storm/.test(_weather);
      const isCloudy = /cloud|overcast/.test(_weather) || isRain;
      const isFog    = /fog|mist/.test(_weather);

      // Stars (night / dusk)
      if (isNight || isDusk) {
        const mod = isNight ? 1 : 0.35;
        STARS.forEach(s => {
          s.ph += s.spd;
          const a = (0.25 + 0.55 * Math.sin(s.ph)) * mod * (isRain ? 0.15 : 1);
          if (a <= 0.01) return;
          skyCtx.globalAlpha = a;
          skyCtx.fillStyle = '#c8d4ff';
          skyCtx.beginPath();
          skyCtx.arc(s.x * SW, s.y * SH, s.r, 0, Math.PI * 2);
          skyCtx.fill();
        });
        skyCtx.globalAlpha = 1;
      }

      // Moon (night / dusk)
      if (isNight || isDusk) {
        const mx = SW * 0.76, my = SH * 0.24;
        const ma = isNight ? 0.82 : 0.32;
        glow(mx, my, 10, 72, `rgba(190,200,230,${(ma * 0.18).toFixed(2)})`, 'rgba(190,200,230,0)');
        skyCtx.globalAlpha = ma;
        skyCtx.fillStyle = '#d8e0f4';
        skyCtx.beginPath(); skyCtx.arc(mx, my, 15, 0, Math.PI * 2); skyCtx.fill();
        // Crescent: paint shadow circle using clip
        skyCtx.save();
        skyCtx.beginPath(); skyCtx.arc(mx, my, 15, 0, Math.PI * 2); skyCtx.clip();
        skyCtx.fillStyle = 'rgba(8,12,28,0.82)';
        skyCtx.beginPath(); skyCtx.arc(mx + 9, my - 4, 13, 0, Math.PI * 2); skyCtx.fill();
        skyCtx.restore();
        skyCtx.globalAlpha = 1;
      }

      // Sun (day / dusk / dawn)
      if (!isNight) {
        let sx, sy, inner, outer;
        if (isDawn) {
          [sx, sy] = [SW * 0.12, SH * 0.90];
          [inner, outer] = ['rgba(255,155,55,0.72)', 'rgba(255,100,20,0)'];
        } else if (/morning/.test(_time)) {
          [sx, sy] = [SW * 0.18, SH * 0.38];
          [inner, outer] = ['rgba(255,225,130,0.62)', 'rgba(255,200,80,0)'];
        } else if (/midday|noon/.test(_time)) {
          [sx, sy] = [SW * 0.50, SH * 0.06];
          [inner, outer] = ['rgba(255,248,190,0.44)', 'rgba(255,245,160,0)'];
        } else if (/afternoon/.test(_time)) {
          [sx, sy] = [SW * 0.80, SH * 0.32];
          [inner, outer] = ['rgba(255,215,100,0.55)', 'rgba(255,185,60,0)'];
        } else { // evening / dusk
          [sx, sy] = [SW * 0.88, SH * 0.84];
          [inner, outer] = ['rgba(245,110,28,0.75)', 'rgba(210,65,0,0)'];
        }
        glow(sx, sy, 4, 95, inner, outer);
        glow(sx, sy, 0, 26, inner.replace(/[\d.]+\)$/, '1)'), inner.replace(/[\d.]+\)$/, '0)'));
      }

      // Clouds
      const cloudAlpha = isRain ? 0.40 : isCloudy ? 0.22 : isFog ? 0.14 : 0.08;
      const cloudCount = isRain ? 5  : isCloudy ? 4 : 2;
      const cloudColor = isNight ? '#303248' : isDusk ? '#a05828' : isRain ? '#3a3c4c' : '#c4c8d8';
      const windMult   = isRain ? 2.2 : 1;

      CLOUDS.forEach((c, i) => {
        if (i >= cloudCount) return;
        c.x -= c.spd * windMult;
        if (c.x < -300 * c.sc) resetCloud(c, true);
        const ao = cloudAlpha * (0.7 + 0.06 * i);
        drawCloud(c, ao, cloudColor);
      });

      // Bottom gradient fade — sky dissolves into scene background
      skyCtx.globalCompositeOperation = 'destination-out';
      const fade = skyCtx.createLinearGradient(0, SH * 0.52, 0, SH);
      fade.addColorStop(0, 'rgba(0,0,0,0)');
      fade.addColorStop(1, 'rgba(0,0,0,1)');
      skyCtx.fillStyle = fade;
      skyCtx.fillRect(0, SH * 0.52, SW, SH * 0.48);
      skyCtx.globalCompositeOperation = 'source-over';
    },
  };
})();

// ═══════════════════════════════════════════════════════════════════
// PLACE PICTURE (scene backgrounds D1, D5, D8, D10, AC6)
// ═══════════════════════════════════════════════════════════════════
// Under the mood layer: two <img> layers that crossfade, a wash of the
// scene's colours over them, and a blurred black scrim behind the
// narration column whose strength is measured from the picture so body
// text keeps 4.5:1 (WCAG AA) against it.

const PLACE = (window.TV && window.TV.place) || null;
const INK_LUM = PLACE ? PLACE.relLum(0xf1, 0xeb, 0xe1) : 0.83;   // --ink
const PLACE_BRIGHT = 0.72;      // the picture is darkened (D8)
const TINT_ALPHA = 0.35;        // the scene's colours over it
const SCRIM_MIN = 0.22, SCRIM_MAX = 0.88;
const SAMPLE_W = 160;           // the offscreen measuring canvas's width

const place = (() => {
  const host = document.getElementById('tv-bg');
  const sky = document.getElementById('sky');
  const before = sky;
  const mk = (tag, cls) => { const e = document.createElement(tag); e.className = cls; e.setAttribute('aria-hidden', 'true'); return e; };
  const imgs = { a: mk('img', 'place-img'), b: mk('img', 'place-img') };
  imgs.a.id = 'place-a'; imgs.b.id = 'place-b';
  imgs.a.alt = imgs.b.alt = '';
  imgs.a.decoding = imgs.b.decoding = 'async';
  const tint = mk('div', 'place-tint'); tint.id = 'place-tint';
  const scrim = mk('div', 'place-scrim'); scrim.id = 'place-scrim';
  host.insertBefore(imgs.a, before);
  host.insertBefore(imgs.b, before);
  host.insertBefore(tint, before);
  host.insertBefore(scrim, before);
  document.documentElement.style.setProperty('--place-bright', String(PLACE_BRIGHT));

  const model = PLACE ? PLACE.makePlaceModel() : null;
  let front = 'a';          // the img that is (or was last) showing
  let shownUrl = null;      // the picture up now
  let report = { image: null, scrim: 0, ratio: null, p95: null };
  let lums = null;          // luminances behind the column, before the scrim

  function act(a) {
    if (!a) return;
    if (a.action === 'show') load(a.url);
    else if (a.action === 'clear') clear();
  }

  function load(url) {
    if (url === shownUrl) return;
    const back = front === 'a' ? 'b' : 'a';
    const el = imgs[back];
    el.onload = () => {
      const a = model.loaded(url);
      if (a && a.action === 'show' && el.dataset.url === url) reveal(back);
    };
    el.onerror = () => act(model.failed(url));
    el.dataset.url = url;
    el.src = url;
  }

  function reveal(back) {
    const el = imgs[back];
    const url = el.dataset.url;
    imgs[front].classList.remove('on');
    el.classList.add('on');
    front = back;
    shownUrl = url;
    host.classList.add('has-place');
    schedule();
  }
  function clear() {
    imgs.a.classList.remove('on');
    imgs.b.classList.remove('on');
    shownUrl = null;
    host.classList.remove('has-place');
    lums = null;
    report = { image: null, scrim: 0, ratio: null, p95: null };
    applyMask(null);
  }

  function set(pl) {
    if (model) act(model.set(pl));
  }

  // The wash follows the scene's colours (the mood colour over the picture).
  function retint() {
    const [top, bot] = activeScene.colors;
    tint.style.background = `radial-gradient(ellipse at 50% 30%, ${top} 0%, ${bot} 100%)`;
    if (shownUrl) schedule();
  }

  // ── Where the narration column is, and how dark the scrim must be ──

  function rectOf(id) {
    const el = document.getElementById(id);
    if (!el || el.hidden) return null;
    const r = el.getBoundingClientRect();
    return (r.width > 0 && r.height > 0) ? { x: r.left, y: r.top, w: r.width, h: r.height } : null;
  }
  function union(rs) {
    rs = rs.filter(Boolean);
    if (!rs.length) return null;
    const x0 = Math.min(...rs.map(r => r.x)), y0 = Math.min(...rs.map(r => r.y));
    const x1 = Math.max(...rs.map(r => r.x + r.w)), y1 = Math.max(...rs.map(r => r.y + r.h));
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }
  function column() {
    return union([rectOf('tv-feed'), rectOf('tv-inputbar')]);
  }

  function measure() {
    const img = imgs[front];
    const col = column();
    if (!shownUrl || !img.naturalWidth || !col) { placeScrim(col, SCRIM_MIN); return; }
    const vw = window.innerWidth, vh = window.innerHeight;
    const k = SAMPLE_W / vw;
    const cw = SAMPLE_W, ch = Math.max(1, Math.round(vh * k));
    const c = document.createElement('canvas');
    c.width = cw; c.height = ch;
    const g = c.getContext('2d', { willReadFrequently: true });
    const r = PLACE.coverRect(img.naturalWidth, img.naturalHeight, cw, ch);
    g.filter = `brightness(${PLACE_BRIGHT})`;
    g.drawImage(img, r.x, r.y, r.w, r.h);
    g.filter = 'none';
    const [top, bot] = activeScene.colors;
    const grad = g.createRadialGradient(cw / 2, ch * 0.3, 0, cw / 2, ch * 0.3, Math.max(cw, ch));
    grad.addColorStop(0, top); grad.addColorStop(1, bot);
    g.globalAlpha = TINT_ALPHA; g.fillStyle = grad; g.fillRect(0, 0, cw, ch); g.globalAlpha = 1;
    const sx = Math.max(0, Math.floor(col.x * k)), sy = Math.max(0, Math.floor(col.y * k));
    const sw = Math.max(1, Math.min(cw - sx, Math.ceil(col.w * k))), sh = Math.max(1, Math.min(ch - sy, Math.ceil(col.h * k)));
    let data;
    try { data = g.getImageData(sx, sy, sw, sh).data; } catch (_) { placeScrim(col, SCRIM_MAX); return; }
    lums = [];
    for (let i = 0; i < data.length; i += 4) lums.push(PLACE.relLum(data[i], data[i + 1], data[i + 2]));
    const extra = currentExtraDim();
    const a = Math.min(SCRIM_MAX, PLACE.scrimFor(lums, { ink: INK_LUM, target: 4.5, min: SCRIM_MIN, max: SCRIM_MAX }) + extra);
    const p95 = PLACE.percentile(lums, 95);
    const after = PLACE.toLinear(PLACE.toSrgb(p95) * (1 - a));
    report = { image: shownUrl, scrim: +a.toFixed(3), ratio: +PLACE.contrastRatio(INK_LUM, after).toFixed(2), p95: +p95.toFixed(4) };
    placeScrim(col, a);
  }

  function currentExtraDim() { return 0; }

  function placeScrim(col, alpha) {
    const pad = parseFloat(getComputedStyle(document.documentElement).fontSize) * 2.5 || 40;
    if (!col) { scrim.style.width = '0px'; applyMask(null); return; }
    const x = Math.max(0, col.x - pad), w = Math.min(window.innerWidth - x, col.w + pad * 2);
    scrim.style.left = x + 'px';
    scrim.style.width = w + 'px';
    document.documentElement.style.setProperty('--scrim', String(alpha));
    applyMask(shownUrl ? { x: col.x, w: col.w } : null);
  }

  // Particles at half strength over the column while a picture is up, so
  // they don't fight the text (the picture already carries the detail).
  function applyMask(col) {
    for (const el of [canvas, softCanvas]) {
      if (!col) { el.style.maskImage = el.style.webkitMaskImage = ''; continue; }
      const l = col.x, r = col.x + col.w;
      const m = `linear-gradient(to right, #000 ${l}px, rgba(0,0,0,.5) ${l}px, rgba(0,0,0,.5) ${r}px, #000 ${r}px)`;
      el.style.maskImage = m; el.style.webkitMaskImage = m;
    }
  }

  let timer = null;
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(() => { try { measure(); } catch (err) { console.error('tv: place measure failed', err); } }, 120);
  }

  // Re-measure when the picture, the layout mode or the column's size changes.
  const stage = document.getElementById('tv-stage');
  if (stage && typeof MutationObserver !== 'undefined') {
    new MutationObserver(schedule).observe(stage, { attributes: true, attributeFilter: ['data-mode'] });
  }
  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(schedule);
    for (const id of ['tv-feed', 'tv-inputbar', 'tv-party', 'tv-map']) {
      const el = document.getElementById(id);
      if (el) ro.observe(el);
    }
  }
  window.addEventListener('resize', schedule);
  schedule();

  return {
    set,
    report: () => Object.assign({}, report),
    remeasure: schedule,
    retint,
  };
})();
placeReady = true;
place.retint();

// ── Animation loop ────────────────────────────────────────────────

function loop(now) {
  mood.frame(now || performance.now());
  skyRenderer.draw();
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);

const TV = (window.TV = window.TV || {});
TV.bg = {
  applyScene,
  setWorldTime(wt) { try { skyRenderer.setState(wt); } catch (_) {} },
  moodWeights() { return mood.weights(performance.now()); },
  setPlace(pl) { place.set(pl); },
  contrastReport() { return place.report(); },
};
})();
