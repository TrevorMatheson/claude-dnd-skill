# D&D Skill — Reference Procedures (load on demand)

Procedures for commands a session rarely uses. Not loaded at `/dm:dnd load`: read this file only when one of these commands is invoked, so the context a session starts with stays free for play.

> **Path note:** commands below use `${CLAUDE_SKILL_DIR}` for the skill directory. This file is read verbatim, so that token is **not** auto-expanded here — substitute the absolute skill-dir path (from `SKILL.md`) before running any command.

---

## `/dm:dnd import <filepath> [campaign-name]`

Import a pre-written campaign from a source file (PDF, MD, TXT, DOCX) and create a playable campaign from it.

**Supported file types:** `.pdf` `.md` `.txt` `.markdown` `.docx`

### Step 1 — Extract source text
```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/import_campaign.py "<filepath>" --info
```
**PDF sources:** extraction uses PyMuPDF (column-aware) so multi-column modules de-column into reading order and segment into chapters correctly — without it, two-column books collapse into one chapter. If the script prints a `pip3 install pymupdf` notice on its stderr, tell the DM to install it and re-run; it falls back to `pdftotext` otherwise but segmentation is less reliable.

Print file info. If it reports more than one chunk, read it chunk by chunk (a chunk is ~4000 words of English or ~6000 characters of Chinese/Japanese/Korean, with the source's line breaks intact):
```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/import_campaign.py "<filepath>" --chunks  # total chunks
python3 ${CLAUDE_SKILL_DIR}/scripts/import_campaign.py "<filepath>" --chunk 0  # first chunk
```
For a single-chunk source, read in full:
```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/import_campaign.py "<filepath>"
```

### Step 2 — Analyse structure
Read the extracted text and identify:
- **Campaign title and system**
- **Structure type:** `linear` (scene chain A→B→C) | `hub-and-spoke` (central hub + spoke locations, player-driven order) | `faction-web` (multi-faction city/complex, overlapping arcs)
- **Acts and chapters** — numbered sections, chapter headings, or named scenes
- **Key beats** — required story events the DM must deliver (boss reveals, faction turns, mandatory encounters)
- **Locations** — distinct named places with descriptions
- **NPCs** — names, roles, motivations, relationships, stat blocks if present
- **Factions** — groups with agendas, relationships to party
- **Quest hooks and seeds** — explicit adventure hooks, side quests, optional encounters
- **Starting conditions** — where does the party begin, what level, what's the inciting event

For large sources, read all chunks before proceeding.

### Step 3 — Confirm campaign name
If `[campaign-name]` not supplied, suggest one from the title and ask to confirm.

### Step 4 — Display summary and confirm
Show a structured summary before writing any files:

```
Title:    <source title>
Type:     structured / <structure type>
Acts:     N  |  Chapters: N  |  Key beats: N
NPCs:     N named  |  Factions: N
Locations: N distinct

Campaign name: <name>
Campaign dir:  ~/.claude/dnd/campaigns/<name>/

Proceed? [y/n]
```

### Step 5 — Create campaign files
On confirmation:

1. `mkdir -p ~/.claude/dnd/campaigns/<name>/characters`
2. Copy templates from `${CLAUDE_SKILL_DIR}/templates/`
3. Write **world.md** (load-time core — kept small so it can be read in full every load):
   - `## World Foundations` — setting, geography, tone, magic level, calendar if present
   - `## Three Truths` — one settlement, one threat, one mystery (drawn from source)
   - `## Threat Escalation Arc` — map source acts to the 5-stage table; set stage 1
   - `## Factions` — all factions with archetype, current activity, relationship to party

3a. Write **world-nodes.md** (lazy reference — NOT read at load, pulled per current act):
   - `## Quest Seed Bank` — all explicit hooks + 2–3 implied side threads
   - `## Adventure Nodes` — named locations with one-line descriptions, grouped by act/chapter

   For a small module the split is optional, but for any published adventure it is the
   single biggest load-time saving — the whole quest/location bank no longer sits in
   context every session. If you write `world-nodes.md`, do **not** duplicate its
   sections into `world.md`.

4. Write **npcs.md** index table (one row per NPC: name, role, location, one-line demeanor)

5. Write **npcs-full.md** — full entry for each named NPC:
   - Role, motivation, secret, speech quirk, faction affiliation
   - Relationships to other NPCs (min 2 per NPC)
   - Stat block summary if present in source

6. Write **arc.md** from `${CLAUDE_SKILL_DIR}/templates/arc.md` — the **full** act/chapter tree: every chapter's `id`, `title`, `location`, `source_ref` (its file in the lazy corpus, see step 6b), `key_beats`, `telegraph_scene`, `branching_notes`, plus `outstanding_beats` and `steering_notes`. This is the heavy structure; it lives here so it is read on demand, not at every load.

6a. Write **state.md** from template:
   - Populate `## Current Situation` — starting location and party placeholder
   - Populate `## World State` — in-world date if given, factions, threat arc stage 1
   - Populate `## Campaign Arc` with the **STRUCTURED ARC POINTER only** (see template): `type: structured`, `source`, `structure`, `arc_file: arc.md`, `current_act`, `current_chapter`, the `current_chapter_detail` block, `next_chapter`, `outstanding_beats`, `steering_notes`. **Delete the entire DYNAMIC ARC yaml block** from the template — do not leave both arc forms in the file. The full tree is in arc.md; state.md carries only the current + next chapter window.
   - Leave `## Active Quests`, `## Session Flags` (autosave defaults on), `## DM Style Notes` as template defaults

6b. Write the **lazy corpus** — the full source text, kept available but out of the hot path:
   - `mkdir -p ~/.claude/dnd/campaigns/<name>/source`
   - For each chapter in arc.md, write `source/<chapter-id>.md` (e.g. `source/1.1.md`) containing that chapter's source text from the extracted chunks. Use the same chapter ids as arc.md.
   - Write `source-index.md` — a table mapping `chapter-id → source/<id>.md → one-line scope`, plus source title and import date.
   - Validate the layout: `python3 ${CLAUDE_SKILL_DIR}/scripts/corpus_check.py --campaign <name>` (expects "lazy-corpus layout OK"). Fix any orphan/missing-file problems before finishing. If it prints an **oversized-chapter WARNING**, split that chapter into sub-chapters (e.g. `1.1a` / `1.1b` at natural scene breaks) across `arc.md`, `source-index.md`, and `source/`, then re-run — a single giant chapter is the one thing that still bloats a load, so keep each `source/<id>.md` comfortably under the limit.

7. Write **session-log.md** with Session 0 import record:
   ```
   ## Session 0 — Import — <date>
   Source: <filepath>
   Imported: <N> acts, <N> chapters, <N> NPCs, <N> locations
   ```

### Step 6 — Gap-fill wizard
After writing files, identify anything the source left ambiguous:
- If starting level not specified → ask
- If party size not specified → ask
- If calendar/in-world date absent → offer to generate or leave blank
- If tone not clear from source → offer Tone/Genre Wizard

### Step 7 — Confirm and offer next step
Print summary of files written. Offer:
```
Campaign "<name>" created from <source title>.
→ /dm:dnd character new      — create your character
→ /dm:dnd load <name>        — start playing immediately
```

---

## `/dm:dnd data [sync|status]`
- `sync` → `python3 ${CLAUDE_SKILL_DIR}/scripts/sync_srd.py` — checks upstream SHAs (5e-bits + FoundryVTT) and rebuilds `dnd5e_srd.json` only if either source has new commits
- `sync --force` → `python3 ${CLAUDE_SKILL_DIR}/scripts/sync_srd.py --force` — rebuild regardless
- `sync --check` → check upstream without rebuilding
- `status` → `python3 ${CLAUDE_SKILL_DIR}/scripts/build_srd.py --status` — show current dataset metadata

Dataset is bundled at `${CLAUDE_SKILL_DIR}/data/dnd5e_srd.json` (1467 records: spells, equipment, magic items, conditions, monsters, races, subraces, class features). No download required at runtime. Run `sync` only when you want to pull new upstream content.

---

## `/dm:dnd path [<new-path> | reset]`

View or configure where campaign and character data is stored. Wraps the
`DND_CAMPAIGN_ROOT` env var.

- No args → `python3 ${CLAUDE_SKILL_DIR}/scripts/path_config.py` and show output.
- New path → `python3 ${CLAUDE_SKILL_DIR}/scripts/path_config.py set <path>`. Confirm to user, then remind them the change only takes effect in new shells (or after they `source` their rc on macOS/Linux).
- `reset` → `python3 ${CLAUDE_SKILL_DIR}/scripts/path_config.py reset`.

Persistence is via shell rc on macOS/Linux and via `setx` on Windows. Existing campaigns are not auto-migrated; `paths.find_campaign()` handles legacy fallback + copy-on-access.

---

## `/dm:dnd update [--check]`

Pull the latest skill changes from `origin/main`.

- No args → `python3 ${CLAUDE_SKILL_DIR}/scripts/update_skill.py` and stream output (script prompts before pulling).
- `--check` → `python3 ${CLAUDE_SKILL_DIR}/scripts/update_skill.py --check` — report status without pulling.
- The script refuses to update if the working tree is dirty and uses `--ff-only` so it never silently merges divergent history.
- After a successful pull, remind the user to restart Claude Code so the new `SKILL.md` and `SKILL-commands.md` are reloaded.

---

## `/dm:dnd npc rename "Old Name" <"New Name" | random> [flags]`
Rename a character across an entire campaign — `npcs.md`, `npcs-full.md`, `state.md` (every section), `session-log.md`, `graph.json` (node + edges preserved), and `characters/<slug>.md` if `--type pc`. Backs up the campaign first.

Maps to: `python3 ${CLAUDE_SKILL_DIR}/scripts/npc_rename.py --campaign <current> --old "..." --new "..." [flags]`. Use the currently loaded campaign by default; for explicit-campaign use, pass `--campaign <name>` directly.

Flags:
- `--random` — pick a name from the bundled fantasy-name corpus (~4800 unique combinations) that isn't already in `~/.claude/dnd/.name_registry.json`. Mutually exclusive with explicit "New Name".
- `--type npc | pc` (default `npc`) — `pc` also moves the character file and updates the global roster.
- `--dry-run` — show all hits across files without writing. Always run first for sanity.
- `--yes` — skip the confirmation prompt.
- `--include-archive` — also rename in `session-log-archive.md`. **Default is to leave the archive untouched** for historical accuracy and add a one-line audit note at the top: *"`<old>` renamed to `<new>` at S<N>; historical entries below preserve the original name."*

The script always backs up the campaign to `<name>.backup-rename-<old-slug>-YYYYMMDD-HHMMSS/` before any writes. Revert command is printed at the end.

After rename, the name registry is updated: old name marked `retired_from` this campaign with `replaced_by` pointing at the new slug; new name added with this campaign's current session as `first_session`.

## `/dm:dnd registry <subcommand>`
View and manage the cross-campaign name registry at `~/.claude/dnd/.name_registry.json`. Used by `/dm:dnd npc rename --random` to never reuse a name and (in a follow-up) by `/dm:dnd new` / `/dm:dnd character new` / `/dm:dnd npc <new>` to flag duplicates at creation time.

Maps to: `python3 ${CLAUDE_SKILL_DIR}/scripts/name_registry.py <subcommand> [args]`.

- `/dm:dnd registry rebuild [--include-prose]` — scan every campaign's `npcs.md`, `npcs-full.md`, `characters/*.md`, and `graph.json` (node names); rebuild the registry from canonical sources. Preserves any existing `retired_from` history. Run once on install, then ad hoc when desired.

  **`--include-prose` (added 2026-05-07, opt-in):** also scan `session-log.md` and `session-log-archive.md` for capitalized 2–3-word sequences (likely-name patterns) in any cased script — accented Latin, Cyrillic and Greek included — and, for Chinese, Japanese and Korean, which have no capitals, for a name directly followed by an honorific (田中さん, 李明先生, 민수씨). Filtered against a stopword list (places, factions, mechanic words like "Theo Stealth", sentence starts) but **regex-based extraction is inherently noisy** — typically 5–15× more entries than canonical, with maybe 10–20% real catches. Tagged `source: prose` to distinguish; query with `/dm:dnd registry list --source prose` to manually review and prune. For high-quality prose extraction, the future move is LLM-backed (similar to `/dm:dnd graph extract`).

- `/dm:dnd registry list [--campaign C] [--type npc|pc] [--source canonical|prose]` — print all registry entries; filter by campaign-currently-active, type, or source.
- `/dm:dnd registry lookup <name>` — case-insensitive lookup; prints the full entry as JSON.
- `/dm:dnd registry check <name> [--json]` — check whether a proposed name collides with the registry. Exit 0 if unique, 1 if duplicate. A name whose first name (titles like Lady / Sir / Captain stripped) matches a player character's is also flagged — "Lady Willa Torne" beside a PC "Willa Finch" — as a warning, exit 0; rename the NPC unless the echo is deliberate.
- `/dm:dnd registry clashes [--campaign C]` — list every NPC that shares a first name with a player character; exit 1 if any. Severity (`warn` default, `strict` opt-in via `<DND_CAMPAIGN_ROOT>/.name_registry_config.json`) controls whether duplicates are reported as warnings or hard refusals. Used by `/dm:dnd new`, `/dm:dnd character new`, `/dm:dnd npc <new>` procedures.
- `/dm:dnd registry add --name N --type npc|pc --campaign C --session N` — record a new entry manually (auto-called by `/dm:dnd npc rename` and the creation-time uniqueness hooks).
- `/dm:dnd registry retire --name N --campaign C [--replaced-by NEW]` — mark a name as no longer active in a campaign (auto-called by `/dm:dnd npc rename`).

The registry by default captures **canonical** characters (those in `npcs.md` / `npcs-full.md` / `characters/` / graph.json node names). Names that appear only in session-log prose (one-off mentions, throwaway NPCs, skill-check labels) are NOT registered by default — that's deliberate, to avoid banning common names because of incidental use. The `--include-prose` flag is opt-in for users who want the broader (noisier) view.

**Severity config:** create `~/.claude/dnd/.name_registry_config.json` with `{"severity": "strict"}` to make all duplicate detections refuse-by-default rather than warn-and-allow. Set to `"none"` to disable checks entirely (registry rebuild and rename still work).

---

## `/dm:dnd graph <subcommand>` — campaign relationship graph

Local-only typed-edge relationship graph supplementing markdown. Stored at `~/.claude/dnd/campaigns/<name>/graph.json`. Supplements `npcs-full.md` / `session-log.md` — does not replace them. Edges are time-stamped (`since_session` / `until_session`), so historical state is recoverable.

**Auto-pulled at `/dm:dnd load` step 5** (scene-context) and **swept at `/dm:dnd save`** (relationship-shift extraction). The DM also uses `/dm:dnd graph scene-context` on demand mid-session, especially before heavy social or political scenes.

For background reading on the design and the A/B replay study that motivated it, see `docs/research/graph/`.

All subcommands invoke `python3 ${CLAUDE_SKILL_DIR}/scripts/campaign_graph.py <subcommand> --campaign <name> [args]`.

### `/dm:dnd graph init [campaign-name]`
First-time bootstrap. Run `campaign_graph.py init --campaign <name>` to create the empty `graph.json` — idempotent on an empty graph, and it refuses to overwrite one that already holds nodes or edges. Then read existing `npcs.md` / `world.md` / `state.md` for the campaign. Propose a node list (NPCs as `npc_*`, factions as `faction_*`, key locations as `place_*`) and a starter edge list (faction membership from npcs.md tables, NPC location from "Lives in / Based at" fields, faction relationships from world.md). Display the proposed list to the DM and **ask for approval** before writing — do not silently extract. After approval, run `add-node` and `add-edge` for each. Use `--since` matching state.md's current session count.

For existing campaigns being initialized for the first time, the `/dm:dnd load` flow offers to back the campaign directory up first; honour that flow rather than running init from a cold prompt.

### `/dm:dnd graph add-node --type T --name N [--tags ...] [--summary ...]`
Add a single node. Type is open vocab; suggested: `npc`, `faction`, `place`, `item`, `thread`. Default id is `<type>_<name-slug>`.

### `/dm:dnd graph add-edge --from <id> --to <id> --type T [--since N] [--note ...]`
Add a typed edge between two existing nodes. Edge type is open vocab; common: `loyal_to`, `opposes`, `allied_with`, `member_of`, `lives_in`, `controls`, `knows_about`, `friends_with`, `lover_of`, `owes`, `rules`, `related_by_blood`, `advances_thread`, `blocks_thread`. Always supply `--since` (the current session number from state.md) so historical replay works.

### `/dm:dnd graph set-disposition --to <npc-or-faction> --level <L> [--since N] [--note ...]`
Type how the **party** stands toward an NPC or faction on the normalized scale `allied | friendly | neutral | suspicious | hostile` — the same five values the display's faction panel uses, so the graph and the sidebar speak one language. The edge runs from the shared `party` node (auto-created on first use) to the target; its type is inferred from the target — `disposition` for an NPC, `standing` for a faction — and it carries the `level`.

Single-valued and current: setting a new stance **closes** any prior active party→target stance edge at `--since` (its arc stays queryable with `--at-session <old N>`) and adds the new level, so `scene-context` only ever surfaces the party's *current* stance. `scene-context` renders it as `The Party --[disposition:suspicious]--> Aldric` so the stance reads at a glance. Always pass `--since <current-session-N>`.

Use it whenever the fiction shifts the party's standing — an NPC turns on them, a faction they wronged goes hostile, an alliance is earned. It is the graph-side counterpart to the `standing` values pushed to the display via `push_stats.py --factions` and to the NPC-disposition lines in `state.md → ## Live State Flags`; keep the three consistent when a stance changes.

### `/dm:dnd graph close-edge --id <edge-id> --at-session N`
Mark an edge as ended at session N (e.g. when an alliance breaks). Original edge is preserved with `until_session` set; it remains visible in historical queries but is excluded from "active at session ≥ N" results.

### `/dm:dnd graph list [--type T] [--at-session N]`
Print a compact node table grouped by type. With `--at-session`, also reports active edge count at that session.

### `/dm:dnd graph show --id <node-id>`
Print one node with all incoming and outgoing edges.

### `/dm:dnd graph scene-context --place <id> [--present id1,id2] [--threads id1,id2] [--hops H] [--at-session N]`
**Primary query for in-session use.** Returns a focused subgraph from the current scene (place + present NPCs + active threads) bounded by hop count, optionally filtered to edges active at a given session. Output is grouped: nodes by type, then a relationships block. Default `--hops 2`. Use this when you need to recall who-relates-to-whom in the current scene without re-reading `npcs-full.md` or session-log archives.

### `/dm:dnd graph subgraph --seed <id> [--seed <id>] [--hops H] [--at-session N]`
Lower-level traversal — same as `scene-context` but with arbitrary seed nodes. Use when the scene framing doesn't fit (e.g. tracing faction politics independent of any specific place).

### `/dm:dnd graph extract [campaign-name] [--last-session-only]`
Run a Haiku pass over the campaign's session-log to propose new edges with verbatim source-anchors. Outputs a proposal JSON to `~/.claude/dnd/campaigns/<name>/graph-proposals-<date>.json` for human review. Does **not** write to graph.json — that's the apply step.

### `/dm:dnd graph extract --deterministic [--last-session-only] [--write FILE]`
**Zero-LLM alternative.** Pattern-matches session-log sentences against the bundled verb-table seed (`data/graph/verb_table_seed.yaml`) and emits the same proposal shape as the Haiku pass — no Claude API call, no cost, fully portable. Trades recall (~50%, clean subject-verb-object only) for precision (~95%) and determinism. Prints proposals to stdout, or writes them with `--write`.

### `/dm:dnd graph extract --deterministic --apply [--min-confidence low|medium|high] [--no-auto-nodes]`
One-shot auto-apply: run deterministic extraction and write proposals at/above `--min-confidence` (default `high`) straight into `graph.json` — deduped against existing edges and **idempotent** (re-running adds nothing new). Missing nodes are auto-created as `npc_*` placeholders unless `--no-auto-nodes` is set. Use this for a hands-off relationship sweep at `/dm:dnd save`; use the review path below when you want a human in the loop.

### `/dm:dnd graph extract-apply --proposals <file> [--pick N1,N2,...] [--review]`
Apply previously-extracted proposals (from either the Haiku or deterministic pass). Without `--pick`/`--review`, applies all. With `--pick`, applies only the listed proposal indices. With `--review`, walks proposals one at a time with y/n/q prompts.

### Suggested DM workflow

1. **First session after install:** `/dm:dnd load` will offer to initialize the graph (with a backup-first prompt). Accept; review the proposed seed; approve.
2. **During session:** when a relationship shifts in narration, run `/dm:dnd graph add-edge` (or `close-edge`) with `--since` set to the current session number. Don't batch this — record at the moment of the narrative change so you don't forget.
3. **Before a heavy social/political scene:** run `/dm:dnd graph scene-context --place <current-place> --present <key-NPCs>` to refresh which relationships matter right now.
4. **At `/dm:dnd save`:** review the session log and add any edges you missed during play (the save flow runs an automatic sweep and presents proposals for approval).

---

## `/dm:dnd oracle <subcommand>` — solo/improv oracle tools

Dice-driven oracles for improvised play — they keep pacing transparent and rollable instead of letting the DM invent every beat. All subcommands invoke `python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py <subcommand>`. Rolls are stdlib-random and seedable (`--seed N`) for reproducibility. Zero LLM calls.

### `/dm:dnd oracle chaos [--campaign N]`
Show the campaign's current **chaos factor** (Mythic-style, 1–9). 1 = the PCs are firmly in control; 9 = the world is spinning out from under them. Stored in `state.md → ## Session Flags` as `chaos_factor: N` (default 5).

### `/dm:dnd oracle chaos set --campaign N --value V`
Set the chaos factor to V (clamped 1–9) and persist it to `state.md`.

### `/dm:dnd oracle chaos adjust --campaign N (--pc-won | --pc-lost)`
Move the factor one step the standard Mythic direction: `--pc-won` (PC achieved the scene goal) → −1; `--pc-lost` (PC was reactive or failed) → +1. Adjust once per scene.

### `/dm:dnd oracle ask [--likelihood L] [--campaign N | --chaos C] [--seed S]`
Ironsworn-shaped **yes/no** oracle. Likelihood ∈ {`sure-thing`, `likely`, `50/50`, `unlikely`, `no-way`} (default `50/50`); the chaos factor (read from the campaign, or `--chaos`) shifts the odds. Returns a verdict — `yes`/`no` optionally suffixed `-and` (extreme, on doubles) or `-but` (qualified, near the threshold) — plus the d100. Use when the fiction poses a question the prep doesn't answer.

### `/dm:dnd oracle event [--seed S]`
Mythic **Random Event Focus** (d100). Returns a direction label (`new NPC`, `NPC action`, `move toward thread`, `PC negative`, etc.) — interpret it against the campaign's current threads, NPCs, and locations. Use when a scene needs an unexpected turn.

### `/dm:dnd oracle scene [--seed S]`
Two-word **scene-meaning** generator (action verb + subject noun, One Page Solo Engine). Use as a spark when narration runs dry or an event focus rolls. Interpret loosely.

---

## `/dm:dnd recap` — precomputed party state-diff

Deterministic state-diff between two character snapshots — `python3 ${CLAUDE_SKILL_DIR}/scripts/session_recap.py`. Recaps are the #1 thing an LLM hallucinates (wrong HP, dropped facts); this computes the change set from data so narration never has to. Reads the character sheets at `~/.claude/dnd/campaigns/<name>/characters/*.md` and merges live `tracker.json` conditions/concentration. Zero LLM calls.

### `/dm:dnd recap snapshot --campaign N`
Snapshot the party's current state (HP/temp/level/hit dice/death saves/conditions/concentration/exhaustion/inspiration/spell slots) to `~/.claude/dnd/campaigns/<name>/.recap/`. Rolls the previous `last.json` to `prev.json` so the next diff has a baseline. **Take a snapshot at `/dm:dnd end`** so the next session's load can diff against it.

### `/dm:dnd recap diff --campaign N [--before FILE] [--after FILE]`
Diff the prior snapshot against the current state and print a one-paragraph plain-English summary (e.g. *"Aldric: took 18 damage (30→12 HP); gained Poisoned; spent 2 level 1 slots."*). With no `--before`, uses the stored `prev`/`last` snapshot; with no `--after`, snapshots live state on the fly. **Inject this line at `/dm:dnd load`** as the mechanical half of the recap. `--json` emits the structured change list.

---

---

# Script reference (on demand)

## Session Recap — `scripts/session_recap.py`

Deterministic state-diff between two character snapshots. Computes the mechanical change set (HP/temp/level/hit dice/death saves/conditions/concentration/exhaustion/inspiration/spell slots) from data so narration never recomputes it — recaps are the single thing an LLM is most likely to hallucinate. Reads `<campaign>/characters/*.md` and merges live `tracker.json` conditions/concentration. Zero LLM calls.

```bash
CAMP=my-campaign

# Snapshot the party now — sets the baseline (writes to <campaign>/.recap/,
# rolling last → prev). Run this at session START (e.g. /dm:dnd load) so there
# is a baseline to diff against later.
python3 ${CLAUDE_SKILL_DIR}/scripts/session_recap.py snapshot --campaign $CAMP

# Diff the baseline against current state → one-paragraph summary, then ADVANCE
# the baseline to "now" so the next diff chains from here. Run at /dm:dnd save
# (end of session) for a since-start recap, or each turn for a since-last-turn
# recap — either way it advances, so consecutive diffs never re-report old deltas.
python3 ${CLAUDE_SKILL_DIR}/scripts/session_recap.py diff --campaign $CAMP
# → "Aldric: took 18 damage (30→12 HP); gained Poisoned; spent 2 level 1 slots."

# Same comparison without moving the baseline (ad-hoc "what changed so far?"):
python3 ${CLAUDE_SKILL_DIR}/scripts/session_recap.py diff --campaign $CAMP --no-roll

# Structured change list instead of prose:
python3 ${CLAUDE_SKILL_DIR}/scripts/session_recap.py diff --campaign $CAMP --json

# Diff two snapshot files directly (no campaign lookup):
python3 ${CLAUDE_SKILL_DIR}/scripts/session_recap.py diff-files before.json after.json
```

---

## Oracle — `scripts/oracle.py`

Dice-driven solo/improv oracles (Mythic chaos factor, Ironsworn yes/no, Random Event Focus, scene-meaning word pairs). Keeps pacing transparent and rollable instead of invented. Rolls are stdlib-random and seedable (`--seed N`). The chaos factor persists in `state.md → ## Session Flags` as `chaos_factor: N`. Zero LLM calls.

```bash
CAMP=my-campaign

# Chaos factor (1-9): show / set / adjust (persisted to state.md)
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py chaos --campaign $CAMP
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py chaos set --campaign $CAMP --value 7
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py chaos adjust --campaign $CAMP --pc-lost

# Yes/no oracle — likelihood + chaos modifier → verdict + d100
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py ask --likelihood likely --campaign $CAMP
# → "NO-BUT  (d100=82, likelihood=likely, chaos=8)"

# Random Event Focus (d100 → direction label)
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py event

# Scene-meaning word pair (action / subject)
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py scene
```

Likelihoods: `sure-thing`, `likely`, `50/50`, `unlikely`, `no-way`. Verdict suffixes: `-and` (extreme, on doubles), `-but` (qualified, near threshold).

---

## Deterministic Graph Extraction — `scripts/graph_extract_deterministic.py`

Zero-LLM relationship extractor. Pattern-matches session-log sentences against the bundled verb-table seed (`data/graph/verb_table_seed.yaml`) and emits typed edge proposals in the exact shape `campaign_graph.py` consumes. ~50% recall (clean subject-verb-object only), ~95% precision, no Claude API call. Usually driven through `campaign_graph.py extract --deterministic` rather than directly:

```bash
CAMP=my-campaign

# Propose edges (stdout), no writes:
python3 ${CLAUDE_SKILL_DIR}/scripts/campaign_graph.py extract --campaign $CAMP --deterministic

# One-shot auto-apply high-confidence proposals into graph.json (idempotent):
python3 ${CLAUDE_SKILL_DIR}/scripts/campaign_graph.py extract --campaign $CAMP \
    --deterministic --apply --min-confidence high
```

---

## Display Companion Setup (one-time)

```bash
cd ${CLAUDE_SKILL_DIR}/display
pip3 install -r requirements.txt
```

```
Terminal (run claude directly — no wrapper needed)
    ↓ send.py calls per narration block / dice roll / stat change
Flask on https://localhost:5001 (dnd-display-app.py — HTTPS, self-signed cert)
    ↓ Server-Sent Events
Browser tab → Chromecast → TV
```

**Start the display:**
```bash
bash ${CLAUDE_SKILL_DIR}/display/start-display.sh          # localhost
bash ${CLAUDE_SKILL_DIR}/display/start-display.sh --lan    # LAN mode (phones, tablets)
open https://localhost:5001                                  # open browser before /dm:dnd load
```

`start-display.sh` always force-kills any previous instance before starting — no manual pre-kill needed.

**Load a campaign:**
```
/dm:dnd load <campaign-name>   # skill auto-detects running display, pushes party stats
```

The DM skill sends each narration block, dice result, and stat update via `send.py` calls (see Active DM Mode in SKILL.md for full send sequence and stat flag reference).

Open the browser tab and Chromecast it *before* running `/dm:dnd load` so the browser is connected when the opening narration streams in. The display buffers the last 60 chunks and replays them to reconnecting browsers.

**Scene detection:** server scans narration for keywords and shifts background gradient + particle type (17 scenes: tavern, dungeon, forest, crypt, arcane, ocean, etc.). Crossfades over ~2.5 s.

**Audio (Python-side):** `audio.py` auto-imported by `dnd-display-app.py`. Two toggles: Ambient (looping soundscape) and Effects (one-shot SFX). Both default off. Scene changes crossfade the ambient loop. All synthesis via numpy — no audio files needed.

---

## Continuity Autosave — `scripts/autosave_checkpoint.py`, `scripts/install_autosave_hook.py`

Behind-the-scenes continuity checkpoint for long sessions, so a context compaction never loses the player's place. Two layers; see the *Continuity micro-save* rule in SKILL.md and the `/dm:dnd autosave` command.

```bash
# Opt-in: register the Stop hook (writes ~/.claude/settings.json, idempotent)
python3 ${CLAUDE_SKILL_DIR}/scripts/install_autosave_hook.py
python3 ${CLAUDE_SKILL_DIR}/scripts/install_autosave_hook.py --uninstall
python3 ${CLAUDE_SKILL_DIR}/scripts/install_autosave_hook.py --status

# The hook target (also runnable by hand to force a snapshot or inspect state)
python3 ${CLAUDE_SKILL_DIR}/scripts/autosave_checkpoint.py --status
python3 ${CLAUDE_SKILL_DIR}/scripts/autosave_checkpoint.py --campaign <name> --snapshot-only
```

`autosave_checkpoint.py` runs as a Claude Code **Stop hook** (after each turn). It reads the active campaign from `<runtime-dir>/active-campaign.json` (written at `/dm:dnd load`) and the `autosave` flag from that campaign's `state.md`. It **no-ops** when no campaign is active (e.g. a non-D&D session), when `autosave: off`, or when already inside a hook-driven continuation. Every turn it snapshots `state.md` to the runtime dir; every N turns (default 10, `DND_AUTOSAVE_EVERY` to override) it emits a Stop-hook `block` decision that prompts the DM to flush continuity before yielding. The hook is **opt-in** — the in-model micro-save cadence works without it.

**When to use:** offer `install_autosave_hook.py` to players running long imported modules who hit compaction mid-session. The flag toggle (`/dm:dnd autosave on|off`) is the in-session control.

## Lazy Corpus — `scripts/corpus_check.py`

Imported (structured) campaigns keep the full module text as a lazily-loaded reference layer instead of inlining it. Layout:

```
<campaign>/
  world.md           # load-time core (Foundations, Three Truths, factions)
  world-nodes.md     # lazy: full Quest Seed Bank + Adventure Nodes (per-act read)
  arc.md             # lazy: full act/chapter tree (state.md holds current+next only)
  source-index.md    # chapter-id -> source file -> one-line scope
  source/<id>.md     # lazy: one file per chapter, the module's source text
```

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/corpus_check.py --campaign <name>
```

Validates that every chapter id in `source-index.md` has a matching `source/<id>.md` (and vice-versa) and that `arc.md` exists. Run it at the end of `/dm:dnd import`. A campaign with no `source/` layer (dynamic, sandbox, or a pre-v2.2.0 import) is reported as a clean no-op — nothing to validate, and its load path is unchanged.
