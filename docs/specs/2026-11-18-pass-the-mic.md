---
title: Pass the mic — speaking to the DM from your phone
status: approved        # draft → approved → built
date: 2026-11-18
areas: [phone, display, tts, narration]
---

# Pass the mic

## Goal

At the couch, players should be able to just say what they do. A player holds
the button on their phone, talks, lets go, and it goes to Claude, with no
pressing Send after every sentence. One person talks at a time, and the table
can see who has the mic and who is next. The TV narration gets quieter so the
speaker can be heard. Typing stays available at all times.

## Decisions

**How it works**

- **D1 — Each player's phone is the mic.** No shared room mic. *Why:* a room
  mic would have to work out who is speaking and ignore table chatter, and it
  would send things to Claude by mistake.
- **D2 — Typing is always there.** The text box sits next to the mic button
  on every screen and in every state: while someone else holds the mic, while
  speech-to-text is off, and while waiting in the queue. Speaking is an
  option, never the only way to act.
- **D3 — Hold to talk, and swipe up to lock.** Press and hold to speak, and
  let go to finish. Swiping up while holding locks the mic on for a long
  speech, and a tap ends it. This replaces tap to start and tap again to stop.
- **D4 — Speech sends itself, with a short undo.** After transcription the
  text shows on the phone for about 3 seconds with **Edit** and **Cancel**,
  then sends by itself. **Edit** puts it in the text box and waits for Send.
  An empty or unsure transcription is never sent by itself. It waits in the
  box for the player.
- **D5 — One speaker at a time, and others queue.** While someone is talking,
  everyone else's button says "Mira is talking". Holding it anyway puts you
  next in line, and your phone buzzes when it's your turn. A mic held for more
  than 60 seconds is released automatically, as it is today.

**The table**

- **D6 — The TV narration gets quieter while someone talks.** It drops to
  about 30% volume and comes back up when they let go. The text keeps typing,
  and narration never stops. What they said goes to Claude in turn as it does
  today.
- **D7 — The TV shows who's talking.** A small "🎙 Mira" badge while someone
  holds the mic, with the queue beneath it.
- **D8 — Out of character is Claude's call.** Claude decides from what was
  said whether it's an action or a question to the DM ("wait, how far is the
  door?"). It answers questions without moving the story on. There's no extra
  button.
- **D9 — Spoken actions are not read back aloud** (foundation spec D22).

**Failure and speed**

- **D10 — When speech-to-text fails, fall back to typing.**
  - **Speech is off or slow:** if speech-to-text is off, the model won't
    load, or a clip takes more than about 8 seconds, the phone says so in one
    line ("Couldn't hear that — type it instead") and opens the text box with
    anything already transcribed in it.
  - **Mic blocked:** if the browser blocks the mic (no permission, or not on
    HTTPS), the button says how to fix it, instead of failing with no
    message.
- **D11 — Fast enough to feel like talking.** About 2 seconds for a typical
  sentence. Transcription uses the GPU when Kokoro does, and otherwise the CPU
  with `small.en`, falling back to `base.en` if that's too slow. No new
  install. faster-whisper is already the requirement, and Windows setup is
  unchanged.

**Scope**

- **D12 — Built on `/phone`.** This replaces the Talk button on the Phone
  sheet spec's `/phone` page and supersedes that spec's D15. The old page is
  left as it is.

## Acceptance criteria

- **AC1** — The text box and Send work in every mic state: idle, someone else talking, queued, speech-to-text off, mic blocked. *Check:* browser at phone width, each state.
- **AC2** — Holding the button records and letting go stops. Swiping up locks it, and a tap ends the locked recording. *Check:* browser (touch emulation) + playtest on a real phone.
- **AC3** — A confident transcription shows with Edit and Cancel and sends after about 3s. Edit moves it to the box with no send, and Cancel discards it. An empty or unsure result never sends by itself. *Check:* unit test on the confidence rule + browser.
- **AC4** — With the mic held, a second player's button shows "<name> is talking". Holding it queues them, and they are buzzed and given the mic when it's freed. A mic held over 60s is released. *Check:* unit test (mic lock and queue) + browser with two phones.
- **AC5** — While the mic is held, TV narration plays at about 30% and returns to full on release, and the text never pauses. *Check:* browser (audio volume read via JS).
- **AC6** — The TV shows the talking badge and the queue within 1s of a change. *Check:* browser.
- **AC7** — With speech-to-text off, a failed load or a clip over about 8s, the phone shows the one-line message and opens the box with any partial text. *Check:* unit test (simulated failure and slowness) + browser.
- **AC8** — With mic permission denied or on plain HTTP, the button explains the fix. *Check:* browser.
- **AC9** — A typical 5-second sentence comes back in about 2s on the user's PC. *Check:* timing script on the user's machine.
- **AC10** — Table check: a game night where players mostly speak rather than type, with no stuck mic, nothing sent by mistake and no lost speech. *Check:* playtest at a real table (user).

## At the table

Mira holds her button and says, "I creep up to the door and listen."
The TV narration gets quieter and the TV shows "🎙 Mira". She lets go, and
her phone shows the words with Edit and Cancel for a moment before they go
to Claude. Havoc's player holds his button meanwhile. It says "Mira is
talking", and he's queued, and his phone buzzes the moment she's done. The
rogue would rather type, so she just types. If speech-to-text dies
mid-session, the phones say so in a line and everyone keeps playing by
typing.

## Out of scope

- A shared room mic, or working out who is speaking from one mic.
- Reading players' spoken actions aloud, or changing their voice.
- The old phone page.
- What the DM says, its tone or pacing.

## Open questions

- None.
