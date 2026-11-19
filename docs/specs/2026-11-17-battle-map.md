---
title: Battle map — our own map with fog, timed moves and attack animations
status: approved        # draft → approved → built
date: 2026-11-17
areas: [display, maps, rules, campaign, narration]
---

# Battle map

## Goal

In combat the TV shows a battle map the whole table can follow from the
couch. It looks good with no art to find. Tokens move as the voice describes
the move. Monsters come out of the dark when the party could actually see
them instead of popping into view. Attacks play as a sword swing, an arrow or
a magic bolt. Claude already keeps positions in `battlemap.json` and checks
moves against walls and speed. This spec grows that map instead of bringing
in an outside VTT.

## Decisions

**Approach**

- **D1 — Grow our own map, no outside VTT.** We keep extending
  `battlemap.py` / `combat.py map` and draw it on the new `/tv` page.
  Foundry, Owlbear Rodeo and D&D Beyond Maps were ruled out. *Why:* Claude
  already moves tokens with our scripts, and an outside VTT adds an install,
  an account or a second app. A VTT is reconsidered only if the table needs
  something we can't build, such as dynamic lighting.
- **D2 — Combat only.** The map appears when a fight starts, through combat
  mode in the foundation spec. An exploration map (moving through a dungeon
  between fights) is a separate later item. *Why:* exploration needs its own
  answers on party movement and fog that persists over time.
- **D3 — Stop work on D&D Beyond Maps.** The `/dm:maps` skill and the
  once-per-round mirror stay in the repo unchanged, but receive no more work.
  The DM no longer offers them (remove the offer from `skills/dnd/SKILL.md`).
  *Why:* it has no API, setting up one scene took about 60 browser actions,
  and it can't keep up with turns.
- **D4 — Only the DM moves tokens.** Players say what they do, and Claude
  moves the tokens. Tapping a square on the phone to ask for a move, and
  showing a movement range, belong to the Phone sheet spec.
- **D5 — Built on the foundation's `/tv` page**, not the old `index.html`.

**The map itself**

- **D6 — Claude draws the map as text, and the TV makes it look good.** For
  each fight Claude writes a text layout (the existing format: `#` wall,
  `.` floor, `~` difficult terrain, `T` obstacle, `D` door, letters for
  tokens). The TV renders each square as themed tiles: stone floor, solid
  walls, doors, water and rough ground. A background image from the
  campaign's `maps/` folder stays optional. *Why:* there's no art to find for
  every fight. AI-generated map images belong to the Scene backgrounds spec.
- **D7 — The whole map always shows, at most about 30×20 squares.** It is
  scaled to fit its area (~60% in combat mode), never panned or zoomed. Claude
  splits or simplifies a bigger battle. *Why:* a square must stay readable
  from the couch at 1080p.
- **D8 — Tokens.**
  - Each token is a colored disc with initials, or a portrait if the
    character has one.
  - A ring around the token shows HP, and small icons show conditions.
  - PCs show their exact HP. Monsters only show a rough state: healthy,
    bloodied or near death.
  - The current turn's token is highlighted.

**What the party can see**

- **D9 — Fog follows the PCs' line of sight.**
  - **Worked out by the TV:** line of sight comes from every PC, using the
    walls already in the layout.
  - **Three kinds of square:** squares no PC can see are dark. Squares seen
    earlier stay dim, showing the room's shape but no monsters. Squares a PC
    can see now are fully shown.
  - **When monsters appear:** a monster only shows up once a PC could see it.
  - **Manual override:** Claude can reveal or hide an area by hand, for
    things like scrying or a trapdoor.

  *Why:* monsters just appearing feels misleading, and fog that works itself
  out can't be forgotten.
- **D10 — The hidden flag works inside sight too.** A token can be hidden
  even in plain view (invisible, hiding behind cover, disguised). It stays off
  the TV until Claude reveals it. Fog covers what the party can't see, and the
  hidden flag covers what they can't notice.
- **D11 — Everything is lit for now.** No light sources, darkness or
  darkvision in this spec. They are a later item.

**Moves, attacks and timing**

- **D12 — Moves go with narration blocks.** In the turn call, each move goes
  with the narration block that describes it, and the token slides into place
  when that block starts playing. *Why:* today every move lands before the
  voice has said anything.
- **D13 — An impossible move is bounced back, never narrated.**
  - **A player's move:** before narrating it, Claude checks it with a dry
    run of `combat.py move`. If it fails, Claude tells that player the limit
    (for example, "you have 10 ft left, and the wall blocks the direct
    route") and asks again.
  - **Claude's own moves:** if a move in Claude's turn call is invalid, the
    whole call is rejected with the reason (as today). Nothing reaches the
    TV, and Claude fixes it and sends again.
- **D14 — Spell areas flash with their narration.** A Fireball, cone or line
  outlines the squares it covers for a few seconds, starting with its
  narration block.
- **D15 — Attack animations.**
  - **In the turn call:** each attack goes with its narration block and
    names the attacker, the target, the kind of attack (melee, ranged weapon
    or spell) and whether it hit.
  - **On the TV:** a sword swing, an arrow from a bow, or a magic bolt. A hit
    flashes the target, and a miss shows a whiff or a deflection.
  - **Same mechanism as D14:** spell areas are played the same way.

  This is milestone 2 (see D19). The turn-call format for both milestones is
  designed together.
- **D16 — Sprites are drawn in code.** Everything is SVG and CSS on the page,
  matching the theme, with no sprite sheets, downloads or build step. Real art
  can replace them later without changing the turn-call format.
- **D17 — Animations never hold up the story.** Moves and attacks play within
  about 1 second while narration continues. A TV that is behind (or a tab that
  was in the background) skips straight to the current state and doesn't
  replay a backlog.

**State and failure**

- **D18 — Reconnecting and older saves.**
  - **After a reconnect or reload:** the TV redraws from `battlemap.json` as
    it is now: tokens in place, fog as currently seen, the current turn
    highlighted, and no animations replayed. Phones still highlight their own
    token.
  - **Older saves:** an older `battlemap.json` with no fog data, hidden flags
    or attack data loads with fog worked out from the current PC positions and
    nothing hidden. No migration.
  - **Setup:** no new dependency, port or install step.
- **D19 — Two milestones.**
  - **Milestone 1:** the map, tiles, tokens, fog, hidden flag, moves timed to
    narration, bounced moves and spell areas.
  - **Milestone 2:** attack animations.

## Acceptance criteria

**Milestone 1**

- **AC1** — A text layout renders on `/tv` as themed tiles (floor, wall, door, difficult terrain, obstacle). With an image in `maps/`, the image is drawn under the grid. *Check:* browser screenshot at 1920×1080.
- **AC2** — In combat mode a 30×20 layout fits its ~60% area with no panning. A layout over the cap is refused with a clear message to Claude. *Check:* unit test + browser screenshot.
- **AC3** — Tokens show a disc with initials (or a portrait), an HP ring and condition icons. PCs show exact HP, monsters only healthy, bloodied or near death. The current turn is highlighted. *Check:* unit test on the payload + browser screenshot.
- **AC4** — In a turn call, a move attached to a later narration block animates when that block starts, not before. The animation finishes within about 1s and never delays the text or voice. *Check:* unit test on the turn payload + browser (timed).
- **AC5** — A move through a wall or beyond speed in Claude's turn call rejects the whole call with the reason, and nothing reaches the TV. *Check:* unit test.
- **AC6** — A dry-run move check returns the specific limit (feet left, blocking wall) without changing the map. *Check:* unit test on `combat.py`.
- **AC7** — Squares no PC can see are dark, previously seen squares are dim with no monsters, and a monster appears only when a PC could see it. Moving a PC around a corner reveals the monster there. *Check:* unit test on the line-of-sight function + browser.
- **AC8** — Claude can reveal and hide an area by hand, and the TV follows within 1s. *Check:* unit test + browser.
- **AC9** — A hidden token is never sent to the TV or phones until revealed, even inside sight. *Check:* unit test (payload has no hidden tokens).
- **AC10** — A spell area outlines its squares for a few seconds, starting with its narration block. *Check:* browser.
- **AC11** — Reloading the TV mid-fight redraws the current state with no replayed animations. A backgrounded tab that comes back jumps to the current state. *Check:* browser.
- **AC12** — An older `battlemap.json` without the new fields loads and plays with defaults. A campaign with no map works as before. *Check:* unit test with an old fixture.
- **AC13** — The DM no longer offers D&D Beyond Maps. The `maps` skill files are unchanged. *Check:* unit test on SKILL.md text + diff review.
- **AC14** — Milestone 1 table check: a 3-room layout fight on `/tv` where tokens move as the voice reaches them, fog reveals a monster around a corner, and a bad player move is bounced back with its reason. *Check:* playtest at a real table (user).

**Milestone 2**

- **AC15** — An attack in the turn call (attacker, target, kind, hit) plays the right SVG sprite when its block starts: a sword for melee, an arrow for a ranged weapon, a bolt for a spell. A hit flashes the target, and a miss shows a whiff. *Check:* unit test on the payload + browser screenshots.
- **AC16** — Attacks never delay narration, and a lagging TV skips them. *Check:* browser (timed, backgrounded tab).
- **AC17** — Milestone 2 table check: every attack in a real fight plays the right sprite with hit or miss. *Check:* playtest at a real table (user).

## At the table

Claude rolls initiative and draws the room as text. The TV switches to
combat mode with the map on the left: stone floor and walls, the party's
tokens in the doorway, and the rest of the room dark. A player says, "I run
to the pillar and look behind it." Claude checks the move. It's 35 ft and the
player has 30, so Claude says so, and the player picks a closer square. As
the narrator says "you round the pillar," the token slides there, the fog
pulls back and a goblin appears in the corner. On the goblin's turn the voice
says "an arrow hisses past your ear." An arrow flies from the goblin and
shows a miss.

If the TV drops off the network, it comes back with everything in place and
nothing replayed. If Claude sends an impossible move, the turn is rejected
and Claude fixes it before anything shows.

## Out of scope

- Foundry, Owlbear Rodeo or any other outside VTT. New work on D&D Beyond Maps.
- A map outside combat (exploration).
- Players moving tokens from their phones, and showing movement range (Phone sheet spec).
- Light sources, darkness, darkvision.
- AI-generated map images or scene backgrounds (Scene backgrounds spec).
- Panning or zooming the map, maps over about 30×20.
- Changes to the old `index.html` map beyond keeping it working until `/tv` takes over.
- What the DM says, its tone or pacing.

## Open questions

- None.
