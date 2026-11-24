// The Sheet tab (phone sheet spec D5): the character sheet, read from the
// character's .md file through GET /sheet/<name>, drawn like a sheet.
//
// sheetView(json) turns the server's JSON into an ordered list of blocks with
// no DOM, so node can test it; render() draws them. A section the server
// couldn't read comes back in `plain` and is drawn as plain text, never left
// out. The sheet reloads on a `sheet` event for this character, on `rewind`
// and on every reconnect.
(function (root) {
  'use strict';

  const ABILS = ['STR', 'DEX', 'CON', 'INT', 'WIS', 'CHA'];
  const list = (v) => (Array.isArray(v) ? v : []);
  const sign = (n) => (n == null || n === '' || isNaN(n) ? '—' : (Number(n) >= 0 ? '+' : '−') + Math.abs(Number(n)));

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // Slot dots: one per slot, `used` of them spent.
  function slotDots(slot) {
    const total = Math.max(0, Number(slot && slot.total) || 0);
    const used = Math.max(0, Math.min(total, Number(slot && slot.used) || 0));
    const dots = [];
    for (let i = 0; i < total; i++) dots.push(i < total - used);   // true = still available
    return { level: slot.level, label: slot.label || '', total, used, left: total - used, dots };
  }

  // The blocks the Sheet tab draws, in order. Each block has a `kind` (the
  // section key, or 'plain'), a `title`, and its data. `editable` marks the
  // D7 sections a player may change by hand.
  function sheetView(json) {
    const s = (json && json.sheet) || {};
    const editable = new Set(list(json && json.editable));
    const blocks = [];
    const push = (kind, title, data) => blocks.push({ kind, title, data, editable: editable.has(kind) });

    const id = s.identity || {};
    push('identity', s.name || '', {
      line: [id.race, id.class ? id.class + (id.level ? ' ' + id.level : '') : (id.level ? 'Level ' + id.level : ''), id.background]
        .filter(Boolean).join(' · '),
      fields: id,
      player: s.player || '',
    });
    if (s.combat) push('combat', 'Combat', s.combat);
    if (s.abilities) {
      push('abilities', 'Abilities', ABILS.filter((k) => s.abilities[k]).map((k) => ({
        key: k, score: s.abilities[k].score, mod: s.abilities[k].mod,
      })));
    }
    if (s.saves) {
      push('saves', 'Saving throws', ABILS.filter((k) => s.saves[k]).map((k) => ({
        key: k, bonus: s.saves[k].bonus, proficient: !!s.saves[k].proficient,
      })));
    }
    if (s.skills) push('skills', 'Skills', list(s.skills));
    if (Array.isArray(s.attacks)) push('attacks', 'Attacks', list(s.attacks));
    if (Array.isArray(s.slots)) push('slots', 'Spell slots', list(s.slots).map(slotDots));
    if (Array.isArray(s.spells)) push('spells', 'Spells', { groups: list(s.spells), casting: s.spellcasting || null });
    if (Array.isArray(s.features)) push('features', 'Features & traits', list(s.features));
    if (s.inventory) push('inventory', 'Inventory', s.inventory);
    if (s.notes != null) push('notes', 'Notes', { text: String(s.notes || '') });
    list(s.plain).forEach((p) => push('plain', String(p.title || ''), { text: String(p.text || '') }));
    return blocks;
  }

  // ── Drawing ──────────────────────────────────────────────────────────────

  // Light markdown for plain text: bold, italic, bullets, paragraphs.
  function md(text) {
    const lines = String(text || '').split('\n');
    let html = '', inList = false;
    const inline = (t) => esc(t)
      .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
      .replace(/(^|[^*\w])\*(?!\s)(.+?)\*(?!\w)/g, '$1<i>$2</i>');
    lines.forEach((ln) => {
      const b = ln.match(/^\s*[-*+]\s+(.*)$/);
      if (b) {
        if (!inList) { html += '<ul>'; inList = true; }
        html += `<li>${inline(b[1])}</li>`;
        return;
      }
      if (inList) { html += '</ul>'; inList = false; }
      const h = ln.match(/^#{3,}\s+(.*)$/);
      if (h) html += `<h4>${inline(h[1])}</h4>`;
      else if (ln.trim().startsWith('|')) html += `<p class="mono">${esc(ln)}</p>`;
      else if (ln.trim()) html += `<p>${inline(ln)}</p>`;
    });
    if (inList) html += '</ul>';
    return html;
  }

  const hooks = { pencil: null, roll: null, cast: null, use: null, spell: null };

  function pencil(block) {
    return block.editable && hooks.pencil
      ? `<button type="button" class="pencil" data-edit="${esc(block.kind)}" aria-label="Edit ${esc(block.title)}">✎</button>` : '';
  }

  function head(block, extra) {
    return `<header class="sec-h"><h2>${esc(block.title)}</h2>${extra || ''}${pencil(block)}</header>`;
  }

  const DRAW = {
    identity(b) {
      const d = b.data;
      return `<div class="id-line">${esc(d.line)}</div>`;
    },
    combat(b) {
      const c = b.data, hp = c.hp || {};
      const pct = hp.max ? Math.max(0, Math.min(100, (hp.current / hp.max) * 100)) : 100;
      const hd = c.hit_dice;
      const ds = c.death_saves || {};
      const chips = list(c.conditions).map((x) => `<span class="chip">${esc(x)}</span>`).join('');
      return head(b) + `<div class="hp-row">
        <div class="hp"><span class="hp-cur${pct <= 35 ? ' low' : ''}">${esc(hp.current ?? '—')}</span><span class="hp-max">/ ${esc(hp.max ?? '—')}</span>
          ${c.temp_hp ? `<span class="hp-temp">+${esc(c.temp_hp)} temp</span>` : ''}</div>
        <div class="hp-bar"><i style="width:${pct}%"></i></div></div>
        <dl class="stats">
          <div><dt>AC</dt><dd title="${esc(c.ac_note)}">${esc(c.ac ?? '—')}</dd></div>
          <div><dt>Init</dt><dd>${sign(c.initiative)}</dd></div>
          <div><dt>Speed</dt><dd>${esc((c.speed || '—').replace(/\.$/, ''))}</dd></div>
          ${hd ? `<div><dt>Hit dice</dt><dd>${esc(hd.remaining)}/${esc(hd.total)} <small>d${esc(String(hd.die).split('d')[1] || '')}</small></dd></div>` : ''}
        </dl>
        <div class="sub-row">
          <span class="ds">Death saves <b>${'●'.repeat(ds.successes || 0)}${'○'.repeat(3 - (ds.successes || 0))}</b> <b class="f">${'●'.repeat(ds.failures || 0)}${'○'.repeat(3 - (ds.failures || 0))}</b></span>
          ${c.inspiration ? '<span class="chip insp">Inspiration</span>' : ''}
          ${chips}
        </div>`;
    },
    abilities(b) {
      return head(b) + `<div class="abils">${b.data.map((a) =>
        `<div class="abil"><span class="k">${a.key}</span><span class="m">${sign(a.mod)}</span><span class="s">${esc(a.score)}</span></div>`).join('')}</div>`;
    },
    saves(b) {
      return head(b) + `<div class="rolls">${b.data.map((s) =>
        `<button type="button" class="roll-row" data-roll="save" data-key="${s.key}">
          <span class="p${s.proficient ? ' on' : ''}"></span><span class="n">${s.key}</span><span class="b">${sign(s.bonus)}</span></button>`).join('')}</div>`;
    },
    skills(b) {
      return head(b) + `<div class="rolls">${b.data.map((s, i) =>
        `<button type="button" class="roll-row" data-roll="skill" data-i="${i}">
          <span class="p${s.proficient ? ' on' : ''}"></span><span class="n">${esc(s.name)} <small>${esc(s.ability)}</small>${s.note ? `<em>${esc(s.note)}</em>` : ''}</span><span class="b">${sign(s.bonus)}</span></button>`).join('')}</div>`;
    },
    attacks(b) {
      if (!b.data.length) return head(b) + '<p class="muted">None listed</p>';
      return head(b) + `<div class="attacks">${b.data.map((a, i) =>
        `<div class="atk"><button type="button" class="roll-row" data-roll="attack" data-i="${i}">
          <span class="n">${esc(a.name)}</span><span class="b">${sign(a.bonus)}</span></button>
          <div class="atk-d"><button type="button" class="dmg" data-roll="damage" data-i="${i}">${esc(a.damage || '—')}</button> ${esc(a.type)}</div>
          ${a.notes ? `<div class="atk-n">${esc(a.notes)}</div>` : ''}</div>`).join('')}</div>`;
    },
    slots(b) {
      if (!b.data.length) return head(b) + '<p class="muted">None</p>';
      return head(b) + `<div class="slots">${b.data.map((s) =>
        `<div class="slot"><span class="lv">${esc(s.label || ('Level ' + s.level))}</span>
          <span class="dots" aria-label="${s.left} of ${s.total} left">${s.dots.map((on) => `<i class="${on ? 'on' : ''}"></i>`).join('')}</span></div>`).join('')}</div>`;
    },
    spells(b) {
      const c = b.data.casting;
      const cast = c ? `<div class="casting">${esc(c.ability || '')}${c.attack != null ? ` · attack ${sign(c.attack)}` : ''}${c.dc != null ? ` · save DC ${esc(c.dc)}` : ''}</div>` : '';
      if (!b.data.groups.length) return head(b) + cast + '<p class="muted">None listed</p>';
      return head(b) + cast + b.data.groups.map((g, gi) => {
        const names = list(g.names);
        const body = names.length
          ? `<ul class="spell-list">${names.map((n, i) =>
              `<li><button type="button" class="spell-name" data-spell="${esc(n)}">${esc(n)}</button>${hooks.cast ? `<button type="button" class="act" data-cast="${esc(n)}">Cast</button>` : ''}</li>`).join('')}</ul>`
          : `<p>${esc(g.text)}</p>`;
        return `<div class="spell-g">${g.label ? `<h3>${esc(g.label)}</h3>` : ''}${body}</div>`;
      }).join('');
    },
    features(b) {
      return head(b) + b.data.map((f) =>
        `<details class="feat"><summary>${esc(f.title || 'Features')}</summary>${md(f.text)}</details>`).join('');
    },
    inventory(b) {
      const inv = b.data;
      const cur = inv.currency && Object.keys(inv.currency).length
        ? ['pp', 'gp', 'ep', 'sp', 'cp'].filter((k) => inv.currency[k] != null).map((k) => `<span class="coin"><b>${esc(inv.currency[k])}</b> ${k}</span>`).join('')
        : `<span class="muted">${esc(inv.currency_text || 'No coins recorded')}</span>`;
      return head(b) + `<div class="coins">${cur}</div>` + list(inv.groups).map((g) =>
        `<div class="inv-g">${g.label ? `<h3>${esc(g.label)}</h3>` : ''}<ul class="items">${list(g.items).map((it) =>
          `<li><span class="n">${esc(it.name)}</span>${it.qty > 1 ? `<span class="q">×${esc(it.qty)}</span>` : ''}${hooks.use ? `<button type="button" class="act" data-use="${esc(it.name)}">Use</button>` : ''}</li>`).join('')}</ul></div>`).join('');
    },
    notes(b) { return head(b) + (b.data.text ? `<div class="md">${md(b.data.text)}</div>` : '<p class="muted">No notes yet</p>'); },
    plain(b) { return head(b) + `<div class="md plain">${md(b.data.text)}</div>`; },
  };

  // Sections that start folded on a phone (the long ones).
  const FOLDED = new Set(['skills', 'features', 'plain', 'notes']);

  function html(blocks) {
    return blocks.map((b) => {
      const inner = (DRAW[b.kind] || DRAW.plain)(b);
      return `<section class="sec sec-${esc(b.kind)}${FOLDED.has(b.kind) ? ' fold' : ''}" data-kind="${esc(b.kind)}">${inner}</section>`;
    }).join('');
  }

  // ── Wiring (browser only) ────────────────────────────────────────────────

  let app = null, el = null, last = null, loading = false, again = false;

  function load() {
    if (!app || !app.me) return Promise.resolve();
    if (loading) { again = true; return Promise.resolve(); }
    loading = true;
    return fetch(`/sheet/${encodeURIComponent(app.me)}`, { headers: app.headers(false), cache: 'no-store' })
      .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
      .then(({ ok, j }) => {
        if (!ok) { el.innerHTML = `<p class="placeholder">${esc(j.error || 'No character sheet found')}</p>`; return; }
        last = j;
        draw();
      })
      .catch(() => { if (!last) el.innerHTML = '<p class="placeholder">Can’t reach the game — the sheet will load when it’s back.</p>'; })
      .finally(() => { loading = false; if (again) { again = false; load(); } });
  }

  function draw() {
    if (!last) return;
    const open = new Set([...el.querySelectorAll('.sec.unfold')].map((s) => s.dataset.kind));
    const blocks = sheetView(last);
    if (!last.path_ok) blocks.splice(1, 0, { kind: 'plain', title: '', data: { text: '*Read-only: this is the roster copy, not the campaign’s.*' } });
    el.innerHTML = html(blocks);
    el.querySelectorAll('.sec').forEach((s) => { if (open.has(s.dataset.kind)) s.classList.add('unfold'); });
    const name = (last.sheet && last.sheet.name) || app.me;
    const meEl = root.document.getElementById('me-name');
    if (meEl && name) meEl.textContent = name;
  }

  // A `sheet` event names whose file changed: reload only for this phone's.
  function wantsReload(p, me) {
    if (!p || typeof p !== 'object') return false;
    if (p.sheet && typeof p.sheet === 'object') {
      const n = String(p.sheet.name || '').toLowerCase();
      return !!me && n === String(me).toLowerCase();
    }
    return !!(p.rewind || p.rewound);
  }

  function start(a) {
    app = a;
    el = root.document.getElementById('sheet-body');
    el.addEventListener('click', (ev) => {
      const h = ev.target.closest('.sec.fold > .sec-h');
      if (h && !ev.target.closest('button')) h.parentElement.classList.toggle('unfold');
    });
    let hellos = 0;
    app.on((p) => {
      if (p.hello) { if (hellos++ > 0) load(); return; }
      if (wantsReload(p, app.me)) load();
    });
    load();
  }

  const api = { sheetView, slotDots, wantsReload, md, html, start, load, hooks, esc,
                get last() { return last; } };
  if (root) { root.PH = root.PH || {}; root.PH.sheet = api; }
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : null);
