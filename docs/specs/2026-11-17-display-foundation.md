---
title: Display foundation — TV layout, combat mode, theme and voices
status: approved       # draft → approved → built
date: 2026-11-17
areas: [display, tts, narration, campaign]
---

# Display foundation

## Goal

Rebuild the TV display as a clean, new page instead of patching the
10k-line `index.html`, so the couch game reads well at a distance: narration
front and center, side panels the host arranges per campaign, a combat mode
that puts the map on the main screen while narration stays readable, a new
theme, and a cast where every character keeps their own voice.

## Decisions

**Scope and approach**

- **D1 — This spec is the foundation only.** New TV page, panels and layout
  page, combat mode, theme, voice settings and the cast. The map system,
  phone sheet, pass-the-mic speech input and scene backgrounds are separate
  specs (`docs/ROADMAP.md` Phase 5). *Why:* the foundation decides where
  everything else lives; each later piece has its own hard questions.
- **D2 — Built alongside, then switched.** The new page is served at `/tv`.
  The old page stays the default at `/` until one real game night runs on
  `/tv` with no fallback; then `/` serves the new page. The old file is
  deleted only once phones have moved too. *Why:* never lose a working game
  night during the rebuild.
- **D3 — Plain JavaScript and CSS, split into small files served by Flask.**
  No framework, no npm, no build step, no new install requirement. *Why:* it
  must work after a plain install on Windows.
- **D4 — TV only.** Phones keep using the old page until the phone sheet spec.

**Layout**

- **D5 — Story layout by default.** Narration in the center and always the
  largest region outside combat; a side panel on each side; the player-input
  bar along the bottom.
- **D6 — Layout set from the laptop, saved with the campaign.** A layout
  page in the browser on the laptop running Claude: pick a preset or fill the
  panels; the TV follows live. Saved to `<campaign>/display.json`. A campaign
  without one (including every campaign saved before this change) gets the
  Story preset. *Why:* the laptop is always there and has a mouse; per-campaign
  saving keeps older campaigns working.
- **D7 — Panels hold a stack of 1–3 modules.** Modules: Party (HP and
  conditions), Turn order, Battle map, Quests, World clock & effects.
  Presets: **Story** (Party left; Quests + Clock right), **Tactical** (Party
  left; Turn order + Map right), **Minimal** (no panels).
- **D8 — Designed for 1080p, sharp at 4K.** Laid out at 1920×1080 in units
  relative to the screen, never fixed pixels; body text no smaller than ~28px
  at 1080p. Checked at both 1080p and 4K. *Why:* some TVs are 4K; couch
  distance is the hardest viewing case.

**Combat mode**

- **D9 — Combat mode turns on and off by itself.** It turns on when the turn
  order is set and goes back to the saved layout when it is cleared. The host
  can flip it by hand from the laptop at any time.
- **D10 — Combat layout keeps narration fully readable.** Map on the left
  ~60%; narration in a full-height column on the right ~40% at normal reading
  size, typing in step with the voice, with earlier lines visible above the
  newest; turn order as a slim bar across the top with the current turn
  highlighted; Party HP and conditions in a compact header above the
  narration. *Why:* the table must still follow what happens; side-by-side
  means the map never moves.
- **D11 — Combat without a map.** Turn order large in the center, narration
  at full size below it, Party on the right.
- **D12 — Starting combat starts combat mode.** The command that rolls
  initiative pushes the turn order itself, and the command that ends combat
  clears it. *Why:* a step the DM can forget will be forgotten.

**What carries over, what changes**

- **D13 — Parity before the switch.** The new page matches the old on:
  Kokoro narration with typing in step with the voice; blocks in order with
  roll requests pinned to the bottom; the ↶ rewind; NPC voice blocks; the
  "Tap for audio" prompt; a "reconnecting" badge when the connection drops,
  catching up when it returns; the phone-approval cards (a new phone asking
  to join shows a card with Approve and Deny — added 2026-11-18, D24).
- **D24 — New phones can be approved from `/tv`.** The old TV's
  phone-approval cards are part of the parity list. The old TV's on-screen
  dice roller, DM Help button and sound-effects toggle are not: players roll
  and ask from their phones. *Why:* without the cards, a new phone can't join
  while the TV runs `/tv`.
- **D25 — Any narration or NPC block can be heard again, from the TV.**
  A faint ▶ on each narration and NPC block on `/tv` (history included)
  replays that block's audio in its own voice; the text stays as it is.
  Tapping it again stops it. Live narration wins: a replay stops when the
  next block's voice is due, and a replay can't start while live voice is
  playing. TV only, not the phones. *Why:* the old page's per-block play
  button; a line missed across the room should be easy to hear again.
  Added 2026-11-19.
- **D14 — The background is its own layer.** It shows the current scene
  background unchanged, as a placeholder. The Scene backgrounds spec
  (animations, generated images) replaces that layer later without touching
  layout or theme.
- **D15 — New theme, picked from mockups.** Everything about the current
  theme goes except the background. Three mockups (TV at 1080p and 4K, one
  story screen, one combat screen); the user's pick is added here as a
  decision before the Architect step.
- **D16 — The DM's words, tone and pacing do not change.**
- **D23 — Theme: Ink & Margin with Hearthlight type.** Picked 2026-11-17
  from the mockups (`docs/specs/mockups/display-themes.html`, "Your pick").
  Layout and color from Ink & Margin: almost no panel boxes, text set
  directly on the background with a soft shadow, thin rules between the
  columns, a deep red accent (`#e06a55`). Type from Hearthlight: Fraunces for
  headings, Literata for body text, Fraunces italic for labels. The mockup's
  tokens are the starting point for the build's color and type tokens.
  *Why:* the user's pick; open layout lets the background carry the mood.

**Voices**

- **D17 — Voice section on the laptop page.** Narrator voice picker with a
  preview (default `bm_lewis`); NPC voices: **Kokoro only** (default) or
  **Gemini, falling back to Kokoro** (for a paid key); volume and speech
  speed. When Gemini's daily quota runs out, the laptop page says NPCs moved
  to Kokoro; the TV shows nothing. Saved in `display.json`. *Why:* the free
  Gemini tier (~10 voice requests a day) runs out early in a night; paid
  costs roughly $0.25–0.60 a night for NPC lines.
- **D18 — A cast list.** The Voice section lists every character and their
  voice, with preview and change. Voices are stored with the characters (in
  `npcs.md` and the character files), not in `display.json`.
- **D19 — Every NPC has its own voice.** The DM picks a fitting, unused voice
  when it creates an NPC and writes it to `npcs.md`. If an NPC speaks with no
  voice, the server assigns the least-used free voice and writes it back. No
  NPC is read in the narrator's voice. *Why:* Claude matches a description
  better than a rule; the server makes sure nobody falls through.
- **D20 — When voices run out, blend.** Past the ~20 English Kokoro voices, a
  new NPC gets a saved blend of two Kokoro voices. If blending is not
  supported or sounds bad, reuse the least-used voice instead, never the
  narrator's or a player character's.
- **D21 — Each NPC's Gemini voice falls back to its own Kokoro voice.** Every
  cast member has a Kokoro voice. With Gemini on, an NPC also has a Gemini
  voice; when Gemini runs out, it falls back to its own Kokoro voice, not a
  shared mapping. *Why:* today two NPCs can fall back to the same voice.
- **D22 — Player characters have voices.** Each gets a voice that fits it at
  creation or D&D Beyond import, stored in its character file, changeable in
  the cast list, never given to an NPC. "Read player actions aloud" is off by
  default, per campaign; when on, typed actions are read in the character's
  voice before the DM responds. Spoken (Talk) actions are not read back.

## Acceptance criteria

- **AC1** — `/tv` serves the new page; `/` still serves the old page unchanged. *Check:* unit test (Flask client).
- **AC2** — The new page runs from a plain install on Windows with no build step and no new dependencies. *Check:* unit test that the static files are served + fresh-install check.
- **AC3** — In the Story preset at 1920×1080, narration is the largest region on screen and body text is ≥ 28px. *Check:* browser at 1920×1080, screenshot.
- **AC4** — A layout change on the laptop page reaches the TV within 1s without a reload. *Check:* browser, two tabs.
- **AC5** — The layout is saved to `<campaign>/display.json` and survives a server restart; a campaign without the file opens in the Story preset. *Check:* unit test.
- **AC6** — Each panel takes 1–3 of the five modules; the Story, Tactical and Minimal presets match D7. *Check:* unit test + browser.
- **AC7** — Setting the turn order switches the TV to combat mode within 1s; clearing it restores the saved layout; the manual override works both ways. *Check:* unit test + browser.
- **AC8** — Combat with a map matches D10: map ~60% left, narration column ~40% at ≥ 28px with earlier lines visible and typing in step, turn-order bar on top, Party header. *Check:* browser screenshot + playtest.
- **AC9** — Combat with no map matches D11. *Check:* browser screenshot.
- **AC10** — Rolling initiative pushes the turn order; ending combat clears it, with no separate push command. *Check:* unit test on the combat script.
- **AC11** — Every D13 parity item works on `/tv`, and the existing display tests still pass. *Check:* unit tests + playtest.
- **AC12** — When the server stops, a "reconnecting" badge shows within 3s; when it returns, the page catches up with no duplicated blocks. *Check:* browser (stop and restart the server).
- **AC13** — At 3840×2160 the page has the same proportions as at 1080p, with no blurry text or icons. *Check:* browser at both sizes, screenshots.
- **AC14** — Voice settings save to `display.json` and apply from the next line; a Gemini daily-quota error shows the note on the laptop page and nothing on the TV. *Check:* unit test (simulated 429) + browser.
- **AC15** — An NPC that speaks with no voice is given one, written to `npcs.md`, never the narrator's; it has the same voice next session. *Check:* unit test.
- **AC16** — No two NPCs share a voice until the pool is used up; after that, blended voices are saved per NPC and sound distinct. *Check:* unit test + a listening test by the user.
- **AC17** — With Gemini on and the quota used up, each NPC is read in its own Kokoro voice. *Check:* unit test (simulated 429).
- **AC18** — Importing or creating a player character stores a voice in its file that no NPC is ever given; "Read player actions aloud" is off by default; when on, typed actions (not Talk) are read in that voice before the DM's response. *Check:* unit test + playtest.
- **AC20** — On `/tv`, the ▶ on a narration or NPC block replays its audio in that block's voice without retyping; a second tap stops it; live narration stops a replay and is never talked over by one. *Check:* unit test + browser.
- **AC19** — One real game night runs on `/tv` with no fallback to the old page; only then does `/` switch. *Check:* playtest at a real table (user).

## At the table

The laptop runs Claude and opens the layout page; the TV shows `/tv`; players
use their phones as today. Outside combat, the TV reads like a book page:
narration large in the middle, the party on one side, quests and the clock on
the other. The narrator speaks in one voice, every NPC in their own, and a
returning innkeeper sounds the same as last week. When the DM rolls
initiative, the TV switches to the map on the left with the story still
readable on the right and whose turn it is across the top; when the fight
ends, it switches back. If the TV loses its connection, a small badge says so
and the page catches up when it reconnects. If Gemini runs out mid-session,
only the laptop mentions it; NPCs keep their own voices through Kokoro.

## Out of scope

- The phone page (input, dice, sheet, stat edits) — Phone sheet spec.
- Building or integrating a different map system; D&D Beyond Maps mirroring — Map spec.
- Pass-the-mic speech input — Pass the mic spec.
- New scene backgrounds, animations, generated images — Scene backgrounds spec.
- What the DM says, its tone or pacing.
- Changing players' live speech into their character's voice.

## Open questions

- **Kokoro voice blending.** Whether the installed Kokoro engine supports saved blends (D20). The Architect confirms from the code; if not, D20's fallback applies.
