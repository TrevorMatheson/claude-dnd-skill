// The player page's start-up (phone sheet spec D1–D4).
//
// Binds the page to one character (?char=, or the picker), picks the layout,
// draws the tabs, keeps the update stream open and hands every payload to the
// modules that registered with PH.app.on(). Modules (play, sheet, talk, map)
// attach themselves to window.PH and are started from here.
(function (root) {
  'use strict';
  if (!root || !root.document) return;
  const doc = root.document;
  const PH = root.PH = root.PH || {};
  const L = PH.layout;

  const $ = (id) => doc.getElementById(id);
  const store = {
    get(k) { try { return root.localStorage.getItem(k); } catch (_) { return null; } },
    set(k, v) { try { root.localStorage.setItem(k, v); } catch (_) {} },
    del(k) { try { root.localStorage.removeItem(k); } catch (_) {} },
  };

  function meta(name) {
    const m = doc.querySelector(`meta[name="${name}"]`);
    return m ? m.getAttribute('content') || '' : '';
  }

  function uuid() {
    if (root.crypto && typeof root.crypto.randomUUID === 'function') return root.crypto.randomUUID();
    return 'd' + Date.now().toString(16) + Math.random().toString(16).slice(2);
  }

  // Same device id key as the old page, so an approved phone stays approved.
  let deviceId = store.get('dnd_device_id');
  if (!deviceId) { deviceId = uuid(); store.set('dnd_device_id', deviceId); }
  const token = meta('dnd-token');

  function headers(json) {
    const h = {};
    if (json !== false) h['Content-Type'] = 'application/json';
    if (token) h['X-DND-Token'] = token;
    if (deviceId) h['X-DND-Device'] = deviceId;
    return h;
  }

  function boundChar() {
    const qp = new URLSearchParams(root.location.search);
    return (qp.get('char') || qp.get('character') || '').trim().slice(0, 50);
  }

  // ── Shared state and a tiny event hub for the modules ──────────────────
  const listeners = [];
  const app = {
    me: boundChar(),
    deviceId,
    headers,
    stats: {},
    caps: { check: meta('map-check') === '1', board: meta('map-board') === '1' },
    layout: 'phone',
    tab: 'play',
    on(fn) { listeners.push(fn); },
    setTab,
    store,
    // True when a name is this phone's character (case-insensitive).
    isMe(name) { return !!name && !!app.me && String(name).toLowerCase() === app.me.toLowerCase(); },
  };
  PH.app = app;

  // ── Layout and tabs ────────────────────────────────────────────────────
  function coarse() {
    try { return root.matchMedia('(pointer: coarse)').matches; } catch (_) { return false; }
  }

  function applyLayout() {
    app.layout = L.chooseLayout({ width: root.innerWidth, coarse: coarse(), saved: store.get(L.LAYOUT_KEY) });
    doc.body.dataset.layout = app.layout;
    $('layout-switch').textContent = app.layout === 'phone' ? 'Desktop version' : 'Phone version';
    drawTabs();
  }

  function drawTabs() {
    const tabs = L.tabsFor(app.layout, app.stats, app.caps);
    doc.querySelectorAll('#tabbar .tab').forEach((b) => {
      b.hidden = !tabs.includes(b.dataset.tab);
      b.classList.toggle('on', b.dataset.tab === app.tab);
      b.setAttribute('aria-pressed', b.dataset.tab === app.tab ? 'true' : 'false');
    });
    // The Map pane exists in both layouts only while the Map tab is allowed.
    $('pane-map').hidden = !tabs.includes('map');
    if (!tabs.includes(app.tab)) setTab('play');
    doc.body.dataset.tab = app.tab;
  }

  function setTab(name) {
    const tabs = L.tabsFor(app.layout, app.stats, app.caps);
    app.tab = tabs.includes(name) ? name : 'play';
    doc.body.dataset.tab = app.tab;
    doc.querySelectorAll('#tabbar .tab').forEach((b) => {
      b.classList.toggle('on', b.dataset.tab === app.tab);
      b.setAttribute('aria-pressed', b.dataset.tab === app.tab ? 'true' : 'false');
    });
    emit({ _tab: app.tab });
  }

  $('layout-switch').addEventListener('click', () => {
    store.set(L.LAYOUT_KEY, app.layout === 'phone' ? 'desktop' : 'phone');
    applyLayout();
  });
  doc.querySelectorAll('#tabbar .tab').forEach((b) => {
    b.addEventListener('click', () => setTab(b.dataset.tab));
  });
  let resizeTimer = null;
  root.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(applyLayout, 150);
  });

  // ── Character picker ───────────────────────────────────────────────────
  function drawPicker() {
    const list = $('picker-list');
    const names = (app.stats.players || []).map((p) => p && p.name).filter(Boolean);
    if (!names.length) { list.innerHTML = '<p class="muted">Loading party…</p>'; return; }
    list.innerHTML = '';
    names.forEach((name) => {
      const b = doc.createElement('button');
      b.type = 'button';
      b.className = 'picker-opt';
      b.textContent = name;
      b.addEventListener('click', () => {
        const url = new URL(root.location.href);
        url.searchParams.set('char', name);
        root.location.href = url.toString();
      });
      list.appendChild(b);
    });
  }

  // ── Stream ─────────────────────────────────────────────────────────────
  function emit(p) {
    listeners.forEach((fn) => { try { fn(p); } catch (err) { console.error('phone: module failed', err); } });
  }

  function onPayload(p) {
    if (!p || typeof p !== 'object') return;
    if (p.stats && typeof p.stats === 'object') {
      app.stats = Object.assign({}, app.stats, p.stats);
      drawTabs();
      if (!app.me) drawPicker();
    }
    emit(p);
  }

  function start() {
    $('me-name').textContent = app.me || 'Choose a character';
    doc.title = app.me ? `${app.me} — DnD` : 'DnD — Player';
    if (!app.me) {
      $('char-picker').hidden = false;
      $('panes').hidden = true;
      $('tabbar').hidden = true;
    }
    applyLayout();
    // Modules start in the order they were loaded.
    ['net', 'play', 'talk', 'sheet', 'maptab'].forEach((m) => {
      if (PH[m] && typeof PH[m].start === 'function' && app.me) {
        try { PH[m].start(app); } catch (err) { console.error('phone: start failed', m, err); }
      }
    });
    const q = app.me ? `?character=${encodeURIComponent(app.me)}` : '';
    root.TV.stream.connect(`/stream${q}`, onPayload, $('conn'));
  }

  start();
})(typeof window !== 'undefined' ? window : null);
