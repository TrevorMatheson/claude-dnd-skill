// Which layout the TV shows (display foundation spec D5–D7).
//
// resolve(config, stats, override) is pure so node can test it; apply()
// puts its result on the page. The saved settings are validated again here
// with the same rules as display_config.py, so a hand-edited display.json
// can't break the TV. Combat modes arrive in S8.
(function (root) {
  'use strict';

  const MODULES = ['party', 'turns', 'map', 'quests', 'clock'];
  const MAX_PER_PANEL = 3;
  const PRESETS = {
    story:    { left: ['party'], right: ['quests', 'clock'] },
    tactical: { left: ['party'], right: ['turns', 'map'] },
    minimal:  { left: [],        right: [] },
  };

  function cleanPanel(value, used) {
    const out = [];
    if (!Array.isArray(value)) return out;
    for (const id of value) {
      if (typeof id === 'string' && MODULES.includes(id) && !used.has(id)) {
        used.add(id);
        out.push(id);
      }
      if (out.length === MAX_PER_PANEL) break;
    }
    return out;
  }

  // The panels the saved settings ask for: a missing or broken config is
  // the Story preset (D6); a preset name alone gives that preset's stacks.
  function panelsFrom(config) {
    const layout = config && typeof config === 'object' && config.layout && typeof config.layout === 'object'
      ? config.layout : null;
    if (!layout || !Object.keys(layout).length) {
      return { left: PRESETS.story.left.slice(), right: PRESETS.story.right.slice() };
    }
    if (!('left' in layout) && !('right' in layout)) {
      const p = PRESETS[layout.preset] || PRESETS.story;
      return { left: p.left.slice(), right: p.right.slice() };
    }
    const used = new Set();
    const left = cleanPanel(layout.left, used);
    const right = cleanPanel(layout.right, used);
    return { left, right };
  }

  // eslint-disable-next-line no-unused-vars
  function resolve(config, stats, override) {
    const { left, right } = panelsFrom(config);
    const mode = left.length || right.length ? 'story' : 'minimal';
    return { mode, left, right };
  }

  // Put a resolved layout on the stage: data-mode drives the grid in tv.css.
  function apply(stage, resolved) {
    if (!stage || !resolved) return;
    stage.dataset.mode = resolved.mode;
    stage.dataset.left = resolved.left.length ? 'on' : 'off';
    stage.dataset.right = resolved.right.length ? 'on' : 'off';
  }

  const api = { MODULES, PRESETS, resolve, apply };
  root.TV = root.TV || {};
  root.TV.layout = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
