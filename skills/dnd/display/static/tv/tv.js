// TV page startup: open the update stream and hand each payload to the parts
// of the page.
(function () {
  'use strict';
  const TV = window.TV;
  const token = TV.page.token;
  const stage = document.getElementById('tv-stage');

  // Voice (S9): one audio timeline that the feed types in step with. The TV
  // shows no voice-state or quota notices (D17); with no voice it types
  // text only. Narration asks for the local engine only (`local_only`): one
  // Gemini request per sentence would use up its daily quota in a turn, so
  // with Kokoro down the server refuses and the narration types unvoiced.
  let voiceOn = TV.page.voiceOn;
  const audio = TV.voice.makeWebAudio();
  const tapBtn = document.getElementById('tv-tap-audio');
  tapBtn.addEventListener('click', () => {
    audio.unlock().then(() => { tapBtn.hidden = true; });
  });
  const ttsHeaders = { 'Content-Type': 'application/json' };
  if (token) ttsHeaders['X-DND-Token'] = token;
  let feed = null;
  const voice = TV.voice.makeVoice({
    fetchTts(body, info) {
      if (!voiceOn) return Promise.reject(new Error('voice off'));
      const req = info && info.kind === 'narration' ? Object.assign({ local_only: true }, body) : body;
      return fetch('/tts', { method: 'POST', headers: ttsHeaders, body: JSON.stringify(req) })
        .then((r) => { if (!r.ok) throw new Error('TTS ' + r.status); return r.arrayBuffer(); })
        .then((buf) => audio.decode(buf));
    },
    play: (clip, opts) => audio.play(clip, opts),
    stop: () => audio.stop(),
    type: (seg) => (feed ? feed.type(seg) : undefined),
    isUnlocked: () => audio.isUnlocked(),
    onLocked() {
      if (!voiceOn) return;
      tapBtn.hidden = false;
      // Some browsers allow audio without a tap; then the button goes at once.
      audio.unlock().then(() => { if (audio.isUnlocked()) tapBtn.hidden = true; });
    },
    canVoice: () => voiceOn,
    // ▶ / ■ on the replayed block (D25).
    onReplayChange: () => { if (feed) feed.render(); },
  });
  const showVoiceState = () => { stage.dataset.voice = voiceOn ? 'on' : 'off'; };
  showVoiceState();

  feed = TV.feed.mount(document.getElementById('tv-feed'), TV.feed.makeFeedModel(), {
    token, voice,
    unlockAudio: () => audio.unlock().then(() => { if (audio.isUnlocked()) tapBtn.hidden = true; }),
  });
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
  // Battle-map effects redraw the map as they play; a tab that comes back
  // from the background jumps to the current state, no backlog (D17).
  if (TV.map && TV.map.setOnChange) TV.map.setOnChange(() => redraw());
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && TV.map && TV.map.flushAll) { TV.map.flushAll(); redraw(); }
  });

  // Timed effects count down; refresh them while nothing else changes.
  setInterval(redraw, 30000);
  // The map's cell size follows its box; redraw it when the window changes.
  let resizeTimer = null;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(redraw, 100); });

  function onPayload(p) {
    if (!p || typeof p !== 'object') return;
    // Rewind, clear or a (re)connect: the map shows now, nothing replays.
    if ((p.rewind || p.clear || p.hello) && TV.map && TV.map.flushAll) {
      try { TV.map.flushAll(); } catch (err) { console.error('tv: map flush failed', err); }
    }
    feed.apply(p);
    devices.apply(p);
    let changed = false;
    // The server's voice state ({on, reason}); a block's own `voice` is a string.
    if (p.voice && typeof p.voice === 'object') {
      voiceOn = !!p.voice.on;
      showVoiceState();
      // Voice off mid-burst: stop talking but show every block, as text.
      if (!voiceOn) { feed.settle(); tapBtn.hidden = true; }
    }
    if (Array.isArray(p.read_actions)) voice.readActions(p.read_actions);
    if (p.display && typeof p.display === 'object') {
      config = p.display.config || null;
      if (config && config.voice) voice.setSettings(config.voice);
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
