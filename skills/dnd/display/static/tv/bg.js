// The scene background for the TV page (display foundation spec D14):
// the old page's gradient crossfade, particles and sky, copied unchanged
// from templates/index.html (lines 4445-5027) as a placeholder until the
// Scene backgrounds spec replaces this layer. Exposes TV.bg.applyScene(scene)
// and TV.bg.setWorldTime(wt).
(function () {
'use strict';
if (typeof document === 'undefined') return;   // loaded by node tests: nothing to draw

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
  indicator.classList.add('changing');
  setTimeout(() => {
    indicator.textContent = '— ' + (activeScene.label || activeScene.name) + ' —';
    indicator.classList.remove('changing');
  }, 400);

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

  // Switch particle type
  particleSystem.switchType(activeScene.particles, activeScene.accent);
  // no audio hook on scene change — ambient removed
}

// Initialise background to default scene
;(function initBg() {
  const el = document.getElementById('bg-' + bgActive);
  const [top, bot] = SCENE_DEFAULTS.colors;
  el.style.background =
    `radial-gradient(ellipse at 50% 30%, ${top} 0%, ${bot} 100%)`;
})();


// ═══════════════════════════════════════════════════════════════════
// PARTICLE SYSTEM
// ═══════════════════════════════════════════════════════════════════

const canvas  = document.getElementById('particles');
const ctx     = canvas.getContext('2d');
let W = 0, H = 0;

function resizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  W = window.innerWidth;
  H = window.innerHeight;
  canvas.width  = W * dpr;
  canvas.height = H * dpr;
  canvas.style.width  = W + 'px';
  canvas.style.height = H + 'px';
  ctx.scale(dpr, dpr);
}
window.addEventListener('resize', resizeCanvas);
resizeCanvas();

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
  constructor(type, accent) {
    this.type   = type;
    this.accent = accent;
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
    } else {
      // fallback = dust
      this.vx = 0; this.vy = 0;
      this.size = 1;
      this.maxLife = 300;
      this._color = '#808080';
    }
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
    }

    return this.life < this.maxLife;
  }

  draw(ctx) {
    const T = this.type;
    const [r,g,b] = hexToRgb(this._color);
    ctx.globalAlpha = Math.max(0, Math.min(1, this.alpha));

    if (T === 'smoke') {
      const grad = ctx.createRadialGradient(
        this.x, this.y, 0, this.x, this.y, this.size);
      grad.addColorStop(0, `rgba(${r},${g},${b},0.3)`);
      grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
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

// ── Particle system controller ────────────────────────────────────

const PARTICLE_COUNT = {
  embers: 80, fireflies: 45, smoke: 20, dust: 90,
  snow: 110, stars: 160, bubbles: 45, sparks: 50,
  sand: 80, rain: 130, drips: 35,
  mist: 12, leaves: 55, ripples: 18,
};

const particleSystem = {
  particles: [],
  type: 'embers',
  accent: SCENE_DEFAULTS.accent,
  transitioning: false,

  init() {
    this.populate(this.type, this.accent);
  },

  populate(type, accent) {
    const count = PARTICLE_COUNT[type] || 60;
    this.particles = [];
    for (let i = 0; i < count; i++) {
      const p = new Particle(type, accent);
      p.life = randInt(0, p.maxLife);  // stagger so they don't all start together
      this.particles.push(p);
    }
  },

  switchType(newType, accent) {
    if (newType === this.type) return;
    this.type   = newType;
    this.accent = accent || '#ffffff';
    // Fade out current by letting them die, then repopulate
    // Mark old particles so they run out fast
    this.particles.forEach(p => { p.life = Math.max(p.life, p.maxLife - 30); });
    // Add new particles over ~2s
    let added = 0;
    const target = PARTICLE_COUNT[newType] || 60;
    const interval = setInterval(() => {
      if (added >= target) { clearInterval(interval); return; }
      const p = new Particle(newType, this.accent);
      this.particles.push(p);
      added++;
    }, 2000 / target);
  },

  update() {
    this.particles = this.particles.filter(p => p.update());
    // Replenish
    const target = PARTICLE_COUNT[this.type] || 60;
    while (this.particles.length < target) {
      const p = new Particle(this.type, this.accent);
      this.particles.push(p);
    }
  },

  draw() {
    ctx.clearRect(0, 0, W, H);
    this.particles.forEach(p => p.draw(ctx));
  },
};

particleSystem.init();

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

// ── Animation loop ────────────────────────────────────────────────

function loop() {
  particleSystem.update();
  particleSystem.draw();
  skyRenderer.draw();
  requestAnimationFrame(loop);
}
loop();

const TV = (window.TV = window.TV || {});
TV.bg = {
  applyScene,
  setWorldTime(wt) { try { skyRenderer.setState(wt); } catch (_) {} },
};
})();
