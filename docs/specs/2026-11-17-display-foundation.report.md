---
spec: 2026-11-17-display-foundation.md
plan: 2026-11-17-display-foundation.plan.md
branch: feat/display-foundation (pushed 2026-11-20)
built: S1–S16 (+ S9a); S17 gated on a game night
---

# Quality report: display foundation

Every step went through the pipeline: QA wrote failing tests first, the
step was built until they and the full suite passed, QA verified it in the
running app (against scratch copies of real campaigns, never the live data),
then the reviewer read the whole diff against the spec and plan. The final
suite is **958 tests, OK (4 skipped)**. The reviewer's second pass said
**ready to merge**.

## Acceptance criteria

| AC | What it asks | Result | Evidence |
|---|---|---|---|
| AC1 | `/tv` serves the new page; `/` unchanged | Pass | Flask tests (S1); `/` untouched until S17 |
| AC2 | Plain Windows install, no build step, no new deps | Pass | Fresh clone + clean venv + `pip install -r requirements.txt` served `/tv` and `/layout`, fonts offline (QA, S16); no `requirements.txt` change vs main |
| AC3 | Story preset at 1080p: narration largest, body ≥ 28px | Pass | QA S6/S7 screenshots; body text 31px at 1080p |
| AC4 | Layout change reaches the TV within 1 s, no reload | Pass | QA S7/S10 two-tab browser check |
| AC5 | Layout saved to `display.json`, survives restart, default Story | Pass | `test_display_config.py` |
| AC6 | 1–3 modules per panel; presets match D7 | Pass | Unit tests + browser (S7, S10) |
| AC7 | Turn order → combat within 1 s; clear restores; override both ways | Pass | Unit tests + browser (S4, S8, S10) |
| AC8 | Combat with map matches D10 | Pass | Map 0.566 wide, narration 0.377 at ≥ 28px, turn bar on top, Party header — same at 1080p and 4K (QA S16, `mockups/qa-2026-11-20/tv-combat-*.jpg`) |
| AC9 | Combat without a map matches D11 | Pass | QA S8 screenshot |
| AC10 | Initiative pushes the turn order; end clears it | Pass | Combat-script unit tests (S5) |
| AC11 | Every D13 parity item works on `/tv`; old tests pass | Pass, with one note | QA walked every D13 item (voiced typing, block order, rewind, NPC voices, Tap for audio, reconnect, approval cards). Three old tests in `tests/test_npc_voices.py` were rewritten to the D17/D19/D21 behaviour, as the plan's Blockers section foresaw — **you haven't said yes to these yet** |
| AC12 | "Reconnecting" badge within 3 s; catch-up with no duplicates | Pass | Badge at 2.5 s, no duplicate blocks after a server restart. A TV-only disconnect wasn't separately tested |
| AC13 | Same proportions at 4K, nothing blurry | Pass (sharpness from code) | Identical fractions at 1080p and 4K (`tv-story-*`, `tv-combat-*`). The browser pane downsizes 4K screenshots, so sharpness was checked from the code: live text, full-resolution canvases, no raster icons. Background embers and the map's 1-device-pixel grid don't scale (cosmetic) |
| AC14 | Voice settings save and apply from the next line; quota note on the laptop only | Pass | Narrator, speed, volume and engine each changed the next `/tts` request on `/tv`; simulated daily 429 showed the note on `/layout` only (`layout-quota-note.jpg`) |
| AC15 | A voiceless NPC gets its own voice, saved, never the narrator's, same next session | Pass | `test_cast.py`, `test_cast_qa.py`, `test_cast_review.py`; real-campaign dry runs; narrator change now reaches the TV (review fix) |
| AC16 | No shared NPC voices until the pool runs out; then distinct blends | Pass, **listening test needs you** | `test_voice_blends*.py`; real Kokoro blends differ from their parts. Listen to `mockups/blend-listening-test.wav` (not committed) |
| AC17 | Gemini on + quota gone → each NPC in its own Kokoro voice | Pass | `test_npc_engine*.py`; live run against real Kokoro; Gemini comes back after the wait (fix 9811d1d) |
| AC18 | PC voices; read-aloud off by default; typed (not Talk) actions read first | Pass | `test_pc_voices*.py`; live playtest with three phones |
| AC20 | Replay ▶ on a block, stop on second tap, never over live narration | Pass | S9a tests + browser |
| AC19 | One real game night on `/tv` | **Needs you** | Unlocks S17 (switch `/`) |

## What the checks caught

QA's verify found real bugs in S11, S12, S13 and S15, and the reviewer found
11 more across S11–S16 (the blocker: a narrator change from the laptop never
reached the TV). All are fixed, each with a regression test that fails on
the old code.

## Open items

- Your yes on the three rewritten `test_npc_voices.py` tests and one changed
  assertion in `test_npc_engine.py` (a Gemini narrator fallback voice is no
  longer given to NPCs).
- AC16 listening test; AC19 game night, then S17.
- Spec gap: in "Kokoro only" mode with Kokoro down, NPC lines go to Gemini.
- Low: a narrator set with `cast.py set --narrator` reaches the TV only on
  its next display broadcast; the laptop's settings save doesn't share the
  cast lock with a simultaneous terminal save.
- Cosmetic (QA): back-filled voices ignore sex/accent; Talk suppression is an
  exact-text match; small overlaps in an 800×600 window; no narrator row in
  the cast table.
