// The laptop page's Scene pictures section (scene backgrounds spec D4, D6,
// D7, D17): picture generation on or off, the campaign's art style, and the
// note when pictures can't be made (generation off, no key, quota used up,
// an error). The note is shown here only, never on the TV.
//
// Settings go to POST /display-config as a partial {"scenes": {...}}.
(function (root) {
  'use strict';

  function mount(section) {
    const token = (document.querySelector('meta[name="dnd-token"]') || {}).content || '';
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['X-DND-Token'] = token;
    const status = document.getElementById('lp-status');
    const $ = (id) => section.querySelector('#' + id);
    const gen = $('lp-scene-generate'), style = $('lp-scene-style'), note = $('lp-scene-note');
    let busy = 0, drawn = '';

    function say(text, kind) {
      if (!status) return;
      status.textContent = text;
      status.dataset.kind = kind || '';
    }

    function save(patch) {
      busy++;
      say('Saving…');
      fetch('/display-config', { method: 'POST', headers, body: JSON.stringify({ scenes: patch }) })
        .then((r) => r.json().catch(() => ({})).then((j) => {
          if (!r.ok) throw new Error(j.error || `The server said ${r.status}.`);
          say('Saved.', 'ok');
        }))
        .catch((err) => say(err.message || 'Could not save.', 'error'))
        .finally(() => { busy--; load(); });
    }

    function renderNote(text) {
      note.textContent = text || '';
      note.hidden = !text;
    }

    function render(scenes, styles) {
      const sig = JSON.stringify([scenes, styles]);
      if (sig === drawn) return;
      if (document.activeElement === style) return;   // never close an open menu
      drawn = sig;
      gen.checked = !!scenes.generate;
      style.textContent = '';
      for (const s of styles) {
        const o = document.createElement('option');
        o.value = s.id;
        o.textContent = s.label;
        if (s.prompt) o.title = s.prompt;
        style.appendChild(o);
      }
      style.value = scenes.style;
      style.disabled = styles.length < 2;
    }

    gen.addEventListener('change', () => save({ generate: gen.checked }));
    style.addEventListener('change', () => save({ style: style.value }));

    function load() {
      if (busy) return Promise.resolve();
      return fetch('/display-config', { headers }).then((r) => r.json()).then((cfg) => {
        if (busy) return;
        const scenes = (cfg.config && cfg.config.scenes) || { generate: true, style: '' };
        const styles = Array.isArray(cfg.scene_styles) ? cfg.scene_styles : [];
        render(scenes, styles);
        renderNote(cfg.status && cfg.status.scene_note);
      }).catch(() => { /* layout.js reports the server being away */ });
    }
    load();
    setInterval(load, 5000);
  }

  const api = { mount };
  root.LAYOUT_SCENES = api;
  if (typeof module !== 'undefined') module.exports = api;
  if (typeof document !== 'undefined' && document.getElementById('lp-scenes')) {
    mount(document.getElementById('lp-scenes'));
  }
})(typeof window !== 'undefined' ? window : globalThis);
