// Phone-approval cards on the TV (display foundation spec D24): a new phone
// asking to join shows a card with its address, Approve and Deny.
//
// makeDeviceModel() has no DOM so node can test it; mount() draws it.
(function (root) {
  'use strict';

  function makeDeviceModel() {
    let cards = new Map();   // id -> {id, ip}

    function apply(p) {
      if (!p || typeof p !== 'object') return;
      // The server replays every still-pending phone after the hello, so a
      // phone answered while this TV was away doesn't keep its card.
      if (p.hello) { cards = new Map(); return; }
      const req = p.device_request;
      if (req && typeof req === 'object' && req.id != null && !cards.has(String(req.id))) {
        cards.set(String(req.id), { id: String(req.id), ip: String(req.ip || '') });
      }
      if (p.device_approved != null) cards.delete(String(p.device_approved));
      if (p.device_denied != null) cards.delete(String(p.device_denied));
    }

    return {
      apply,
      cards() { return [...cards.values()].map((c) => Object.assign({}, c)); },
      // The request to send for an answer; the card goes at once.
      respond(id, approve) {
        cards.delete(String(id));
        return { url: approve ? '/device/approve' : '/device/deny', body: { id: String(id) } };
      },
    };
  }

  function mount(el, model, opts) {
    opts = opts || {};
    const headers = { 'Content-Type': 'application/json' };
    if (opts.token) headers['X-DND-Token'] = opts.token;

    function render() {
      const frag = document.createDocumentFragment();
      for (const c of model.cards()) {
        const card = document.createElement('div');
        card.className = 'device-card';
        card.dataset.deviceId = c.id;
        const label = document.createElement('span');
        label.className = 'device-card-label';
        label.textContent = 'A new phone wants to join';
        const ip = document.createElement('span');
        ip.className = 'device-card-ip';
        ip.textContent = c.ip;
        card.append(label, ip);
        for (const [text, approve] of [['Approve', true], ['Deny', false]]) {
          const b = document.createElement('button');
          b.type = 'button';
          b.className = approve ? 'device-approve-btn' : 'device-deny-btn';
          b.textContent = text;
          b.addEventListener('click', () => {
            const r = model.respond(c.id, approve);
            render();
            fetch(r.url, { method: 'POST', headers, body: JSON.stringify(r.body) }).catch(() => {});
          });
          card.appendChild(b);
        }
        frag.appendChild(card);
      }
      el.replaceChildren(frag);
      el.hidden = model.cards().length === 0;
    }

    render();
    return { apply(p) { model.apply(p); render(); }, render };
  }

  const api = { makeDeviceModel, mount };
  if (root) { root.TV = root.TV || {}; root.TV.devices = api; }
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : null);
