---
title: Phone sheet — play, sheet and map on each player's device
status: approved        # draft → approved → built
date: 2026-11-18
areas: [phone, display, rules, campaign, narration]
---

# Phone sheet

## Goal

Each player's phone becomes their whole seat at the table: what they say or
type, their dice, a character sheet that reads like D&D Beyond and stays up
to date by itself, and in combat a map where they tap a square to move. The
bookkeeping (HP, slots, hit dice, items, gold) is handled automatically as
Claude narrates, so nobody has to track it. A quiet edit is there for when
something needs fixing.

## Decisions

**Approach**

- **D1 — A new player page, built alongside the old one.** Served at
  `/phone` in plain JavaScript and CSS with no build step, using the
  foundation's theme. The old page stays for phones until one game night runs
  on `/phone` without falling back. Device approval and the per-player token
  are unchanged. *Why:* the same approach as `/tv`, so a working game night is
  never lost.
- **D2 — One link that adapts to the device.** A narrow touch screen gets the
  phone layout, and a wider screen gets the desktop layout. A small "Desktop
  version / Phone version" switch overrides it, and each device remembers the
  choice.
- **D3 — The phone layout has three tabs: Play, Sheet, Map.**
  - **Play:** the Send/Talk box, the "Waiting on …" banner, roll requests and
    dice.
  - **Sheet:** the character sheet.
  - **Map:** the battle map, only in combat.

  Play opens by default. A roll request from the DM jumps to Play from any
  tab.
- **D4 — The desktop layout shows Play and Sheet side by side**, with the Map
  below them in combat.

**The sheet**

- **D5 — Read from the existing character file.** The phone shows identity,
  abilities, combat stats, saves, skills, attacks, spells, features and
  inventory from the character's `.md` file. A section the server can't read
  is shown as plain text, not left out. *Why:* older characters keep working
  with no migration.
- **D6 — Claude tracks resources by itself.** Whenever narration uses
  something up, the turn call carries that change: damage, healing, temp HP,
  a spell slot, a hit die, an item used or gained, gold, or a condition added
  or ended. The server applies it to the character file, and the phone and
  the TV's Party panel update live. *Why:* the point is that nobody has to
  track it. Today only HP and conditions are pushed reliably.
- **D7 — Players track resources, and Claude handles the build.** By hand, a
  player can only change: HP and temp HP, spell slots, hit dice, death saves,
  inspiration, conditions, inventory and gold, and notes. Ability scores,
  level-ups, features, proficiencies and max HP stay with Claude and its
  scripts.
- **D8 — Hand edits are low-key.** The sheet reads like a sheet, not a form.
  Each editable section has a small pencil that opens + and − steppers just
  for that section.
- **D9 — A hand edit is applied immediately, and Claude is told.** It shows
  on the TV and reaches Claude with the next input as a short line (for
  example, "Havoc: −7 HP (12→5)"). Claude can correct it. There's no approval
  step.
- **D10 — Every change is stored as a difference, never an overwrite.** Both
  Claude's and the players' changes are recorded as differences ("−7 HP",
  "−1 level-2 slot"), never as a new total. That way a heal and damage
  arriving at the same moment both land.

**Rolls and actions**

- **D11 — Tap to roll from the sheet.** Tapping a skill, save, attack or
  spell rolls it with the right bonus. If the DM asked for that roll, the tap
  answers the request. If not, it goes to Claude as an action ("Havoc rolls
  Athletics: 17"). Entering a roll from physical dice stays available for
  both.
- **D12 — Cast and Use go through Claude.** Tapping **Cast** on a spell or
  **Use** on an item sends the action to Claude. Claude narrates it, and the
  slot or item is used up through D6. The phone never spends a resource by
  itself. Tapping a spell's name shows its description from the project's
  rules data.

**Map tab**

- **D13 — Tap to move, or just type it.** The Map tab shows the battle map
  with fog (Map spec), centered on the player's own token.
  - **Tap:** tap a square to see the path and its cost in feet ("25 of
    35 ft"), then press **Move here**. The move is checked with the Map
    spec's dry run before it's sent, and a move that can't be done shows its
    reason on the phone.
  - **Type or say it:** describing a move in the Send box works as it does
    today.

**Failure, speech and scope**

- **D14 — Nothing is lost when the phone drops.** A roll, action, move or edit
  that can't reach the server shows as **Not sent** with a Retry button. It's
  never queued silently and sent later out of order. When the phone
  reconnects, it reloads the sheet and the current roll request.
- **D15 — Talk stays as it is.** Today's speech-to-text is unchanged. Speech
  input design belongs to the Pass the mic spec.
- **D16 — Two milestones.**
  - **Milestone 1:** everything except the Map tab.
  - **Milestone 2:** the Map tab, built after Map milestone 1.
- **D17 — No new install step, dependency or port.**

## Acceptance criteria

**Milestone 1**

- **AC1** — `/phone` serves the new page, and the old phone page is unchanged. Device approval and the token work on `/phone`. *Check:* unit test (Flask client) + browser.
- **AC2** — At phone width on a touch device the page shows the Play, Sheet and Map tabs. At desktop width it shows Play and Sheet side by side. The switch overrides this and is remembered per device. *Check:* browser at 375×812 (mobile emulation) and 1440×900.
- **AC3** — A roll request jumps the phone to Play from any tab. *Check:* browser.
- **AC4** — The sheet shows every section of a real imported character. An unreadable section shows as plain text. *Check:* unit test on the parser with real and broken fixtures + browser.
- **AC5** — A turn call carrying HP, temp HP, a slot, a hit die, an item, gold and a condition updates the character file, the phone sheet and the TV Party panel within 1s. *Check:* unit test + browser (two tabs).
- **AC6** — Only the D7 fields have a pencil, and steppers apply a difference. Build fields have no edit control. *Check:* unit test + browser.
- **AC7** — A hand edit reaches Claude with the next input as a short line with the before and after values. *Check:* unit test on the input drain.
- **AC8** — Damage from Claude and healing from a player arriving at the same moment both apply. *Check:* unit test (concurrent differences).
- **AC9** — Tapping a skill, save or attack rolls with the right bonus. With a matching request open it answers the request, and without one it is sent as an action. Physical-dice entry works for both. *Check:* unit test + browser.
- **AC10** — Cast and Use send an action to Claude and never change a slot or item on the phone. A spell's name shows its description. *Check:* unit test + browser.
- **AC11** — With the server unreachable, a roll, action or edit shows Not sent with Retry and is never sent later on its own. After reconnecting, the sheet and the open roll request reload. *Check:* browser (stop and restart the server).
- **AC12** — Talk behaves as on the old page. *Check:* browser + the existing STT tests.
- **AC13** — Milestone 1 table check: a full game night with every player on `/phone`, no fallback to the old page or paper. *Check:* playtest at a real table (user).

**Milestone 2**

- **AC14** — In combat the Map tab shows the fogged map centered on the player's token. Tapping a square shows the path and its feet cost. Move here sends the move only if the dry run passes, and otherwise shows the reason. *Check:* unit test + browser at phone width.
- **AC15** — Outside combat the Map tab is hidden. *Check:* browser.

## At the table

Each player opens the same link. Phones get tabs, and a laptop gets the side
by side layout. Havoc's player taps **Athletics** to shove a door, and the
roll goes to Claude. A goblin hits Havoc for 7, and the HP on his phone and on
the TV drops as the narrator says it. The cleric taps **Cast** on Cure Wounds.
Claude narrates the healing, the slot dot empties and Havoc's HP comes back
up, with nobody touching a number. In combat the rogue opens Map, taps a
square behind the goblin ("30 of 30 ft") and presses Move here. If the
Wi-Fi blips, the rogue's phone shows Not sent with a Retry button, and
nothing goes out twice.

## Out of scope

- Speech input design (who holds the mic, push-to-talk, the TV's mic): Pass the mic spec.
- Character creation, level-up flows or ability edits on the phone.
- Changing the character file format.
- The battle map itself (Map spec). This spec only adds the phone's view of it and tap-to-move.
- The TV page, beyond its Party panel showing resource changes live.

## Open questions

- None.
