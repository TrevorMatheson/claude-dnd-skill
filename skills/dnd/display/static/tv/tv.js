// TV page startup: open the update stream and hand each payload to the parts
// of the page. Voice joins in a later step of the plan.
(function () {
  'use strict';
  const TV = window.TV;
  const token = TV.page.token;
  const stage = document.getElementById('tv-stage');

  const feed = TV.feed.mount(document.getElementById('tv-feed'), TV.feed.makeFeedModel(), { token });
  const devices = TV.devices.mount(document.getElementById('tv-devices'), TV.devices.makeDeviceModel(), { token });
  const sceneLabel = document.getElementById('scene-indicator');

  // Layout and panels (S7): the saved settings arrive as a `display` event on
  // connect and after every change on the laptop, so no reload is needed.
  let config = null, override = null, stats = {};
  const panels = TV.panels.mount({
    left: document.getElementById('tv-side-l'),
    right: document.getElementById('tv-side-r'),
    bar: document.getElementById('tv-inputbar'),
    turnbar: document.getElementById('tv-turnbar'),
    party: document.getElementById('tv-party'),
    map: document.getElementById('tv-map'),
  });
  const wait = TV.panels.makeWaitModel({ onChange: () => redraw() });
  function redraw() {
    try {
      const resolved = TV.layout.resolve(config, stats, override);
      TV.layout.apply(stage, resolved);
      panels.render(resolved, stats, wait);
    } catch (err) { console.error('tv: layout failed', err); }
  }
  // Timed effects count down; refresh them while nothing else changes.
  setInterval(redraw, 30000);
  // The map's cell size follows its box; redraw it when the window changes.
  let resizeTimer = null;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(redraw, 100); });

  function onPayload(p) {
    if (!p || typeof p !== 'object') return;
    feed.apply(p);
    devices.apply(p);
    let changed = false;
    if (p.display && typeof p.display === 'object') {
      config = p.display.config || null;
      override = p.display.mode_override == null ? null : p.display.mode_override;
      changed = true;
    }
    if (p.stats && typeof p.stats === 'object') { stats = p.stats; changed = true; }
    if (p.rewind && p.rewind.stats && typeof p.rewind.stats === 'object') {
      stats = p.rewind.stats;
      wait.apply({ stats });
      changed = true;
    }
    try { wait.apply(p); } catch (err) { console.error('tv: wait failed', err); }
    if (changed) redraw();
    if (p.scene && TV.bg) {
      try { TV.bg.applyScene(p.scene); } catch (err) { console.error('tv: scene failed', err); }
    }
    if (p.stats && p.stats.world_time && TV.bg) TV.bg.setWorldTime(p.stats.world_time);
    if (p.rewind && p.rewind.stats && p.rewind.stats.world_time && TV.bg) TV.bg.setWorldTime(p.rewind.stats.world_time);
    if (p.scene && sceneLabel && !TV.bg) sceneLabel.textContent = `— ${p.scene.label || p.scene.name || ''} —`;
  }

  redraw();
  TV.stream.connect('/stream', onPayload, document.getElementById('tv-reconnect'));
})();
