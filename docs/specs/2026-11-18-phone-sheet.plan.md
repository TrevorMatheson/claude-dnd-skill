---
spec: 2026-11-18-phone-sheet.md
status: approved      # proposed → approved
approved: 2026-11-20 (delegated by the user to the orchestrating session)
---

# Build plan: Phone sheet (play, sheet and map on each player's device)

## Approval notes (2026-11-20)

Reviewed against the spec by the build session, with plan approval delegated by
the user. The plan stays within the spec's decisions. Three changes from the
architect's proposal, all made to keep it strictly inside the spec:

1. **No offline fallback in S6.** D6 says the server applies changes. With the
   display down, `send.py` does not write the character file itself. It prints
   every unapplied change on stderr ("NOT APPLIED (display offline): Havoc
   damage 7") and exits 0, so Claude can apply it by hand. Whether to add a
   local fallback is listed as an open question for the user.
2. **S2's imported fixture is synthetic.** `imported.md` copies the section
   layout of a real D&D Beyond-imported sheet (headings, tables, the import
   script's field order) with a made-up character, so nothing from the user's
   campaigns lands in the repo. Real campaigns are used only as scratch copies
   in browser checks.
3. **TV Party panel.** As planned, the Party panel gains temp HP (S7) and
   refreshes live on every change. It shows no slots, hit dice, items or gold
   today. Whether it should is a visual choice and is listed as "needs the
   user". It does not block the build.

## Facts from the code that shape the build

None of these reopens a decision.

1. **No script writes resources to the character file today.** `--stat-hp`
   sends a total (`send.py:261-266`), the turn call's `stats.players` is
   absolute (`SKILL.md:391`), and both go only to `_current_stats`, never to the
   `.md` file. Claude edits sheets by hand (`SKILL-commands.md:528`, `:549`).
   So D6 and D10 need a `changes` key in the turn call, applied as differences
   by a new module (S4, S6).
2. **The TV Party panel shows name, HP, class/AC and conditions**
   (`static/tv/panels.js`). S7 adds temp HP. See approval note 3.
3. **The template has no Conditions or Inspiration line**
   (`templates/character-sheet.md:27-31`). `session_recap.py:137-144` already
   treats `**Inspiration:**` and `**Conditions:**` as optional. The first change
   to either adds the line to `## Combat Stats` in that form. No migration.
4. **Sheet parsing exists twice and neither covers D5** (`index.html`
   `_parsePhoneSheet`, `session_recap.py`). A new server-side parser,
   `sheet.py`, is added (S2). Neither existing file changes.
5. **The open roll request already survives a reconnect.** `/stream` replays
   every active `dice_request` and the `dice_pending` snapshot. For AC11 the
   phone only drops a request it is still showing on each `hello`, before the
   replay arrives (S8).
6. **One rate-limit bucket per IP, 20 writes a minute, shared by every
   endpoint.** Sheet routes use their own key, `sheet:<ip>`, with a higher
   limit (S4).
7. **The stage sanitizer strips `()[]{}!;&|<>$`.** Action text built by the
   phone avoids those characters.
8. **The old page retries Send three times by itself.** D14 forbids that on
   `/phone`: one attempt, then Not sent with Retry (S8).
9. **Send is two requests on the old page** (stage, then ready). S9 adds an
   optional `ready` flag to `/player-input/stage`, so `/phone` sends in one
   request. The old page is unchanged.
10. **A tap that answers a request needs the request id.**
    `/player-input/dice` already matches `request_id` and sends unasked rolls to
    the DM's inbox marked "(not asked for)". That is D11's "goes to Claude as an
    action", so rolls need no new route.
11. **The battle map plan names no endpoint for `check_move`.** This plan adds
    `/map/check` and `/map/move` behind a capability check (S14).
12. **AC2 lists a Map tab that D16 holds to milestone 2 and AC15 hides outside
    combat.** In milestone 1 the tab bar shows Play and Sheet. The Map pane is
    in the markup but hidden until S15.

## Shape

**New page at `/phone` (D1, D2, D17).**
- `templates/phone.html` is a thin Jinja shell with the same meta tags as
  `tv.html` (token, voice) plus `map-check` / `map-board` capability flags.
- It loads the foundation theme (`static/tv/tokens.css`, bundled fonts) and
  reuses `static/tv/stream.js` (`connect()`, reconnect badge) and
  `static/tv/panels.js` (`makeWaitModel`).
- New files live in `skills/dnd/display/static/phone/`. Each attaches to
  `window.PH` and sets `module.exports`, so node tests can load the pure parts.

| File | Owns |
|---|---|
| `phone.css` | Phone layout (tab bar) and desktop layout (Play and Sheet side by side, Map below in combat), chosen by `body[data-layout]`. |
| `layout.js` | `chooseLayout({width, coarse, saved})` → `phone`/`desktop`; the switch saved in `localStorage["dnd-phone-layout"]`; `combatOn(stats)` (same rule as `_combat_on`); `tabsFor(layout, stats, caps)`. |
| `net.js` | Every write. Token and `X-DND-Device` headers. Each request has a client `op` id and is tried once. A failure becomes a **Not sent** item with Retry (same `op`). Nothing is queued or resent by itself. |
| `play.js` | Play tab: Send box, Waiting on banner, roll-request card, dice pad with physical entry, "I roll real dice" toggle. |
| `talk.js` | The Talk button only. The only phone file that touches `/mic/*` or `/stt`. Interface `PH.talk.mount(el, {character, headers, onText})`, mounted in `#talk-slot`. Pass the mic replaces this file. |
| `sheet.js` | Sheet rendering, pencils and steppers, tap to roll, Cast and Use, spell descriptions. Pure helpers `sheetView`, `rollFor`, `matchRequest`, `stepOp`, `damageSpec`. |
| `maptab.js` | Milestone 2: the Map tab, through the seam below. |
| `phone.js` | Startup: character binding (`?char=` or picker), `/stream?character=<me>`, tabs, jump to Play on a request, reload on `hello`/`sheet`/`rewind`. |

**Character sheet on the server (D5, D6, D7, D10).** New module
`skills/dnd/display/sheet.py`:
- `parse(text) -> dict`. Each known section parses inside try/except; a section
  it can't read, and any unknown section, comes back as `{title, text}` in
  `plain`, so nothing is left out.
- `apply(text, changes) -> (new_text, results)` applies differences only, with
  clamps.
- `change(campaign_dir, name, changes, source, dry_run=False)` finds the file
  with `cast._pc_path`, reads/applies/writes under `cast._lock` with
  `cast._read`/`cast._write` (CRLF kept, GBK read, atomic write). Failures
  return a result, never raise into the server.
- `PLAYER_FIELDS`: hp, temp_hp, slot, hit_dice, death_saves, inspiration,
  condition, item, gold, note (D7). `DM_FIELDS`: damage, heal, hp, temp_hp,
  slot, hit_dice, item, gold, condition (D6). Ability scores, level, features,
  proficiencies and max HP are in neither.

**Change format** (same from Claude and a phone, one character and one
resource per entry):
```
{"name":"Havoc","damage":7}            {"name":"Havoc","heal":5}
{"name":"Havoc","hp":-3}               {"name":"Havoc","temp_hp":5}
{"name":"Mira","slot":2,"delta":-1}    {"name":"Mira","hit_dice":-1}
{"name":"Mira","item":"Potion of Healing","delta":-1}
{"name":"Mira","gold":{"gp":-5}}       {"name":"Havoc","condition":"Poisoned","on":true}
{"name":"Havoc","death_saves":"failure","delta":1}
{"name":"Havoc","inspiration":false}   {"name":"Havoc","note":"owes Brann 5 gp","on":true}
```
Damage comes off temp HP first. Conditions, inspiration and notes are set
operations. Short gold is refused with the reason, never made change for.

**Endpoints** in `dnd-display-app.py`:
- `GET /sheet/<name>` (token) → `{sheet, editable, path_ok}`; roster fallback is
  read-only.
- `POST /sheet/<name>/change` (token, own rate bucket), body
  `{op, source:"player"|"dm", changes, dry_run?}`. `dm` needs `_host_only()`;
  `player` needs an approved device and accepts only `PLAYER_FIELDS`. A repeated
  `op` returns the stored result and applies nothing. On success the server
  syncs that player in `_current_stats` from the re-parsed file (hp
  `{current,max,temp}`, conditions, spell_slots, hit_dice, inspiration),
  persists, and broadcasts `{"stats": …}` and `{"sheet": {"name"}}`.

**Hand edits reach Claude (D9).** New stdlib-only `sheet_notes.py` keeps pending
lines in `rt(".sheet_notes.json")`; repeated taps on one field merge, net zero
drops. `check_input.py` and `autorun_wait.py` put the lines before player input,
only when there is input. `/rewind` clears them.

**Turn call (D6).** `send.py --turn` accepts top-level `"changes": [...]`:
shape check, then a dry run (a refusal rejects the whole turn), then blocks, then
`/stats`, then the real change last. With the display offline, nothing is
written locally; unapplied changes are printed (approval note 1).

**Map tab seam (milestone 2, D13).** The battle map is not on this branch.
- **Server `map_moves.py`:** `available()` is `hasattr(battlemap,
  "check_move")`. `check(campaign_dir, character, x, y, dash)` loads
  `battlemap.json`, removes hidden tokens from a copy, calls
  `battlemap.check_move(m, token, x, y, dash=dash)` (the battle map plan's
  contract) and returns only `{ok, cost, budget, left, path, reason, message}`.
  Routes `GET /map/caps`, `POST /map/check` (501 when unavailable),
  `POST /map/move` (re-checks; 409 with the message, or stages "moves to square
  X,Y — C of B ft" as the player's action). Claude still moves the token
  through the turn call.
- **Client `maptab.js`:** uses `window.TV.board` / `window.TV.los` only when
  present; `phone.html` adds `static/map/*` tags only when those files exist.
  The tab shows only when `combatOn(stats) && stats.map && caps.check &&
  caps.board`.

## Steps

Milestone 1 is S1–S13; milestone 2 is S14–S16. One commit per step; each step
leaves the game playable.

### S1: `/phone` route, shell, theme, layout choice and tabs
- **Covers:** AC1 (route, token), AC2, D1–D4 (frame), D17
- **Files:** `dnd-display-app.py` (`GET /phone`, `_page_vars` flags); new
  `templates/phone.html`, `static/phone/{phone.css,layout.js,phone.js}`,
  `tests/test_phone_page.py`, `tests/test_phone_layout_js.py` (node).
- **Tests first:** `/phone` 200 with shell marker and token meta; static served;
  `/` still renders `index.html`; no `http(s)://` in phone files;
  `requirements.txt` unchanged. Node: `chooseLayout` cases, saved choice wins,
  `tabsFor` in milestone 1 never includes `map`.
- **Browser:** 375×812 tabs, 1440×900 side by side, switch survives reload.

### S2: Sheet parser and `GET /sheet/<name>`
- **Covers:** AC4 (server), D5
- **Files:** new `sheet.py` (`parse`), `tests/test_sheet_parse.py`,
  `tests/fixtures/sheets/{template_filled,imported,broken,crlf,gbk}.md`;
  `dnd-display-app.py` (route).
- **Tests first:** every section of `template_filled.md` and `imported.md`
  present; `broken.md`'s bad section is in `plain` with text intact and the rest
  parse; CRLF/GBK parse like LF; route 200 / 403 without token / 404 / 400 for
  `../x`; roster fallback `path_ok:false`; `editable` excludes build fields.

### S3: Sheet tab, read-only and live
- **Covers:** AC4 (browser), D3, D5
- **Files:** new `static/phone/sheet.js`, `tests/test_phone_sheet_js.py`;
  `phone.html`, `phone.css`, `phone.js`.
- **Tests first:** `sheetView` one block per section incl. plain; slot dots;
  a `sheet` event for another character doesn't refetch.

### S4: Differences applied to the file, with a live update
- **Covers:** AC5 (server), AC6 (server), AC8, D6, D7, D10
- **Files:** `sheet.py` (`apply`, `change`, field sets), `dnd-display-app.py`
  (`POST /sheet/<name>/change`, op cache of 512, stats sync, broadcasts),
  `tests/test_sheet_changes.py`.
- **Clamps:** HP 0..max; temp ≥ 0; slots used 0..total; hit dice 0..total;
  death saves 0..3; item qty ≥ 0 (line removed at 0); short gold refused.
- **Tests first:** each field applies a difference with before/after; damage
  takes temp first; clamps; short gold refused, file unchanged; AC8 two threads
  (dm damage 7, player hp +5) → start − 2; same `op` twice applies once;
  `max_hp`/`str`/`level` from a player refused; `dm` from non-loopback 403; CRLF
  kept; `_current_stats` synced and `stats` + `sheet` broadcast; `dry_run`
  writes nothing; 30 sheet posts don't 429 `/player-input/stage`.

### S5: Hand edits reach Claude with the next input
- **Covers:** AC7, D9
- **Files:** new `sheet_notes.py`, `tests/test_sheet_notes.py`;
  `dnd-display-app.py`, `check_input.py`, `autorun_wait.py`.
- **Lines:** `[Sheet] Havoc: −7 HP (12→5)`, `[Sheet] Mira: −1 level-2 slot
  (3→2 left)`, `[Sheet] Havoc: Poisoned ended`. DM changes add no note.
- **Tests first:** note printed before the action, once; no input → nothing
  printed, note kept; `autorun_wait.py` likewise; merging; DM change no note;
  `/rewind` clears.

### S6: `changes` in the turn call, and Claude's instructions
- **Covers:** AC5 (Claude's path), D6, D10
- **Files:** `send.py`, `skills/dnd/SKILL.md`, `SKILL-commands.md`,
  `tests/test_turn_changes.py`.
- **Tests first:** order chunks → `/stats` → `/sheet/…/change` last; a bad entry
  exits 2 with nothing posted; a dry-run refusal exits 2, no chunk posted;
  display offline prints NOT APPLIED lines, writes no file, exits 0; the
  documented example validates; existing turn tests green.

### S7: TV Party panel shows temp HP
- **Covers:** AC5 (TV)
- **Files:** `static/tv/panels.js`, `tv.css`, tests in `tests/test_tv_layout.py`.
- **Tests first:** `partyView` carries `temp`; "+5 temp" only when > 0.
- **Browser:** two tabs, a turn call with damage, temp HP and a condition
  changes `/tv` and `/phone` within 1 s.

### S8: Network layer: Not sent, Retry, reload on reconnect
- **Covers:** AC11, D14
- **Files:** new `static/phone/net.js`, `tests/test_phone_net.py`; `phone.js`,
  `phone.css`.
- **Tests first:** failure → one Not sent item; 10 minutes and a reconnect make
  no fetch; Retry reuses the `op`; failed items keep order; `hello` after an
  error reloads the sheet and resets the request.

### S9: Play tab: Send, Waiting on, roll requests and dice
- **Covers:** AC1 (approval), AC3, AC9 (physical entry, answering), D3, D11
- **Files:** new `static/phone/play.js`, `tests/test_phone_play.py`;
  `dnd-display-app.py` (`/player-input/stage` optional `ready`, `append`, `op`;
  `/player-input/dice` optional `op`); `phone.html`, `phone.js`.
- **Tests first:** stage `ready:true` queues in one call; `append` joins; repeated
  `op` not queued twice (stage) / logged once (dice); old bodies unchanged.
  Node: `requestIsMine`; tab switches to Play on a request.

### S10: Talk, in one file
- **Covers:** AC12, D15
- **Files:** new `static/phone/talk.js`, `tests/test_phone_talk.py`;
  `phone.html`, `play.js`.
- **Tests first:** `talk.js` is the only phone file mentioning `/mic/` or `/stt`;
  `phone.html` mounts it in `#talk-slot`; `test_pass_the_mic.py` green.

### S11: Pencils and steppers
- **Covers:** AC6, D7, D8, D9 (UI)
- **Files:** `sheet.js`, `phone.css`, tests.
- **Tests first:** pencil on exactly the D7 sections; build fields have no edit
  control; `stepOp` outputs.

### S12: Tap to roll, Cast and Use, spell descriptions
- **Covers:** AC9, AC10, D11, D12
- **Files:** `sheet.js`, `play.js`, tests.
- **Tests first:** `rollFor` for skill, save and attack; `matchRequest` with save
  aliases and character match; Cast and Use make one stage call and zero sheet
  calls; spell name → `/srd-lookup`, "No description available" on miss.

### S13: Launcher, docs, milestone 1 table check
- **Covers:** AC13, D1, D16
- **Files:** `start_display.py` (prints the `/phone` link), display `README.md`,
  `docs/ROADMAP.md`, `CHANGELOG.md`.
- **AC13:** the user plays a full game night on `/phone` (needs the user).

### S14: Map seam on the server (milestone 2)
- **Covers:** AC14 (server), D13
- **Files:** new `map_moves.py`, `tests/test_map_moves.py`;
  `dnd-display-app.py` (`/map/caps`, `/map/check`, `/map/move`).
- **Tests first** (fake `battlemap` patched in): no `check_move` → caps false,
  501; whitelisted response; hidden tokens absent from the map passed in;
  failing check → 409, nothing staged; passing → staged "moves to square 6,1 —
  25 of 35 ft"; repeated `op` once; `battlemap.json` byte-identical.

### S15: Map tab on the phone, map under the desktop layout
- **Covers:** AC14, AC15, D3, D4, D13
- **Files:** new `static/phone/maptab.js`, `tests/test_phone_map.py`;
  `phone.html`, `layout.js`, `phone.css`.
- **Tests first:** `tabVisible` false outside combat / no map / caps false;
  `costLabel` "25 of 35 ft"; `cellAt` at dpr 1 and 3; `centerOn`; Move here never
  called after a failed check.

### S16: Map integration and the milestone 2 table check
- **Waits for:** battle map milestone 1 merged. Confirm both capability flags,
  run the S15 browser checks, fix any signature drift in `map_moves.py` /
  `maptab.js`. Open on this branch.

## AC coverage

| AC | Steps |
|---|---|
| AC1 | S1, S9 |
| AC2 | S1, S16 |
| AC3 | S9 |
| AC4 | S2, S3 |
| AC5 | S4, S6, S7 |
| AC6 | S2, S4, S11 |
| AC7 | S5 |
| AC8 | S4 |
| AC9 | S9, S12 |
| AC10 | S12 |
| AC11 | S8 |
| AC12 | S10 |
| AC13 | S13 (user) |
| AC14 | S14, S15, S16 |
| AC15 | S15, S16 |

## Risks

- **Old saves:** missing Temp HP / Conditions / Inspiration / Notes lines are
  added on the first change; GBK becomes UTF-8 on first change (as `cast.py`);
  CRLF kept; roster-only characters are read-only. No migration.
- **Two writers on one sheet:** the server and `cast.py` share `cast._lock`;
  Claude's Edit tool doesn't. S6 tells Claude not to hand-edit resource lines
  while the display runs.
- **PC conditions in two places:** `tracker.py condition` pushes whole lists.
  S6 routes PC conditions through `changes`.
- **Rate limits:** sheet routes have their own bucket.
- **Talk swap:** Pass the mic replaces `talk.js` and `#talk-slot` only.
- **Two phone pages during the trial:** fixes to the old page's dice or Talk
  need making in `/phone` too, until the switch.
- **Merge with the battle map:** its S6 rewrites `send.py`'s turn function;
  this plan's S6 touches the same allowed-keys list.

## Open for the user

- Offline fallback for `changes` when the display is down (approval note 1).
- Slots, hit dice, items and gold on the TV Party panel (approval note 3).
- AC13 and the milestone 2 table check.
