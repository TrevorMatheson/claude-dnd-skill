// TV page startup: open the update stream and hand each payload to the parts
// of the page. Panels, layout and voice join in later steps of the plan.
(function () {
  'use strict';
  const TV = window.TV;
  const token = TV.page.token;

  const feed = TV.feed.mount(document.getElementById('tv-feed'), TV.feed.makeFeedModel(), { token });
  const devices = TV.devices.mount(document.getElementById('tv-devices'), TV.devices.makeDeviceModel(), { token });
  const sceneLabel = document.getElementById('scene-indicator');

  function onPayload(p) {
    if (!p || typeof p !== 'object') return;
    feed.apply(p);
    devices.apply(p);
    if (p.scene && TV.bg) {
      try { TV.bg.applyScene(p.scene); } catch (err) { console.error('tv: scene failed', err); }
    }
    if (p.stats && p.stats.world_time && TV.bg) TV.bg.setWorldTime(p.stats.world_time);
    if (p.rewind && p.rewind.stats && p.rewind.stats.world_time && TV.bg) TV.bg.setWorldTime(p.rewind.stats.world_time);
    if (p.scene && sceneLabel && !TV.bg) sceneLabel.textContent = `— ${p.scene.label || p.scene.name || ''} —`;
  }

  TV.stream.connect('/stream', onPayload, document.getElementById('tv-reconnect'));
})();
