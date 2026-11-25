// Every write the player page makes (phone sheet spec D14, AC11).
//
// One attempt per request. A request that can't reach the server, or that
// the server fails on (5xx), becomes a "Not sent" item with Retry and
// Dismiss. Nothing is ever queued or resent by itself: only the player's
// Retry sends it again, with the same `op` id, so a request that did arrive
// (and only its reply was lost) is not applied twice.
//
// makeNet(opts) has no DOM so node can test it; start() draws the list.
(function (root) {
  'use strict';

  let seq = 0;
  function newOp(kind) {
    const rand = Math.random().toString(36).slice(2, 10);
    return `ph-${kind}-${Date.now().toString(36)}-${(++seq).toString(36)}-${rand}`;
  }

  // opts: fetch, headers() → object, onChange(items), onPending(kind)
  function makeNet(opts) {
    opts = opts || {};
    const doFetch = opts.fetch || ((u, o) => root.fetch(u, o));
    const headers = opts.headers || (() => ({ 'Content-Type': 'application/json' }));
    const onChange = opts.onChange || (() => {});
    const items = [];          // the Not sent list, oldest first
    let nextId = 1;

    function changed() { onChange(items.slice()); }

    // Send once. Resolves {ok, status, body, item?}: ok is true for 2xx,
    // status 202 means the device waits for approval; a 4xx comes back as
    // the server's answer (not a Not sent item: retrying wouldn't help).
    function attempt(entry) {
      const body = Object.assign({}, entry.body, { op: entry.op });
      return Promise.resolve()
        .then(() => doFetch(entry.url, { method: 'POST', headers: headers(), body: JSON.stringify(body) }))
        .then((r) => r.text().then((t) => {
          let j = {};
          try { j = t ? JSON.parse(t) : {}; } catch (_) { j = { text: t }; }
          return { status: r.status, body: j };
        }))
        .then((res) => {
          if (res.status >= 500) throw Object.assign(new Error('server error ' + res.status), { res });
          return { ok: res.status >= 200 && res.status < 300, status: res.status, body: res.body };
        });
    }

    function fail(entry, err) {
      entry.error = (err && err.res && err.res.body && err.res.body.reason)
        || (err && err.message && /server error/.test(err.message) ? 'The game had a problem' : 'No connection');
      if (!items.includes(entry)) items.push(entry);
      changed();
      return { ok: false, status: 0, item: entry, notSent: true };
    }

    // kind: 'roll' | 'action' | 'edit' | 'move'; label: what the player sees.
    function send(kind, url, body, label) {
      const entry = { id: nextId++, kind, url, body: body || {}, op: newOp(kind), label: label || kind };
      return attempt(entry).catch((err) => fail(entry, err));
    }

    function retry(id) {
      const entry = items.find((x) => x.id === id);
      if (!entry) return Promise.resolve({ ok: false, status: 0 });
      entry.retrying = true;
      changed();
      return attempt(entry).then((res) => {
        const i = items.indexOf(entry);
        if (i >= 0) items.splice(i, 1);
        changed();
        if (entry.onDone) entry.onDone(res);
        return res;
      }).catch((err) => { entry.retrying = false; return fail(entry, err); });
    }

    function dismiss(id) {
      const i = items.findIndex((x) => x.id === id);
      if (i >= 0) { items.splice(i, 1); changed(); }
    }

    return { send, retry, dismiss, items: () => items.slice(), newOp };
  }

  // ── Browser wiring ───────────────────────────────────────────────────────
  let net = null;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function draw(items) {
    const el = root.document.getElementById('unsent');
    if (!el) return;
    el.hidden = !items.length;
    el.innerHTML = items.map((x) => `<div class="unsent-item" data-id="${x.id}">
      <span class="unsent-tag">Not sent</span>
      <span class="unsent-label">${esc(x.label)}</span>
      <span class="unsent-why">${esc(x.error || '')}</span>
      <button type="button" class="unsent-retry" data-retry="${x.id}"${x.retrying ? ' disabled' : ''}>${x.retrying ? 'Sending…' : 'Retry'}</button>
      <button type="button" class="unsent-x" data-dismiss="${x.id}" aria-label="Dismiss">✕</button></div>`).join('');
  }

  function start(app) {
    net = makeNet({ headers: () => app.headers(), onChange: draw });
    api.send = net.send;
    api.retry = net.retry;
    api.dismiss = net.dismiss;
    api.items = net.items;
    const el = root.document.getElementById('unsent');
    if (el) {
      el.addEventListener('click', (ev) => {
        const r = ev.target.closest('[data-retry]');
        if (r) { net.retry(Number(r.dataset.retry)); return; }
        const d = ev.target.closest('[data-dismiss]');
        if (d) net.dismiss(Number(d.dataset.dismiss));
      });
    }
  }

  const api = { makeNet, newOp, start, send: null, retry: null, dismiss: null, items: () => [] };
  if (root) { root.PH = root.PH || {}; root.PH.net = api; }
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : null);
