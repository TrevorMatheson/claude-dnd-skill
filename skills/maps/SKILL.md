---
name: maps
description: "Runs D&D Beyond Maps (the official VTT) from the browser for the current campaign: finds a battle map that fits the party's location, adds it to the D&D Beyond game, places character and monster tokens, sets fog of war, starts initiative and shows the map to players. D&D Beyond has no API, so everything is done by driving the Maps page in the browser. Invoke with /dm:maps, or when the DM asks to put a scene, location or encounter on the map / VTT / D&D Beyond."
tools: Read, Write, Edit, Bash, AskUserQuestion
---

# D&D Beyond Maps — browser-driven VTT

D&D Beyond Maps (`https://www.dndbeyond.com/games/<gameId>`) has **no public API or
endpoint**. Every action in this skill is done by operating the page in a browser,
the same way the DM would with a mouse. Read the whole "Driving the page" section
before the first click; the Maps UI has several traps that waste many turns.

`${CLAUDE_SKILL_DIR}` is this skill's directory. The sibling `dnd` skill lives at
`${CLAUDE_SKILL_DIR}/../dnd`; campaign files are found through its `paths.py`.

---

## 1. Which browser

- **Default: the built-in browser** (`mcp__Claude_Browser__*`). Load the
  `anthropic-skills:built-in-browser` skill first. The DM's D&D Beyond sign-in
  persists in its profile.
- **Claude in Chrome** (`mcp__claude-in-chrome__*`) when the DM asks for Chrome, or
  when a local image file must be uploaded (only Chrome has a `file_upload` tool —
  see §4, tier C). Load the `anthropic-skills:chrome-browser` skill first.

Never sign in, out, or type a password for the DM. If the page shows a sign-in
screen, ask the DM to sign in in the browser pane and wait.

Never click **Subscribe**, **Marketplace**, or any purchase/upgrade button. Those
panels appear in place of locked map categories; close them and move on.

---

## 2. Find the game and the scene

1. **Campaign.** If a `dnd` campaign is loaded in this conversation, use it.
   Otherwise ask which one. Resolve its folder:
   `python3 ${CLAUDE_SKILL_DIR}/../dnd/scripts/paths.py campaign-dir <name>`
2. **Saved VTT link.** Read `<campaign-dir>/vtt.md` if it exists (format in §7).
   It holds the D&D Beyond game id and the maps already made for each location.
3. **No game id yet.** Open `https://www.dndbeyond.com/games`, click the
   **Select Game** combobox and read its options (each option's `value` is the
   game id). Ask the DM which D&D Beyond game this campaign plays in if more than
   one exists. Save it to `vtt.md`.
4. **Open the game directly** with `navigate` to
   `https://www.dndbeyond.com/games/<gameId>`. Do **not** use the **GO** button:
   it opens a new tab, which the browser pane blocks.
5. **Scene.** From `state.md → ## Current Situation → **Location:**`, plus the
   matching entry in `world.md` / `world-nodes.md`, work out:
   - location name (used as the map title),
   - terrain/setting keywords (tavern, forest road, cave, ship deck, crypt, city street…),
   - who is present: PCs (party line in `state.md`) and, if combat is running or
     about to, the monsters (from the `tracker.py`/`combat.py` state or the DM).
   If the DM named a scene explicitly ("put the goblin ambush on the map"), that
   wins over `state.md`.

---

## 3. Map lifecycle at a glance

| DM intent | What to do |
|---|---|
| "Put us on the map" / new location | §4 find a map → §5 add it → §6 tokens → fog → Display |
| Back to a place already mapped | Pick it from the **Select Map** dropdown (it keeps its tokens and fog) |
| Encounter starts | Add monster tokens → **Add All Tokens** to the Combat Encounter panel |
| Players move / explore | Drag tokens; reveal fog over the newly seen area |
| "Show the players" | **Display Map** |
| Done with a map | Leave it. Removing deletes its tokens, fog and drawings — only on the DM's explicit yes |

---

## 4. Choosing a map ("grabbing" one for the area)

Work down these tiers and stop at the first good fit. Tell the DM in one line which
map you chose and why ("Tavern → *Game Night at the Sands and Suds* quickplay map").

**Tier A — reuse.** `vtt.md` already lists a map for this location → select it from
the **Select Map** dropdown at the top of the Maps page.

**Tier B — the D&D Beyond library.** Open the **Map Browser** (Select Map dropdown →
**Open Map Browser**). Type 1–2 setting keywords in its **Search** box (`tavern`,
`forest`, `cave`, `ship`, `crypt`, `street`, `swamp`, `keep`…) and read the results
with `get_page_text`. Categories in the left rail:

- *Essentials* — **Basic Maps** (free, always available), **Uploaded Maps**
  (Master tier only), **Quickplay Maps** and **Free Drop-in Encounters** (free,
  pre-built with tokens + fog: "Cursed Ship", "Harried Hideaway", "The Snake Pit",
  "Game Night at the Sands and Suds", "Shipwreck Troubles", …).
- *D&D Beyond Drops* — Drop-in Encounters, Dungeons, Fantastic Locations, Interiors,
  Taverns, Urban, Vessels, Wilderness. These are **subscription-gated**; a locked
  category shows a "Need more maps? / Subscribe" panel instead of maps. If you see
  that panel, the category is not available — go elsewhere, don't click Subscribe.
- Maps from sourcebooks/adventures the DM owns on D&D Beyond also appear here and in
  search results. Prefer an owned map whose name matches the location.

Quickplay maps arrive with their own monster tokens and fog. If those monsters don't
belong in the DM's scene, remove or ignore them (ask before deleting tokens the DM
might want).

**Fallback — Basic Maps by terrain.** Always available; plain textures with a grid:

| Setting | Basic map |
|---|---|
| Forest, road, plains, field | Grass Field / Grass |
| Cave, underdark, mine | Mushroom Cave |
| Tavern, house, shop, any wooden interior | Wood Floor |
| Castle, temple, dungeon, crypt, sewer | Stone Tiling |
| Desert, badlands, arena | Red Sands |
| Arctic, mountain pass, winter | Snow |
| Open water, swimming | Water |
| Ship deck, river boat | Boat |
| Abstract / no fixed space | Theater of the Mind Organizer (DM Tools) |

Each has an "(Animated)" twin; use the animated one only if the DM asks.

**Tier C — the DM's own image (Master tier only).** Only if the DM has the Master
tier and gives you an image (a file path, or asks you to find one). Uploading needs
the **Uploaded Maps** category and a real file upload, which only Claude in Chrome's
`file_upload` tool can do. Never download images from untrusted sites without the
DM's yes (state the file name, source and size). If the account isn't Master tier
(the Uploaded Maps category shows "Create your own BATTLEGROUND / Subscribe"),
say so and fall back to tier B.

---

## 5. Adding the map

1. In the Map Browser, each map row has a **Preview** (eye) and an **Add** (`+`)
   button. `find` returns them as `button "Add <Map Name> map"`.
2. Clicking Add opens **"Add map to game"** with a **Map Title** field prefilled with
   the library name. Replace it with the campaign's location name (e.g.
   `The Drowned Lantern — taproom`) so it is recognisable in the dropdown later:
   triple-click the field, type the title, then click **ADD**.
3. A toast "Added … to the map list" confirms it. Close the Map Browser (`×` top
   right). The map is now the active one for the DM.
4. Record it in `vtt.md` (§7).

---

## 6. Running the map

The left vertical toolbar on the map, top to bottom: **Select** (arrow), **Pan**
(hand), **Fog of War** (cloud), **Draw** (pen), **Shapes**, **Measure** (ruler),
**Ping** (target), **Tokens** (person), **Notes**, **Stickers** (image), **Lock
Scene Pins**. Bottom bar: **Display Map**, zoom −/100%/+, fullscreen, and the Game
Log / dice / info buttons. Left panel: **Combat Encounter**.

**Tokens.** Tokens tool → **Token Browser** with tabs **Monsters / Players /
Companions**.
- *Players* lists the D&D Beyond characters in the game (match them to the campaign's
  PCs by name). Hover a row and click its `+` to drop the token on the map.
- *Monsters*: type the name in **Search Monsters**, then `+` on the right row.
  Prefer the row matching the campaign's ruleset (rows marked *Legacy* are the 2014
  versions). Add one `+` per creature.
- New tokens land near the middle of the view. Close the Token Browser, then
  **drag** each token (`left_click_drag`) to its square. 1 grid square = 5 ft.
- Place PCs and monsters to match the narration (who entered first, who is hidden,
  distances already described). Hidden creatures: don't place them yet, or cover
  their area with fog.

**Fog of war.** Fog tool → **Cover All** / **Reveal All**, plus a brush, an eraser,
and a rectangle tool with size options. Typical start: **Cover All**, then reveal the
area the party can see (rectangle over the room they're in). Reveal more as they
explore. Ask the DM before **Reveal All** mid-session.

**Combat.** Once tokens are on the map, the Combat Encounter panel offers **Add All
Tokens** (or add tokens individually from the map) to track initiative and rounds in
Maps. Keep the `dnd` skill's own combat tracker as the source of truth for HP and
conditions; Maps is the visual layer. If the DM rolled initiative in chat, enter the
same order.

**Display Map.** The DM's view and what players see can differ; **Display Map** makes
the current map the one shown to the players. Do it when the DM says the scene is
ready, not while you're still placing hidden monsters.

**Ping.** Use Ping to point players at something the narration just mentioned, when
the DM asks.

---

## 7. `vtt.md` — remembering the table

Keep `<campaign-dir>/vtt.md` so later sessions reuse maps instead of rebuilding:

```markdown
# D&D Beyond Maps
**Game:** <D&D Beyond game name> — https://www.dndbeyond.com/games/<gameId>
**Account tier:** free (no uploads, Drops locked)   <!-- update if it changes -->

## Maps
| Location (state.md) | Map title in Maps | Source map | Notes |
|---|---|---|---|
| The Drowned Lantern | The Drowned Lantern — taproom | Wood Floor (Basic) | fog: back room hidden |

## PC tokens
| Campaign PC | D&D Beyond character |
|---|---|
| Mira Thornwood | Mira |
```

Update it whenever a map is added, renamed or removed, or a PC↔character match is
learned. It is a plain campaign file like `state.md`; no script manages it.

---

## 8. Driving the page — traps that cost turns

- **"What's New in Maps" popup** appears on load. **Escape** closes it (and also
  closes the Map Browser behind it — reopen that from the Select Map dropdown).
- **Coordinates.** Screenshots may be scaled (e.g. an 800×600 image of a 1024×768
  viewport). Click with coordinates from the latest screenshot's coordinate frame,
  or with a `ref` from `find`/`read_page`. A `ref` for something inside a closed
  dropdown fails ("outside the viewport") — open the dropdown first.
- **Screenshots lag** a frame after clicks that animate (adding tokens, closing
  dialogs). If the result looks unchanged, wait 1 s and screenshot again before
  clicking a second time — double clicks add duplicate maps/tokens.
- **Hover-only buttons.** The `+` on Token Browser rows only renders on hover; click
  its position on the row's right side.
- **Read, don't squint.** Use `get_page_text` / `find` for lists (map names, search
  results, token names); use screenshots for placement on the grid.
- **Feedback survey** widget on the right edge is noise — never fill it in.
- **No `javascript_tool` for actions.** Drive the UI with clicks and typing; use
  JavaScript only to inspect if something is truly stuck.
- **Removing a map** (trash icon in the Select Map dropdown) asks "This will remove
  all modifications currently on the map, such as tokens, fog of war, and drawings."
  Only confirm when the DM asked to remove that specific map. When the last map is
  removed, the Map Browser opens by itself.
- The Maps page is live for players in the same game. Anything placed is visible to
  them once the map is displayed, so stage first, then Display.

---

## 9. Reporting back

After each operation, tell the DM in a line or two what is now on the map (map title,
tokens placed, what's fogged, whether it's displayed). Then return to narration —
this skill supports the `dnd` session; it doesn't replace the DM's voice.
