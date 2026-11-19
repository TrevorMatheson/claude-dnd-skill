// The TV's connection to the server's update stream (/stream).
//
// makeSeqFilter() is the catch-up rule (display foundation spec AC12): the
// server opens every stream with {"hello": {"epoch"}}, new on each server
// start, and stamps every log entry with a rising `seq`. On the same epoch a
// reconnecting screen skips entries it already has; on a new epoch it starts
// over. connect() keeps the stream open and shows the "Reconnecting…" badge.
(function (root) {
  'use strict';

  function makeSeqFilter() {
    let epoch = null;
    let seen = new Set();
    return {
      // "first" (no epoch yet), "same" (reconnected to the same server run) or
      // "new" (the server restarted: forget every seq seen before).
      hello(e) {
        if (epoch === null) { epoch = e; seen = new Set(); return 'first'; }
        if (e === epoch) return 'same';
        epoch = e; seen = new Set();
        return 'new';
      },
      // True when the entry hasn't been seen on this epoch (and records it).
      // Entries without a numeric seq are always kept.
      keep(entry) {
        const s = entry && entry.seq;
        if (typeof s !== 'number') return true;
        if (seen.has(s)) return false;
        seen.add(s);
        return true;
      },
    };
  }

  // Open /stream and hand every payload to onPayload. While the connection is
  // down, `badge` is shown (after a 1 s grace, so a blip doesn't flash it) and
  // the stream is retried with backoff up to 3 s.
  function connect(url, onPayload, badge) {
    let es = null;
    let retry = 500;
    let badgeTimer = null;

    const showBadge = (on) => {
      if (!badge) return;
      clearTimeout(badgeTimer);
      if (on) badgeTimer = setTimeout(() => { badge.hidden = false; }, 1000);
      else badge.hidden = true;
    };

    const open = () => {
      es = new EventSource(url);
      es.onopen = () => { retry = 500; showBadge(false); };
      es.onmessage = (ev) => {
        let payload;
        try { payload = JSON.parse(ev.data); } catch (_) { return; }
        try { onPayload(payload); } catch (err) { console.error('tv: payload failed', err); }
      };
      es.onerror = () => {
        showBadge(true);
        try { es.close(); } catch (_) {}
        setTimeout(open, retry);
        retry = Math.min(retry * 2, 3000);
      };
    };
    open();
  }

  const api = { makeSeqFilter, connect };
  if (root) { root.TV = root.TV || {}; root.TV.stream = api; }
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : null);
