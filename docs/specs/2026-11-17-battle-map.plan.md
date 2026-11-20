---
spec: 2026-11-17-battle-map.md
status: proposed      # proposed → approved
---

# Build plan: Battle map (tiles, fog, timed moves, spell areas, attack sprites)

## Answers to open questions

The spec has no open questions. The code did raise questions the spec doesn't answer. Each one is answered below from the code, and each answer stays inside the decisions.

**Facts the spec states that the code does not match:**

1. **Moves are not tied to narration today.**
   - `send.py:_apply_turn_moves` (`send.py:632-668`) applies every move up front.
   - The resulting map rides the single `/stats` post that goes out after all the chunks (`send.py:694-702`).
   - The TV draws `stats.map` the moment it arrives (`index.html:8611-8623`). So tokens jump while the voice is still pacing the first block.
   - `SKILL.md:194` ("the tokens slide as the narration arrives") is therefore wrong today. D12 is new work, not a fix to a timing setting.
2. **Monsters' exact HP reaches every screen.**
   - `battlemap.display_payload` sends `hp: [cur, max]` for monsters (`battlemap.py:279-282`), and `_clean_map` passes it through (`dnd-display-app.py:2083-2084`).
   - The old page draws the exact fraction (`index.html:8489-8496`).
   - AC3 replaces this. Two existing tests assert exact monster HP in the payload and must change: `tests/test_battlemap.py:127` and `:221` (see S4 and S5).
3. **The size cap is 80×80, not 30×20.** See `battlemap.py:72` and `dnd-display-app.py:2070`.
4. **No portraits exist anywhere** (no `portrait` in `skills/dnd`, apart from a CSS comment). The plan adds an optional per-token `portrait`: a path under the campaign's `maps/` folder, served by the existing `/map-image` route (`dnd-display-app.py:2094-2111`).
5. **Monster conditions never reach the map.**
   - `display_payload` takes a `conditions` argument, but `push_map` never passes it (`combat.py:475`).
   - Monster conditions live in `<campaign>/tracker.json` (`tracker.py:97-100`).
   - `tracker.py` also pushes them as a "player" (`tracker.py:300-310`), and the old page merges those by name (`index.html:8597-8598`).
6. **The layout has one difficult-terrain character, `~`** (`battlemap.py:43`). D6 mentions "water and rough ground", but D6 also keeps the existing format, and AC1 lists only floor, wall, door, difficult terrain and obstacle. `~` gets one "rough ground / shallows" tile. Separate water would need a new layout character, which D6 rules out.
7. **The layout carries no sight information.** The plan treats `#` (wall) and ` ` (outside the map) as blocking sight. `T` (obstacle) and `D` (open door, `battlemap.py:19`) do not. This follows D9's "using the walls already in the layout".
8. **There is no dry run.**
   - `combat.py move` saves on success (`combat.py:517-521`).
   - Its errors only say "blocked or occupied" or "needs X ft … has Y ft" (`battlemap.py:207-212`). Nothing says why the route is long.
   - AC6's "blocking wall" reason is new.
9. **There is no hidden flag** on tokens (`battlemap.py:190`).
10. **D18's "redraw from `battlemap.json`" happens through the server's copy.**
    - The TV redraws from `stats.map`. The server holds it in `_current_stats`, persists it in `stats.json` (`dnd-display-app.py:1223-1247`, `:1827-1828`) and re-sends it on connect (`:3451-3454`).
    - That copy is the last push of `battlemap.json`, so it is the same state as long as every map change pushes. All `combat.py` map commands do (`combat.py:548-549`).
    - Rewind restores `battlemap.json` along with `stats` (`rewind.py:33-43`: top-level `.json` files), so fog history rewinds too.
11. **The D&D Beyond Maps offer exists only in `SKILL.md:198-200`.** `README.md:66,219` and `.claude-plugin/plugin.json:4` describe the `maps` skill, which D3 keeps in the repo, so they stay.
12. **`combat.py init` does not push a turn order yet.** That is foundation S5. Combat mode (D2) depends on it.

## Shape

**Python: `battlemap.py` stays the single source of map rules** (D1). It gains these functions:

- **`find_path(m, start, goal, mover, kind) -> (cost, path) | None`.** This is today's Dijkstra (`battlemap.py:134-167`) plus predecessor tracking. `path_cost_ft` becomes a thin wrapper around it.
- **`check_move(m, name, x, y, dash=False) -> dict`, the reusable dry run** (D13, AC6). It never mutates the map. It returns:
  - `ok`, `name`, `from`, `to`, `cost`, `budget`, `left`, `straight_ft`, `path`;
  - `reason`, one of `None|"off_map"|"blocked"|"occupied"|"no_path"|"too_far"`;
  - `occupant`, `detour_by` (`"walls"|"creatures"|None`), `difficult_ft`, and a one-line `message`.
  - `detour_by` comes from two extra cheap searches on the same grid: one ignoring walls and obstacles, one ignoring hostile creatures. Either can explain why `cost > straight_ft`.
  - The message keeps today's "needs N ft … has M ft" wording, so `test_battlemap.py:148` stays green, and adds, for example, "walls block the direct route (25 ft straight)".
  - `move()` calls `check_move` and raises `MapError(message)`.
  - The Phone sheet's Map tab will later call `check_move` through a server endpoint and draw `path` and `cost`.
- **The size cap.** `MAX_W = 30` and `MAX_H = 20`, with `check_size(w, h)` (D7). It is used only by `map init`, so older, larger saves still load.
- **Hidden flag.** Tokens can carry `hidden: true`. `place(..., hidden=, portrait=)` and `set_hidden(m, name, bool)` set it.
- **Fog data in `battlemap.json`.** It is `"fog": {"trail": [[x,y]…], "show": [[x,y]…], "hide": [[x,y]…]}`.
  - `trail` holds the unique squares PCs have stood on or passed through: every square of a PC's non-forced path, a forced move's end square, and a PC's placement square. It is capped at `w*h`.
  - `show` and `hide` are Claude's manual overrides. `set_fog(m, mode, x1, y1, x2, y2)` takes `mode` `reveal|hide|auto` and edits both lists.
  - The TV turns `trail` into the dim "seen earlier" squares (see below), so line of sight stays the TV's job (D9) while surviving a reload (D18).
  - An old file with no `fog` key gives an empty trail and no overrides. The TV then works fog out from the current PC positions, which is exactly D18.
- **`hp_state(cur, max)`.** It returns `healthy` above 50%, `bloodied` from 50% down to above 25%, and `near_death` at 25% or less.
- **`area_squares(m, area) -> list`** (D14). Shapes:
  - `sphere` (alias `cylinder`): `at` and radius `size`.
  - `cube`: top-left `at`, side `size`.
  - `cone`: `from` (a token name or square) toward `to`. Length is `size`, with a half-angle of atan(½), so the width equals the length.
  - `line`: `from` toward `to`. Length is `size` and `width` defaults to 5.
  - A square is included when its centre lies inside the shape. Wall squares and squares off the map are excluded.
- **`display_payload(m, conditions=None)` v2.**
  - Hidden tokens are left out entirely (AC9).
  - Monsters get `hp_state` and never `hp`. PCs keep `hp` only if it was set on the token; the TV prefers `stats.players`.
  - Tokens gain `portrait` and `conditions`, and the payload gains `fog`.
  - `combat.py push_map` passes conditions read from `tracker.json` when that file exists.

**`combat.py` CLI.**
- `move NAME X Y --check [--json]`: a dry run with exit 0 or 2, which saves and pushes nothing.
- `hide NAME` and `reveal NAME`.
- `place … --hidden --portrait maps/tokens/x.png`.
- `fog reveal|hide|auto X1 Y1 [X2 Y2]` and `fog auto --all`.
- `map init` refuses layouts or sizes over 30×20: "A battle map is at most 30 squares wide and 20 tall; this layout is 34×22. Split the fight into areas or simplify the drawing." It exits 2 and writes nothing.

**Turn-call format, for both milestones** (D12, D14, D15).
- Any text block may carry:
  - `"moves": [{"name","x","y","dash"?,"force"?,"turn"?,"hp"?}]`
  - `"area": {"shape","size","at"|"from","to"?,"width"?,"label"?}`
  - `"attack": {"from","to","kind":"melee|ranged|spell","hit":bool}` or `"attacks": [...]`
- A turn-level `moves` (today's form) still works. It is treated as belonging to the first block.
- Validation and rejection:
  - `send.py` loads the map once and applies every move in block order, with today's checks. It also validates every area (shape, size 5–150, squares on the map, known `from` token) and every attack (known tokens, kind, boolean `hit`).
  - Any failure rejects the whole call with the reason, before any `_post` (D13, AC5). The map is saved once, only on success.
- What gets sent:
  - The first chunk of each block carries `map_fx: {"moves":[{"name","x","y","path"?,"hp_state"?}], "area":{"squares","label","shape"}?, "attacks":[…]?}`.
  - Hidden tokens are stripped from `map_fx`. Their moves are dropped. An attack from a hidden attacker loses its `from`. An attack on a hidden target is dropped.
  - The final `stats.map` still goes in the one `/stats` post after the blocks, as today.
- Milestone 1 already validates and forwards `attacks`. The TV ignores them until milestone 2, and the DM docs introduce them in milestone 2.

**Server (`dnd-display-app.py`).**
- `/chunk` (`:1459-1617`) forwards a cleaned `map_fx` on the live broadcast only.
  - The cleaning (`_clean_map_fx`) whitelists keys, takes ints only, and caps the sizes: moves ≤ 20, path ≤ 60, area squares ≤ 600, attacks ≤ 8, names ≤ 40 characters.
  - `map_fx` is not written to `_text_log`, so a replay never carries effects (AC11).
- `_clean_map` (`:2061-2091`):
  - keeps `fog`, `hp_state` and `portrait`;
  - drops any token flagged `hidden` (a second guard for AC9);
  - turns a monster's `hp` list into `hp_state`, covering raw `push_stats --json` pushes.
- `_load_stats` (`:1233`) runs a stored `map` through `_clean_map`, so a `stats.json` saved before this change can't replay exact monster HP.

**The TV: shared map code in `skills/dnd/display/static/map/`.** This is new, so `/tv` now and the Phone sheet's `/phone` later load the same files. Each file follows the foundation pattern (attach to `window.TV`, export to `module` for node tests).

| File | What it owns |
|---|---|
| `board.js` | Pure `fit(w, h, boxW, boxH, dpr)` → integer square size and offsets (whole map, no pan or zoom, D7). Pure `tileKind(ch)`. Pure `tokenView(token, players, current, mine)` → initials, ring fraction (exact for PCs, fixed steps for `hp_state`), HP label (PCs only), condition icons, highlight classes. The renderer: a tile canvas at `devicePixelRatio` (stone floor with per-square seeded texture, solid bevelled walls, plank doors, rough ground, obstacle blocks; translucent over the background image when `image` is set, as `index.html:8509-8513` does), tokens as DOM discs with an SVG HP ring and `<img>` portraits from `/map-image/…`, and an SVG overlay layer for areas and sprites. |
| `los.js` | Pure line of sight. `visibleFrom(cells, x, y)` casts centre-to-centre rays and stops at the first `#` or ` `. The blocker itself is visible, so a room's walls show. A ray passing exactly through a corner between two diagonal blockers is stopped, matching the movement rule at `battlemap.py:157`. Results are memoised per origin for each `cells`. `fog(map, shownTokens, seen) → {visible, seen}`, applying `show`/`hide`, plus `tokenShown(tok, fog)`: PCs always, others only when one of their squares is visible. |
| `fx.js` | The effect timeline (pure state machine; timers are injected). `arrive(id, fx)`, `start(id)`, `skip(id)`, `setTarget(map)`, `flushAll()`, `shownState()`. A token with unplayed effects keeps its on-screen state until its last effect plays; everything else takes the latest `stats.map` at once. |
| `sprites.js` | Milestone 2: SVG and CSS builders for the sword swing, arrow, bolt, hit flash, whiff and deflection (D16). |
| `map.css` | Tile, token, fog-fade, area-outline and sprite keyframes, using the foundation's `tokens.css` variables (`--map-floor`, `--map-wall`, `--map-line`, `--hp`, `--hp-low`, `--cond`, `--accent`). |

`static/tv/map.js` (foundation S8) becomes the TV adapter. It:
- picks the container (the combat column or a Tactical side panel) and feeds `stats` into `fx.setTarget`;
- passes feed hooks on to `fx` and redraws on resize.

**Timing on the TV** (D12, D17).
- `feed.js` and `voice.js` (foundation S6 and S9) call:
  - `TV.map.blockArrived(seq, map_fx)` when a live block is queued;
  - `TV.map.blockStarted(seq)` when its paced reveal begins;
  - `TV.map.blockSkipped(seq)` when it is rendered as history or skipped.
- Map handlers are fire-and-forget, so the feed never awaits them.
- Moves slide over about 600 ms with a CSS transform. Areas outline for 3 s. Sprites (milestone 2) finish within 1 s.
- **Seen squares.** The TV rebuilds `seen` from `fog.trail` only when it loads a map: first load, reconnect, or new `cells`. After that it grows `seen` from the squares PCs actually cross on screen, using each played move's `path`. Dim squares therefore never run ahead of the voice.
- **Skips** (AC11, AC16). `visibilitychange`→visible, `rewind`, `clear` and a reconnect each call `flushAll()`, which snaps to the current state with no animation. A 90 s safety flush covers a block that never starts, so the map can't stay stale.

**Data flow summary.**
- `combat.py` or `send.py` writes `battlemap.json`, then pushes `/stats {map}` through `_clean_map`; the server stores it in `stats.json` and broadcasts `stats`.
- `send.py` sends `/chunk {…, map_fx}`, which is broadcast live and never logged.
- On reconnect, `/stream` sends `stats.map` (`:3451-3454`), and the TV draws it with no effects.
- No new endpoint, port or dependency (D18).

## Steps

Each step lists the foundation steps it must wait for. Steps S1–S5 need none and can land now.

### Milestone 1

### S1: Stop offering D&D Beyond Maps
- **Covers:** AC13, D3
- **Waits for:** nothing.
- **Files:**
  - Changed: `skills/dnd/SKILL.md` (remove the `## D&D Beyond Maps (VTT)` section and its separators, `:196-200`)
  - New test: `tests/test_maps_offer_removed.py`
- **Change:**
  - Delete the section. In "Battle map on the TV" (`:176-194`), remove any reference to Maps.
  - `skills/maps/**`, `README.md` and `plugin.json` are untouched.
- **Done when:**
  - The unit test passes: `SKILL.md` contains neither `/dm:maps`, `vtt.md` nor "D&D Beyond Maps"; `skills/maps/SKILL.md` still exists.
  - `git diff --stat skills/maps` is empty.

### S2: Dry-run move check with reasons
- **Covers:** AC6, D13 (player moves), D4
- **Waits for:** nothing.
- **Files:**
  - Changed: `skills/dnd/scripts/battlemap.py` (`find_path`, `check_move`; `path_cost_ft` and `move` built on them), `skills/dnd/scripts/combat.py` (`move --check`, `--json`), `skills/dnd/SKILL.md` ("Battle map on the TV": before narrating a player's move, run `move … --check`; on failure, tell that player the limit and ask again; never narrate it)
  - New test: `tests/test_battlemap_check.py`
- **Change:** as in Shape. `--check` prints `OK: Havoc (1,1) → (6,1): 35 ft, 0 ft left` or the reason (exit 2). `--json` prints the `check_move` dict. Neither saves nor pushes.
- **Done when:** unit tests pass on the `CELLAR` fixture (`test_battlemap.py:26-37`):
  - Too far through the door: `reason == "too_far"`, `cost` 35, `left` 30, `detour_by == "walls"`, and the message has "30 ft" and "wall".
  - Goal on a wall: `blocked`. Goal on an ally: `occupied`, naming Anony. Walled-in square: `no_path`.
  - Difficult terrain: `difficult_ft` is reported.
  - `path` starts at the token and ends at the goal.
  - CLI `move Havoc 9 1 --check` exits 2, and `battlemap.json` is byte-identical before and after.
  - `--check --json` parses.
  - `move … --bogus` exits 2.
  - `tests/test_battlemap.py` stays green unchanged.

### S3: The 30×20 cap at map creation
- **Covers:** AC2 (server side), D7
- **Waits for:** nothing.
- **Files:**
  - Changed: `battlemap.py` (`MAX_W`, `MAX_H`, `check_size`), `combat.py` (`_map_cmd` init, `:485-496`), `SKILL.md` (state the cap and "split or simplify")
  - Test: additions in `tests/test_battlemap_check.py`
- **Change:** `map init` with `--layout` or `--size` calls `check_size` before saving. `load()` and `_clean_map` keep accepting up to 80 for older saves.
- **Done when:** unit tests pass:
  - A 31×20 layout and `--size 30x21` exit 2 with "at most 30 squares wide and 20 tall" plus the actual size, and no `battlemap.json` is written.
  - 30×20 succeeds.
  - An existing 40×30 `battlemap.json` still loads and moves.

### S4: Hidden flag, fog data and the v2 payload in the scripts
- **Covers:** AC3 (payload), AC8 (server side), AC9, AC12, D8, D9 (storage), D10, D18
- **Waits for:** nothing.
- **Files:**
  - Changed:
    - `battlemap.py`: `place` with `hidden` and `portrait`, `set_hidden`, `set_fog`, trail recording in `move` and `place`, `hp_state`, `display_payload` v2.
    - `combat.py`: `hide`, `reveal`, `fog`, `place --hidden --portrait`, and `push_map` reading `tracker.json` conditions. `main` (`:569`) routes the new subcommands to `_map_cmd`.
    - `SKILL.md`: hide/reveal and manual fog, with when to use each, for example scrying or a trapdoor.
    - `tests/test_battlemap.py:122-127`: assert `hp_state == "bloodied"` and no `hp`, as AC3 requires.
  - New tests: `tests/test_battlemap_fog.py`, `tests/fixtures/battlemap_v1.json` (today's shape, from `battlemap.py:25-29`)
- **Change:** as in Shape. `render_ascii`/`map show` marks hidden tokens with `(hidden)` so Claude still sees them.
- **Done when:** unit tests pass:
  - Hidden: a hidden token is absent from `display_payload` even when it stands next to a PC; `reveal` brings it back.
  - Monster health: HP 3/7 gives `bloodied`, 1/7 gives `near_death`, 7/7 gives `healthy`, and no monster entry has `hp`.
  - PC moves: a PC move adds its whole path to `fog.trail`; a monster move adds nothing.
  - Manual fog: `fog reveal 2 2 4 3` adds 6 squares to `show`; `fog auto` on the same rectangle removes them; `fog auto --all` empties both lists.
  - Older saves: the v1 fixture loads, moves and gives a payload with `fog == {"trail":[],"show":[],"hide":[]}`.
  - CLI: `hide`, `reveal` and `fog` with `--bogus` exit 2.

### S5: Server cleaning of the v2 map
- **Covers:** AC3, AC9, AC12 (server side)
- **Waits for:** nothing (it edits the `/stats` map branch and `_clean_map`, not the `turn_order` code foundation S4 changes).
- **Files:**
  - Changed:
    - `skills/dnd/display/dnd-display-app.py`: `_clean_map` (`:2061`), and `_load_stats` (`:1233`) cleaning a stored map.
    - `tests/test_battlemap.py:221`: assert `hp_state`.
  - Test: additions in `tests/test_battlemap_fog.py`
- **Change:** as in Shape. `_clean_map` imports `battlemap.hp_state`; `scripts/` is already on `sys.path` (`dnd-display-app.py:55-56`).
- **Done when:** unit tests pass:
  - A raw `/stats` map holding a `hidden: true` token and a monster `hp: [3,7]` is stored and broadcast without the hidden token and with `hp_state` only.
  - `fog` and `portrait` survive; a `portrait` with `..` is dropped.
  - A `stats.json` holding an old map is cleaned on load.
  - `map: null` still clears.
  - The rest of `tests/test_battlemap.py` stays green.

### S6: Effects ride their narration blocks in the turn call
- **Covers:** AC4 (payload), AC5, D12, D13 (Claude's moves), D14 (data), D15 (format)
- **Waits for:** foundation S2, because both edit `/chunk` and the TV keys effects by `seq`.
- **Files:**
  - Changed:
    - `skills/dnd/display/send.py`: header comment `:526-551`; `_turn_block_payloads` `:575` ignores the effect keys; `_apply_turn_moves` `:632` becomes `_apply_turn_effects`, returning the map payload plus per-block `map_fx`; `send_turn` `:671`.
    - `skills/dnd/scripts/battlemap.py`: `area_squares`.
    - `dnd-display-app.py`: `/chunk` forwards `_clean_map_fx` output on the broadcast only.
    - `SKILL.md`: put each move on the block that narrates it, and spell areas on their block.
  - New test: `tests/test_turn_map_fx.py`
- **Change:**
  - As in Shape. Effect keys on a non-text block (an award) are rejected.
  - A turn-level `moves` attaches to the first block.
  - A move's `hp` becomes `hp_state` in `map_fx` for monsters.
- **Done when:** unit tests pass, using the `_fake_post` pattern from `test_battlemap.py:191-193`:
  - Block placement: in a two-block turn whose second block has a move, the first chunk payload has no `map_fx` and the second has the move with its `path`.
  - The final `stats.map` has the end positions.
  - A long block split by `_split_paragraphs` carries `map_fx` on its first chunk only.
  - Rejection: a move through a wall, or beyond speed, in block 2 exits 2 with the reason, `_post` is never called, and `battlemap.json` is unchanged (AC5).
  - A bad area (unknown shape, off the map, unknown `from`) and an attack naming an unknown token are rejected the same way.
  - Areas: a 20-ft sphere gives the expected squares on an open 12×12 grid, and wall squares are excluded.
  - Hidden tokens: a hidden token's move is absent from `map_fx`.
  - Server: the `/chunk` broadcast carries `map_fx`, and the `_text_log` entry does not.
  - `test_battlemap.py` (`test_turn_moves_validated_and_sent` and `test_bad_move_sends_nothing`) and `tests/test_turn_and_awaiting.py` stay green.

### S7: Themed board on `/tv`: tiles, tokens, fit to the area
- **Covers:** AC1, AC2 (TV), AC3 (TV), D5, D6, D7, D8, D16
- **Waits for:** foundation S7 (the map module slot in `panels.js`) and S8 (combat layout, `static/tv/map.js`). S7 replaces S8's port of `_bmDrawCells` and `_bmRender` and keeps S8's container and sizing hooks.
- **Files:**
  - New: `skills/dnd/display/static/map/{board.js,map.css}`, `tests/test_map_board.py` (node, skipped without node, as in `tests/test_tts_autoqueue.py:27,66`)
  - Changed: `static/tv/map.js`, `templates/tv.html` (script and link tags)
- **Change:**
  - As in Shape. The current turn comes from `stats.turn_order.current`, and the token gets an accent ring and a lift.
  - PCs show `cur/max` under the disc. Monsters show only the stepped ring.
  - The board shows no numbers for a monster, even if an old payload still carries `hp`; it derives the state instead.
- **Done when:**
  - Node tests pass:
    - `fit(30, 20, …)` for the combat-map column at 1920×1080 gives an integer square with the whole grid inside the box. At 3840×2160 the proportions are the same.
    - `tileKind` maps `# . ~ T D ' '`.
    - `tokenView` gives a PC exact HP and fraction, a monster `bloodied` with no number, initials "GR" for "Giant Rat", and `current` true for the turn holder.
  - Browser screenshots at 1920×1080 (AC1, AC2, AC3):
    - a 30×20 three-room layout without an image;
    - the same with a `maps/` image under the grid;
    - tokens showing a portrait, a condition icon and the current-turn highlight.
    - No scrollbars.

### S8: Fog from the PCs' line of sight
- **Covers:** AC7, AC8 (TV), AC9 (TV side), D9, D11, D18 (fog after reload)
- **Waits for:** S4, S5 and S7.
- **Files:**
  - New: `skills/dnd/display/static/map/los.js`, `tests/test_map_los.py` (node)
  - Changed: `static/map/board.js` (fog canvas: dark and dim layers, a 400 ms crossfade), `static/tv/map.js`
- **Change:** as in Shape. Monster tokens outside `visible` are not drawn at all, rather than drawn under the fog.
- **Done when:**
  - Node tests pass:
    - Room behind a wall: in an L-shaped corridor, a monster round the corner is not shown; after the PC steps to the corner square, it is.
    - Earlier squares: they stay in `seen` (dim) after the PC leaves, and a monster standing on a dim square is not shown.
    - Walls bordering a visible room are visible.
    - Diagonal wall corners block sight.
    - `show` makes a walled-off square visible with its monster. `hide` darkens a square in plain sight and hides its monster.
    - A map with no `fog` key uses the current PCs only.
    - A 30×20 map with a 200-square trail computes in under 200 ms in node.
  - Browser:
    - Moving a PC with `combat.py move` round a corner reveals the monster.
    - `combat.py fog reveal` and `fog hide` show on the TV within 1 s (AC8).
    - Reloading keeps the dim squares (D18).

### S9: Moves and spell areas play when their block starts
- **Covers:** AC4 (TV), AC10, AC11, D12, D14, D17
- **Waits for:** foundation S6 (`feed.js`) and S9 (`voice.js` paced reveal), plus S6 and S8 here.
- **Files:**
  - New: `skills/dnd/display/static/map/fx.js`, `tests/test_map_fx.py` (node)
  - Changed: `static/tv/feed.js` and `static/tv/voice.js` (three hook calls, each in try/catch), `static/tv/map.js`, `static/map/board.js` (area overlay), `map.css`
- **Change:**
  - As in Shape. A move slides in about 600 ms, and fog follows the sliding PC's path.
  - An area outlines its squares (stroke `--accent`, soft fill) for 3 s, then fades.
  - Flush on `visibilitychange`, `rewind`, `clear` and reconnect, plus the 90 s safety flush.
- **Done when:**
  - Node tests pass, with fake timers:
    - Blocks A and B arrive, B with a move, then `stats.map` with the moved token: the token stays at its old square after `start(A)` and moves on `start(B)`.
    - `skip(B)` snaps with no animation flag.
    - `flushAll` gives the target state.
    - An area is present for 3 s after its block starts, then gone.
    - A `setTarget` with no pending effects (the reconnect case) gives the target at once.
    - A pending effect older than 90 s is flushed.
  - Browser, with Kokoro on:
    - A two-block turn with the move on block 2: the token moves when block 2 starts, not before, and the text and voice timing are unchanged with and without the move (AC4).
    - A Fireball area outlines on its block (AC10).
    - Reloading mid-fight shows the current state with nothing replayed.
    - A tab backgrounded during a turn jumps to the current state on return (AC11).

### S10: Milestone 1 table check
- **Covers:** AC14
- **Waits for:** S1–S9 and foundation S16 (the launcher prints `/tv`).
- **Files:**
  - Changed: `docs/ROADMAP.md` (Phase 3 and the Map item, `:165-200`, `:261`), `CHANGELOG.md`, `skills/dnd/display/README.md` (map section)
- **Change:** docs only, then the playtest.
- **Done when:**
  - The full suite is green.
  - The user plays a three-room fight on `/tv`: tokens move as the voice reaches them, fog reveals a monster round a corner, and a bad player move is bounced with its reason. The result is recorded in the spec.

### Milestone 2

### S11: Attack sprites
- **Covers:** AC15, AC16, D15, D16, D17
- **Waits for:** S9.
- **Files:**
  - New: `skills/dnd/display/static/map/sprites.js`, `tests/test_map_sprites.py` (node)
  - Changed: `static/map/fx.js` (plays `attacks`), `board.js`, `map.css`
- **Change:**
  - Sprites by kind:
    - `melee`: a sword arc swung over the target from the attacker's side.
    - `ranged`: an arrow flying attacker → target.
    - `spell`: a glowing bolt.
  - A hit flashes the target (and its ring updates then if it was pending). A miss shows a whiff for melee or a glance-off for an arrow or bolt.
  - Each attack lasts ≤ 900 ms. Several attacks in one block run back to back, capped at 2 s in total, after which the rest snap.
  - An attacker that is not visible, or has no `from` (hidden): only the target's hit or miss plays. A target that is not visible: only the attacker's swing or launch plays.
  - Skipped or behind blocks play nothing (same flush rules).
- **Done when:**
  - Node tests pass:
    - Each kind gives its sprite id.
    - `hit` gives the flash class and `miss` the whiff or deflection.
    - Every sprite's duration is ≤ 1000 ms.
    - A skipped block adds no sprite.
    - A hidden attacker gives a target-only effect.
  - Browser:
    - Screenshots of all three sprites, hit and miss (AC15).
    - A timed run shows the text and voice start times unchanged with attacks.
    - A backgrounded tab plays no backlog (AC16).

### S12: Attacks in the DM's instructions, and the milestone 2 table check
- **Covers:** AC17, D15
- **Waits for:** S11.
- **Files:**
  - Changed: `skills/dnd/SKILL.md` (every attack in a turn call goes on its narration block, with `from`, `to`, `kind` and `hit` taken from the `combat.py attack` result), `send.py` header comment, `CHANGELOG.md`
  - Test: an addition to `tests/test_turn_map_fx.py` checking that the documented example parses and validates.
- **Done when:** the unit test passes, and the user confirms in a real fight that every attack played the right sprite with hit or miss.

**AC coverage:**

| AC | Steps |
|---|---|
| AC1 | S7 |
| AC2 | S3, S7 |
| AC3 | S4, S5, S7 |
| AC4 | S6, S9 |
| AC5 | S6 |
| AC6 | S2 |
| AC7 | S8 |
| AC8 | S4, S8 |
| AC9 | S4, S5, S6, S8 |
| AC10 | S6, S9 |
| AC11 | S6 (not logged), S9 |
| AC12 | S4, S5 |
| AC13 | S1 |
| AC14 | S10 |
| AC15 | S6 (format), S11 |
| AC16 | S11 |
| AC17 | S12 |

**Playable after each step:**
- S1–S6 change only scripts, the server and docs. The old page and `/tv` keep working: the new `map_fx` key is ignored by `index.html`, and tokens still jump there as today.
- S7–S9 touch only `/tv`.

## Risks

- **Saved campaigns from before this change.**
  - A `battlemap.json` without `fog`, `hidden` or `portrait` loads with an empty trail, nothing hidden and fog from the current PCs (S4 fixture).
  - A saved map over 30×20 still loads and draws, with smaller squares; the cap applies only to new maps (S3).
  - A `stats.json` holding an old-shape map is cleaned on load, so exact monster HP never reaches a screen (S5).
  - No migration writes anything.
  - Campaigns with no map: `send_turn` loads no map unless a block carries effects, the same as today.
- **The old phone page (`index.html?char=`) has no fog.** It draws every token it is sent (`index.html:8565-8607`), so until the Phone sheet's Map tab lands, phones show monsters the TV hides; hidden tokens are still never sent. The spec rules out `index.html` changes beyond "keeping it working". **Needs the user's call:** accept this until Phone sheet milestone 2, or allow one small change so the old page loads `static/map/los.js` and hides unseen monsters.
- **The turn bar can name hidden or unseen monsters.** Foundation S5/S8 put every combatant's name in the turn bar, so a hidden or not-yet-seen goblin is named before it appears. AC9 covers tokens, not names. **Needs the user's call:** for example, Claude leaves hidden creatures out of `combat.py init` until they're revealed, or the turn bar shows "?" for names not on the map payload.
- **Effects that never play.** If a block never starts (feed error, rewind), the 90 s safety flush and the rewind and clear flushes snap the map to the truth. Map handlers run inside try/catch and are never awaited, so a map bug cannot stall narration.
- **HP rings follow `/stats`, not the voice,** except for tokens with pending effects. That matches today's party sidebar.
- **Fog judgement.**
  - Centre-to-centre rays can disagree with what the DM pictures, and the trail only records squares PCs stood on or passed through.
  - Claude has `fog reveal` and `fog hide` for both cases.
  - Friendly NPCs (`kind: npc`) are fogged like monsters.
  - Spell areas skip wall squares but ignore line of effect behind walls (no lighting or effect simulation, D11).
- **Line-of-sight cost on a TV browser.** Per-origin results are memoised, so 30×20 with a long trail is the worst case. S8 tests a time budget.
- **Two map renderers during the trial.** `index.html` keeps its own map code. Fixes made in one are not made in the other.
- **Existing tests that assert replaced behaviour.** `tests/test_battlemap.py:127` and `:221` assert exact monster HP in the display payload. AC3 replaces that, so S4 and S5 update them. Battlemap.json itself keeps exact HP for Claude (`map show`).
- **Unrelated finding (one line):** `tracker.py condition add <monster>` pushes the monster as a party "player" (`tracker.py:300-310`), so a monster can appear in the Party module.

## Blockers

None. Two decisions looked as if they might clash but don't:
- D9 ("worked out by the TV") and D18 ("fog as currently seen after a reload"). The TV computes line of sight, and `battlemap.json` stores only where PCs have been (`fog.trail`), so a reload rebuilds the same dim squares.
- D17 and D12. Effects are timed by the TV's own paced reveal and dropped whenever it skips.

The two "needs the user's call" items under Risks are gaps in the spec, not decisions that can't be built.
