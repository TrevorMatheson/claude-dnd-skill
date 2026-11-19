// Page facts the server puts in the TV page's meta tags (token, voice state).
// Loaded first; every other TV script attaches to window.TV.
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
