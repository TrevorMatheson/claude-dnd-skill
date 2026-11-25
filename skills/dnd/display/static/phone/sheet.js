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


  // ── Rolls from the sheet (D11) ───────────────────────────────────────────
  const ABIL_FULL = { STR: 'Strength', DEX: 'Dexterity', CON: 'Constitution', INT: 'Intelligence', WIS: 'Wisdom', CHA: 'Charisma' };
  const ABIL_ALIAS = { strength: 'str', dexterity: 'dex', constitution: 'con', intelligence: 'int', wisdom: 'wis', charisma: 'cha' };

  // "1d8+3 (1d10+3 two-handed)" → {spec: '1d8', modifier: 3}. Null when the
  // damage isn't one die expression (flat damage, or 2d6 + 1d4): roll by hand.
  function damageSpec(text) {
    const main = String(text || '').replace(/\([^)]*\)/g, '').trim();
    const m = /^(\d{1,2})d(\d{1,3})\s*(?:([+\-−])\s*(\d+))?\s*(?:[a-z ]*)$/i.exec(main);
    if (!m) return null;
    const mod = m[3] ? (m[3] === '+' ? 1 : -1) * Number(m[4]) : 0;
    return { spec: `${m[1]}d${m[2]}`, modifier: mod };
  }

  // What a tap rolls: {spec, modifier, label}, or null when there's nothing
  // to roll. kind: skill | save | attack | damage | spell_attack.
  function rollFor(kind, item) {
    if (!item) return null;
    if (kind === 'skill') return { spec: '1d20', modifier: Number(item.bonus) || 0, label: String(item.name) };
    if (kind === 'save') {
      return { spec: '1d20', modifier: Number(item.bonus) || 0, label: `${ABIL_FULL[item.key] || item.key} save` };
    }
    if (kind === 'attack') {
      if (item.bonus == null) return null;
      return { spec: '1d20', modifier: Number(item.bonus) || 0, label: `${item.name} attack` };
    }
    if (kind === 'damage') {
      const d = damageSpec(item.damage);
      return d ? Object.assign(d, { label: `${item.name} damage` }) : null;
    }
    if (kind === 'spell_attack') {
      if (item.attack == null) return null;
      return { spec: '1d20', modifier: Number(item.attack) || 0, label: 'Spell attack' };
    }
    return null;
  }

  // "Dexterity saving throw", "Dex save" → "dex save"; "Athletics check" →
  // "athletics".
  function normLabel(s) {
    let t = String(s || '').toLowerCase().replace(/[^a-z ]+/g, ' ');
    t = t.replace(/\bsaving throw\b/g, 'save').replace(/\b(check|roll|test)\b/g, ' ');
    t = t.split(/\s+/).filter(Boolean).map((w) => ABIL_ALIAS[w] || w).join(' ');
    return t.trim();
  }

  // Does a tap labelled `label` answer the open request `req` for `me`?
  function matchRequest(label, req, me) {
    if (!req || !me) return false;
    const names = (Array.isArray(req.characters) && req.characters.length ? req.characters : [req.character || 'any'])
      .map((n) => String(n || '').toLowerCase());
    const m = String(me).toLowerCase();
    if (!names.includes(m) && !names.includes('any') && !names.includes('everybody')) return false;
    const a = normLabel(label), b = normLabel(req.label);
    if (!a || !b) return false;
    if (a === b) return true;
    // "athletics" answers "athletics shove the door"; a save only a save.
    const isSave = (x) => /\bsave\b/.test(x);
    if (isSave(a) !== isSave(b)) return false;
    return (' ' + b + ' ').includes(' ' + a + ' ');
  }

  // ── Hand edits (D7, D8, D10): one difference per tap ─────────────────────
  function stepOp(field, key, delta) {
    switch (field) {
      case 'hp': return { hp: delta };
      case 'temp_hp': return { temp_hp: delta };
      case 'hit_dice': return { hit_dice: delta };
      case 'slot': return { slot: Number(key), delta };
      case 'item': return { item: String(key), delta };
      case 'gold': return { gold: { [key]: delta } };
      case 'death_saves': return { death_saves: key, delta };
      case 'condition': return { condition: String(key), on: !!delta };
      case 'inspiration': return { inspiration: !!delta };
      case 'note': return { note: String(key), on: !!delta };
      default: return null;
    }
  }

  const CONDITIONS = ['Blinded', 'Charmed', 'Deafened', 'Frightened', 'Grappled', 'Incapacitated', 'Invisible',
    'Paralyzed', 'Petrified', 'Poisoned', 'Prone', 'Restrained', 'Stunned', 'Unconscious', 'Exhaustion'];

  const step = (field, key, label, value) =>
    `<div class="stepper"><span class="st-l">${esc(label)}</span>
      <button type="button" class="st" data-step="${esc(field)}" data-key="${esc(key)}" data-d="-1" aria-label="${esc(label)} minus one">−</button>
      <span class="st-v">${esc(value)}</span>
      <button type="button" class="st" data-step="${esc(field)}" data-key="${esc(key)}" data-d="1" aria-label="${esc(label)} plus one">+</button></div>`;

  // The edit controls for one section, drawn under it while its pencil is on.
  function editorHtml(kind, data) {
    if (kind === 'combat') {
      const c = data || {}, hp = c.hp || {}, hd = c.hit_dice, ds = c.death_saves || {};
      const conds = (c.conditions || []);
      return `<div class="editor">
        ${step('hp', '', 'HP', `${hp.current ?? '—'} / ${hp.max ?? '—'}`)}
        ${step('temp_hp', '', 'Temp HP', c.temp_hp || 0)}
        ${hd ? step('hit_dice', '', 'Hit dice left', `${hd.remaining} / ${hd.total}`) : ''}
        ${step('death_saves', 'success', 'Death save successes', ds.successes || 0)}
        ${step('death_saves', 'failure', 'Death save failures', ds.failures || 0)}
        <div class="stepper"><span class="st-l">Inspiration</span>
          <button type="button" class="tog${c.inspiration ? ' on' : ''}" data-toggle="inspiration" data-on="${c.inspiration ? 0 : 1}">${c.inspiration ? 'Have it — use' : 'Gain'}</button></div>
        <div class="ed-conds">${conds.map((x) => `<button type="button" class="chip x" data-cond="${esc(x)}" data-on="0">${esc(x)} ✕</button>`).join('')}
          <select class="ed-add-cond" aria-label="Add a condition"><option value="">+ Condition</option>
          ${CONDITIONS.filter((x) => !conds.some((y) => y.toLowerCase() === x.toLowerCase())).map((x) => `<option>${x}</option>`).join('')}</select></div>
      </div>`;
    }
    if (kind === 'slots') {
      return `<div class="editor">${(data || []).map((s) =>
        step('slot', s.level, `Level ${s.level} slots left`, `${s.left} / ${s.total}`)).join('')}</div>`;
    }
    if (kind === 'inventory') {
      const inv = data || {}, coins = inv.currency || {};
      const items = [];
      (inv.groups || []).forEach((g) => (g.items || []).forEach((it) => items.push(it)));
      return `<div class="editor">
        ${['pp', 'gp', 'ep', 'sp', 'cp'].filter((k) => coins[k] != null || k === 'gp').map((k) =>
          `<div class="stepper"><span class="st-l">${k}</span>
            <input type="number" inputmode="numeric" min="1" value="1" class="amt" data-amt="${k}" aria-label="${k} amount">
            <button type="button" class="st" data-step="gold" data-key="${k}" data-d="-1" aria-label="Spend ${k}">−</button>
            <span class="st-v">${esc(coins[k] ?? 0)}</span>
            <button type="button" class="st" data-step="gold" data-key="${k}" data-d="1" aria-label="Gain ${k}">+</button></div>`).join('')}
        ${items.map((it) => step('item', it.name, it.name, it.qty)).join('')}
        <form class="ed-add" data-add="item"><input type="text" maxlength="80" placeholder="Add an item" aria-label="New item">
          <button type="submit" class="btn">Add</button></form>
      </div>`;
    }
    if (kind === 'notes') {
      const bullets = String((data && data.text) || '').split('\n')
        .map((l) => (/^\s*[-*+]\s+(.*)$/.exec(l) || [])[1]).filter(Boolean)
        .map((t) => t.replace(/\*\*/g, ''));
      return `<div class="editor">
        ${bullets.map((t) => `<div class="ed-note"><span>${esc(t)}</span><button type="button" class="st x" data-note="${esc(t)}" data-on="0" aria-label="Remove note">✕</button></div>`).join('')}
        <form class="ed-add" data-add="note"><input type="text" maxlength="300" placeholder="Add a note" aria-label="New note">
          <button type="submit" class="btn">Add</button></form></div>`;
    }
    return '';
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
  const openEdits = new Set();       // sections whose pencil is on

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
        <div class="hp-line"><span class="hp-cur${pct <= 35 ? ' low' : ''}">${esc(hp.current ?? '—')}</span><span class="hp-max">/ ${esc(hp.max ?? '—')}</span>
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
        `<div class="atk-row"><button type="button" class="roll-row" data-roll="attack" data-i="${i}">
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
      const cast = c ? `<div class="casting">${esc(c.ability || '')}${c.attack != null ? ` · <button type="button" class="link" data-roll="spell_attack">attack ${sign(c.attack)}</button>` : ''}${c.dc != null ? ` · save DC ${esc(c.dc)}` : ''}</div>` : '';
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
      const editing = hooks.pencil && b.editable && openEdits.has(b.kind);
      return `<section class="sec sec-${esc(b.kind)}${FOLDED.has(b.kind) ? ' fold' : ''}${editing ? ' editing unfold' : ''}" data-kind="${esc(b.kind)}">${inner}${editing ? editorHtml(b.kind, b.data) : ''}</section>`;
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

  // ── Taps: rolls, Cast, Use, spell text, edits ────────────────────────────

  function note(text, warn) {
    const n = root.document.getElementById('sheet-note');
    if (!n) return;
    n.textContent = text || '';
    n.hidden = !text;
    n.className = 'sheet-note' + (warn ? ' warn' : '');
    clearTimeout(note.t);
    if (text) note.t = setTimeout(() => { n.hidden = true; }, 6000);
  }

  // A hand edit: one difference, sent once; a failure shows Not sent (D14).
  function sendChange(change, label) {
    const net = root.PH.net;
    if (!net || !net.send || !change) return;
    net.send('edit', `/sheet/${encodeURIComponent(app.me)}/change`,
      { source: 'player', changes: [change] }, label).then((res) => {
      if (!res || res.notSent) return;
      if (res.status === 202) { note('Waiting for the DM to approve this phone…', true); return; }
      if (!res.ok) note((res.body && res.body.reason) || 'That change was refused', true);
    });
  }

  function itemFor(kind, i, key) {
    const s = (last && last.sheet) || {};
    if (kind === 'skill') return (s.skills || [])[i];
    if (kind === 'save') return s.saves && s.saves[key] ? Object.assign({ key }, s.saves[key]) : null;
    if (kind === 'attack' || kind === 'damage') return (s.attacks || [])[i];
    if (kind === 'spell_attack') return s.spellcasting;
    return null;
  }

  // D11: a tap rolls with the sheet's bonus; it answers the DM's open request
  // when the labels match, otherwise it goes to Claude as an unasked roll.
  function tapRoll(btn) {
    const kind = btn.dataset.roll;
    const r = rollFor(kind, itemFor(kind, Number(btn.dataset.i), btn.dataset.key));
    const play = root.PH.play;
    if (!r) { note('Roll this one by hand with the dice on Play'); return; }
    if (!play || !play.roll) return;
    const req = play.openRequest && play.openRequest();
    if (req && matchRequest(r.label, req, app.me)) r.request_id = req.request_id;
    if (r.spec === '1d20') r.advantage = play.adv || 'normal';
    play.roll(r).then((res) => {
      if (res && res.ok && res.body) {
        note(`${r.label}: ${res.body.total}${res.body.asked ? ' — answered the DM' : ' — sent to the DM'}`);
      }
    });
  }

  // D12: a spell's name shows its description from the rules data.
  function showSpell(btn) {
    const name = btn.dataset.spell;
    const li = btn.closest('li');
    const next = li.nextElementSibling;
    if (next && next.classList.contains('spell-text')) { next.remove(); return; }
    const box = root.document.createElement('li');
    box.className = 'spell-text';
    box.textContent = 'Looking it up…';
    li.after(box);
    fetch(`/srd-lookup?name=${encodeURIComponent(name)}&category=spell`, { headers: app.headers(false) })
      .then((r) => r.json().catch(() => ({})))
      .then((j) => { box.innerHTML = j && j.found ? md(j.text) : '<p class="muted">No description available</p>'; })
      .catch(() => { box.innerHTML = '<p class="muted">No description available</p>'; });
  }

  function onClick(ev) {
    const t = ev.target;
    const pen = t.closest('.pencil');
    if (pen) {
      const k = pen.dataset.edit;
      if (openEdits.has(k)) openEdits.delete(k); else openEdits.add(k);
      draw();
      return;
    }
    const st = t.closest('[data-step]');
    if (st) {
      let d = Number(st.dataset.d);
      const field = st.dataset.step, key = st.dataset.key;
      if (field === 'gold') {
        const amt = el.querySelector(`[data-amt="${key}"]`);
        d *= Math.max(1, Math.floor(Number(amt && amt.value) || 1));
      }
      const what = field === 'gold' ? key : field === 'item' ? key
        : field === 'slot' ? `level-${key} slot` : field.replace('_', ' ');
      sendChange(stepOp(field, key, d), `${d > 0 ? '+' : '−'}${Math.abs(d)} ${what}`);
      return;
    }
    const tog = t.closest('[data-toggle="inspiration"]');
    if (tog) { sendChange(stepOp('inspiration', null, tog.dataset.on === '1'), 'inspiration'); return; }
    const cond = t.closest('[data-cond]');
    if (cond) { sendChange(stepOp('condition', cond.dataset.cond, false), `${cond.dataset.cond} ended`); return; }
    const nt = t.closest('[data-note]');
    if (nt) { sendChange(stepOp('note', nt.dataset.note, false), 'remove note'); return; }
    const roll = t.closest('[data-roll]');
    if (roll) { tapRoll(roll); return; }
    const sp = t.closest('[data-spell]');
    if (sp) { showSpell(sp); return; }
    // D12: Cast and Use go to Claude as an action; the phone spends nothing.
    const cast = t.closest('[data-cast]');
    if (cast && root.PH.play) {
      root.PH.play.act(`casts ${cast.dataset.cast}`, `Cast ${cast.dataset.cast}`)
        .then((res) => { if (res && res.ok) note(`Sent: casts ${cast.dataset.cast}`); });
      return;
    }
    const use = t.closest('[data-use]');
    if (use && root.PH.play) {
      root.PH.play.act(`uses ${use.dataset.use}`, `Use ${use.dataset.use}`)
        .then((res) => { if (res && res.ok) note(`Sent: uses ${use.dataset.use}`); });
      return;
    }
    const h = t.closest('.sec.fold > .sec-h');
    if (h && !t.closest('button')) h.parentElement.classList.toggle('unfold');
  }

  function start(a) {
    app = a;
    el = root.document.getElementById('sheet-body');
    hooks.pencil = hooks.cast = hooks.use = true;
    const n = root.document.createElement('div');
    n.id = 'sheet-note';
    n.className = 'sheet-note';
    n.hidden = true;
    n.setAttribute('role', 'status');
    el.parentNode.insertBefore(n, el);
    el.addEventListener('click', onClick);
    el.addEventListener('change', (ev) => {
      const sel = ev.target.closest('.ed-add-cond');
      if (sel && sel.value) sendChange(stepOp('condition', sel.value, true), `${sel.value} added`);
    });
    el.addEventListener('submit', (ev) => {
      const f = ev.target.closest('.ed-add');
      if (!f) return;
      ev.preventDefault();
      const v = f.querySelector('input').value.trim();
      if (!v) return;
      if (f.dataset.add === 'item') sendChange(stepOp('item', v, 1), `+1 ${v}`);
      else sendChange(stepOp('note', v, true), 'add note');
    });
    let hellos = 0;
    app.on((p) => {
      if (p.hello) { if (hellos++ > 0) load(); return; }
      if (wantsReload(p, app.me)) load();
    });
    load();
  }

  const api = { sheetView, slotDots, wantsReload, md, html, start, load, hooks, esc,
                rollFor, damageSpec, matchRequest, normLabel, stepOp, editorHtml, openEdits,
                get last() { return last; } };
  if (root) { root.PH = root.PH || {}; root.PH.sheet = api; }
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : null);
