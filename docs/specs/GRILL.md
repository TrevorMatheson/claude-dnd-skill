# Grill setup — MyDM

Read by `/grill-me` before it starts. It sets when to grill, what every grill
must cover, and where the spec goes. The spec's shape is `TEMPLATE.md` here.

## When to grill

**Grill:** a new feature, a change in what players or the DM experience, or
anything touching more than one area (phone, display, TTS/narration, rules,
maps, campaign files, launcher).

**Skip:** a bug fix whose cause is already known, a typo, a refactor with no
behavior change, a test-only change. Say it qualifies for a skip and let the
user decide; on a skip, the next step is QA writing the failing test.

## Branches every grill must cover

Seed the design tree with these on top of whatever the idea raises. Settle or
explicitly rule out each one before the frontier can be empty.

1. **The table.** Walk a real session: 2–4 players on phones, the display on
   a TV, the DM in Claude. Who does what, on which screen, in what order?
2. **Failure mid-session.** What does a player see when this breaks: a dropped
   phone connection, a slow or failed voice, a backgrounded tab, a DM turn
   that never arrives? The game must keep moving.
3. **Timing.** Does anything wait on anything else (narration, dice, input,
   audio)? What is the longest acceptable wait, and what shows during it?
4. **The DM's voice.** Does this change narration, tone, or pacing? The tone
   belongs to the story, not a narrator persona.
5. **Rules and state.** Does it touch 2024 rules data, combat, or campaign
   files? What happens to campaigns saved before this change?
6. **Windows and setup.** Does it need a new install step, port, or
   dependency? It must work after a plain install on Windows.
7. **Done.** What is the smallest version worth playing with, and how will we
   know it works at a real table?

## Facts to look up, not ask

Before round 1, check `docs/ROADMAP.md` for related items and read the code
the idea touches. Ask the user only for decisions.

## Output

Write the spec to `docs/specs/<YYYY-MM-DD>-<slug>.md` from `TEMPLATE.md`,
`status: draft`. On the user's approval, set `status: approved`. The next
step is the `architect` agent's build plan (see `PIPELINE.md`) — do not
start building.
