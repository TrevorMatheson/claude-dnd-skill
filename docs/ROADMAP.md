# Roadmap — MyDM (fork of claude-dnd-skill)

Written 2026-11-13 from a full live test on Windows: 2024 rules, a 4-player
couch setup with a TV display, phones, a Gemini voice, D&D Beyond character
import and D&D Beyond Maps. Everything below was hit during that session or
confirmed in the code afterwards. Sizes: **S** ≈ an hour or two, **M** ≈ a day,
**L** ≈ several days.

The two areas the table asked to iterate on are **narration** and **the map
following the action**. Phase 1 clears the bugs that get in the way of any
game. Phases 2 and 3 are those two areas.

---

## Status — 2026-11-13

All of Phases 0–4 are implemented on `feat/auto-narrate-queue`, one commit per
item, with tests (497 pass, 4 skipped). Checked in a sandboxed display in the
browser: the one-call turn, the one-player Send, physical dice through to
`--wait`, NPC voice tags, the TV and phone battle map, and the launcher.

Still needs a live check at a real table:
- `ddb_import.py` against a real (public) D&D Beyond character — it was built
  and tested on a fixture of the v5 JSON shape only.
- Audio with the real Gemini voice: prefetch timing, per-NPC voices, the
  "Tap for audio" button on the actual TV browser.
- `start_display.py --lan` with the phones and TV on the home network.
- The once-per-round D&D Beyond mirror (§3.4) is a documented procedure in the
  maps skill; it has not been run against Maps.

---

## Where things stand

**Works well:** campaign files and world generation, the 2024 SRD dataset (once
built), the combat, XP and tracker scripts, the TV display and phone input,
Gemini narration, the continuity graph, and tutor hints for new players.
399 unit tests pass.

**Rough at the table:**
- Every turn is several hand-written `send.py` calls, so narration is slow and
  easy to get out of order.
- Audio did not follow the text. That is fixed on `feat/auto-narrate-queue`
  (uncommitted).
- Input waited for the whole party even when one player was asked to act.
- Physical dice had no place to be entered.
- D&D Beyond Maps can only be driven by clicking through the web page:
  setting up one scene took about 60 browser actions, and tokens never move
  during a fight.
- Windows support is partial: bash launchers, `python3` everywhere, and
  text-mode writes that change line endings.

---

## Phase 0 — land what's already done (S)

| Item | Where |
|---|---|
| Auto-narrate on by default, plus a play queue so a burst of blocks no longer cuts itself off (with tests) | `display/templates/index.html`, `tests/test_tts_autoqueue.py` |
| Voice change no longer adds a `\r` to every line of state.md, and the chosen voice actually saves (with test) | `display/dnd-display-app.py`, `tests/test_voice_persist_eol.py` |
| Commit the `maps` skill from the other session, with its two field notes: 2024 monster tokens may be locked behind the 2024 Monster Manual, and drag-to-rectangle fog doesn't work, so use Cover/Reveal All or brush strokes | `skills/maps/SKILL.md` |

---

## Phase 1 — table-blocking bugs (S–M each)

1. **The tests overwrite the live display.** `tests/test_milestone_counter.py`
   (and anything else that imports `dnd-display-app.py`) writes to the real
   `~/.claude/dnd/.runtime/stats.json`. Running the suite during a game
   replaced the TV's party with "Aldric". Point `DND_RUNTIME_DIR` and
   `DND_CAMPAIGN_ROOT` at a temp directory for the whole test run, and add a
   guard test that fails if anything touches the real home directory. **S**
2. **Version drift.** `VERSION` is 2.7.0 but `plugin.json` and
   `marketplace.json` say 2.4.0, so the install sits in `cache/.../2.4.0` and
   `/plugin update` can't see new versions. Run `scripts/bump_version.py`, and
   add a test that all five version strings match. **S**
3. **Scripts that ignore options they don't know.** `combat.py` parses argv by
   hand, so `--advantage` was silently dropped and the rat's Pack Tactics roll
   was wrong. Move `combat.py`, `character.py` and `dice.py` to argparse
   (unknown flag = error). Add `--advantage` and `--disadvantage` to
   `combat.py attack`, using the same keep-higher / keep-lower rule as
   `dice.py`. **M**
4. **Commands that wait on stdin forever.** `send.py --xp-award` (and the
   other flag-only sends) read stdin when it isn't a terminal, so from a tool
   call they hang until killed. Only read stdin for content flags, or add a
   `--no-body` switch. **S**
5. **`--dice-request --wait` doesn't return the roll.** It only reports that a
   roll happened. The DM had to dig the number out of `session_tail.json`. The
   default timeout (120 s) is also shorter than a real table takes. Print the
   result, or JSON with `--json`, and default the wait to about 10 minutes. **S**
6. **Line endings across writers.** `xp.py`, `migrate_ruleset.py`,
   `npc_rename.py`, `oracle.py` and `path_config.py` all use
   `Path.write_text` on campaign files. On Windows that rewrites LF files as
   CRLF, and any read-raw/write-text pair adds a `\r` each pass (the voice bug).
   Add one `utf8io.write_text(path, text)` that keeps the file's line ending,
   use it everywhere, and extend the encoding-tree test to forbid bare
   `write_text` on campaign files. **M**
7. **DM Help fails silently.** `dm_help.py` calls `claude -p --model
   claude-sonnet-4-6` and quietly returns "SKIP" when the CLI isn't signed in
   or the model is retired. Use the session's default model (no `--model`),
   and show a visible "DM Help unavailable: sign in with `claude` → /login"
   block instead of nothing. Same for the hard-coded Haiku id in
   `campaign_graph.py`. **S**
8. **2024 dataset fragility.** `build_srd.py --ruleset 2024` silently built 0
   spells and 0 features without PyYAML, and the built file lives in the
   plugin cache, so any reinstall deletes it. Fail loudly when a source is
   skipped, list PyYAML in the requirements, and build into the data root
   (`~/.claude/dnd/data`) instead of the plugin directory. **S–M**

---

## Phase 2 — narration (the main ask)

The goal: one DM turn is **one call**, arrives on the TV in the right order,
and is read aloud in full, with characters in distinct voices.

1. **One-call turns.** A `send.py --turn turn.json` (or stdin JSON) that takes
   the whole turn — player echoes, dice lines, narration, NPC lines, tutor hint,
   stat changes, turn pointer and input threshold — and sends it in order.
   Today a turn is 4–6 heredoc sends, and the model has to remember the block
   order, `< /dev/null`, and which flags read stdin. This removes most of that
   overhead and most of the mistakes. **M**
2. **NPCs speak in their own voice.** Add a `voice:` field to each NPC entry in
   `npcs.md`. `--npc <name>` blocks look it up and pass it to `/tts`, the
   narrator keeps the campaign voice, and tutor hints get a third voice. Today
   every block uses one narrator voice. **M**
3. **Read everything, in order, without dead air.**
   - Start fetching the next block's audio while the current one plays.
   - Let dice lines and tutor hints be read too, as a setting. Right now only
     DM narration blocks get a play bar.
   - One "audio on" control for the TV that also unlocks the browser's
     autoplay, so nobody has to click the page first.
   **M**
4. **Re-check the voice key without a restart.** After a 503 the page stops
   asking the server until it's reloaded, and a new key needs a server restart.
   Re-check `tts.key` every minute or so on the server, and have the page retry
   after any `tts_available` broadcast. **S**
5. **Narration that keeps to the table's rules.** Add a short `## Narration`
   block to the DM Style Notes template:
   - the length budget from the slider;
   - "name who acts next";
   - "end on a question to a named character";
   - "tell first-time players the die and the target number".

   During the test these lived only in the DM's head. **S**

## Phase 2b — input and dice

1. **Automatic one-player Send.** When the DM points the turn at one character
   (`--turn-current`, or a dice request for one character), the server should
   accept that player's single Send without waiting for the others. Today it
   only works if the DM remembers `push_stats --autorun-threshold 1`, and
   every non-acting player has to press "skip". **S**
2. **Physical dice entry.** In the phone dice drawer and the TV roller, add a
   "I rolled it myself" number pad: pick the die, type the face, and the bonus
   is added. The result goes back through the same `dice-request` path as a
   virtual roll, so `--wait` and the transcript treat both the same. Today
   players type "rolled 14" into the action box, and the DM has to guess
   whether that's the face or the total. **M**
3. **Clear roll prompts.** Every PC roll prompt shows the die, the bonus, the
   target, and whether to send the face or the total. **S**

---

## Phase 3 — the map follows the action

D&D Beyond Maps has no API, so everything goes through the browser. That works
for setting a scene (slowly) but can't keep up with turns. **Recommendation:
put the live battle map in our own TV display, and treat D&D Beyond Maps as an
optional mirror.**

1. **Positions in the combat state.** `combat.py` stores `x,y` (in squares)
   per combatant. A new `combat.py move <name> <x> <y>` (or `--to`) checks the
   distance against speed, and `attack` checks reach and range from positions.
   **M**
2. **Map panel on the TV.**
   - A new `push_stats --map` payload: a background image or grid size, walls,
     and tokens with positions, HP rings and conditions.
   - The panel animates tokens when they move.
   - Each player's phone highlights their own token.
   - Because the DM's turn call (Phase 2.1) includes the moves, the map moves
     as the narration happens.

   **L**
3. **Map source.**
   - Any image the DM drops in the campaign's `maps/` folder, with the grid set
     in `vtt.md`.
   - Or a generated grid with walls and furniture from a short text layout,
     like the Rusty Mimic.
   - D&D Beyond library maps can't be downloaded, so they stay in Maps.

   **M**
4. **Optional D&D Beyond mirror, done in batches.** If a table still wants
   Maps: work out the grid once per map (where the origin is, pixels per square
   at 100% zoom, stored in `vtt.md`), then turn a whole round of moves into one
   `browser_batch` of drags at the end of each round. Never move token by token
   during narration. Also document in the maps skill:
   - the fog-rectangle limit;
   - the locked-token fallback to Legacy tokens;
   - that a Basic map version starts with no prebuilt tokens.

   **M**

---

## Phase 4 — setup and platform

1. **Windows launcher.** `start-display.ps1` (or a Python `start_display.py`
   for every platform) that:
   - uses `python` or `py`, not `python3`;
   - finds the LAN address on Windows, and picks the real adapter over VMware
     and link-local ones;
   - starts the server, pings it, and prints the TV and phone URLs.

   Have the skill call `sys.executable` instead of hard-coding `python3`. **M**
2. **D&D Beyond character import.** `/dm:dnd character import-ddb <url>`.
   Reading the sheet page took about 10 browser steps per character. Turn that
   into a documented procedure, or a script fed by the user's PDF export. It
   should flag problems such as:
   - gear in the inventory but not equipped (D&D Beyond showed AC 10);
   - no spells prepared;
   - Weapon Mastery picks for weapons the character doesn't carry;
   - a 2014 background on a 2024 character, which means no origin feat;
   - an unfinished sheet (D&D Beyond's "Character Not Ready").

   **M**
3. **A "table test" checklist.** A `/dm:dnd doctor` that checks:
   - the Python and Flask versions;
   - the 2024 dataset counts;
   - the TTS key, and whether the server has picked it up;
   - that the display answers on the LAN;
   - the firewall rule;
   - that the `claude` CLI is signed in (for DM Help).

   **S–M**
4. **Context size.** `SKILL.md` plus its two companion files come to about
   2,100 lines and load into every session. Move rarely used sections (import,
   oracle, graph extract) into files loaded on demand, so the narration has
   more room. **M**

---

## Suggested order

1. Phase 0, then Phase 1 items 1–5. Those make a real session safe.
2. Phase 2.1 (one-call turns) and Phase 2b.1–2 (one-player Send, physical
   dice). These change how the game feels the most.
3. Phase 2.2–2.4 (voices, read-everything, key re-check).
4. Phase 3.1–3.2 (positions and the TV map), then 3.3, with 3.4 only if the
   table still wants D&D Beyond Maps.
5. Phase 4 alongside, as time allows.
