---
title: Scene backgrounds — mood layers and a picture of where you are
status: approved        # draft → approved → built
date: 2026-11-18
areas: [display, narration, campaign, launcher]
---

# Scene backgrounds

## Goal

The TV background should show where the party is. An animated mood layer
(fog, embers, rain, torchlight dust) always sets the feel. When the party
reaches a place, a painted image of that room, cave or street (what the
characters would see, not the characters themselves) fades in underneath.
Narration stays easy to read on top of it. We test the image generator first
and only build around it if the pictures are good enough.

## Decisions

**What the background is**

- **D1 — Two layers: mood and place.**
  - **Mood layer:** always present, built on today's gradients and
    particles, which the user likes. It gets richer per scene type (drifting
    fog, embers, rain, dust in torchlight and so on).
  - **Place image:** sits under the mood layer when one exists for the
    current location.

  *Why:* the mood layer works with nothing generated, and images make places
  memorable.
- **D2 — Mood follows the scene type as it does today.** The server already
  detects the scene type from keywords in the narration. This spec only makes
  the layers richer.
- **D3 — The image shows the place, through the party's eyes.** A wide view
  of the location as the party would see it on arrival, built from Claude's
  description of the place. The request always says no people, no creatures
  and no text. Characters and monsters stay on the map and in the narration.

**Where images come from**

- **D4 — Gemini, or images you supply.** Images come from Gemini's image
  model on the existing key when image generation is turned on, or from images
  dropped in `<campaign>/scenes/`. No local Stable Diffusion. The Gemini call
  uses the standard library, as `tts.py` does, so there's no new dependency.
  *Why:* a local generator is a multi-GB install, which breaks the "plain
  install on Windows" rule.
- **D5 — Made once per place, saved and reused.**
  - **The turn call names the place:** it carries a location (a name plus a
    short visual description) when the party arrives somewhere new.
  - **Generated in the background:** the current background stays until the
    image is ready, then crossfades. Nothing ever waits on it.
  - **Saved and reused:** stored as `<campaign>/scenes/<location>.png` and
    reused on return.
  - **Replaceable:** Claude can ask for a new image when a place changes
    (after a fire, say). An image the user dropped in with the same name
    always wins.
- **D6 — One art style per campaign.** Chosen on the laptop page from a few
  presets and added to every image request so the scenes look like they
  belong together. The presets are picked after the trial (D12).
- **D7 — Quota out or Gemini off means mood layer only.** A note on the laptop
  page and nothing on the TV. That place gets the mood layer until an image
  exists. *Why:* the same fallback as the voice.

**Readability and screens**

- **D8 — Narration always stays readable.** The image is darkened, softly
  blurred behind the narration column, and vignetted at the edges. The page
  checks body-text contrast against the image and dims it further if the
  contrast is low.
- **D9 — In combat, the map's area is plain.** In combat mode the map area
  has a plain background so the map reads clearly. Behind the narration
  column the scene image stays, dimmed further.
- **D10 — Soft is fine at 4K.** Request the largest widescreen size the model
  offers. A slightly soft image behind dimmed text is acceptable at 4K. The
  mood layer is drawn at full sharpness on top.
- **D11 — No generated battle maps.** The map stays as themed tiles (Map spec
  D6).

**Milestones and state**

- **D12 — Milestone 0 is a trial.** Generate about 6 scenes from real
  campaign locations (a tavern, a cave, a forest road, a dungeon room, a town
  square, a ruin). Show them on the TV at 1080p and 4K with narration on
  top. The user decides whether they're good enough.
  - **If yes:** build everything.
  - **If no:** build the mood layers and user-supplied images only, and
    drop D4's Gemini generation, D6 and D7.
- **D13 — Milestone 1** is the richer mood layers, place images (generated or
  supplied), the readability rules, combat behavior and the style setting.
- **D14 — Older campaigns.** A campaign with no `scenes/` folder gets the mood
  layer only. No migration, no new install step or port.
- **D15 — Built on the foundation's `/tv` background layer** (foundation D14).
  The old page is not changed.
- **D16 — The DM's words, tone and pacing don't change.** Naming the location
  in the turn call is the only addition.

**Milestone 0 result (recorded 2026-11-22)**

- **D17 — The trial passed: build everything.** Six pictures were made on the
  free-tier key with `gemini-2.5-flash-image` (tavern, cave, forest road,
  flooded crypt, town square, ruined abbey), with no quota error. None showed
  people, creatures or text. They were shown on `/tv` behind sample narration
  at 1920×1080 and 3840×2160, with body-text contrast between 8.5:1 and 14.4:1
  measured on screenshot pixels. Two came back with black letterbox bars, so
  the request now asks for a full frame. The user's decision was yes, so D4's
  generation, D6 and D7 are built. The user also decided:
  - **Style presets (D6):** one preset for now, "Painterly, muted":
    *painterly fantasy illustration, muted colours, soft atmospheric light,
    dark edges* (the trial's style).
  - **Generation default:** on whenever a Gemini key exists; it can be turned
    off per campaign on the laptop page.
  - **While a new place's picture is being made (refines D5):** the previous
    place's picture fades out at once, and the new place shows its mood layer
    alone until its picture is ready, then the picture fades in. A place that
    already has a picture shows it at once.

## Acceptance criteria

**Milestone 0**

- **AC1** — A trial script generates about 6 scene images from real location descriptions, with no people, creatures or text. It shows them on `/tv` behind sample narration at 1920×1080 and 3840×2160. *Check:* screenshots + the user's decision, recorded as a decision here.

**Milestone 1**

- **AC2** — Each scene type has its own animated mood layer, and switching scene crossfades. *Check:* browser screenshots per scene type.
- **AC3** — A turn call naming a new location starts generation in the background, the TV crossfades when the image is ready, and narration never waits. *Check:* unit test (generation stubbed) + browser (timed).
- **AC4** — Images are saved to `<campaign>/scenes/<location>.png` and reused on return with no new request. A user-supplied file with the same name is used instead of generating one. Claude can force a new image. *Check:* unit test.
- **AC5** — Every image request includes the campaign's style and says no people, no creatures and no text. *Check:* unit test on the request builder.
- **AC6** — Body text over every trial image meets the contrast floor (target WCAG AA, 4.5:1, measured behind the narration column), with the automatic dimming. *Check:* browser contrast check script.
- **AC7** — In combat mode the map area is plain and the image is dimmed behind the narration column only. *Check:* browser screenshot.
- **AC8** — With image generation off, the quota used up (simulated 429) or an error, the TV shows the mood layer only, and the laptop page shows a note. *Check:* unit test + browser.
- **AC9** — A campaign with no `scenes/` folder shows mood layers with no errors. *Check:* unit test.
- **AC10** — No new dependency in `requirements.txt` and no new port. *Check:* unit test / diff review.
- **AC11** — Table check: a game night where every new place gets a background within the first few lines of narration and nobody has trouble reading the text. *Check:* playtest at a real table (user).

## At the table

The party walks into a flooded crypt. The narrator starts describing it, and
the fog and dripping-water mood layer is already there. A few lines later a
dark painting of a half-submerged stone hall fades in underneath, softened
behind the text. When they fight, the map takes the left side on a plain
background, and the crypt stays faintly behind the story. Next week they come
back, and the same picture is waiting. If Gemini has run out for the day,
the TV just shows the mood layer, and only the laptop mentions it.

## Out of scope

- Generated battle maps, characters, monsters or portraits.
- Local image generation (Stable Diffusion etc.).
- Video backgrounds.
- Changes to the old page's background.
- Layout or theme (foundation spec).

## Open questions

- ~~The art-style presets are chosen after the milestone 0 trial (user, then).~~
  Answered in D17.
