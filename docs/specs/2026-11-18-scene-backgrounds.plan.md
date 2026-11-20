---
spec: 2026-11-18-scene-backgrounds.md
status: approved       # proposed → approved
approved_by: orchestrating session, 2026-11-20 (plan approval delegated by the user on 2026-11-20 for plans that stay within the approved spec)
---

# Build plan: Scene backgrounds (mood layers and a picture of the place)

## Answers to open questions

The spec has one open question: **the art-style presets**. Only the user can answer it, after the milestone 0 trial (D6, D12). The trial script (S1) takes `--style "<text>"` (repeatable), so candidate styles can be compared side by side before the user picks. S8 fills the preset list from that choice.

**Facts from the code that the plan relies on, or that the spec doesn't state:**

1. **How the scene type is chosen today** (D2).
   - The server scores keywords over the last 20 cleaned DM-narration chunks (`dnd-display-app.py:1040-1070`), only for plain narration (`:1797-1803`).
   - There are 17 scene types (`SCENES`, `:590-776`). Each carries `colors`, `accent`, one `particles` kind and a `label`.
   - The current scene goes out on every `/stream` connect, right after `hello` (`:3784-3787`). `tests/test_stream_seq.py:188-189` pins the order hello → scene → replay, so the plan adds no new event at connect. The place rides `stats`, which already goes out on connect (`:3801-3804`).
2. **What `/tv` draws today.** `static/tv/bg.js` is the old page's code, copied as a placeholder "until the Scene backgrounds spec replaces this layer" (`bg.js:1-5`, foundation D14).
   - Each scene has one particle kind out of 14 (`bg.js:358-363`).
   - Only the gradient crossfades (2.5 s, `tv.css:14`). Particles switch by letting the old ones die off (`bg.js:385-401`).
   - `resizeCanvas` calls `ctx.scale(dpr, dpr)` on every resize without resetting, so the scale builds up (`bg.js:79`). S4 rewrites this file and fixes it with `setTransform`.
3. **The scene changes when its chunk arrives, not when it is typed** (`tv.js:107-109`). The mood can therefore lead the voice by up to a block. D2 keeps this behaviour.
4. **The turn call has no location, and unknown keys are refused** (`send.py:673-677`). `location` has to be added to that set. An older `send.py` given a turn with `location` fails loudly rather than dropping it.
5. **`display.json` keeps only `layout` and `voice`.** `display_config.validate` rebuilds from defaults and copies just those two (`display_config.py:64-95`). `POST /display-config` merges only those two (`dnd-display-app.py:1540-1546`). A per-campaign scene setting (D4 toggle, D6 style) needs both changed (S8).
6. **A channel for laptop-only notes already exists.** `_display_status()` (`:1484-1493`) goes out in `GET /display-config` as `status` (`:1520-1528`), and `voices.js:238` renders `status.voice_note`. The scene note (D7) uses the same channel, so the TV never sees it.
7. **Stats live in the runtime folder, and rewind restores them.**
   - `stats.json` is in the runtime dir (`:139`), not the campaign. Rewind restores it (`:3685-3687`).
   - So a place stored in `stats` rewinds along with the turn that named it.
   - Changing campaign does not clear stats keys (`:2109-2133`). S2 clears the place there.
   - Rewind leaves `<campaign>/scenes/` alone (`rewind.py:33-43` takes top-level files and `characters/` only), so images survive a rewind.
8. **The key and the tests.** The Gemini key comes from `DND_TTS_KEY`, then `GEMINI_API_KEY`, then `~/.config/claude-dnd/tts.key` (`tts.py:135-146`).
   - `tests/__init__.py:35-36` redirects HOME, so the key file can't be read during tests.
   - It does not clear the env vars. A real `GEMINI_API_KEY` in the user's environment would be visible to tests.
   - Every scene test therefore clears both env vars and patches `urllib.request.urlopen` to raise.
9. **Quota.** Free-tier limits are per model. `tts.py:214-217` gives the TTS model's daily limit; the image model has its own. Whether the free tier allows image requests at all, and how many a day, is unknown until the trial. `scene_trial.py list-models` (no quota cost) and the first real request will answer that. 429 parsing is reused from `tts._parse_quota` (`tts.py:230-247`).
10. **`/map-image` is the template for serving scene images** (`dnd-display-app.py:2339-2356`): name regex, no `..`, extension whitelist, `resolve()` plus `relative_to`.
11. **Ports.** The port is hard-coded to 5001 (`display_url.py:19`). Clients follow `DND_DISPLAY_URL` (`display_url.py:30-33`), so `send.py` and the trial's `show` command can reach a QA server on 5301–5309. QA starts it by importing `dnd-display-app.py` as a module and calling `app.app.run(port=…)` with a scratch `DND_CAMPAIGN_ROOT`. The runtime dir follows that root (`runtime_paths.py:26-30`).
12. **AC10 baseline.** `requirements.txt` is `flask, flask-cors, pygame, numpy, pyyaml` (`skills/dnd/display/requirements.txt`).
13. **"Real campaign locations" (AC1, D12).** The script ships six sample descriptions (tavern, cave, forest road, dungeon room, town square, ruin). It also takes `--locations FILE` (a JSON list of `{name, look}`), so the user can paste descriptions from their own campaign. Only the user can supply those.

## Shape

**Layers on `/tv`, bottom to top, all inside `#tv-bg`** (D1, D8, D10, D15). The page structure and theme don't change; `bg.js` creates the new elements itself.

1. **Mood base:** the existing `#bg-a`/`#bg-b` gradients from the scene colours.
2. **Place images:** two `<img class="place-img">` layers that crossfade. They use `object-fit: cover` and are darkened with `filter: brightness(var(--place-bright))`.
3. **Place tint:** the scene gradient again, as a 35% wash. It is shown only while an image is up, so the mood colour sits over the picture.
4. **Scrim:** sits on the narration column's rectangle. It has `backdrop-filter: blur(.5rem)`, `background: rgba(0,0,0,var(--scrim))` and a soft mask at its edges. `--scrim` is set by the automatic dimming. `@supports not (backdrop-filter…)` adds +0.1 alpha instead of the blur.
5. **Sky and mood canvas:** `#sky` and `#particles` are drawn sharp (D10). The mood canvas is masked to 50% inside the narration column so particles don't fight the text. The sky fades out while a place image shows, because the picture shows its own sky (the user can judge this at the trial).
6. **Vignette:** the existing `.vig`, which provides D8's edge vignette.

**New pure TV modules.** Both follow the foundation pattern: attach to `window.TV`, export to `module` for node tests.

| File | What it owns |
|---|---|
| `skills/dnd/display/static/tv/mood.js` | **Mood recipes.** `MOODS` holds one recipe per `SCENES` name: 2–4 layers drawn from particles (the existing kinds plus `fogbank`, `shafts`, `flicker` and `glowmotes`), counts, colours and a light-flicker setting. Examples: crypt is low fog, drips and a cold glow; tavern is embers, hearth flicker and dust in light shafts; city is rain, mist and lamplight.<br>**Other exports:** `recipeFor(scene)` falls back to the server's `particles` for unknown names. `makeMixer(fadeMs=2500)` gives `set(name, now)` and `weights(now)`; at most two recipes are live at once. `budget(recipe, w, h)` caps particle totals at 4K. |
| `skills/dnd/display/static/tv/place.js` | **Image geometry and contrast:** `coverRect(iw, ih, vw, vh)`, `relLum(r, g, b)`, `contrastRatio(l1, l2)`, `percentile(arr, p)`, and `scrimFor(lums, {ink, target: 4.5, min, max})`. `scrimFor` returns the scrim alpha that brings the 95th-percentile luminance behind the column to the target against `--ink`.<br>**`regions(mode, rects)`:** returns `{clip, plain, extraDim}` for each layout mode (D9).<br>**`makePlaceModel()`:** `set(place)`, `loaded(url)` and `failed(url)` return `show url`, `keep`, `clear` or nothing. Stale loads are ignored. A pending place keeps the current image (D5); a place with no image clears to mood (D7, AC9). |

`static/tv/bg.js` becomes the DOM driver for both modules. It:
- keeps `TV.bg.applyScene` and `TV.bg.setWorldTime`, and adds `TV.bg.setPlace(place)` and `TV.bg.contrastReport()`;
- watches `#tv-stage[data-mode]` with a MutationObserver, and `#tv-feed`, `#tv-party`, `#tv-inputbar` and `#tv-map` with a ResizeObserver;
- re-measures, debounced, whenever the image, the mode or the size changes. To measure, it draws the image (cover-fitted) plus the tint into an offscreen canvas at most 160 px wide, crops to the column, and solves for the scrim. The image is same-origin, so the canvas is not tainted.

New `static/tv/scene.css` holds the layer rules and the combat rules, so `tv.css` is not touched.

**Combat** (D9, AC7).
- In `combat-map` the place images are clipped (`clip-path: inset(…)`) to the narration column: the union of `#tv-party`, `#tv-feed` and `#tv-inputbar`. They get extra dimming there.
- `.stage[data-mode="combat-map"] .mapbox` gets an opaque `--scene-plain` background, so the map area is plain. The mapbox sits above the whole background layer, particles included.
- `combat-nomap` has no map area. The image is clipped to its narration column, with the same extra dimming.

**Server.**
- **New `skills/dnd/display/scene_images.py`** (stdlib only):
  - `slug(name)`: lowercase, hyphens, letters kept in any script, Windows-reserved characters and names removed, at most 60 characters, `None` if nothing is left.
  - `clean_place(obj)` returns `{name, look, renew}` or `None`.
  - `find_image(camp_dir, slug)` returns `(path, source)` or `None`. A missing `scenes/` folder gives `None`, never an error. User files match leniently: `The Flooded Crypt.jpg` matches `the-flooded-crypt`.
  - `image_url(path)` returns `/scene-image/<file>?v=<mtime>`.
  - If yes, it also gains the manifest, the worker and the status note.
- **`dnd-display-app.py`:**
  - `GET /scene-image/<name>`, copied from `/map-image`, serving from `<campaign>/scenes/`. The content type comes from the file's magic bytes.
  - A `place` branch in `/stats`. It resolves the image outside the stats lock, then stores `_current_stats["place"] = {name, slug, image, pending}`. `look` is never stored or broadcast, because stats reach phones.
  - The place is cleared when the campaign changes.
- **Data flow:**
  - `send.py --turn` posts `place` in the one `/stats` post after the blocks. Chunks post in milliseconds while the TV paces them, so the picture appears as the narration starts.
  - The TV reads `stats.place` (live, on connect, on rewind) and the small `{"place": …}` event the worker sends when an image is ready.
  - No new port, dependency or connect event.

**Turn call** (D5, D16). A turn-level `"location": {"name": "The Flooded Crypt", "look": "a half-submerged stone hall, pillars in black water"}`. `"location": null` clears the place. `"renew": true` exists only if the answer is yes.

**Gemini, if yes** (D4, D5, D6, D7).
- **New `skills/dnd/display/scene_gen.py`.** Written in S1 for the trial and reused if yes.
  - `GEMINI_IMAGE_MODEL` defaults to `gemini-2.5-flash-image`; the trial confirms it.
  - `build_request(look, style)` always says: a wide view of the place as the party sees it on arrival, no people, no creatures or animals, no text, letters or signs. It asks for the 16:9 aspect ratio (D10, the widest the model offers).
  - `generate(...)` uses `urllib` and the key from `tts._get_api_key`, and raises `SceneGenError(status, quota_id, retry_after)`.
  - `list_image_models()` does a GET that costs no quota.
- **The worker.** If yes, `scene_images` runs one daemon worker thread.
  - It writes to a temp file, then `os.replace` to `<campaign>/scenes/<slug>.png`.
  - It records `scenes/generated.json` as `{slug: {sha256, made, style}}`. A matching file whose hash is not recorded counts as the user's, and the user's file always wins.

**Trial** (D12, AC1). New `skills/dnd/display/scene_trial.py`:
- It prints a dry run by default.
- It makes real requests only with `--send`, and prints the request count before sending.
- It stops at the first 429.
- `show` puts the images on a running `/tv` (scratch campaign, `DND_DISPLAY_URL`) behind sample narration, through the real place path from S2–S5.

## Steps

All foundation steps this needs (`/tv`, the background layer, `/layout`, `display.json`) are already built (S1–S16). **Steps S1–S7 don't depend on the trial outcome.** They are built while the trial runs, and S7 is where the user decides.

### Part A: independent of the trial

### S1: Trial image script (dry run by default) and the no-new-dependency guard
- **Covers:** AC1 (generation), AC10, D3, D4 (stdlib call), D10, D12
- **Files:**
  - New: `skills/dnd/display/scene_gen.py`, `skills/dnd/display/scene_trial.py`, `tests/test_scene_trial.py`, `tests/test_scene_no_deps.py`
- **Change:**
  - **`scene_gen.py`:** as in Shape.
  - **`scene_trial.py [--locations FILE] [--style TEXT ...] [--out DIR] [--model M] [--max 8] [--send] [--again]`:**
    - Without `--send` it prints each request (model, aspect ratio, prompt; the key shown as `***`) and "Would make N image requests — none made (add --send)".
    - With `--send` it first prints "Making N image requests on your Gemini key", then writes `<out>/<slug>.png` and `<out>/index.json`.
    - It refuses (exit 2) when N > `--max`, and when there is no key.
    - It skips images that already exist unless `--again`.
    - On a 429 it stops at once and exits 3, naming the quota and how many images it made.
    - `--out` defaults to `<tempdir>/mydm-scene-trial`.
    - N = locations × styles.
  - **`scene_trial.py list-models`:** a GET-only check of which image models the key can see. It is explicit too.
  - **The real `--send` run spends the user's free quota.** The user runs it, or the main session runs it with the user's go-ahead, once. QA never does.
- **Done when (QA writes these first, no network):**
  - **Setup in every test:** clear `DND_TTS_KEY` and `GEMINI_API_KEY`, and patch `urllib.request.urlopen` to raise.
  - **Dry run:** the default run prints 6 requests and the count, calls `urlopen` zero times, and writes no files.
  - **Prompts:** every prompt contains the no-people, no-creatures and no-text phrases.
  - **`--send` with a stubbed `urlopen` returning inline PNG data:** 6 slug-named files and an `index.json`; the count is printed before the first call.
  - **Stub returning 429 on the 3rd call:** exit 3, 2 files, no 4th call, a message with the quota id.
  - **Limits:** no key exits 2 with zero calls; 9 locations without `--max 9` exits 2 with zero calls.
  - **Re-run:** existing files are skipped and the count drops.
  - **Key:** the key never appears in output.
  - **`list-models`:** calls the network only when named.
  - **`test_scene_no_deps.py` (AC10):**
    - `requirements.txt` equals the baseline set.
    - AST imports of every `skills/dnd/display/scene_*.py` are stdlib or local display/scripts modules.
    - None of those files contains `app.run(`, `.bind(` or a port number.
    - `display_url.PORT == 5001`.
    - This test is re-run at every later step.

### S2: Place images from `<campaign>/scenes/`, served and stored in stats
- **Covers:** AC4 (user-supplied file used), AC9, D5 (saved and reused; the user's file wins), D14
- **Files:**
  - New: `skills/dnd/display/scene_images.py`, `tests/test_scene_places.py`
  - Changed: `skills/dnd/display/dnd-display-app.py`:
    - the `import scene_images` after `import display_config` (`:53`);
    - the `GET /scene-image/<path:name>` route, placed after `/display-mode` (`:1572`) to stay away from the map code;
    - a `place` branch in `/stats` before `world_time` (`:2032`), with the lookup done before `with _stats_lock` (`:1868`);
    - `_current_stats.pop("place", None)` in the `campaign` branch (`:2109`) when the name changes.
- **Change:**
  - As in Shape. No active campaign, or no folder, gives `image: null`.
  - Among several of the user's files with the same slug, the order is png, jpg, jpeg, webp.
  - `place: null` clears.
  - `pending` is always false until S9.
- **Done when (unit tests through the Flask test client, scratch campaign root):**
  - **Slugs:** "The Flooded Crypt" gives `the-flooded-crypt`. Non-Latin letters are kept. "CON" and "a/../b" come out safe. "!!!" gives `None`. Length is at most 60.
  - **A user's file:** with `scenes/The Flooded Crypt.jpg`, `POST /stats {"place": {"name": "The Flooded Crypt", "look": "…"}}` broadcasts `stats.place.image == "/scene-image/The%20Flooded%20Crypt.jpg?v=<int>"` (or the encoded filename). GET on it returns 200 with `image/jpeg`. `look` appears in neither the broadcast nor `stats.json`.
  - **AC9:** with no `scenes/` folder, the response is 204, `image` is `None`, nothing is logged to stderr, and `GET /scene-image/x.png` returns 404.
  - **Unsafe requests:** `..`, `%2e%2e`, `.txt` and an absolute path all return 404.
  - **Bad shapes:** a non-dict, an empty name or an over-long name never gives a 500.
  - **Campaign switch:** changing campaign clears the place.
  - **Existing tests:** `tests/test_stream_seq.py` (connect order) and `tests/test_display_config.py` stay green.

### S3: The turn call names the place
- **Covers:** D5 (the turn call names the place), D16, AC4 (format)
- **Files:**
  - Changed:
    - `skills/dnd/display/send.py`: the `location` key in the allowed set (`:673-674`); a new `_turn_location(turn)` validator after `_turn_stats` (`:618-629`); `_turn_stats` adds `stats["place"]`; a new comment paragraph after `:551` (the existing header text is left alone).
    - `skills/dnd/SKILL.md`: one paragraph and the example key in "Display sync" (`:380-395`).
  - New test: `tests/test_turn_location.py`
- **Change:**
  - The location shape is `{"name": str ≤ 80, "look": str ≤ 400}` or `null`. Anything else rejects the whole turn before any `_post`, the same as a bad block.
  - **SKILL.md tells Claude:**
    - name the place on every arrival, including a return, and use the same name;
    - `look` is what the party sees on arrival (the place, not the people);
    - send `null` when the party leaves for somewhere unnamed.
  - No other narration guidance changes (D16).
- **Done when (fake `_post`, as in `tests/test_battlemap.py:191-193`):**
  - **Accepted:** a turn with a location sends one `/stats` post carrying `place` with name and look, after every chunk.
  - **Rejected:** a missing name, a non-dict, an over-long look or a numeric name exits 2 with zero posts.
  - **`null`:** sends `place: null`.
  - **No location:** no `place` key is sent.
  - **Docs:** the SKILL.md example, extracted from the file, parses and validates.
  - **Existing tests:** `tests/test_turn_and_awaiting.py` and the turn tests in `tests/test_battlemap.py` stay green.

### S4: A richer mood layer for every scene type, crossfading
- **Covers:** AC2, D1 (mood layer), D2, D10 (sharp on top), D15
- **Files:**
  - New: `skills/dnd/display/static/tv/mood.js`, `skills/dnd/display/static/tv/scene.css`, `tests/test_tv_mood.py` (node; skipped without node, as in `tests/test_tv_layout.py:75,110`)
  - Changed:
    - `skills/dnd/display/static/tv/bg.js`: rewritten. It keeps the gradient crossfade and sky, and draws recipe layers through the mixer. Soft layers (fog, shafts, flicker) go on a half-resolution canvas; particles on the full-resolution `#particles`. Resizing uses `setTransform`.
    - `skills/dnd/display/templates/tv.html`: `<script src="/static/tv/mood.js">` just before `bg.js` (`:54`), `<link … scene.css>` after `tv.css` (`:14`), and an updated comment at `:18-19`.
- **Change:** as in Shape. A scene switch crossfades all of its layers over 2.5 s. A third switch during a fade drops the oldest recipe.
- **Done when:**
  - **Node, recipe coverage:** every `SCENES` key (the test imports the app module and passes the keys to node) has a recipe with at least 2 layers, and no two recipes have the same signature.
  - **Node, fallback:** an unknown name falls back to the server's `particles`.
  - **Node, crossfade:** the mixer gives old weight 1 / new 0 at t0, about 0.5 / 0.5 at 1250 ms, and new only at 2500 ms. Three switches within 1 s leave at most 2 live recipes.
  - **Node, budget:** `budget` at 3840×2160 stays under the total particle cap.
  - **Python:** `/tv` loads `mood.js` before `bg.js`, and `tests/test_tv_feed.py:576-587` stays green.
  - **Browser** (scratch root, port 5301):
    - One screenshot per scene type at 1920×1080, by calling `TV.bg.applyScene({...})` with each `SCENES` entry.
    - A mid-fade screenshot shows both layers.
    - Average frame time at 3840×2160 is under 20 ms over 5 s (measured in the console).
    - No console errors.

### S5: Place image on `/tv`, with readability and automatic dimming
- **Covers:** AC6 (dimming and measurement), AC4 (TV side), AC9 (TV side), D1 (place image), D8, D10
- **Files:**
  - New: `skills/dnd/display/static/tv/place.js`, `tests/test_tv_place.py` (node and Python)
  - Changed:
    - `static/tv/bg.js`: image layers, tint, scrim, measuring, `setPlace` and `contrastReport`.
    - `static/tv/scene.css`
    - `templates/tv.html`: the `place.js` tag before `bg.js`.
    - `static/tv/tv.js`: about 5 lines after `:111` that pass `p.place`, `p.stats.place` and `p.rewind.stats.place` to `TV.bg.setPlace`, and `p.clear` as `setPlace(null)`, each in try/catch.
- **Change:**
  - As in Shape. A crossfade takes 2.5 s.
  - A failed image load fades to the mood layer with no TV notice.
  - `TV.bg.contrastReport()` returns `{image, scrim, ratio, p95}` for the current column.
- **Done when:**
  - **Node, maths:**
    - `relLum(#f1ebe1)` is about 0.83, and white against black gives a ratio of 21.
    - `scrimFor` on all-white samples gives an alpha whose result is at least 4.5:1 and at most `max`. On dark samples it gives `min`. `percentile` works.
    - `coverRect` handles wide and tall images.
  - **Node, place model:**
    - `set(A)` shows A. `set(B)` before A loads makes A's load ignored.
    - `failed(B)` clears.
    - `set({image: null, pending: true})` while A is shown keeps A; `pending: false` clears.
    - The same URL twice does nothing.
  - **Python:** `/tv` loads `place.js` and `scene.css`.
  - **Browser** (scratch root, port 5302; `send.py` with `DND_DISPLAY_URL=http://127.0.0.1:5302`). The test helper writes a near-white noise PNG and a dark PNG (stdlib zlib) into `scenes/`.
    - The image fades in within 3 s of the turn, and switching places crossfades.
    - `contrastReport().ratio` is at least 4.5 for both images.
    - Screenshots at 1920×1080 and 3840×2160.
    - A place with no file shows the mood only.
    - A reload shows the same image.
    - No console errors.

### S6: Combat: plain map area, image only behind the narration column
- **Covers:** AC7, D9
- **Files:**
  - Changed: `static/tv/place.js` (`regions`), `static/tv/bg.js` (clip and extra dimming), `static/tv/scene.css` (`.stage[data-mode="combat-map"] .mapbox { background: var(--scene-plain) }`, the clip variables)
  - Test: additions to `tests/test_tv_place.py`
- **Change:** as in Shape. Leaving combat restores the full image within one crossfade.
- **Done when:**
  - **Node, `regions`:**
    - `combat-map`: the clip equals the column union, the plain area equals the map rect, and `extraDim` is greater than in story.
    - `combat-nomap`: the clip is its narration column.
    - `story` and `minimal`: no clip.
  - **Browser** (port 5303, a bright place image, `combat.py init` plus a map):
    - A screenshot shows the map box's pixels outside the board in the plain colour and the image only in the right column.
    - `contrastReport().ratio` is at least 4.5 in combat.
    - After `combat.py end` the full image is back within 3 s.

### S7: Milestone 0 on `/tv`: show the trial images and record the decision
- **Covers:** AC1 (shown on `/tv` at 1080p and 4K, the decision), AC6 (measured over every trial image), D12
- **Waits for:** S1–S6, and the user's one `--send` run.
- **Files:**
  - Changed: `skills/dnd/display/scene_trial.py` (the `show` subcommand)
  - New: `tests/scene_contrast_check.py` (a QA tool, not run by discover: it decodes a screenshot PNG with stdlib zlib and prints the p95 luminance and ratio against `--ink` for a rectangle), with tests added to `tests/test_scene_trial.py`
  - After the decision: `docs/specs/2026-11-18-scene-backgrounds.md` gets a recorded decision (D17: yes or no, and the chosen styles if yes).
- **Change:** `scene_trial.py show --images DIR --campaign-root SCRATCH [--only N]`:
  - makes the scratch campaign `scene-trial` with the images copied under their slugs (as user files);
  - sets it active through `/stats {"campaign"}`;
  - sends, for each location, a turn built with `send.send_turn`: the location plus three sample narration paragraphs.
- **Done when:**
  - **Unit tests:** the built turns pass `send.py` validation and carry the location; files are copied by slug; the only posts go to the faked display. `scene_contrast_check` on a synthetic white/black PNG gives the expected ratios.
  - **Real run** (scratch root, port 5304):
    - Every trial image is shown at 1920×1080 and 3840×2160 behind narration: 12 or more screenshots.
    - For each image, `contrastReport()` is at least 4.5, and the screenshot check is at least 4.5 with `#tv-layer` hidden through the console.
    - The user says yes or no, and the decision is recorded in the spec.

### Decision gate (D12)

- **If yes:** build S8–S10, then S11.
- **If no:** skip S8–S10 and go to S11.
  - **Dropped:** AC3, AC5 and AC8, along with D4's Gemini generation, D6 and D7.
  - **AC4** shrinks to "a file the user supplies is used", which S2 already covers.
  - **`renew`** is never added or documented.
  - **AC6** stays covered by S5–S7 (trial images, user images).
  - **S11 removes** `scene_gen.py`, `scene_trial.py` and `tests/test_scene_trial.py`, so no unused Gemini image code ships. The user can choose to keep them as a dev tool instead.

### Part B: only if the user says yes

### S8: Per-campaign scene settings and style presets on the laptop page
- **Covers:** D6, D4 (generation on or off), D7 (the setting the note refers to)
- **Files:**
  - Changed:
    - `skills/dnd/display/display_config.py`: `SCENE_STYLES = {id: {label, prompt}}` from the user's trial picks; `default()` and `validate()` gain `"scenes": {"generate": <see Needs a spec decision 3>, "style": <first preset>}`.
    - `dnd-display-app.py`: `display_config_post` merges `data["scenes"]` (`:1542-1546`); `display_config_get` adds `scene_styles` (`:1522-1528`).
    - `templates/layout.html`: a new "Scene pictures" section after Voices, with the on/off toggle, the style menu, `<p id="lp-scene-note">` and the folder hint.
  - New: `skills/dnd/display/static/layout/scenes.js` (mount and 5 s poll, the `voices.js` pattern), `tests/test_scene_settings.py`
- **Done when (unit tests):**
  - **Validation:** an unknown style falls back to the default; a non-bool `generate` falls back to the default.
  - **Older campaigns:** a `display.json` without `scenes` loads defaults and the file is not rewritten.
  - **Partial save:** `POST {"scenes": {"style": "x"}}` keeps layout, voice and `generate`.
  - **Host only:** a non-localhost POST gets 403.
  - **GET:** lists `scene_styles`.
  - **Layout page:** has the section, and `scenes.js` is served.
  - **Existing tests:** `test_display_config.py`, `test_layout_page.py` and `test_layout_voices.py` stay green.

### S9: Generation in the background, saved and reused
- **Covers:** AC3, AC4 (no new request on return, the user's file wins, forced renewal), AC5, D3, D5
- **Files:**
  - Changed:
    - `scene_images.py`: manifest, `is_users`, a single worker with a de-duplicating queue, atomic writes, `on_done`.
    - `scene_gen.build_request`: the campaign style becomes required.
    - `dnd-display-app.py`: the `place` branch starts a job when there is no image, generation is on, a key exists and the quota isn't out. `_scene_ready(slug, url)` sets the image if that slug is still current, persists, and broadcasts `{"place": …}`.
    - `send.py` (`_turn_location`): accepts `renew: bool`.
    - `SKILL.md`: one sentence on `renew`.
  - New: `tests/test_scene_generation.py`
- **Change:**
  - **Pending:** `place.pending` is true while a job runs, so the TV keeps its current picture (D5).
  - **Renewing:** `renew` on a generated image regenerates it, and the old one shows until the new one is ready. `renew` on the user's image does nothing.
  - **Leaving early:** if the party moves on before a job finishes, the image is saved for later but not shown.
  - **Limits:** timeout 120 s; every exception is caught and goes to the status (S10).
- **Done when:**
  - **Unit tests:** `scene_gen.generate` is stubbed and the `urlopen` guard is on.
    - **AC3:** `/stats` returns in under 0.3 s while the stub blocks on an Event. The first broadcast has `pending: true, image: null`. On release: one `{"place": {image}}` broadcast, the file is in `scenes/`, and a manifest entry exists.
    - **AC4:** a second arrival makes zero stub calls. The user's file makes zero calls. `renew` makes one call, replaces the file and changes `?v=`. `renew` against the user's file makes zero calls.
    - **Moving on:** after leaving before completion there is no broadcast for the old slug, and the current place is unchanged.
    - **AC5:** every captured request body includes the campaign's style prompt and the no-people, no-creatures and no-text phrases.
    - **No key:** zero calls and `pending: false`.
  - **Browser, timed** (port 5305; QA patches `scene_gen.generate` in the imported module to sleep 8 s and return a PNG):
    - The first narration block starts typing at the same time with and without generation.
    - The picture crossfades in about 8 s later.

### S10: Quota out, generation off or an error: a laptop note, mood layer only on the TV
- **Covers:** AC8, D7
- **Files:**
  - Changed:
    - `scene_images.py`: `status()`, `quota_until` from `retry_after` or the end of the day for a daily quota.
    - `dnd-display-app.py`: `_display_status()` gains `scene_note` (`:1484-1493`).
    - `static/layout/scenes.js`: renders the note.
  - New: `tests/test_scene_quota_note.py`
- **Change:** the notes read:
  - "Scene pictures are off — places show the mood layer only."
  - "No Gemini key — …"
  - "Gemini's image quota is used up — back in about N h"
  - "Couldn't make the picture for <name> (<reason>)", which lasts 10 min.

  While the quota window lasts, no requests are made.
- **Done when:**
  - **Unit tests** (stub raising `SceneGenError(429, "…PerDay…")`, a 500, a network error; generation off):
    - The place ends `pending: false, image: null`.
    - `GET /display-config` has `status.scene_note` with the right text.
    - No captured TV broadcast contains the note text.
    - No calls are made inside the quota window.
    - The note goes when the window ends.
  - **Browser:** the laptop page shows the note, and the TV shows the mood only, with no notice.

### Part C: either answer

### S11: Docs and the table check
- **Covers:** AC11
- **Files:**
  - Changed: `skills/dnd/display/README.md` (the scenes folder, naming files after the place, the trial), `CHANGELOG.md`, `docs/ROADMAP.md` (`:271-273`), and the spec status
  - If the answer was no: delete `scene_gen.py`, `scene_trial.py` and `tests/test_scene_trial.py`
- **Done when:**
  - The full suite is green and `test_scene_no_deps.py` passes.
  - The user plays a game night on `/tv`. Every new place gets its picture within the first few lines (with generation, or from the user's own files if the answer was no), and nobody struggles to read the text. The result is recorded in the spec.

**AC coverage:**

| AC | Steps | If no |
|---|---|---|
| AC1 | S1, S7 | same |
| AC2 | S4 | same |
| AC3 | S9 | dropped |
| AC4 | S2, S3, S5, S9 | S2, S3, S5 (the user's file only) |
| AC5 | S1 (phrases), S9 | dropped |
| AC6 | S5, S6, S7 | same |
| AC7 | S6 | same |
| AC8 | S10 | dropped |
| AC9 | S2, S5 | same |
| AC10 | S1 (re-run every step) | same |
| AC11 | S11 | same |

**Playable after each step:**
- S1 adds tools only.
- S2–S3 add an optional stats key and turn key. Old pages and phones ignore `stats.place`.
- S4–S6 change only `/tv`'s background.
- S8–S10 change the server, the laptop page and the files on disk, and never block a request.

## Risks

- **Saved campaigns from before this change.**
  - No `scenes/` folder: the mood layer only (S2 test).
  - `display.json` without `scenes`: defaults, and the file is not rewritten (S8 test).
  - `stats.json` without `place`: no picture.
  - No migration writes anything. The old page at `/` is unchanged (D15).
- **The wrong picture lingering.** A place lasts until the next `location`. If Claude forgets to name an arrival, the old room stays under the new mood. Guards:
  - SKILL.md asks for a location on every arrival, or `null`.
  - A mood change never brings a picture back.
- **Performance at 4K.**
  - Soft layers are drawn at half resolution, particle totals are capped (`budget`), and S4 measures frame time.
  - `requestAnimationFrame` already pauses in a hidden tab.
- **TV browsers without `backdrop-filter`.** The `@supports` fallback darkens instead of blurring, so readability is kept.
- **The contrast measurement is approximate.**
  - It covers the image, tint and scrim. Particles are left out but masked to 50% in the column, and text shadows aren't counted, which is a safety margin.
  - The screenshot check in S7 confirms the result on real pixels.
- **The image model's free tier may be tiny or zero.** The trial shows this before anything depends on it. In play, a 429 pauses generation until the window ends and the TV keeps the mood layer (S10).
- **The response format.** Gemini may return JPEG bytes. The file keeps D5's `<slug>.png` name and is served by magic bytes, so browsers decode it either way.
- **Privacy.** The location `look` goes to Google, as narration already does for the voice. It never goes to phones, because it is stripped from stats.
- **A key in the user's environment.** `tests/__init__.py` doesn't clear `GEMINI_API_KEY`. Every scene test clears the key env vars and patches `urlopen` to raise, so the suite can never spend quota.
- **A rewind to a moment when the image was still pending** shows the mood layer until the next arrival names the place again. This is minor and accepted.
- **Files shared with the parallel battle-map and phone-sheet worktrees.** All of our edits are kept small:
  - `templates/tv.html`: the battle map also adds a `<link>` after `tv.css` and script tags. Expect a trivial conflict on adjacent lines; keep both.
  - `static/tv/tv.js`: one small block after `:111`. The battle map edits `feed.js`, `voice.js` and `map.js`, not this file.
  - `.mapbox` styling: ours is in `scene.css`, and the battle map's is in its own `map.css`. Both make the area behind the board plain, so whichever rule wins is fine.
  - `send.py`: the battle map rewrites `_apply_turn_moves` and `send_turn` (`:632-702`) and the header comment. We touch only the allowed-keys line (`:673-674`), `_turn_stats` and a new function after it, plus a separate comment paragraph. The phone sheet may also add keys to `:673`.
  - `dnd-display-app.py`: the battle map edits `_clean_map` (`:2290-2336`), `_load_stats` (`:1314`) and `/chunk`. Our hunks:
    - one import line (`:53`);
    - a route after `/display-mode` (`:1572`);
    - the `place` branch before `world_time` (`:2032`);
    - one line in the `campaign` branch (`:2109`);
    - if yes, `_display_status` (`:1484`) and `display_config_post` (`:1540`).
  - `display_config.py` and `templates/layout.html`: only if yes. Watch for the phone sheet adding settings.
  - `skills/dnd/SKILL.md`: the battle map edits `:176-200`; we edit "Display sync" (`:380-395`).
- **Unrelated finding (one line):** in `/stats`, the `autorun_*` early returns (`dnd-display-app.py:2085-2104`) drop `map`, `quests` or a future `place` when they come in the same post as an autorun key.

## Needs a spec decision

All three are buildable either way. Each is one setting or constant in the plan. The plan builds the default shown; the user can change any of them (sent to the orchestrating session 2026-11-20; items 1 and 3 matter only if the trial answer is yes).

1. **Arriving somewhere whose picture is being made.**
   - D5 says "the current background stays until the image is ready". Built literally, the previous place's painting stays under the new mood for the 10–30 s generation takes.
   - The "At the table" story suggests the new mood shows on its own first.
   - **The plan builds D5 literally.** A pending place keeps the old image. Off, quota out, failed or no file means mood only, per D7.
   - The alternative ("clear to mood at once, fade the picture in when ready") is one line in `place.js`.
   - This only matters if the answer is yes.
2. **What "body text" means for AC6's 4.5:1.**
   - **The plan measures `--ink` (#f1ebe1, the current narration).**
   - `contrastReport()` and the screenshot tool also report `--ink-dim` (older blocks, #b6aa98), `--npc` and `--you` for information.
   - Holding `--ink-dim` to 4.5:1 would need a much darker scrim (background luminance about 0.04 or less).
3. **Generation default and older campaigns** (if yes).
   - **Is "image generation on" the default?**
     - Option A: on whenever a Gemini key exists.
     - Option B: off until switched on in `/layout`.
     - The plan proposes A, since the user opted in at the trial. B is safer for quota.
   - **May generation create `scenes/` in a campaign that has none?**
     - D14 says such a campaign "gets the mood layer only"; D4 and D5 say generated images are saved there.
     - The plan proposes: D14 applies while generation is off. With generation on, the first image creates the folder.

## Blockers

None.
