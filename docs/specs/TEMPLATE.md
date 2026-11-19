---
title: <short feature name>
status: draft          # draft → approved → built
date: <YYYY-MM-DD>
areas: []              # any of: phone, display, tts, narration, rules, maps, campaign, launcher
---

# <Feature name>

## Goal

<One paragraph, in the user's words: what changes for the people at the table,
and why it matters now.>

## Decisions

<Every decision settled during the grill, one per line, with the reason.
Later roles treat these as closed: the Architect plans within them, the
Reviewer flags a diff that contradicts them.>

- **D1 — <decision>.** <why>
- **D2 — <decision>.** <why>

## Acceptance criteria

<Concrete and checkable. QA turns each into a failing test or a playtest
step, and reports against these IDs. Say how each is checked.>

- **AC1** — <observable behavior>. *Check:* <unit test | browser at phone width | TV display | playtest>
- **AC2** — <observable behavior>. *Check:* <…>

## At the table

<What a live session looks like with this in place: players on phones, the
display on the TV, the DM narrating. Include what a player sees when it
fails mid-session.>

## Out of scope

<What this change will not touch. The Reviewer flags a diff that strays here.>

- <…>

## Open questions

<Anything deliberately left unresolved, with who decides and when. Empty is fine.>

- <…>
