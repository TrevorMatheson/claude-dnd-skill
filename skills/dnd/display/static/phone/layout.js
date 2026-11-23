// Which layout and tabs the player page shows (phone sheet spec D2, D3, D4).
//
// Pure functions, no DOM, so node can test them; phone.js applies them.
// - chooseLayout: a narrow touch screen gets the phone layout, anything else
//   the desktop one; the device's saved "Desktop / Phone version" choice wins.
// - combatOn: the same rule as the server's _combat_on.
// - tabsFor: Play and Sheet always; Map only in combat, with a map, and only
//   when the battle map's dry run and board are on this install.
(function (root) {
  'use strict';

  const LAYOUT_KEY = 'dnd-phone-layout';
  const PHONE_MAX_WIDTH = 900;   // below this, a touch screen is a phone
  const NARROW_WIDTH = 700;      // below this, two columns don't fit with any pointer

  function chooseLayout(o) {
    o = o || {};
    if (o.saved === 'phone' || o.saved === 'desktop') return o.saved;
    const w = Number(o.width) || 0;
    if (w > 0 && w < NARROW_WIDTH) return 'phone';
    return o.coarse && w < PHONE_MAX_WIDTH ? 'phone' : 'desktop';
  }

  function combatOn(stats) {
    const to = stats && stats.turn_order;
    return !!(to && typeof to === 'object' && !Array.isArray(to)
      && Array.isArray(to.order) && to.order.length > 0);
  }

  function mapAllowed(stats, caps) {
    return combatOn(stats) && !!(stats && stats.map)
      && !!(caps && caps.check && caps.board);
  }

  function tabsFor(layout, stats, caps) {
    const tabs = ['play', 'sheet'];
    if (mapAllowed(stats, caps)) tabs.push('map');
    return tabs;
  }

  const api = { LAYOUT_KEY, PHONE_MAX_WIDTH, NARROW_WIDTH, chooseLayout, combatOn, mapAllowed, tabsFor };
  if (root) { root.PH = root.PH || {}; root.PH.layout = api; }
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : null);
