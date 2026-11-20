// The laptop's layout page (display foundation spec D6, AC4, AC6, AC7):
// pick a preset or fill each TV side panel with 1–3 modules, and force the
// TV into combat or story. Every change is POSTed at once and the TV follows.
//
// The config edits are pure functions (no DOM) so node can test them; they
// return a new config and leave voice and version alone, because
// POST /display-config replaces the whole saved config. mount() is the page.
(function (root) {
  'use strict';

  const MODULES = ['party', 'turns', 'map', 'quests', 'clock'];
  const MAX_PER_PANEL = 3;
  const PRESETS = {
    story:    { left: ['party'], right: ['quests', 'clock'] },
    tactical: { left: ['party'], right: ['turns', 'map'] },
    minimal:  { left: [],        right: [] },
  };
  const SIDES = ['left', 'right'];

  const same = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);

  // No or junk layout is the Story preset, as everywhere else (D6).
  function presetOf(layout) {
    const l = layout && Array.isArray(layout.left) ? layout.left : null;
    const r = layout && Array.isArray(layout.right) ? layout.right : null;
    if (!l || !r) return 'story';
    for (const name of Object.keys(PRESETS)) {
      if (same(PRESETS[name].left, l) && same(PRESETS[name].right, r)) return name;
    }
    return 'custom';
  }

  // A copy of `config` with a usable layout: no or junk layout is Story.
  function clone(config) {
    const c = config && typeof config === 'object' ? JSON.parse(JSON.stringify(config)) : {};
    const lay = c.layout && typeof c.layout === 'object' ? c.layout : null;
    const ok = (v) => Array.isArray(v) && v.every((id) => MODULES.includes(id));
    if (!lay || !ok(lay.left) || !ok(lay.right)) {
      c.layout = { preset: 'story', left: PRESETS.story.left.slice(), right: PRESETS.story.right.slice() };
    }
    return c;
  }

  function finish(c) {
    c.layout.preset = presetOf(c.layout);
    return c;
  }

  function presetConfig(config, name) {
    const c = clone(config);
    if (!Object.prototype.hasOwnProperty.call(PRESETS, name)) return c;
    c.layout = { preset: name, left: PRESETS[name].left.slice(), right: PRESETS[name].right.slice() };
    return c;
  }

  function addModule(config, side, id) {
    const c = clone(config);
    if (!SIDES.includes(side) || !MODULES.includes(id)) return c;
    const panel = c.layout[side];
    if (panel.includes(id) || panel.length >= MAX_PER_PANEL) return c;
    const other = side === 'left' ? 'right' : 'left';
    c.layout[other] = c.layout[other].filter((x) => x !== id);
    panel.push(id);
    return finish(c);
  }

  function removeModule(config, side, id) {
    const c = clone(config);
    if (!SIDES.includes(side)) return c;
    c.layout[side] = c.layout[side].filter((x) => x !== id);
    return finish(c);
  }

  function moveModule(config, side, id, delta) {
    const c = clone(config);
    if (!SIDES.includes(side)) return c;
    const panel = c.layout[side];
    const i = panel.indexOf(id);
    if (i < 0) return c;
    const j = Math.max(0, Math.min(panel.length - 1, i + (Number(delta) || 0)));
    panel.splice(i, 1);
    panel.splice(j, 0, id);
    return finish(c);
  }

  function overrideValue(choice) {
    return choice === 'combat' ? true : choice === 'story' ? false : null;
  }

  function overrideChoice(value) {
    return value === true ? 'combat' : value === false ? 'story' : 'auto';
  }

  // ── The page ────────────────────────────────────────────────────────────

  const LABELS = { party: 'Party', turns: 'Turn order', map: 'Battle map', quests: 'Quests', clock: 'World clock & effects' };
  const PRESET_NOTES = {
    story: 'Party on the left; quests and the world clock on the right.',
    tactical: 'Party on the left; turn order and the battle map on the right.',
    minimal: 'Narration only, no panels.',
  };

  function mount(page) {
    const token = (document.querySelector('meta[name="dnd-token"]') || {}).content || '';
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['X-DND-Token'] = token;
    const status = page.querySelector('#lp-status');
    let config = null, override = null, busy = 0, offline = false;

    function say(text, kind) {
      status.textContent = text;
      status.dataset.kind = kind || '';
    }

    function post(url, body) {
      busy++;
      say('Saving…');
      return fetch(url, { method: 'POST', headers, body: JSON.stringify(body) })
        .then((r) => r.json().catch(() => ({})).then((j) => {
          if (!r.ok) throw new Error(j.error || `The server said ${r.status}.`);
          return j;
        }))
        .finally(() => { busy--; });
    }

    // POST /display-config replaces the whole config, so send this page's
    // layout with the server's current voice settings, read just before —
    // never this page's copy, which may be seconds old.
    function save(next) {
      const before = config;
      config = next;
      render();
      busy++;
      fetch('/display-config', { headers })
        .then((r) => r.json())
        .then((j) => Object.assign({}, j.config, { layout: next.layout }))
        .finally(() => { busy--; })
        .then((body) => post('/display-config', body))
        .then((j) => {
          config = j.config || next;
          say(j.saved ? 'Saved — the TV has it.' : 'The TV has it. No campaign is loaded, so it isn\'t saved.', 'ok');
          render();
        })
        .catch((err) => { config = before; say(err.message, 'error'); render(); });
    }

    function setOverride(choice) {
      const before = override;
      override = overrideValue(choice);
      render();
      post('/display-mode', { combat: override })
        .then((j) => { override = j.mode_override == null ? null : j.mode_override; say('The TV has it.', 'ok'); render(); })
        .catch((err) => { override = before; say(err.message, 'error'); render(); });
    }

    function button(text, opts) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = text;
      if (opts.cls) b.className = opts.cls;
      if (opts.label) { b.title = opts.label; b.setAttribute('aria-label', opts.label); }
      if (opts.pressed !== undefined) b.setAttribute('aria-pressed', String(!!opts.pressed));
      if (opts.disabled) b.disabled = true;
      b.addEventListener('click', opts.onClick);
      return b;
    }

    function renderPresets() {
      const box = page.querySelector('#lp-presets');
      box.replaceChildren();
      const current = config.layout.preset;
      for (const name of Object.keys(PRESETS)) {
        const b = button('', { cls: 'preset', pressed: current === name, onClick: () => save(presetConfig(config, name)) });
        b.innerHTML = `<strong>${name[0].toUpperCase() + name.slice(1)}</strong><small>${PRESET_NOTES[name]}</small>`;
        box.appendChild(b);
      }
      page.querySelector('#lp-custom').hidden = current !== 'custom';
    }

    function renderPanel(side) {
      const box = page.querySelector(`#lp-${side}`);
      const ids = config.layout[side];
      const list = box.querySelector('ol');
      list.replaceChildren();
      ids.forEach((id, i) => {
        const li = document.createElement('li');
        const name = document.createElement('span');
        name.textContent = LABELS[id];
        li.append(name,
          button('↑', { label: `Move ${LABELS[id]} up`, disabled: i === 0, onClick: () => save(moveModule(config, side, id, -1)) }),
          button('↓', { label: `Move ${LABELS[id]} down`, disabled: i === ids.length - 1, onClick: () => save(moveModule(config, side, id, 1)) }),
          button('✕', { label: `Remove ${LABELS[id]}`, onClick: () => save(removeModule(config, side, id)) }));
        list.appendChild(li);
      });
      box.querySelector('.empty').hidden = ids.length > 0;
      const add = box.querySelector('select');
      add.replaceChildren(new Option(ids.length >= MAX_PER_PANEL ? 'Full (3 modules)' : 'Add a module…', ''));
      const other = config.layout[side === 'left' ? 'right' : 'left'];
      for (const id of MODULES) {
        if (ids.includes(id)) continue;
        add.appendChild(new Option(other.includes(id) ? `${LABELS[id]} (move from the other side)` : LABELS[id], id));
      }
      add.disabled = ids.length >= MAX_PER_PANEL;
    }

    function renderOverride() {
      const box = page.querySelector('#lp-override');
      box.replaceChildren();
      const now = overrideChoice(override);
      for (const [choice, text] of [['auto', 'Auto'], ['combat', 'Combat'], ['story', 'Story']]) {
        box.appendChild(button(text, { pressed: now === choice, onClick: () => setOverride(choice) }));
      }
    }

    // Rebuild the controls only when something they show changed, so the
    // 5-second poll doesn't steal keyboard focus or close an open menu.
    let drawn = '';
    function render() {
      if (!config) return;
      const sig = JSON.stringify([config.layout, override]);
      if (sig === drawn) return;
      drawn = sig;
      renderPresets();
      SIDES.forEach(renderPanel);
      renderOverride();
    }

    for (const side of SIDES) {
      page.querySelector(`#lp-${side} select`).addEventListener('change', (ev) => {
        const id = ev.target.value;
        ev.target.value = '';
        if (id) save(addModule(config, side, id));
      });
    }

    // Poll for changes made elsewhere (combat starting clears the override,
    // another laptop tab, a campaign switch). Skipped while a save is out.
    function load() {
      if (busy) return Promise.resolve();
      return fetch('/display-config', { headers })
        .then((r) => r.json())
        .then((j) => {
          if (busy) return;
          config = clone(j.config);
          override = j.mode_override == null ? null : j.mode_override;
          render();
          if (offline) { offline = false; say('Connected again.', 'ok'); }
        })
        .catch(() => { offline = true; say('Can\'t reach the game server. Is the display running?', 'error'); });
    }
    load();
    setInterval(load, 5000);
  }

  const api = { MODULES, PRESETS, MAX_PER_PANEL, presetOf, presetConfig, addModule, removeModule,
                moveModule, overrideValue, overrideChoice, mount };
  root.LAYOUT = api;
  if (typeof module !== 'undefined') module.exports = api;
  if (typeof document !== 'undefined' && document.getElementById('layout-page')) {
    mount(document.getElementById('layout-page'));
  }
})(typeof window !== 'undefined' ? window : globalThis);
