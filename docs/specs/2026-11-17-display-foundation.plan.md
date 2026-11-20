---
spec: 2026-11-17-display-foundation.md
status: approved      # proposed → approved
---

# Build plan: Display foundation (TV layout, combat mode, theme and voices)

## Answers to open questions

**Kokoro voice blending (D20): supported.**
- The installed engine is `kokoro-onnx` 0.6.1, in `~/.claude/dnd/kokoro-venv/Lib/site-packages/kokoro_onnx`.
- In its `__init__.py`, `Kokoro.create(text, voice: str | NDArray[np.float32], …)` (line 246) takes a style vector as well as a voice name. `get_voice_style(name)` (line 164) returns each voice's vector, so a weighted sum of two vectors is a valid voice.
- `kokoro_setup.py:66` installs `kokoro-onnx` without a version pin, so a fresh install gets a version that has the same API.
- Today `kokoro_server.py:68-76` only ever passes a voice name, and it swaps any unknown voice for `bm_lewis`. It has to learn to accept blends (S12).
- The D20 fallback (reuse the least-used voice) still applies whenever `/health` does not report blend support. That covers an older helper still running from before the update.

**Facts the spec states that the code does not match:**
1. **The old page has no "reconnecting" badge and does not de-duplicate.** `index.html:9304-9310` closes the stream and reconnects silently. On every connect the server re-sends the last 200 entries (`dnd-display-app.py:3429-3432`). `renderReplayBatch` (`index.html:5994`) appends them again, so the old TV duplicates the feed after a drop. The D13 badge and the catch-up are therefore new work, built to AC12 (S2 and S6), not a port.
2. **"Roll requests pinned to the bottom" does not exist as a feed element.** The old TV shows pending rolls as a separate `#dice-pending-badge` ("Waiting on…", `index.html:9989`). `send.py` sends the dice request last (`send.py:549-551`). The new page shows a pending request as the mockup's roll card. The card stays directly under the newest block until the request resolves.
3. **Turn order is replaced, not merged, on the server.** `dnd-display-app.py:1752-1754` stores whatever arrives. `push_stats.py --turn-current` (`push_stats.py:325-329`) and `send.py --turn` (`send.py:619-626`) send `{"current": …}` with no `order`. The old page merges on the client (`index.html:7046-7049`), but a TV that reconnects mid-fight gets a turn order with no names. The new turn bar needs the full order, so S4 merges partial updates on the server.
4. **There is no "end combat" command.** `combat.py` has `init` but no `end` (`combat.py:362`, `:566-582`). Ending combat today is `push_stats.py --turn-clear`, a step the DM runs by hand (`SKILL-commands.md:530`). D12 needs a new `combat.py end`.
5. **There are more Kokoro voices than "~20".** `tts.py:355-363` lists 28 English voices. Blending starts later than the spec expects.
6. **Today's NPC voices are Gemini names with one shared fallback table.** `templates/npcs.md:14` asks for a Gemini name. `kokoro_standin` (`tts.py:406-410`) sends every Gemini voice to a fixed Kokoro voice (`GEMINI_TO_KOKORO`, `tts.py:367-371`). D21 replaces this with a Kokoro voice per NPC. The shared table stays only for requests that carry no fallback (the old page).
7. **Today's panel choice is per browser.** The current panels are saved in `localStorage["dnd-panels"]` (`index.html:7080`), not with the campaign. The new layout ignores that value.
8. **Narrator voice storage.** The narrator voice is now stored in `state.md` as `tts_voice` (`dnd-display-app.py:2305-2381`). D17 moves it to `display.json`. Older campaigns keep working because the reader falls back to `state.md`.

## Shape

**New TV page at `/tv`** (D2, D3, D4). It is a thin Jinja shell, `skills/dnd/display/templates/tv.html`. It carries the same meta tags as `index.html:8-12` (token, narrator voice, voice state). It loads plain scripts and stylesheets from `skills/dnd/display/static/tv/`; Flask serves `/static/` itself, with no build step. The files are:

| File | What it owns |
|---|---|
| `tokens.css` | Theme tokens (D23). |
| `tv.css` | Layout grids. |
| `fonts/` | Bundled Fraunces (roman and italic) and Literata as WOFF2, plus `OFL.txt`. Nothing is fetched from Google at the table. |
| `stream.js` | The SSE connection, reconnecting badge, `seq`/`epoch` de-duplication and catch-up. |
| `feed.js` | Block renderers in order, typewriter, the pinned roll card, the ↶ rewind and replay. |
| `voice.js` | Paced reveal, NPC voice lines, "Tap for audio", volume and speed. |
| `panels.js` | The five modules: Party, Turn order, Battle map, Quests, World clock & effects. |
| `map.js` | Battle map renderer, drawn at the device pixel ratio. |
| `layout.js` | Pure function `resolve(config, stats, override)` returning `{mode, left, right}`, and applying it to the page. |
| `bg.js` | The background layer. |
| `tv.js` | Startup. |

- Each script attaches to `window.TV` and also sets `module.exports` when `module` exists, so node tests can load the pure parts. This follows the node-harness pattern of `tests/test_tts_autoqueue.py`.
- `bg.js` is the old scene, particle and sky code copied as-is from `index.html:4445-5030` (D14).
- The page is drawn at 1920×1080 in `rem`. The root size is `html{font-size:min(100vw/120, 100vh/67.5)}`: 16px at 1080p, 32px at 4K. Text is rendered at native resolution, not a scaled bitmap (D8, AC13). Narration is 1.9375rem (31px) in Story and 1.875rem (30px) in combat.

**New laptop page at `/layout`.** It is `templates/layout.html` with `static/layout/layout.js` and `layout.css`, and reuses `tokens.css`.
- It has a Layout section (presets, modules per panel, combat override) and a Voices section (narrator, NPC engine, volume, speed, read player actions, cast list, quota note).
- It writes through new endpoints. These need `_token_ok()` and a loopback client (`127.0.0.1` or `::1`), because the token is embedded in every page the server serves, phones included.
- It polls `GET /display-config` every 5 s for status (the Gemini note), so it adds no SSE client.

**Saved settings** go in `<campaign>/display.json`, owned by a new module `skills/dnd/display/display_config.py`:
```
{"version":1,
 "layout":{"preset":"story","left":["party"],"right":["quests","clock"]},
 "voice":{"narrator":"bm_lewis","npc_engine":"kokoro","volume":1.0,"speed":1.0,"read_player_actions":false}}
```
- Module ids are `party`, `turns`, `map`, `quests` and `clock`.
- The presets are Story (L: party; R: quests, clock), Tactical (L: party; R: turns, map) and Minimal (no panels).
- Validation: a panel holds 0 modules (hidden) or 1–3. Each module appears at most once. Unknown ids are dropped.
- With no file, the defaults are the Story preset and the `state.md` narrator. The file is written only when the host changes something, so old campaigns are untouched until then.
- Writes are atomic (temp file, then `os.replace`). The settings are re-read when the active campaign changes (`/stats` with `campaign`, `dnd-display-app.py:1860-1866`).

**Cast and voices** live in a new module `skills/dnd/display/cast.py`. It is both a library and a CLI.

*Where voices are stored:*
- NPC voices go in `npcs.md` as `- **Voice:** <kokoro or a+b blend>`, plus `- **Gemini voice:** <name>` when Gemini is on.
- Player-character voices go in `<campaign>/characters/<name>.md` as `- **Voice:** <kokoro>` under `## Identity`.

*Reading and writing:*
- The parser widens `_NPC_VOICE_PAT` (`dnd-display-app.py:2104`) to accept `a+b`.
- Writes use `utf8io.read_text` and keep the file's own line endings, the same pattern as `_write_narrator_voice` (`:2353-2378`). They run under one lock.

*Assigning voices:*
- The pool is the 28 Kokoro voices minus the narrator, minus player-character voices, minus voices NPCs already use.
- Once the pool is used up, a new NPC gets a blend `a+b` (50/50, same voice group, a pair not used before, never containing the narrator's or a player character's voice) when Kokoro reports blend support. Otherwise it reuses the least-used allowed voice.
- An existing `**Voice:** Kore` (a Gemini name) is migrated the first time that NPC is resolved. The line becomes its Kokoro voice (preferring the old stand-in if that voice is free), and `**Gemini voice:** Kore` is added.

*Hooks in the app:*
- `_block_voice` (`dnd-display-app.py:2137`) calls `cast.npc_voices(campaign, name, engine)`. That ensures the NPC has a voice, writes it back, and returns `{voice, fallback}`.
- `/chunk` adds `voice_fallback` to the block payload when the engine is Gemini.
- `/tts` (`:2393`) accepts an optional `fallback` (must be a Kokoro voice or blend). `_local()` (`:2438`) uses it instead of `kokoro_standin`.
- When Gemini runs out with Kokoro up (`:2477-2481`), the server records a `voice_note`. `GET /display-config` reports it under `status`, and the TV never shows it.

**Data flow on the update stream** (`/stream`, `dnd-display-app.py:3409`).
- On connect the stream first sends `{"hello":{"epoch":E}}`, where `E` is a fresh per-boot id.
- Every log entry and live block now carries `seq`. It is a counter that continues from the highest `seq` in the loaded `text_log.json`; older entries without one get numbered at load.
- On connect the stream also sends `{"display":{config, mode_override}}`, the same payload broadcast after every settings change.
- Other new broadcasts: `{"read_actions":[…]}` (D22) and `voice_note`.
- The old page ignores all of the above, because it acts only on keys it knows.
- On reconnect the TV checks the epoch:
  - Same epoch: skip replayed entries with `seq` it has already seen.
  - New epoch (the server restarted): clear the feed and render the replay as history, not spoken.
  - Either way: no duplicated blocks (AC12).

**Combat mode.**
- The mode is computed on the TV by `layout.resolve`:
  - The host's override wins if one is set.
  - Otherwise a `turn_order` whose `order` names at least one combatant means combat: with a map when `stats.map` is set, without one otherwise. A bare `{"current": …}` or `{"order": []}` is not combat (same rule as the server's `_combat_on`, added in S4).
  - Otherwise the saved layout applies.
- The override is runtime-only and is set through `POST /display-mode {"combat": true|false|null}`.
- The server clears the override whenever `turn_order` goes from empty to set or back, so the automatic switch resumes (D9).
- `combat.py init` pushes the turn order itself and the new `combat.py end` clears it. Both use the `push_stats.py` subprocess pattern of `push_map` (`combat.py:469-478`), so neither does anything when no display is running (D12).

**Changes to existing code.**
- `dnd-display-app.py`:
  - `Flask(__name__)` (`:523`) gets absolute `template_folder` and `static_folder`.
  - New routes: `/tv`, `/layout`, `/display-config`, `/display-mode`, `/cast`.
  - `seq` and `hello` added to the stream.
  - Partial `turn_order` merge (`:1752`).
  - Voice hooks (`:2137`, `:2393-2489`, `:2305`).
  - `read_actions` in `_check_auto_trigger` (`:412-453`).
  - The last speech-to-text transcript is remembered per character in `/stt` (`:3169`), so a Talk action can be recognized at stage time.
- `tts.py`: `valid_voice`/`is_kokoro` accept blends, and a cached blend-capability flag.
- `kokoro_server.py`: blend synthesis, and `"blend": true` in `/health`.
- `combat.py`: the init push and the new `end`.
- Skill docs and templates: voice lines, combat steps, cast CLI.
- `start_display.py:181-186`: print the `/tv` and `/layout` URLs.
- `index.html` does not change.

## Steps

### S1: `/tv` route, static files, theme tokens and fonts
- **Covers:** AC1, AC2, D2, D3, D4, D15, D23
- **Files:**
  - Changed: `skills/dnd/display/dnd-display-app.py`
  - New: `skills/dnd/display/templates/tv.html`; `skills/dnd/display/static/tv/{tokens.css,tv.css,tv.js}`; `skills/dnd/display/static/tv/fonts/{Fraunces*.woff2,Literata*.woff2,OFL.txt}`
- **Change:**
  - Give `Flask()` absolute `template_folder` and `static_folder` (based on `_HERE`). Without that, tests that load the app with `spec_from_file_location` resolve templates against the working directory.
  - Add `GET /tv`, which renders `tv.html` with the same variables `index()` passes (`:1319-1326`).
  - `tokens.css` holds the mockup's `data-look="ink"` tokens with the `data-type="hearth"` type (`display-themes.html:84-98`):
    - Colors: `--ink #f1ebe1`, `--ink-dim #b6aa98`, `--accent #e06a55`, `--accent-soft rgba(224,106,85,.16)`, `--npc #f0c89b`, `--you #a9cfe0`, `--hp #c8d79a`, `--hp-low #e06a55`, `--cond #d7a9df`, `--panel transparent`, `--panel-line rgba(241,235,225,.22)`, `--radius 0`.
    - Map: `--map-floor #201914`, `--map-line rgba(241,235,225,.08)`, `--map-wall #0a0705`.
    - Fonts: `--f-display`/`--f-label` Fraunces, `--f-body` Literata. Labels are italic with tracking .01em, at 1.3125rem.
    - Text shadow on open panels: `0 2px 12px rgba(0,0,0,.9)`.
    - The mockup's px sizes, converted to rem at 16px = 1rem.
  - Fonts are declared with `@font-face` pointing at local files, falling back to Georgia. The builder downloads the WOFF2 files once at development time and commits them with the OFL licence. The table needs no network.
  - The shell shows only the empty stage and background placeholder.
- **Done when:**
  - Flask-client tests pass:
    - `/tv` returns 200 with the shell marker.
    - `/static/tv/tokens.css` returns 200 `text/css` and contains `#e06a55`, `Fraunces` and `Literata`.
    - Each font file is served.
    - `/` still renders `index.html` (an old-page marker such as `id="sidebar-right"`).
    - `tv.html` and the CSS reference no `http(s)://` URL.
  - `requirements.txt` still lists exactly flask, flask-cors, pygame, numpy and pyyaml.
  - The full suite is green.

### S2: Stream sequence numbers and boot epoch
- **Covers:** AC12 (server side), D13
- **Files:**
  - Changed: `skills/dnd/display/dnd-display-app.py`
  - New test: `tests/test_stream_seq.py`
- **Change:**
  - Add a `_seq` counter and `_epoch` (a random hex string per boot).
  - `_load_log` (`:1189`) numbers entries that have no `seq` and sets the counter to the maximum.
  - Every place that appends to `_text_log` stamps `seq` on both the log entry and the broadcast payload: `/chunk` (`:1592-1599`) and the award paths (`:1476`, `:1496`, `:1524`).
  - `/stream` sends `{"hello":{"epoch"}}` before the scene and the replay.
  - `/rewind` keeps the counter running and never resets it.
- **Done when:** unit tests pass:
  - Two `/chunk` posts get increasing `seq` in both the payload and `_text_log`.
  - Reloading a log file that contains `seq`-less entries numbers them, and the next post continues above the maximum.
  - The first item from `/stream` is `hello` with the epoch, and the replay entries carry `seq`.
  - Existing tests in `test_display_text.py`, `test_display_robustness.py` and `test_voice_kokoro_and_rewind.py` stay green.

### S3: `display.json` settings and endpoints
- **Covers:** AC4 (server side), AC5, AC6 (server side), D6, D7
- **Files:**
  - New: `skills/dnd/display/display_config.py`, `tests/test_display_config.py`
  - Changed: `skills/dnd/display/dnd-display-app.py`
- **Change:**
  - `display_config.py` provides `PRESETS`, `MODULES`, `default()`, `validate(cfg)`, `load(campaign_dir)` and `save(campaign_dir, cfg)`.
  - The app keeps the current settings in memory. They are reloaded at startup and when `campaign` changes.
  - `GET /display-config` returns `{config, presets, modules, mode_override, status}`.
  - `POST /display-config` (token plus loopback) validates the settings, saves them if a campaign is active, and broadcasts `{"display":…}`.
  - `/stream` sends `{"display":…}` on connect.
- **Done when:** unit tests pass:
  - A campaign with no file gets the Story preset.
  - Saving writes `<campaign>/display.json`, and a fresh `load` (simulating a restart) returns the same settings.
  - The three presets equal D7.
  - A panel with 4 modules, unknown ids or a duplicated module is rejected or cleaned.
  - A POST from a non-loopback address gets 403.
  - A POST broadcasts `display`.

### S4: Combat mode on the server
- **Covers:** AC7 (server side), D9
- **Files:**
  - Changed: `skills/dnd/display/dnd-display-app.py`
  - New test: `tests/test_combat_mode.py`
- **Change:**
  - In `/stats` (`:1752-1754`), a `turn_order` dict without `order` merges into the stored one, so `current` and `round` update and `order` is kept. `None` clears.
  - Track whether combat is on. On the empty↔set transition, clear `_mode_override` and broadcast `display`.
  - Add `POST /display-mode {"combat": true|false|null}` (token plus loopback).
- **Done when:** unit tests pass:
  - `{"turn_order":{"current":"X"}}` after a full order keeps `order`.
  - Setting, then clearing, the turn order each clear the override.
  - The manual override true/false/null is stored and broadcast.
  - `tests/test_turn_and_awaiting.py` stays green.

### S5: Starting combat starts combat mode
- **Covers:** AC10, D12
- **Files:**
  - Changed: `skills/dnd/scripts/combat.py`, `skills/dnd/SKILL-commands.md` (`:513-530`), `skills/dnd/SKILL-scripts.md` (`:106`, `:244-254`, `:301`)
  - New test: `tests/test_combat_push.py`
- **Change:**
  - `combat.py init` builds `{"turn_order":{"order":[names in initiative order],"current":first,"round":1}}` and pushes it through `push_stats.py --json`. `--no-push` opts out.
  - The new `combat.py end [--clear-map]` pushes `{"turn_order": null}`, and also `{"map": null}` with `--clear-map`.
  - The docs drop the separate `push_stats.py --turn-order` and `--turn-clear` steps from combat start and end.
- **Done when:** unit tests pass with the push function mocked:
  - `init` pushes exactly the ordered names with the first as `current` and round 1.
  - `end` pushes `turn_order: None`.
  - `--no-push` pushes nothing.
  - `combat.py end --bogus` exits 2 (`tests/test_strict_cli.py` pattern).

### S6: TV feed, reconnect and background
- **Covers:** AC3 (partly), AC11, AC12, D13, D14, D16
- **Files:**
  - New: `skills/dnd/display/static/tv/{stream.js,feed.js,bg.js}`, `tests/test_tv_feed.py`
  - Changed: `tv.html`, `tv.css`, `tv.js`
- **Change:**
  - `stream.js` handles the SSE connection.
    - On error it shows a "Reconnecting…" badge right away (a 1 s grace period, so it appears well inside 3 s) and retries with backoff up to 3 s, as at `index.html:9304`.
    - It hides the badge on open.
    - It applies the `seq`/`epoch` rules from Shape.
  - `feed.js` renders, in order: narration, NPC, player, action, dice, tutor, inspiration, milestone, XP and effect-expired blocks.
    - It ports the markdown parsing (bold, italic, paragraphs). *As built:* blocks appear whole in S6; the typewriter and its deferral queue (`index.html:5523-5583`) only matter once text types out, so they arrive with the voice-paced reveal in S9.
    - Pending `dice_request` and `dice_pending` show as a roll card that stays directly after the newest block, removed on cancel or completion.
    - The ↶ appears on the newest player or action block, with a two-tap confirm and `POST /rewind` (`index.html:5604-5652`).
    - `rewind` and `clear` events re-render the feed.
    - Earlier blocks are dimmed. The DOM is pruned to about the last 80 blocks.
  - `bg.js` copies the scene, particle and sky code unchanged, driven by the `scene` event and `stats.world_time`.
  - `devices.js` (D24) ports the phone-approval cards (`index.html:8937-9015`): a `device_request` event (live, or replayed on connect, `dnd-display-app.py:3659`) shows a card with the phone's IP, Approve and Deny; the buttons POST `/device/approve` or `/device/deny` with the page token; `device_approved` / `device_denied` remove the card. A request already on screen is not shown twice. The old page's own-phone "Awaiting approval…" logic stays on the phone page and is not ported.
  - Each event handler runs inside try/catch, so one bad payload cannot stop the feed.
- **Done when:**
  - Node tests (skipped without node) pass:
    - Feeding `hello(E1)` plus a replay, then a reconnect with `hello(E1)` and an overlapping replay, renders each `seq` once.
    - `hello(E2)` re-renders from the replay with no duplicates.
    - A roll card stays last after later blocks and goes away on `dice_pending: []`.
    - A `device_request` shows one card per device id (a replayed request doesn't duplicate it); Approve posts `/device/approve` with `{id}`; `device_approved` and `device_denied` remove the card.
  - Browser checks:
    - Stopping the server shows the badge within 3 s.
    - Restarting it hides the badge with no duplicated blocks.
    - The ↶ appears and rewinds.

### S7: Panels and the story layouts
- **Covers:** AC3, AC4 (TV side), AC6, D5, D7, D8
- **Files:**
  - New: `skills/dnd/display/static/tv/{panels.js,layout.js}`, `tests/test_tv_layout.py`
  - Changed: `tv.css`, `tv.js`
- **Change:**
  - `layout.resolve()` returns story, minimal or custom panel stacks from the saved settings.
  - The Story grid is `26.25rem 1fr 26.25rem` with the player-input bar along the bottom of the centre column (mockup `.story`). Minimal centres narration with a capped line length.
  - Modules:
    - Party: name, HP bar, conditions.
    - Turn order: list with the current turn highlighted.
    - Quests: from `stats.quests`.
    - World clock & effects: from `stats.world_time` and `players[].effects`.
  - Thin rules separate the columns. There are no boxes.
  - The input bar ports the wait-banner text logic (`index.html:8403-8441`).
  - A `display` event re-applies the layout with no reload.
- **Done when:**
  - Node tests pass: `resolve` gives D7's stacks for each preset; a custom 3-module panel is kept; Minimal has no panels.
  - Browser at 1920×1080:
    - Narration's bounding box is the largest region.
    - The computed narration font size is ≥ 28px.
    - A `POST /display-config` from a second tab changes the TV in under 1 s.

### S8: Combat layouts and the battle map
- **Covers:** AC7, AC8, AC9, AC13, D10, D11
- **Files:**
  - New: `skills/dnd/display/static/tv/map.js`, test additions in `tests/test_tv_layout.py`
  - Changed: `tv.css`, `layout.js`, `panels.js`
- **Change:**
  - **Combat with a map:** a grid of `60% 40%`, with the turn bar across the top (round plus a token per combatant, the current turn highlighted).
    - The map fills the left column.
    - The right column has a compact Party header above a full-height narration column at 1.875rem. The newest block sits at the bottom, older blocks show above it, and the top edge fades out (mockup `.combat`).
  - **Combat without a map:** turn order large in the centre, narration at full size below it, Party on the right.
  - `map.js` ports `_bmRender` and `_bmDrawCells` (`index.html:8499-8624`) with three changes:
    - The canvas is sized at `devicePixelRatio`.
    - Tokens are sized in rem.
    - The map is drawn into whichever container holds it: the combat column, or a Tactical side panel.
  - Mode switches come from `stats` and `display` events.
- **Done when:**
  - Node tests pass: `resolve` with `turn_order` and a map gives combat-map; without a map, combat-nomap; with the override false, the story layout; a cleared turn order goes back to the saved layout.
  - Browser:
    - Pushing a turn order switches the TV within 1 s, and `--turn-clear` switches it back.
    - Screenshots of both combat layouts at 1920×1080 and 3840×2160 have the same proportions, with no blurry text or map.

### S9: Voice on the TV
- **Covers:** AC8 (typing in step), AC11, D13
- **Files:**
  - New: `skills/dnd/display/static/tv/voice.js`, `tests/test_tv_voice.py`
  - Changed: `feed.js`, `tv.js`
- **Change:** port from `index.html`:
  - The paced reveal: gates, prefetch and a single audio timeline (`:7404-7516`).
  - The auto-narrate queue (`:7594-7636`).
  - `/tts` fetching with `voice`, `fallback` and `speed` from the settings (`:7665`).
  - Roll stripping (`:7698`).
  - The "Tap for audio" button (`:7881-7897`).

  New behaviour on the TV:
  - NPC blocks type in step with their own voice.
  - Volume goes through a GainNode.
  - The TV shows no voice-state or quota toasts (D17).
  - `read_actions` items join the same audio timeline ahead of any later narration.
- **Done when:**
  - Node tests pass, with the same harness approach as `test_tts_autoqueue.py`: blocks of a burst play one after another; a gate that fails or times out types without voice; narration that arrives while a read action is playing waits for it.
  - Browser with Kokoro: narration types in step with the voice, and "Tap for audio" appears and unlocks.

### S9a: Replay a block (added 2026-11-19)
- **Covers:** AC20, D25
- **Files:**
  - Changed: `static/tv/voice.js`, `feed.js`, `tv.js`, `tv.css`; tests in `tests/test_tv_voice.py`
- **Change:**
  - `voice.replay(block)` reads a whole narration or NPC block again: narration sentence by sentence with the same `/tts` bodies (`local_only` kept), an NPC line in one request with its voice and fallback. Audio only; `type` is not called.
  - It stops any earlier replay. It does nothing while live voice is playing or queued. A block enqueued while a replay plays stops the replay first (`stop()`), so live narration starts on time.
  - `voice.stopReplay()` and `voice.replaying()` (the block, or null) let the feed show ▶ / ■ and stop on a second tap.
  - `feed.js` adds a faint ▶ button to each narration and NPC block that is done typing (history included), styled like ↶. It is hidden while voice is off.
- **Done when:**
  - Node tests pass: a replay fetches the block's sentences in order with the same bodies and never calls `type`; a second replay of the same block stops it; a live block arriving mid-replay stops it and plays on time; a replay asked for while live voice plays does nothing; an unspoken kind (dice, player) can't be replayed.
  - Browser: ▶ replays a narration block and an NPC block in their voices, ■ stops it, and new narration cuts a replay off.

### S10: Laptop page, layout section
- **Covers:** AC4, AC6, AC7 (manual override), D6
- **Files:**
  - New: `skills/dnd/display/templates/layout.html`, `skills/dnd/display/static/layout/{layout.js,layout.css}`
  - Changed: `dnd-display-app.py` (`GET /layout`)
- **Change:**
  - Preset buttons.
  - Left and right panel pickers (up to 3 modules each, order editable).
  - A combat override control: Auto, Combat or Story.
  - Each change POSTs immediately.
- **Done when:**
  - Flask test: `/layout` returns 200.
  - Browser, laptop and TV tabs side by side: each preset and a custom stack appear on the TV within 1 s; the override flips both ways; settings survive a server restart.

### S11: The cast, with an NPC voice for every NPC
- **Covers:** AC15, AC16 (without blends), D18, D19
- **Files:**
  - New: `skills/dnd/display/cast.py`, `tests/test_cast.py`
  - Changed: `dnd-display-app.py` (`_npc_voices`/`_block_voice` at `:2104-2156`, `_read_narrator_voice` at `:2305`, `/voice` at `:2492`); `skills/dnd/templates/npcs.md:14`; `SKILL-commands.md` (`/dm:dnd npc` new at `:494`, `/dm:dnd new` step 11 at `:43`)
- **Change:**
  - `cast.py` parses NPCs and characters, computes the free pool, assigns and writes voices, and migrates Gemini names.
  - CLI: `cast.py voices --free -c <campaign>` (lists free voices by accent and sex for the DM to choose from), and `cast.py set -c <campaign> --npc|--pc <name> <voice>` (validates and writes).
  - An NPC that speaks with no section gets a stub `### Name` plus `- **Voice:**` appended. Name matching:
    - A case-insensitive exact match comes first.
    - Then a unique heading whose first word matches.
  - The narrator comes from `display.json` first, then `state.md`, then the default.
  - Saving a narrator voice that an NPC holds re-assigns that NPC.
  - `GET /cast` and `POST /cast` (token plus loopback) serve the laptop's cast list.
  - The docs tell the DM to pick a fitting free voice and write it when creating an NPC.
- **Done when:** unit tests pass:
  - An NPC block with no voice gets one written to `npcs.md`.
  - The voice is never the narrator's or a player character's.
  - Re-loading the app (next session) resolves the same voice.
  - Twenty-plus new NPCs all get different voices until the pool is used up.
  - A CRLF `npcs.md` keeps CRLF.
  - A Gemini-named `Voice:` line is migrated.

### S12: Kokoro blends
- **Covers:** AC16, D20
- **Files:**
  - Changed: `skills/dnd/display/kokoro_server.py` (`:68-76`, `:91-95`), `skills/dnd/display/tts.py` (`:398-410`, `:413-421`, `:424-452`), `cast.py`
  - New test: `tests/test_voice_blends.py`
- **Change:**
  - `Engine.synth` accepts `a+b`, synthesizing with `0.5*get_voice_style(a)+0.5*get_voice_style(b)` and the language of `a`.
  - `/health` adds `"blend": hasattr(self.k, "get_voice_style")`.
  - `tts.valid_voice`/`is_kokoro` accept a blend whose two parts are both known.
  - `kokoro_health` caches the blend flag.
  - `cast.py` assigns blends past the pool only when the flag is true; otherwise it reuses the least-used allowed voice.
- **Done when:**
  - Unit tests pass:
    - Pool exhausted with blending on: unique blends are saved per NPC and never contain the narrator's or a player character's voice.
    - Blending off: the least-used voice is reused, again never the narrator's or a player character's.
    - `valid_voice("am_onyx+am_adam")` is true and `"am_onyx+nobody"` is false.
    - The `Engine.synth` blend path, with a fake Kokoro object.
  - Listening test by the user: five blended NPCs sound distinct.

### S13: Gemini mode with each NPC's own fallback, and the laptop note
- **Covers:** AC14 (server side), AC17, D17, D21
- **Files:**
  - Changed: `dnd-display-app.py` (`/chunk` `:1544-1546`, `/tts` `:2414-2489`), `cast.py`, `display_config.py`
  - New test: `tests/test_npc_engine.py`
- **Change:**
  - With `npc_engine: "kokoro"`, NPC blocks carry their Kokoro voice, so Gemini is never called for them.
  - With `npc_engine: "gemini"`:
    - NPC blocks carry the NPC's Gemini voice (assigned least-used from `VALID_VOICES` and written back if missing) plus `voice_fallback` = its Kokoro voice.
    - `/tts` uses `fallback` for every local stand-in path (`:2457-2458`, `:2462-2463`, `:2477-2481`, `:2486-2487`).
  - When the daily quota runs out with Kokoro up, the server stores `voice_note`: "NPCs moved to Kokoro — Gemini quota used up, back in about N h". `GET /display-config` returns it under `status`. Nothing is sent to the TV.
  - Voice settings apply from the next line: settings are read at `/chunk` time, and volume and speed on the TV are read per request.
- **Done when:** unit tests pass:
  - Simulated 429 in Gemini mode: two NPCs come back with their own distinct Kokoro voices (`X-Audio-Voice`).
  - The note appears in `/display-config` status.
  - No `tts_quota` or `voice` broadcast goes out.
  - In Kokoro mode, `synthesize_strict` is never called for an NPC block.
  - A `/tts` request without `fallback` still uses `kokoro_standin`, so `tests/test_voice_kokoro_and_rewind.py:86-97` stays green.

### S14: Player-character voices and "Read player actions aloud"
- **Covers:** AC18, D22
- **Files:**
  - Changed: `cast.py`, `dnd-display-app.py` (`_check_auto_trigger` `:412-453`, `/stt` `:3164-3169`), `skills/dnd/templates/character-sheet.md` (Identity), `SKILL-commands.md` (character new step 7 at `:425`, import-ddb step 3 at `:445`, import at `:454`)
  - New test: `tests/test_pc_voices.py`
- **Change:**
  - At character creation, D&D Beyond import or campaign import, the DM runs `cast.py set --pc <name> <voice>`, choosing a fitting free voice. This happens before the roster mirror step.
  - The server back-fills a player character with no voice when the cast list loads or the character's action is about to be read.
  - Player-character voices are excluded from every NPC assignment.
  - `/stt` stores the character's last transcript and the time.
  - In `_check_auto_trigger`, when `read_player_actions` is on, the server broadcasts `{"read_actions":[{character, text, voice}]}` for each action whose text does not contain that character's transcript from the last 10 minutes. Talk actions are not read back.
- **Done when:** unit tests pass:
  - `cast.py set --pc` writes `**Voice:**` into the character file.
  - NPC assignment never returns a player character's voice.
  - The setting defaults to off, and when off, `read_actions` is not broadcast.
  - When on, a typed action is broadcast with the character's voice.
  - An action whose text came from `/stt` is not broadcast.

### S15: Laptop page, voices section and cast list
- **Covers:** AC14 (browser), D17, D18
- **Files:**
  - Changed: `templates/layout.html`, `static/layout/layout.js`, `layout.css`
- **Change:**
  - A narrator picker with Preview (a `/tts` call through the laptop's own audio).
  - NPC voices: "Kokoro only" or "Gemini, falling back to Kokoro".
  - Volume and speed sliders.
  - A "Read player actions aloud" toggle.
  - The quota note, read from status polling.
  - The cast list: narrator, player characters and NPCs with their voices, each with Preview and Change (`POST /cast`).
- **Done when:** browser checks:
  - Each setting appears in `display.json` and takes effect on the next line.
  - A simulated quota exhaustion shows the note on the laptop only; the TV shows nothing.
  - Changing an NPC's voice in the cast list updates `npcs.md`.

### S16: Launcher, docs and the parity pass
- **Covers:** AC2 (fresh install), AC11, AC13
- **Files:**
  - Changed: `skills/dnd/display/start_display.py` (`:181-186`), `skills/dnd/display/README.md`, `docs/ROADMAP.md` (Phase 5), `CHANGELOG.md`
- **Change:**
  - Print `TV (new): http://<ip>:5001/tv` and `Layout & voices (this laptop): http://localhost:5001/layout`.
  - Document the `/tv` trial period.
- **Done when:**
  - Full suite green, plus the tests in Blockers once updated with the user's yes.
  - Fresh install on Windows: `pip install -r requirements.txt`, then `start_display.py` serves `/tv` with fonts loading offline.
  - A playtest run against every D13 item, with screenshots at 1080p and 4K.

### S17: Switch `/` after the game night (gated)
- **Covers:** AC19, D2, D4
- **Files:**
  - Changed: `dnd-display-app.py` (`index()` at `:1316`)
- **Change:** done only after the user confirms one real game night on `/tv` with no fallback.
  - `/` serves `tv.html`.
  - `/` with `?char=`, `?character=` or `?view=input` still serves `index.html`, so phones keep the old page (D4).
  - `/old` serves `index.html` as the TV fallback.
  - `index.html` is not deleted.
- **Done when:**
  - Flask tests pass: `/` gives the TV shell; `/?char=Mira` and `/old` give the old page.
  - The user's playtest note is recorded in the spec.

**AC coverage:**

| AC | Steps |
|---|---|
| AC1 | S1 |
| AC2 | S1, S16 |
| AC3 | S6, S7 |
| AC4 | S3, S7, S10 |
| AC5 | S3 |
| AC6 | S3, S7, S10 |
| AC7 | S4, S8, S10 |
| AC8 | S8, S9 |
| AC9 | S8 |
| AC10 | S5 |
| AC11 | S6, S9, S16 |
| AC12 | S2, S6 |
| AC13 | S8, S16 |
| AC14 | S13, S15 |
| AC15 | S11 |
| AC16 | S11, S12 |
| AC17 | S13 |
| AC18 | S14 |
| AC19 | S17 |

## Risks

- **Older campaigns.**
  - No `display.json`: Story preset, narrator from `state.md` (S3, S11).
  - `npcs.md` voices that are Gemini names: migrated one NPC at a time when they speak. Each migration rewrites one line and adds one line, keeping the file's line endings.
  - Character files with no `Voice:` line: back-filled only when needed.
  - `text_log.json` entries without `seq`: numbered at load.
  - Nothing is written to a campaign until the host changes a setting or a voice is needed.
- **The old page keeps working throughout.**
  - New stream keys (`hello`, `seq`, `display`, `read_actions`) are ignored by `index.html`.
  - NPC blocks in Kokoro mode now carry Kokoro voices, which the old page already plays through `/tts`.
  - `/tts` without `fallback` behaves exactly as today.
- **Name mismatches create voice stubs.** If the DM sends `--npc "Odessa"` while the heading is `### Odessa Varn`, the first-word rule matches. A truly new name gets a stub section in `npcs.md`; the stubs are visible and editable in the cast list.
- **The loopback rule for writes.** If someone opens `/layout` from another device, its changes are refused (403). The page should say "Open this on the computer running the game."
- **Gaps in the TV parity list.** The old TV's on-screen dice roller, DM Help button, sound effects toggle and device-approval cards are not in D13, so `/tv` lacks them. A player without a phone needs `/` until the phone-sheet spec. The game night for AC19 will show whether anything else is missing.
- **Silent network drops.** EventSource only reports errors when the connection actually fails. A Wi-Fi stall with no reset may not show the badge in 3 s. AC12 (server stopped) is met; a heartbeat event can be added later if needed.
- **Fonts.** If the WOFF2 files are missing, the page falls back to Georgia: still readable, but off-theme. The mockup's Google Fonts link must not be copied.
- **Blend quality.** Blends are subjective; the user's listening test (AC16) decides. Per-NPC blends can be swapped from the cast list.
- **Two copies of the display code.** Code ported from `index.html` (feed, voice, map, background) will drift from the old page during the trial. Fixes made in one place during the trial must be made in both.
- **One unrelated finding:** `resizeCanvas` in the copied background code calls `ctx.scale` on every resize, so the scale accumulates (`index.html:4507-4517`). It is copied unchanged per D14.

## Blockers

- **AC11 versus D17, D19 and D21: two existing tests check behaviour these decisions replace.**
  - `tests/test_npc_voices.py:73-74` expects Mira's block to carry the Gemini voice `Kore`. With the default Kokoro-only NPC engine (D17, D21), it carries her own Kokoro voice.
  - `tests/test_npc_voices.py:76-77` (`test_placeholder_voice_ignored`) expects an NPC with a placeholder voice to get no voice. D19 requires the server to assign one.
  - "Existing display tests still pass" cannot hold for these two as written. The plan updates them to assert the D17/D19 behaviour in S11 and S13, and needs the user's yes to do so. Every other existing test is expected to stay green unchanged.
