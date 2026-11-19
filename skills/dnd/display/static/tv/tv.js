// TV page startup. Each part of the page attaches to window.TV; the feed,
// panels, layout and voice arrive in later steps of the build plan.
(function () {
  'use strict';
  const TV = (window.TV = window.TV || {});

  function meta(name) {
    const el = document.querySelector(`meta[name="${name}"]`);
    return el ? el.content : '';
  }

  TV.page = {
    token: meta('dnd-token'),
    narratorVoice: meta('narrator-voice'),
    voiceOn: meta('voice-on') === '1',
    voiceLocal: meta('voice-local') === '1',
    voiceReason: meta('voice-reason'),
  };
})();
