"""
app.py — DnD DM display server

Receives text chunks from wrapper.py, detects scene context from keywords,
and pushes both to the browser via Server-Sent Events.

Endpoints:
    GET  /                   → serves index.html
    POST /chunk              → receives text chunk from wrapper.py
    POST /stats              → receives character/combat stat updates (merged, persisted)
    GET  /stream             → SSE stream to browser (text + scene + stats events)
    GET  /ping               → health check
    POST /clear              → wipe text log and broadcast clear event
    POST /player-input         → legacy queue endpoint (check_input.py compat)
    POST /player-input/drain   → drain legacy queue (check_input.py compat)
    POST /player-input/stage   → stage an action for review before firing
    POST /player-input/ready   → mark a staged action as ready
    POST /player-input/unstage → remove a staged action
    POST /player-input/skip    → skip a character's turn (stages + readies a skip entry)
    GET  /srd-lookup           → look up a spell/item/feature/condition by name
"""

import copy
import hmac
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import unicodedata
from collections import deque
from typing import Optional
from flask import Flask, Response, request, render_template, jsonify, send_from_directory
from flask_cors import CORS

# This file lives at <code-root>/display/ — resolve dirs from its location so
# paths work in any install mode (plugin, standalone skill, or dev clone).
# Writable runtime state goes to rt() (the update-safe runtime dir), NOT here.
_HERE         = os.path.dirname(os.path.abspath(__file__))
_ROOT         = os.path.dirname(_HERE)
SCRIPTS_DIR   = os.path.join(_ROOT, "scripts")
# scripts/ has a calendar.py (the campaign calendar) that shadows the standard
# library's calendar once scripts/ is on sys.path (runtime_paths puts it
# there) — and http.cookiejar / huggingface_hub (speech-to-text) need the real
# one. Load the real one first so it is the one cached in sys.modules.
import calendar as _stdlib_calendar  # noqa: F401
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt          # resolves <data-root>/.runtime
import display_config                 # per-campaign display.json (layout, voice)
LOG_FILE      = rt("text_log.json")

# SRD lookup module — degrades silently if dataset not built
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
try:
    import lookup as _lookup
    _SRD_AVAILABLE = True
except Exception:
    _lookup = None          # type: ignore
    _SRD_AVAILABLE = False

import functools as _functools
import script_ranges

from paths import find_campaign as _find_campaign
from utf8io import read_text as _read_text

# Audio module — degrades silently if numpy not installed
_AUDIO_DIR = os.path.dirname(os.path.abspath(__file__))
import sys as _sys
if _AUDIO_DIR not in _sys.path:
    _sys.path.insert(0, _AUDIO_DIR)
try:
    import audio as _audio
    _audio.init()
except Exception:
    _audio = None   # type: ignore

# TTS module — degrades silently if Gemini API key not configured
try:
    import tts as _tts
except Exception:
    _tts = None   # type: ignore
try:
    import cast as _cast     # who speaks in which voice (npcs.md, characters/)
except Exception:
    _cast = None   # type: ignore

# Undo the last turn (snapshots taken when player actions reach the DM).
try:
    import rewind as _rewind
except Exception:
    _rewind = None   # type: ignore

# Speech-to-text for "pass the mic" — local faster-whisper; off (with a
# reason) when it is not installed. See stt.py.
try:
    import stt as _stt
except Exception:
    _stt = None   # type: ignore


def _apply_campaign_sfx_languages() -> None:
    """Read sfx_languages from the active campaign's state.md Session Flags.

    state.md line shape:  `sfx_languages: en,zh,es`
    Takes precedence over the DND_SFX_LANGUAGES env var when present; both
    fall back to English-only if neither is set.
    """
    if _audio is None:
        return
    try:
        camp = open(rt(".campaign"), encoding="utf-8").read().strip()
        if not camp:
            return
        state_md = _find_campaign(camp) / "state.md"
        if not state_md.exists():
            return
        text = state_md.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return
    m = re.search(r"^\s*sfx_languages:\s*([\w,\s\-]+)$", text, re.MULTILINE)
    if not m:
        return
    langs = [l.strip() for l in m.group(1).split(",") if l.strip()]
    valid = [l for l in langs if l in _audio.available_languages()]
    if valid:
        _audio.set_sfx_languages(valid)


_apply_campaign_sfx_languages()

HELP_LOCK     = rt(".help-lock")
CAMP_FILE     = rt(".campaign")
STATS_FILE    = rt("stats.json")
TOKEN_FILE    = rt(".token")
INPUT_FILE    = rt("player_input.json")
TRIGGER_FILE  = rt(".input_trigger")
QUEUE_FILE    = rt(".input_queue")
DEVICES_FILE         = rt(".approved_devices.json")
PENDING_DEVICES_FILE = rt(".pending_devices.json")

# ─── LAN / TLS mode ───────────────────────────────────────────────────────────
# Pass --lan to bind on 0.0.0.0 and protect write endpoints with a token.
# Pass --tls (requires --lan) to enable HTTPS with a self-signed cert.
# Without --lan the server binds to localhost only; no token is required.

_LAN_MODE: bool = "--lan" in sys.argv
_TLS_MODE: bool = "--tls" in sys.argv
if _LAN_MODE:
    sys.argv.remove("--lan")   # prevent Flask from seeing an unknown flag
if _TLS_MODE:
    sys.argv.remove("--tls")


def _get_or_create_token() -> str:
    """Load or generate the LAN token. Upgrades short legacy tokens to 64-char."""
    try:
        token = open(TOKEN_FILE, encoding="utf-8").read().strip()
        if len(token) >= 48:   # 48+ chars = already long enough
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_hex(32)   # 64-char hex — brute force infeasible
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    os.chmod(TOKEN_FILE, 0o600)
    return token


_lan_token: Optional[str] = _get_or_create_token() if _LAN_MODE else None


# ─── Rate limiting ────────────────────────────────────────────────────────────
# Simple in-process sliding window: max 20 write requests per IP per minute.
# Prevents spam injection and brute-force token guessing on write endpoints.

import time as _time

_rate_buckets: dict[str, list] = {}
_rate_lock = threading.Lock()
_RATE_WINDOW = 60    # seconds
_RATE_MAX    = 20    # requests per window per IP


def _rate_ok(ip: str, limit: int = 0) -> bool:
    now = _time.time()
    with _rate_lock:
        bucket = [t for t in _rate_buckets.get(ip, []) if now - t < _RATE_WINDOW]
        if len(bucket) >= (limit or _RATE_MAX):
            return False
        bucket.append(now)
        _rate_buckets[ip] = bucket
    return True


# ─── Input validation helpers ─────────────────────────────────────────────────

_PRINTABLE    = re.compile(
    "[^"
    "\x20-\x7E"                 # ASCII printable
    " -ɏ"             # Latin-1 + Latin Extended A/B (é ñ ö ć ş ž etc.)
    "Ͱ-Ͽ"             # Greek
    "Ѐ-ӿ"             # Cyrillic (Russian, Ukrainian)
    "֐-׿"             # Hebrew
    "؀-ۿ"             # Arabic
    "ݐ-ݿ"             # Arabic Supplement
    "ऀ-ॿ"             # Devanagari (Hindi, Marathi)
    "ঀ-৿"             # Bengali
    "஀-௿"             # Tamil
    "ఀ-౿"             # Telugu
    "฀-๿"             # Thai
    "Ḁ-ỿ"             # Latin Extended Additional (Vietnamese diacritics)
    "　-〿"             # CJK Symbols and Punctuation
    "぀-ゟ"             # Hiragana
    "゠-ヿ"             # Katakana
    "㐀-䶿"             # CJK Extension A
    "一-鿿"             # CJK Unified Ideographs
    "가-힯"             # Hangul Syllables
    "＀-￯"             # Halfwidth / Fullwidth
    "]"
)
_SHELL_CHARS  = re.compile(r'[$`\\;|&><()\[\]{}!]')
# Unicode \w covers letters from all scripts above. Allow space, apostrophe, hyphen
# inside the name; trim 1-2 char names to a separate branch.
_CHAR_NAME_RE = re.compile(r"^\w[\w '\-]{0,48}\w$|^\w{1,2}$", re.UNICODE)


def _sanitize_input(text: str) -> str:
    """Strip control chars and shell metacharacters from player input text."""
    text = _SHELL_CHARS.sub("", text)
    text = _PRINTABLE.sub("", text)
    return text[:500].strip()


def _char_ok(name: str, known: set) -> bool:
    """Return True if character name is syntactically valid and in the party."""
    if not _CHAR_NAME_RE.match(name):
        return False
    if known and name not in known and name != "Everybody":
        return False
    return True


# ─── Device approval system ───────────────────────────────────────────────────
# Each browser generates a UUID device ID (localStorage). On first input attempt
# from an unseen LAN device, the request is held and the DM sees an Approve/Deny
# card on the display. Localhost is auto-approved. Denied devices are blocked for
# the session.

_approved_devices: set[str]       = set()
_denied_devices:   set[str]       = set()
_pending_devices:  dict[str, dict] = {}  # device_id -> {ip, first_seen}
_devices_lock = threading.Lock()


def _persist_approved_devices() -> None:
    """Persist approved devices to disk. Must be called WITHOUT _devices_lock held."""
    try:
        with _devices_lock:
            data = list(_approved_devices)
        with open(DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(DEVICES_FILE, 0o600)
    except Exception:
        pass


def _load_approved_devices() -> None:
    try:
        with open(DEVICES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with _devices_lock:
            for d in data:
                _approved_devices.add(str(d))
    except Exception:
        pass


def _persist_pending_devices() -> None:
    """Persist pending devices to disk so they survive app restarts. Must be called WITHOUT _devices_lock held."""
    try:
        with _devices_lock:
            data = list(_pending_devices.values())
        with open(PENDING_DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(PENDING_DEVICES_FILE, 0o600)
    except Exception:
        pass


def _load_pending_devices() -> None:
    try:
        with open(PENDING_DEVICES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with _devices_lock:
            for d in data:
                if isinstance(d, dict) and d.get("id"):
                    # Skip if already approved/denied during this run
                    if d["id"] not in _approved_devices and d["id"] not in _denied_devices:
                        _pending_devices[d["id"]] = d
    except Exception:
        pass


_load_approved_devices()
_load_pending_devices()


# A casual home-LAN game doesn't need a per-device approval gate — it's friction
# (every phone sits on "Awaiting approval" until the DM taps a card). Default:
# trust any device that can already reach the server. Set DND_REQUIRE_APPROVAL=1
# to restore the approve/deny gate (e.g. on an untrusted/shared network).
_REQUIRE_APPROVAL = os.environ.get("DND_REQUIRE_APPROVAL", "").strip().lower() in ("1", "true", "yes", "on")


def _device_ok(device_id: str, ip: str) -> str:
    """Return 'approved', 'pending', or 'denied' for a given device."""
    if not device_id:
        return "denied"
    _need_persist_approved = False
    _need_persist_pending  = False
    with _devices_lock:
        if device_id in _approved_devices:
            return "approved"
        if device_id in _denied_devices:
            return "denied"
        # Auto-approve localhost always, and every reachable device unless the
        # approval gate is explicitly required.
        if not _REQUIRE_APPROVAL or ip in ("127.0.0.1", "::1"):
            _approved_devices.add(device_id)
            _need_persist_approved = True
        # New LAN device with the gate on — hold and notify DM
        elif device_id not in _pending_devices:
            _pending_devices[device_id] = {
                "id":         device_id,
                "ip":         ip,
                "first_seen": _time.time(),
            }
            _need_persist_pending = True
            _broadcast({"device_request": {"id": device_id, "ip": ip}})
    # Persist outside the lock to avoid deadlock (Lock is not reentrant)
    if _need_persist_approved:
        _persist_approved_devices()
        return "approved"
    if _need_persist_pending:
        _persist_pending_devices()
    return "pending"


# ─── Staged input system ──────────────────────────────────────────────────────
# Players stage their actions from the display companion UI. When all expected
# players mark ready, the combined action is written to TRIGGER_FILE for
# wrapper.py to inject into Claude's PTY stdin.

_staged: dict[str, dict] = {}   # {char_name: {text, ready, timestamp}}
_staged_lock = threading.Lock()
_expected_count = 1             # updated when stats arrive; min 1
_autorun_threshold: Optional[int] = None  # overrides _expected_count when set via push_stats --autorun-threshold
# Who the DM is waiting on. Set explicitly by a stats payload {"awaiting": [...]}
# (send.py --turn), else taken from turn_order.current. When someone is awaited,
# their Send alone completes the turn: the rest of the party no longer has to
# press "skip", and the DM no longer has to remember --autorun-threshold 1.
_awaiting: Optional[list] = None


def _awaited_names() -> Optional[list]:
    """Names the auto-trigger waits for, or None for "the whole party"."""
    if _awaiting:
        return list(_awaiting)
    with _stats_lock:
        to = _current_stats.get("turn_order") or {}
        known = {p.get("name", "").lower() for p in _current_stats.get("players", [])}
    cur = to.get("current") if isinstance(to, dict) else None
    if cur and cur.lower() in known:
        return [cur]
    return None

# Tracks which character names are currently sitting in .input_queue waiting
# for the DM to press Enter. Set when queue is written, cleared when wrapper
# POSTs /queue/consumed after injection. Persists through page reloads via SSE
# initial data and is broadcast to all connected clients on change.
_queue_status: list = []
_queue_status_lock = threading.Lock()

# Last autorun cycle broadcast — replayed on SSE reconnect so late-joining
# clients start the countdown from the correct elapsed position.
# Cleared when autorun_waiting=false (turn resolved or autorun disabled).
_autorun_cycle: Optional[dict] = None
_autorun_cycle_lock = threading.Lock()


def _normalize_slot(slot: dict) -> None:
    """Coerce a spell-slot entry to the canonical {used, max} shape in place.

    Tolerates legacy/alt payloads that use `remaining` instead of `used`.
    Without this, _slot_use/_slot_restore raise KeyError on a slot stored
    under the alt schema (e.g. after a long-rest --spell-slots full-replace).
    """
    if "used" in slot:
        return
    mx = slot.get("max", 0)
    if "remaining" in slot:
        slot["used"] = max(mx - int(slot.get("remaining", 0)), 0)
    else:
        slot["used"] = 0


def _staged_snapshot() -> dict:
    """Return a serialisable copy of the staged dict (no IP field)."""
    return {k: {"text": v["text"], "ready": v["ready"]} for k, v in _staged.items()}


# What each character last said through Talk (/stt): {name lower: (text, time)}.
# A typed action that contains it was spoken at the table, so it is not read
# back (D22).
_stt_said: dict = {}
_stt_said_lock = threading.Lock()
_STT_SAID_FOR = 600.0      # seconds a transcript keeps its action from being read


def _read_actions_aloud(inputs) -> None:
    """With "Read player actions aloud" on, send the TV each typed action with
    the character's voice, before the DM responds (D22, AC18)."""
    try:
        if not _display_config().get("voice", {}).get("read_player_actions"):
            return
    except Exception:
        return
    name = _active_campaign_name()
    now = _time.time()
    reads = []
    for character, text in inputs:
        text = str(text or "").strip()
        if not text or character == "Everybody":
            continue
        with _stt_said_lock:
            said = _stt_said.get(str(character).lower())
        if said and now - said[1] <= _STT_SAID_FOR and said[0].lower() in text.lower():
            continue
        voice = ""
        if _cast is not None and name:
            try:
                voice = _cast.pc_voice(name, character)
            except (OSError, ValueError) as exc:
                print(f"[cast] no voice for {character!r}: {exc}", flush=True)
        reads.append({"character": character, "text": text, "voice": voice})
    if reads:
        _broadcast({"read_actions": reads})


def _check_auto_trigger() -> None:
    """Move staged-and-ready actions into the DM-gated queue file (.input_queue).

    .input_queue is NOT injected immediately — wrapper.py picks it up the next
    time the DM presses Enter (or Claude explicitly triggers via .input_trigger).
    This gives the DM control over when player actions enter Claude's context.
    """
    with _staged_lock:
        if not _staged:
            return
        everybody_ready = "Everybody" in _staged and _staged["Everybody"]["ready"]
        awaited         = _awaited_names()
        if awaited and not everybody_ready:
            ready_ci = {c.lower() for c, v in _staged.items() if v["ready"]}
            if not all(n.lower() in ready_ci for n in awaited):
                return
            taken = [c for c, v in _staged.items() if v["ready"]]
        else:
            all_ready = all(v["ready"] for v in _staged.values())
            threshold = _autorun_threshold if _autorun_threshold is not None else _expected_count
            enough    = len(_staged) >= threshold or everybody_ready
            if not (all_ready and enough):
                return
            taken = list(_staged.keys())
        char_names = taken
        inputs     = [(c, _staged[c]["text"]) for c in taken]
        lines      = [f'[{c}]: {_staged[c]["text"]}' for c in taken]
        content    = "\n".join(lines)
        for c in taken:
            _staged.pop(c, None)

    if not _queue_append(content):
        char_names = []
    if char_names:
        _rewind_mark(inputs)
        _read_actions_aloud(inputs)

    if char_names:
        with _queue_status_lock:
            for c in char_names:
                if c not in _queue_status:
                    _queue_status.append(c)
    _broadcast({"staged_inputs": {}, "queue_status": list(_queue_status)})


# The DM's inbox. Player moves and phone rolls both land here; whatever the DM
# runs next (autorun_wait.py, check_input.py) reads it and clears it. Appending
# matters: a roll written while the DM was not waiting must survive the moves
# that arrive after it, and vice versa.
_queue_file_lock = threading.Lock()


def _queue_append(content: str) -> bool:
    content = (content or "").strip("\n")
    if not content:
        return True
    with _queue_file_lock:
        try:
            prev = ""
            if os.path.exists(QUEUE_FILE):
                with open(QUEUE_FILE, encoding="utf-8") as f:
                    prev = f.read().rstrip("\n")
            if not prev:
                # The inbox was read (and deleted) since the last write, so
                # whoever was listed as queued has been delivered.
                with _queue_status_lock:
                    _queue_status.clear()
            with open(QUEUE_FILE, "w", encoding="utf-8") as f:
                f.write((prev + "\n" + content) if prev else content)
            return True
        except Exception:
            return False


# A `send.py --wait` polls /dice-request/<id> about once a second. If it has
# polled within this window, it is alive and will hand the roll to the DM;
# otherwise (cut off by a command timeout, never started, already gave up)
# the roll goes to the DM's inbox instead of being lost.
_DICE_WAITER_ALIVE = 6.0


def _roll_line(character: str, label: str, spec: str, mod_str: str, total: int,
               natural, dc, physical: bool, asked: bool) -> str:
    what = label or f"{spec}{mod_str}"
    bits = [f"{total}"]
    if natural is not None:
        bits.append(f"natural {natural}")
    if isinstance(dc, int):
        bits.append(f"vs DC {dc} - {'PASS' if total >= dc else 'FAIL'}")
    if physical:
        bits.append("physical die")
    tail = "" if asked else " (not asked for)"
    return f"[{character}]: rolled {what} ({spec}{mod_str}): " + ", ".join(bits) + tail


def _queue_roll_for_dm(line: str, character: str) -> None:
    if _queue_append(line):
        with _queue_status_lock:
            if character not in _queue_status:
                _queue_status.append(character)
            snap = list(_queue_status)
        _broadcast({"queue_status": snap})


def _token_ok() -> bool:
    """Return True if the request carries the correct LAN token (or we're in localhost mode)."""
    if _lan_token is None:
        return True   # localhost mode — no token required
    provided = request.headers.get("X-DND-Token", "")
    return hmac.compare_digest(provided, _lan_token)


# Absolute folders: tests load this file by path, so a relative folder would
# resolve against whatever directory the test runner started in.
app = Flask(
    __name__,
    template_folder=os.path.join(_HERE, "templates"),
    static_folder=os.path.join(_HERE, "static"),
)

app.config['TEMPLATES_AUTO_RELOAD'] = True
CORS(app)

# Wire audio broadcast after _broadcast is defined (see bottom of file)
# — done lazily via set_broadcast() called after app is created.

# ─── Scene definitions ────────────────────────────────────────────────────────
# Each scene: keywords (weighted — more = higher priority hit),
# gradient colors [top, bottom], accent color, particle type, display label.

SCENES: dict[str, dict] = {
    "tavern": {
        "keywords": [
            "tavern", "inn", "guttered", "common room", "hearth",
            "fireplace", "ale", "mead", "barkeep", "innkeeper",
            "candle", "tallow", "flagon", "stool", "bar",
        ],
        "colors": ["#1a0800", "#2e1400"],
        "accent": "#c8601a",
        "particles": "embers",
        "label": "The Inn",
    },
    "dungeon": {
        "keywords": [
            "dungeon", "corridor", "stone floor", "torch", "iron gate",
            "portcullis", "cell", "shackle", "pit", "dank",
        ],
        "colors": ["#080818", "#12082e"],
        "accent": "#6a3aaa",
        "particles": "dust",
        "label": "The Dungeon",
    },
    "mine": {
        "keywords": [
            "mine", "seam", "shaft", "tunnel", "ore", "pickaxe",
            "foreman", "deep seam", "ashstone", "cart", "vein",
        ],
        "colors": ["#0a0a0a", "#1a1008"],
        "accent": "#806040",
        "particles": "dust",
        "label": "The Mine",
    },
    "cave": {
        "keywords": [
            "cave", "cavern", "stalactite", "stalagmite", "underground",
            "grotto", "dripping", "echo", "subterranean",
        ],
        "colors": ["#0a1520", "#0a1030"],
        "accent": "#2060a0",
        "particles": "mist",
        "label": "The Cavern",
    },
    "forest": {
        "keywords": [
            "forest", "wood", "tree", "branch", "leaves", "undergrowth",
            "hollow wood", "canopy", "root", "bark", "moss", "fern",
            "thicket", "grove",
        ],
        "colors": ["#041008", "#081a04"],
        "accent": "#40a040",
        "particles": "leaves",
        "label": "The Forest",
    },
    "castle": {
        "keywords": [
            "castle", "rampart", "battlement", "keep", "parapet",
            "drawbridge", "moat", "throne", "great hall", "manor",
        ],
        "colors": ["#0e0e1a", "#1a1a2e"],
        "accent": "#8080c0",
        "particles": "dust",
        "label": "The Castle",
    },
    "mountain": {
        "keywords": [
            "mountain", "snow", "peak", "blizzard", "frost", "glacier",
            "avalanche", "ridge", "cliff", "altitude", "wind",
        ],
        "colors": ["#0a1020", "#1a2040"],
        "accent": "#a0c0e0",
        "particles": "snow",
        "label": "The Mountains",
    },
    "ocean": {
        "keywords": [
            "ocean", "sea", "ship", "wave", "sailor", "port", "harbour",
            "dock", "tide", "storm", "mast", "hull", "water",
        ],
        "colors": ["#000d1a", "#001a33"],
        "accent": "#0060a0",
        "particles": "ripples",
        "label": "The Sea",
    },
    "desert": {
        "keywords": [
            "desert", "sand", "dune", "oasis", "scorching", "arid",
            "mirage", "camel", "sphinx",
        ],
        "colors": ["#1a0f00", "#2e1a00"],
        "accent": "#c08030",
        "particles": "sand",
        "label": "The Desert",
    },
    "ruins": {
        "keywords": [
            "ruins", "ruin", "crumble", "crumbling", "rubble", "ancient",
            "overgrown", "collapsed", "forgotten", "desolate", "remnant",
        ],
        "colors": ["#100e04", "#1e1a08"],
        "accent": "#806830",
        "particles": "dust",
        "label": "The Ruins",
    },
    "swamp": {
        "keywords": [
            "swamp", "marsh", "bog", "mud", "murky", "fetid", "reed",
            "mire", "sludge", "stagnant",
        ],
        "colors": ["#080e04", "#0e1808"],
        "accent": "#406020",
        "particles": "mist",
        "label": "The Swamp",
    },
    "crypt": {
        "keywords": [
            "crypt", "tomb", "grave", "coffin", "undead", "bones",
            "skeleton", "lich", "mausoleum", "burial", "sarcophagus",
            "dead", "death",
        ],
        "colors": ["#08000a", "#140014"],
        "accent": "#602060",
        "particles": "smoke",
        "label": "The Crypt",
    },
    "fire": {
        "keywords": [
            "fire", "flame", "burn", "blaze", "inferno", "conflagration",
            "ember", "char", "smoke", "ash cloud",
        ],
        "colors": ["#1a0500", "#2e0800"],
        "accent": "#ff4400",
        "particles": "embers",
        "label": "The Fire",
    },
    "arcane": {
        "keywords": [
            "arcane", "magic", "spell", "enchant", "rune", "glyph",
            "mystical", "ritual", "incantation", "ward", "sigil",
            "thaumaturgy", "sorcery",
        ],
        "colors": ["#080020", "#12003a"],
        "accent": "#8040ff",
        "particles": "sparks",
        "label": "The Arcane",
    },
    "city": {
        "keywords": [
            "city", "market", "street", "crowd", "village", "town",
            "square", "cobble", "district", "quarter", "merchant",
            "ashenveil",
        ],
        "colors": ["#0a0f1a", "#15202e"],
        "accent": "#6080a0",
        "particles": "rain",
        "label": "The Town",
    },
    "night": {
        "keywords": [
            "night", "midnight", "moon", "star", "dark sky",
            "constellation", "celestial", "dusk", "twilight",
        ],
        "colors": ["#000008", "#04000f"],
        "accent": "#4060a0",
        "particles": "stars",
        "label": "The Night",
    },
    "temple": {
        # `lantern` and `cleric` REMOVED. Every other word here names a place
        # or a fixture of one -- a nave, an altar, incense. Those two named a
        # thing a character CARRIES and a thing a character IS, so they scored
        # wherever the party went: measured, three mentions of a lantern in a
        # room with no other keyword present rendered the room as THE TEMPLE,
        # and so did a cleric walking down a road.
        #
        # `prayer` is the weakest survivor and is kept: people pray in
        # foxholes, but it at least describes an act performed AT the place.
        "keywords": [
            "temple", "shrine", "altar", "holy", "sacred", "chapel",
            "prayer", "incense", "pew", "nave",
            "pale flame",
        ],
        "colors": ["#0e0c18", "#1a1428"],
        "accent": "#c0a060",
        "particles": "smoke",
        "label": "The Temple",
    },
}

# Priority order — checked in sequence; first match wins per chunk
SCENE_PRIORITY = [
    "mine", "crypt", "arcane", "fire", "temple", "dungeon", "cave",
    "forest", "swamp", "castle", "ocean", "mountain", "desert", "ruins",
    "tavern", "city", "night",
]

# ─── ANSI / TUI chrome stripping ─────────────────────────────────────────────

class _ANSIState:
    """Character-by-character ANSI escape-sequence state machine.

    Regex approaches fail when the PTY delivers bytes one at a time, splitting
    sequences like \\x1b[4;2m across chunk boundaries.  This state machine
    carries its state across calls so cross-chunk splits are handled correctly.

    States
    ------
    normal   → emitting regular characters
    esc      → saw ESC (0x1B), waiting to see what kind of sequence follows
    csi      → inside CSI sequence (ESC [ … letter)
    osc      → inside OSC sequence (ESC ] … BEL or ST)
    osc_esc  → inside OSC, just saw ESC — might be the ST terminator (ESC \\)
    """

    __slots__ = ("_s",)

    def __init__(self) -> None:
        self._s: str = "normal"

    def feed(self, text: str) -> str:
        out: list[str] = []
        s = self._s
        for ch in text:
            c = ord(ch)
            if s == "normal":
                if c == 0x1B:
                    s = "esc"
                elif c >= 0x20 or c in (0x09, 0x0A):   # printable / tab / newline
                    out.append(ch)
                # else: other control char (bell, etc.) — discard
            elif s == "esc":
                if ch == "[":
                    s = "csi"
                elif ch == "]":
                    s = "osc"
                else:
                    s = "normal"    # 2-char ESC sequence; discard both bytes
            elif s == "csi":
                if 0x40 <= c <= 0x7E:   # final byte of CSI
                    s = "normal"
                elif c == 0x1B:         # unexpected ESC — start fresh
                    s = "esc"
                # else: parameter / intermediate byte, keep consuming
            elif s == "osc":
                if c == 0x07:           # BEL terminates OSC
                    s = "normal"
                elif c == 0x1B:
                    s = "osc_esc"
                # else: OSC payload, keep consuming
            elif s == "osc_esc":
                s = "normal" if ch == "\\" else "osc"
        self._s = s
        return "".join(out)


_ansi = _ANSIState()
_ansi_lock = threading.Lock()

_BOX_CHARS = set("╭╮╰╯│─┌┐└┘├┤┬┴┼━═║╔╗╚╝")
_BOX_CHAR_STRIP = "╭╮╰╯│─┌┐└┘├┤┬┴┼━═║╔╗╚╝"  # same set as string for str.strip()

# Characters used by Claude CLI spinner / prompt / UI
_SPINNER_CHARS = set("✽⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏◐◓◑◒◌◎●")
_PROMPT_STARTS = ("❯", ">", "·", "▸", "ℹ", "✓", "⚠", "✗", "⟳", "↳")


def _handle_cr(text: str) -> str:
    """Handle carriage returns the way a real terminal would.

    Two distinct cases:
      \\r\\n  — a real newline (\\r\\n line ending).  Normalise to \\n first
                so the content is preserved.
      bare \\r — cursor-to-column-0 for in-place token updates.  Claude CLI
                streams each token by rewriting the current line:
                  "The" → \\r"The Gut" → \\r"The Gutte" → …
                Keep only the last segment (= the final written state).
    """
    # Step 1: treat \\r\\n as a real newline — must come before bare-\\r logic
    text = text.replace("\r\n", "\n")

    # Step 2: handle remaining bare \\r (in-place rewrites)
    lines = text.split("\n")
    result = []
    for line in lines:
        if "\r" in line:
            parts = line.split("\r")
            result.append(parts[-1])   # last segment = final state of the line
        else:
            result.append(line)
    return "\n".join(result)


def _strip_ansi(text: str) -> str:
    text = _handle_cr(text)
    with _ansi_lock:
        text = _ansi.feed(text)
    return text


def _is_chrome(line: str) -> bool:
    """Return True for lines that are TUI chrome, not DM narration.

    The Claude CLI wraps responses in a box:
        ╭──────────────────╮
        │ narration text   │
        ╰──────────────────╯
    We strip the border characters from line edges first so that content
    lines like "│ The tavern smells of ale │" are NOT filtered — only pure
    border rows (all box chars, no letters) are treated as chrome.
    """
    stripped = line.strip()

    if not stripped:
        return False   # keep blank lines — they separate paragraphs

    # Strip leading/trailing box-drawing border chars to expose the real content.
    # "│ The tavern smells of ale │" → "The tavern smells of ale"
    content = stripped.strip(_BOX_CHAR_STRIP + " ")

    # If nothing remains, the line was entirely box-drawing chrome (a border row).
    if not content:
        return True

    # All remaining checks operate on content (without box border decoration).
    c = content

    # CLI prompt / spinner lines
    if c[0] in _SPINNER_CHARS:
        return True
    if c.startswith(_PROMPT_STARTS):
        return True

    # Common spinner word patterns (e.g. "Thinking…")
    if re.match(r"^[A-Z][a-z]+ing…?$", c):
        return True

    # Claude branding / metadata
    if "claude.ai" in c.lower():
        return True

    # Session-resume instructions emitted at end of response
    if c.startswith("Resume this session with:") or re.match(r"^claude\s+--resume\s+", c):
        return True

    # Status-bar patterns: cost, token counts, rate-limit bars
    # Note: "Tokens300/0" has no space — use \s* not \s+
    if re.search(r"Tokens\s*\d|5hr:|7d:|Session:|Total:\s*\$", c):
        return True

    # Model/plan header lines ("Sonnet 4.6", "Claude Pro", "Professional", etc.)
    if re.search(r"Sonnet|Haiku|Opus|Claude\s*(Pro|Max|Team|Code)\b|Professional\b|claude-\d", c, re.I):
        return True

    # Tool-use labels emitted by Claude CLI ("Bash command", "Read command", etc.)
    if re.match(r"^(Bash|Read|Write|Edit|Glob|Grep|WebFetch|WebSearch|TaskCreate|TaskUpdate|TaskGet|TaskList|NotebookEdit|Agent|ToolSearch|ExitPlanMode|EnterPlanMode|ScheduleWakeup|Monitor|RemoteTrigger|CronCreate|CronDelete|CronList|AskUserQuestion)(\s+(command|tool|result|call))?$", c, re.I):
        return True

    # Timestamp-prefixed lines ("3ts ago …", "2m ago …") — UI timestamps concatenated with content
    if re.match(r"^\d+\s*[smhdt]+s?\s*(ago\s*)?[A-Z]", c):
        return True

    # Bare numbers (token counts, cursor column positions, etc.)
    if re.match(r"^\d+$", c):
        return True

    # Single stray characters that are ANSI/escape remnants, not real words
    if len(c) == 1 and not c.isalpha():
        return True

    # Very short fragments with no word in them (≤3 chars) are escape-code
    # residue, not narration. "A word" is decided per character with
    # str.isalpha, not [a-zA-Z]: the ASCII test dropped 「好的」, 「はい。」,
    # 「不。」, 「Да.」 and 「Нет」 while keeping "No.". Two letters make a word in
    # any script; in a script without case (Han, Kana, Hangul) one letter is
    # already a word -- 不 is "no".
    if len(c) <= 3:
        letters = [ch for ch in c if ch.isalpha()]
        caseless_word = any(unicodedata.category(ch) == "Lo" for ch in letters)
        if len(letters) < 2 and not caseless_word:
            return True

    return False


def _clean(text: str) -> str:
    text = _strip_ansi(text)
    lines = text.split("\n")
    kept = []
    for line in lines:
        if _is_chrome(line):
            continue
        # Strip box-border chars from edges so content reaches the browser clean.
        s = line.strip().strip(_BOX_CHAR_STRIP + " ")
        # Blank line → preserve as paragraph separator
        kept.append(s if s else "")
    # Collapse runs of more than two consecutive blank lines
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
    return result


# ─── Scene detection ──────────────────────────────────────────────────────────

_current_scene_name: str = "tavern"   # default — we start in the inn
_scene_buffer: list[str] = []
_BUFFER_WINDOW = 20   # analyse last N cleaned chunks together

# How much evidence a scene change needs. A bare `max()` over keyword counts
# meant ONE stray keyword could move the background, and two plausible scenes
# traded it back and forth on a one-point edge.
#
# MARGIN is the gap a challenger must open over the scene already on screen.
# The INCUMBENT BONUS lets the current scene defend itself even after its own
# keywords have scrolled out of the window, which is the usual case for a long
# scene: a fight in a crypt stops saying "crypt" after the first line.
#
# The bonus is 1 rather than 2 by measurement against this repo's own keyword
# lists, not by feel. A genuine transition narrated over three chunks --
# arriving at a city -- scores 4; incidental noise, a couple of night words
# while at sea, scores 2. With MARGIN 2 an aged-out incumbent needs the
# challenger at 3, which admits the real change and blocks the noise. At 2 the
# bar becomes 4 and a genuine arrival stops registering.
#
# There is deliberately NO separate minimum-hits floor. One was written here
# first and it could never fire: the test below requires
# `best >= scores.get(current, 0) + BONUS + MARGIN`, which is at least 3, so a
# floor of 2 is unreachable for every input. A constant that decides nothing
# reads as protection and is not, so it is gone rather than documented.
_SCENE_SWITCH_MARGIN = 2    # a challenger must beat the scene on screen by this
_SCENE_INCUMBENT_BONUS = 1


def _keyword_pattern(keyword: str) -> "re.Pattern":
    r"""Match `keyword` as a whole word where the script has word delimiters.

    Scored with `window.count(kw)` before this, which is a SUBSTRING count:
    the arcane keyword `ward` matched inside "deputy warden", so a prison
    fight scored as arcane. It also matches "wardrobe", "forward", "steward".

    Anchors are applied only for keywords in a script that separates words. A
    campaign may override a scene's keyword list in its own language (the lists
    shipped here are English), and `\b` is an impossible condition for Han,
    Kana, Hangul or Thai -- see scripts/script_ranges.py.
    """
    return script_ranges.whole_word_pattern(keyword)


@_functools.lru_cache(maxsize=512)
def _cached_keyword_pattern(keyword: str) -> "re.Pattern":
    return _keyword_pattern(keyword)


def _detect_scene(text: str) -> Optional[dict]:
    global _current_scene_name, _scene_buffer

    _scene_buffer.append(text.lower())
    if len(_scene_buffer) > _BUFFER_WINDOW:
        _scene_buffer.pop(0)

    window = " ".join(_scene_buffer)

    scores: dict[str, int] = {}
    for scene_name in SCENE_PRIORITY:
        scene = SCENES[scene_name]
        score = sum(len(_cached_keyword_pattern(kw).findall(window))
                    for kw in scene["keywords"])
        if score > 0:
            scores[scene_name] = score

    if not scores:
        return None

    best = max(scores, key=lambda k: scores[k])
    if best == _current_scene_name:
        return None   # no change
    # `_current_scene_name` may not be in `scores` at all once its keywords
    # have scrolled out of the window -- that is the case the bonus is for.
    held = scores.get(_current_scene_name, 0) + _SCENE_INCUMBENT_BONUS
    if scores[best] - held < _SCENE_SWITCH_MARGIN:
        return None

    _current_scene_name = best
    return SCENES[best] | {"name": best}


# ─── SSE client registry ─────────────────────────────────────────────────────

_clients: list[queue.Queue] = []
_clients_lock = threading.Lock()
# Maps a connected SSE client (queue) → the character it's bound to, if any.
# Phones connect to /stream?character=<name>; the main display has no character.
# Lets a dice-request know whether a target PC has a live phone (→ route there)
# or not (→ open the on-screen roller). Guarded by _clients_lock.
_client_chars: "dict[queue.Queue, str]" = {}


def _phone_present(char: str) -> bool:
    """True if some connected phone is bound to this character (case-insensitive)."""
    c = (char or "").strip().lower()
    if not c:
        return False
    with _clients_lock:
        return c in _client_chars.values()

# ─── Text replay log ──────────────────────────────────────────────────────────
# Stores cleaned text chunks so late-connecting browsers can catch up.
# Persisted to LOG_FILE so it survives Flask restarts (Chromecast reconnects, new sessions).
# Full body retention (player requirement, 2026-08-18): cap raised 60 → 2000 with
# full reload at startup (_load_log), so cross-session body text stays recoverable
# and archivable in both formats. On plugin updates, re-check these three spots.
_text_log: deque = deque(maxlen=2000)
_text_log_lock = threading.Lock()

# Every log entry and its live broadcast carry `seq`, a counter that only goes
# up (a rewind or reload never lowers it). `_epoch` is new on every server
# start and is sent first on /stream as {"hello": {"epoch": ...}}. A screen
# that reconnects to the same epoch skips replayed entries it already has; a
# new epoch means the server restarted and the screen rebuilds from the replay.
_seq = 0
_epoch = secrets.token_hex(8)


def _log_append(log_entry: dict, payload: Optional[dict] = None) -> None:
    """Append one entry to the text log and the session tail, stamping the
    next `seq` on the entry and on its broadcast payload."""
    global _seq
    with _text_log_lock:
        _seq += 1
        log_entry["seq"] = _seq
        if payload is not None:
            payload["seq"] = _seq
        _text_log.append(log_entry)
    with _tail_lock:
        _tail_buffer.append(log_entry)

# ─── Session tail buffer ──────────────────────────────────────────────────────
# Rolling buffer of the last 30 text events — written to session_tail.json after
# every /chunk POST so it survives crashes. Read at /dnd load for display replay.
# Path is campaign-specific so tails from different campaigns don't overwrite each other.
#
# ROBUSTNESS GUARANTEES (after the 2026-05-01 wipe-bug fix):
#   1. _load_tail is NON-DESTRUCTIVE: it never wipes the in-memory buffer based
#      on an empty/filtered-out load. If the file is empty, missing, or every
#      entry is filtered out by campaign mismatch, the existing buffer stays.
#   2. _persist_tail SKIPS ON EMPTY: it never overwrites an existing non-empty
#      file with an empty buffer. This breaks the "filter zeros buffer →
#      persist writes [] → file lost" failure chain.
#   3. _persist_tail uses ATOMIC WRITES: writes to a tempfile and atomically
#      renames into place, so a partial/crashed write can never produce a
#      truncated or zero-byte file.
#   4. The legacy fallback path is GONE. Tails only ever land in the campaign-
#      specific file. If CAMP_FILE is missing/empty when persist would fire,
#      we keep the buffer in memory and skip the write rather than dropping
#      events into a shared file that bleeds across campaigns.
_tail_buffer: deque = deque(maxlen=30)
_tail_lock   = threading.Lock()


def _get_tail_file() -> "str | None":
    """Return the campaign-specific tail path, or None if no campaign is set.

    Previously this fell back to a process-local path on the skill side. That
    fallback caused tail bleed across campaigns and made the wipe-on-load bug
    much harder to diagnose. The new contract: campaign-specific or nothing.
    """
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if camp:
            return str(_find_campaign(camp) / "session_tail.json")
    except Exception:
        pass
    return None


def _persist_tail() -> None:
    """Write _tail_buffer to disk. Refuses to overwrite content with empty.

    Atomic-write guarantee: writes to <path>.tmp then renames, so observers
    (the next /dnd load reading the file) never see a partial or zero-byte
    state.
    """
    path = _get_tail_file()
    if not path:
        # No active campaign — keep the buffer in memory, skip disk.
        return
    try:
        with _tail_lock:
            data = list(_tail_buffer)
        # Skip-on-empty guard: never blank a file that currently has content.
        if not data and os.path.exists(path):
            try:
                if os.path.getsize(path) > 2:  # 2 bytes = "[]"
                    print(f"_persist_tail: skipping empty write — {path} has content",
                          file=sys.stderr)
                    return
            except OSError:
                pass
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except Exception as e:
        print(f"_persist_tail: write failed: {e}", file=sys.stderr)


def _load_tail() -> None:
    """Load tail from disk into the buffer. NON-DESTRUCTIVE on empty/mismatch.

    Old behavior: cleared the buffer first, then re-appended filtered entries.
    This created the wipe bug: if every entry was filtered out (campaign
    mismatch) the buffer ended up empty and the next persist wrote [] to disk.

    New behavior: build the candidate buffer first, then ONLY swap it into
    place if at least one entry survived filtering. If nothing survives, the
    in-memory buffer is left alone — preserves whatever was already loaded.
    """
    path = _get_tail_file()
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return  # No file yet — keep in-memory state
    except (OSError, json.JSONDecodeError) as e:
        print(f"_load_tail: read failed for {path}: {e}", file=sys.stderr)
        return
    if not isinstance(data, list):
        print(f"_load_tail: file content is not a list — leaving buffer alone",
              file=sys.stderr)
        return

    try:
        current_camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except Exception:
        current_camp = ""

    candidate: list = []
    for item in data[-30:]:
        if not isinstance(item, dict):
            continue
        item_camp = item.get("_camp", "")
        # If we know the campaign and the entry stamps a different campaign,
        # skip it. Entries with no stamp are kept (legacy data + tolerance).
        if current_camp and item_camp and item_camp != current_camp:
            continue
        candidate.append(item)

    if not candidate:
        # Loaded data filtered down to nothing. DO NOT replace the buffer —
        # this is the wipe-bug guard.
        return

    with _tail_lock:
        _tail_buffer.clear()
        for item in candidate:
            _tail_buffer.append(item)


_load_tail()


def _persist_log() -> None:
    """Write the current text log to disk. Called after every chunk."""
    try:
        with _text_log_lock:
            data = list(_text_log)
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _load_log() -> None:
    """Load a previously persisted text log on startup.
    Handles both old string format and new dict format. Entries saved before
    `seq` existed are numbered above the highest one already present."""
    global _seq
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        items = [{"text": i} if isinstance(i, str) else i for i in data]  # old plain strings
        items = [i for i in items if isinstance(i, dict)]
        seen = [i["seq"] for i in items if isinstance(i.get("seq"), int)]
        top = max([_seq] + seen)
        used: set = set()
        for item in items:
            s = item.get("seq")
            if not isinstance(s, int) or s in used:
                top += 1
                item["seq"] = top
            used.add(item["seq"])
        with _text_log_lock:
            _text_log.clear()
            _text_log.extend(items)
            _seq = max(_seq, top)
    except Exception:
        pass


_load_log()


# ─── Character / combat stats ─────────────────────────────────────────────────
# Stored as {"players": [...], "turn_order": {...}|null}
# Players are merged by name so partial updates (just HP, just XP) work.

_current_stats: dict = {}
_stats_lock = threading.Lock()


def _persist_stats() -> None:
    try:
        with _stats_lock:
            data = dict(_current_stats)
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _load_stats() -> None:
    global _expected_count
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with _stats_lock:
            _current_stats.update(data)
        # Initialise expected player count from persisted stats so solo-mode
        # detection is correct immediately after restart, without waiting for
        # the next /stats POST.
        loaded_players = data.get("players", [])
        if loaded_players:
            _expected_count = max(1, len(loaded_players))
    except Exception:
        pass


_load_stats()


# ─── Player input queue ───────────────────────────────────────────────────────
# Stores actions submitted from the display companion (iPad etc.) until the DM
# triggers the next turn. Drained by check_input.py via /player-input/drain.

_input_queue: list[dict] = []
_input_lock = threading.Lock()

# Pending DM-issued dice requests: request_id → {chars: set[str], meta: {...}, started_at: float}
# A request is "complete" when its chars set is empty (every prescribed player rolled).
# send.py --wait polls GET /dice-request/<id> to know when the DM can move on.
_dice_pending: dict = {}
_dice_pending_lock = threading.Lock()
# Finished requests keep their results so `send.py --wait` can print the rolls
# (it used to report only that they happened). Bounded: oldest dropped first.
_dice_done: dict = {}
_DICE_DONE_MAX = 64


def _dice_finish(request_id: str, entry: dict) -> None:
    """Move a drained request to _dice_done. Caller holds _dice_pending_lock."""
    _dice_pending.pop(request_id, None)
    _dice_done[request_id] = {"results": entry.get("results", []), "meta": entry["meta"]}
    while len(_dice_done) > _DICE_DONE_MAX:
        _dice_done.pop(next(iter(_dice_done)))


def _dice_pending_snapshot() -> list:
    with _dice_pending_lock:
        return [
            {"request_id": rid, "pending": sorted(e["chars"]), "label": e["meta"].get("label", "")}
            for rid, e in _dice_pending.items() if e["chars"]
        ]


def _load_input_queue() -> None:
    global _input_queue
    try:
        with open(INPUT_FILE, encoding="utf-8") as f:
            _input_queue = json.load(f)
    except Exception:
        _input_queue = []


def _persist_input_queue() -> None:
    try:
        with open(INPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(_input_queue, f)
    except Exception:
        pass


_load_input_queue()


def _broadcast(payload: dict) -> None:
    with _clients_lock:
        dead = []
        for q in _clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _clients.remove(q)
            _client_chars.pop(q, None)


# ─── Routes ──────────────────────────────────────────────────────────────────

def _page_vars() -> dict:
    """Template variables every display page carries (token, voice state)."""
    return dict(
        # The LAN token lets the browser authenticate its POSTs
        lan_token=_lan_token or "",
        narrator_voice=_read_narrator_voice(),
        voice_on=_voice_payload()["on"],
        voice_reason=_voice_payload()["reason"],
        voice_local=bool(_tts is not None and getattr(_tts, "kokoro_installed", lambda: False)()),
    )


@app.route("/")
def index():
    return render_template("index.html", **_page_vars())


@app.route("/tv")
def tv():
    """The new TV page (display foundation spec). Runs beside the old page
    at / until a real game night has been played on it."""
    return render_template("tv.html", **_page_vars())


@app.route("/layout")
def layout_page():
    """The host's TV layout page (spec D6). Changes are POSTed to
    /display-config and /display-mode, which accept only the game computer."""
    return render_template("layout.html", **_page_vars())


# ─── Display settings (layout and voice, per campaign) ───────────────────────
# Read from <campaign>/display.json on first use and again whenever the active
# campaign changes. Written only when the host changes something.
_display_lock = threading.Lock()
_display_cache: dict = {"campaign": None, "config": None}
_mode_override = None   # True/False forces combat/story on the TV; None = automatic


def _campaign_dir():
    """The active campaign's folder, or None when no campaign is loaded."""
    name = _active_campaign_name()
    if not name:
        return None
    try:
        path = _find_campaign(name)
    except (OSError, ValueError, TypeError):
        return None
    return path if path and os.path.isdir(str(path)) else None


def _display_config() -> dict:
    """The active campaign's display settings (defaults when none are saved)."""
    name = _active_campaign_name()
    with _display_lock:
        if _display_cache["config"] is None or _display_cache["campaign"] != name:
            folder = _campaign_dir()
            _display_cache["config"] = (display_config.load(folder) if folder
                                        else display_config.default())
            _display_cache["campaign"] = name
        return copy.deepcopy(_display_cache["config"])


_voice_note = {"text": "", "until": 0.0}


def _voice_note_set(wait: float) -> None:
    hours = max(1, round(wait / 3600))
    _voice_note["text"] = (f"NPCs moved to Kokoro — Gemini quota used up, "
                           f"back in about {hours} h")
    _voice_note["until"] = _time.time() + wait


def _display_status() -> dict:
    """Notes for the laptop page (never the TV): the Gemini quota note while
    the quota window lasts."""
    status = {}
    now = _time.time()
    # The note lasts as long as the quota window: when Gemini is back (or the
    # window is cleared), it goes.
    if _voice_note["text"] and now < _voice_note["until"] and now < _tts_quota_until:
        status["voice_note"] = _voice_note["text"]
    return status


def _display_reload() -> None:
    """Forget the cached display settings (display.json changed on disk)."""
    with _display_lock:
        _display_cache["config"] = None


def _combat_on(turn_order) -> bool:
    """Combat is on only while the turn order names someone. A bare
    {"current": …} or {"order": []} is not a fight to show."""
    return (isinstance(turn_order, dict)
            and isinstance(turn_order.get("order"), list)
            and len(turn_order["order"]) > 0)


def _display_payload() -> dict:
    return {"display": {"config": _display_config(), "mode_override": _mode_override}}


def _host_only() -> bool:
    """Settings change only from the computer running the game: the LAN token
    is in every page served, phones included, so the token alone is not enough."""
    return _token_ok() and request.remote_addr in ("127.0.0.1", "::1")


@app.route("/display-config", methods=["GET"])
def display_config_get():
    return jsonify(
        config=_display_config(),
        presets=display_config.PRESETS,
        modules=list(display_config.MODULES),
        mode_override=_mode_override,
        status=_display_status(),
    )


@app.route("/display-config", methods=["POST"])
def display_config_post():
    if not _host_only():
        return jsonify(error="Open this on the computer running the game."), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Send the settings as a JSON object."), 400
    # A partial post changes only what it names: {"voice": {"speed": 1.2}}
    # keeps the layout and every other voice setting (the narrator included).
    merged = _display_config()
    if isinstance(data.get("layout"), dict):
        merged["layout"] = data["layout"]
    if isinstance(data.get("voice"), dict):
        merged["voice"] = {**(merged.get("voice") or {}), **data["voice"]}
    clean = display_config.validate(merged)
    folder = _campaign_dir()
    if folder:
        clean = display_config.save(folder, clean)
    with _display_lock:
        _display_cache["config"] = clean
        _display_cache["campaign"] = _active_campaign_name()
    _broadcast(_display_payload())
    return jsonify(config=clean, saved=bool(folder))


@app.route("/display-mode", methods=["POST"])
def display_mode_post():
    """The host forces the TV into combat (true) or story (false), or hands it
    back to the automatic switch (null). Cleared whenever combat starts or ends."""
    global _mode_override
    if not _host_only():
        return jsonify(error="Open this on the computer running the game."), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "combat" not in data:
        return jsonify(error='Send {"combat": true, false or null}.'), 400
    value = data["combat"]
    if value is not None and not isinstance(value, bool):
        return jsonify(error='"combat" must be true, false or null.'), 400
    _mode_override = value
    _broadcast(_display_payload())
    return jsonify(mode_override=_mode_override)


@app.route("/icons/<path:filename>")
def serve_icon(filename):
    """Serve icons, favicon, and brand assets from display/icons/."""
    _icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
    return send_from_directory(_icons_dir, filename)


@app.route("/favicon.ico")
def favicon():
    _icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
    return send_from_directory(_icons_dir, "favicon.ico",
                               mimetype="image/vnd.microsoft.icon")


@app.route("/srd-lookup")
def srd_lookup():
    """Look up a spell, item, condition, feature, or monster by name.

    Query params:
        name      — the name to look up (required)
        category  — spell | item | equipment | magic_item | condition | monster | feature (optional)
        level     — character level (1–20); collapses scale progressions to the matching entry

    Returns JSON: {"found": bool, "name": str, "category": str, "text": str}
    """
    name     = request.args.get("name", "").strip()[:120]
    category = request.args.get("category", "").strip().lower() or None
    level_s  = request.args.get("level", "").strip()
    level    = int(level_s) if level_s.isdigit() and 1 <= int(level_s) <= 20 else None
    if not name:
        return jsonify({"found": False, "error": "name required"}), 400
    if not _SRD_AVAILABLE or _lookup is None:
        return jsonify({"found": False, "error": "SRD dataset not loaded"}), 503

    text = _lookup.lookup_with_level(name, category=category, level=level)
    if text:
        rec = _lookup.lookup_record(name, category=category)
        resolved_cat = (rec or {}).get("_cat", category or "")
        return jsonify({"found": True, "name": name, "category": resolved_cat, "text": text})
    # Not found — offer near-miss "did you mean?" suggestions (typo recovery)
    # plus the wikidot fallback URL so the frontend can still link out.
    # `ref` is {} when there is no VERIFIED destination for this category, and
    # the frontend renders no link at all in that case: a guessed URL reads as
    # an answer and dead-ends, which is worse than saying nothing.
    ref = _lookup.reference_url(name, category=category)
    suggestions = []
    try:
        for sg_name, sg_cat in _lookup.suggest(name, category=category, n=3):
            suggestions.append({"name": sg_name, "category": sg_cat})
    except Exception:
        pass  # suggestion is best-effort; never fail the lookup over it
    return jsonify({"found": False, "name": name,
                    "reference_url": ref.get("url", ""),
                    "reference_label": ref.get("label", ""),
                    # kept so an older cached frontend still gets a link
                    "wikidot_url": ref.get("url", ""),
                    "suggestions": suggestions})


@app.route("/ping")
def ping():
    return "ok", 200


@app.route("/health")
def health():
    """Server-side integrity probe used by send.py --verify and external monitors.

    Returns the live counts the send-side cares about:
      - alive: always True if the route runs
      - tail_buffer: number of entries currently in the rolling tail
      - tail_file_size: size in bytes of the on-disk session_tail.json
      - text_log: number of entries in the replay log
      - campaign: the active campaign name (empty if none set)
      - clients: connected SSE clients

    No auth required — this is a liveness/monitoring endpoint, no PII or game
    content is exposed.
    """
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except Exception:
        camp = ""
    tail_path = _get_tail_file()
    try:
        tail_size = os.path.getsize(tail_path) if tail_path and os.path.exists(tail_path) else 0
    except OSError:
        tail_size = 0
    with _tail_lock:
        tail_count = len(_tail_buffer)
    with _text_log_lock:
        log_count = len(_text_log)
    with _clients_lock:
        client_count = len(_clients)
    return {
        "alive": True,
        "tail_buffer": tail_count,
        "tail_file_size": tail_size,
        "tail_path": tail_path or "",
        "text_log": log_count,
        "campaign": camp,
        "clients": client_count,
        # Whether this server can use the voice key (never the key itself).
        "tts_key": bool(_tts is not None and _tts.key_source() != "unset"),
        "tts_quota_wait": max(0, int(_tts_quota_until - _time.time())),
        # The one voice state every screen follows (see _voice_check).
        "voice_on": _voice_payload()["on"],
        "voice_reason": _voice_payload()["reason"],
        "kokoro": {"on": _kokoro_ok, "provider": _kokoro_info.get("provider", "")},
        "mic": _mic_payload(),
    }, 200


@app.route("/chunk", methods=["POST"])
def chunk():
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    raw = data.get("text", "")
    if not raw:
        return "", 204

    is_action      = bool(data.get("action"))
    is_player      = bool(data.get("player"))
    is_npc         = bool(data.get("npc"))
    is_dice        = bool(data.get("dice"))
    is_tutor       = bool(data.get("tutor"))
    is_inspiration = bool(data.get("inspiration_award"))
    is_milestone_award = bool(data.get("milestone_award"))
    is_milestone_spend = bool(data.get("milestone_spend"))
    is_xp_award    = bool(data.get("xp_award"))

    # ── Milestone award/spend (stack-based reward, distinct from binary Inspiration) ──
    if is_milestone_award or is_milestone_spend:
        name = str(data.get("milestone_award") or data.get("milestone_spend") or "").strip()[:80]
        label = str(data.get("label") or "Milestone").strip()[:40]
        if not name:
            return "", 204
        payload: dict = {
            "milestone_award" if is_milestone_award else "milestone_spend": name,
            "label": label,
            "text": name,
        }
        log_entry: dict = dict(payload)
        if is_milestone_award and data.get("reason"):
            payload["reason"] = str(data["reason"]).strip()[:240]
            log_entry["reason"] = payload["reason"]
        _log_append(log_entry, payload)
        _persist_log()
        _persist_tail()
        _broadcast(payload)
        return "", 204

    # Inspiration and XP awards carry no text from stdin — build synthetic text.
    if is_inspiration:
        name = str(data.get("inspiration_award", "")).strip()[:80]
        if not name:
            return "", 204
        reason = str(data.get("reason", "")).strip()[:240]
        payload: dict = {"inspiration_award": name, "text": name}
        log_entry: dict = {"inspiration_award": name, "text": name}
        if reason:
            payload["reason"] = reason
            log_entry["reason"] = reason
        _log_append(log_entry, payload)
        _persist_log()
        _persist_tail()
        _broadcast(payload)
        # Also update player inspiration state in stats.
        # NOTE: _persist_stats() acquires _stats_lock internally — capture the snapshot
        # inside the lock, then call persist/broadcast OUTSIDE to avoid deadlock.
        stats_snapshot = None
        with _stats_lock:
            players = _current_stats.setdefault("players", [])
            match = next((p for p in players if p.get("name", "").lower() == name.lower()), None)
            if match:
                match["inspiration"] = True
                stats_snapshot = dict(_current_stats)
        if stats_snapshot is not None:
            _persist_stats()
            _broadcast({"stats": stats_snapshot})
        return "", 204

    if is_xp_award:
        xp_data = data.get("xp_award", {})
        if not isinstance(xp_data, dict):
            return "", 204
        payload = {"xp_award": xp_data, "text": xp_data.get("summary", "")}
        log_entry = {"xp_award": xp_data, "text": xp_data.get("summary", "")}
        _log_append(log_entry, payload)
        _persist_log()
        _persist_tail()
        _broadcast(payload)
        return "", 204

    # Player/npc/dice/tutor/action text comes from send.py (no ANSI/chrome) — light clean only.
    # DM narration may come from wrapper.py — full clean.
    cleaned = raw.strip() if (is_action or is_player or is_npc or is_dice or is_tutor) else _clean(raw)
    if not cleaned.strip():
        return "", 204

    payload: dict = {"text": cleaned}

    # Voice for read-aloud: an explicit "voice" on the block wins; an NPC
    # otherwise uses the **Voice:** line of its npcs.md entry, a tutor hint
    # the campaign's tutor_voice. Narration keeps the narrator voice (unset).
    voice, fallback = _block_voices(data, is_npc, is_tutor)
    if voice:
        payload["voice"] = voice
    if fallback:
        payload["voice_fallback"] = fallback

    if is_action:
        payload["action"] = data["action"]
    elif is_player:
        payload["player"] = data["player"]
    elif is_npc:
        payload["npc"] = data["npc"]
    elif is_dice:
        payload["dice"] = True
    elif is_tutor:
        payload["tutor"] = True
    else:
        # Scene detection only on DM narration
        scene = _detect_scene(cleaned)
        if scene:
            payload["scene"] = scene
            if _audio:
                _audio.on_scene_change(scene["name"])
        # SFX scan on all non-player text
        if _audio:
            _audio.on_text(cleaned)

    # Store full typed payload so replay preserves action/player/npc/dice/tutor context
    log_entry: dict = {"text": cleaned}
    if payload.get("voice"):
        log_entry["voice"] = payload["voice"]
    if payload.get("voice_fallback"):
        log_entry["voice_fallback"] = payload["voice_fallback"]
    if is_action:
        log_entry["action"] = data["action"]
    elif is_player:
        log_entry["player"] = data["player"]
    elif is_npc:
        log_entry["npc"] = data["npc"]
    elif is_dice:
        log_entry["dice"] = True
    elif is_tutor:
        log_entry["tutor"] = True

    # Stamp campaign on tail entries to prevent bleed when switching campaigns
    try:
        _camp_stamp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if _camp_stamp:
            log_entry["_camp"] = _camp_stamp
    except Exception:
        pass

    _log_append(log_entry, payload)

    _persist_log()
    _persist_tail()
    _broadcast(payload)
    return "", 204


@app.route("/stats", methods=["POST"])
def stats():
    """Receive character/combat stat updates. Merges players by name, replaces turn_order.

    Pass replace_players=true to replace the entire player list (use on /dnd load to
    prevent stale characters from a previous campaign persisting in the sidebar).
    """
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    if not data:
        return "", 204
    _combat_flipped = False   # set when the turn order goes empty↔set

    if "awaiting" in data:
        global _awaiting
        aw = data["awaiting"]
        if isinstance(aw, str):
            aw = ([] if aw.strip().lower() in ("", "party", "all", "everyone")
                  else [n.strip() for n in aw.split(",") if n.strip()])
        _awaiting = [str(n)[:50] for n in aw] if aw else None
        _broadcast({"awaiting": _awaiting})
        if len(data) == 1:
            _check_auto_trigger()
            return "", 204

    _effect_expire_events: list[dict] = []
    with _stats_lock:
        if "players" in data:
            # replace_players=true wipes the list first — used on campaign load
            if data.get("replace_players"):
                _current_stats["players"] = []
            existing_players: list = _current_stats.setdefault("players", [])
            for incoming in data["players"]:
                name = incoming.get("name")
                if not name:
                    continue
                match = next((p for p in existing_players if p.get("name") == name), None)
                # Keys prefixed with _ are mutation ops, not stored fields
                _MUTATION_KEYS = {
                    "_inventory_add", "_inventory_remove",
                    "_conditions_add", "_conditions_remove",
                    "_slot_use", "_slot_restore",
                    "_hd_use", "_hd_restore",
                    "_effect_start", "_effect_end",
                    "_sheet_spells",
                    "_milestone_inc", "_milestone_dec",
                }
                if match:
                    for key, val in incoming.items():
                        if key == "_inventory_add":
                            inv = match.setdefault("sheet", {}).setdefault("inventory", [])
                            if val not in inv:
                                inv.append(val)
                        elif key == "_inventory_remove":
                            sheet = match.get("sheet", {})
                            sheet["inventory"] = [
                                i for i in sheet.get("inventory", [])
                                if i.lower() != str(val).lower()
                            ]
                        elif key == "_conditions_add":
                            conds = match.setdefault("conditions", [])
                            if val not in conds:
                                conds.append(val)
                        elif key == "_conditions_remove":
                            match["conditions"] = [
                                c for c in match.get("conditions", [])
                                if c.lower() != str(val).lower()
                            ]
                        elif key == "_slot_use":
                            slots = match.setdefault("spell_slots", {})
                            lvl = str(val)
                            slot = slots.setdefault(lvl, {"used": 0, "max": 0})
                            _normalize_slot(slot)
                            slot["used"] = min(slot["used"] + 1, slot.get("max", 99))
                        elif key == "_slot_restore":
                            slots = match.setdefault("spell_slots", {})
                            lvl = str(val)
                            slot = slots.setdefault(lvl, {"used": 0, "max": 0})
                            _normalize_slot(slot)
                            slot["used"] = max(slot["used"] - 1, 0)
                        elif key == "_hd_use":
                            hd = match.setdefault("hit_dice", {"remaining": 0, "max": 0})
                            hd["remaining"] = max(hd.get("remaining", 0) - 1, 0)
                        elif key == "_hd_restore":
                            hd = match.setdefault("hit_dice", {"remaining": 0, "max": 0})
                            hd["remaining"] = min(
                                hd.get("remaining", 0) + int(val),
                                hd.get("max", 99)
                            )
                        elif key == "_effect_start":
                            # val is an effect dict: {name, duration_type, ...}
                            spell_name = val.get("name", "")
                            effects = match.setdefault("effects", [])
                            # Replace any existing effect with the same name
                            match["effects"] = [
                                e for e in effects
                                if e.get("name", "").lower() != spell_name.lower()
                            ]
                            match["effects"].append(val)
                            # Sync concentration field if this is a conc effect
                            if val.get("concentration") and spell_name:
                                match["concentration"] = spell_name
                        elif key == "_effect_end":
                            # val is the spell name string
                            spell_lower = str(val).lower()
                            removed = [
                                e for e in match.get("effects", [])
                                if e.get("name", "").lower() == spell_lower
                            ]
                            match["effects"] = [
                                e for e in match.get("effects", [])
                                if e.get("name", "").lower() != spell_lower
                            ]
                            # If the ended effect was concentration, also clear it
                            if removed and any(e.get("concentration") for e in removed):
                                if match.get("concentration", "").lower() == spell_lower:
                                    match["concentration"] = None
                        elif key == "_sheet_spells":
                            # Patch only the spells sub-key inside sheet
                            sheet = match.setdefault("sheet", {})
                            sheet["spells"] = val
                        elif key == "inspiration" and val is False:
                            match["inspiration"] = False
                        elif key == "_milestone_inc":
                            # Stack-based reward counter — Inspiration variants,
                            # homebrew Hero Coins, Bardic Inspiration tokens, etc.
                            # The label string is the value; per-label cap optional.
                            label = str(val) or "Milestone"
                            ms = match.setdefault("milestones", {})
                            cap = match.get("milestone_caps", {}).get(label, 99)
                            ms[label] = min(ms.get(label, 0) + 1, cap)
                        elif key == "_milestone_dec":
                            label = str(val) or "Milestone"
                            ms = match.setdefault("milestones", {})
                            ms[label] = max(ms.get(label, 0) - 1, 0)
                            if ms.get(label, 0) == 0:
                                ms.pop(label, None)
                        elif isinstance(val, dict) and isinstance(match.get(key), dict):
                            match[key].update(val)
                        else:
                            match[key] = val
                else:
                    # Strip mutation ops — they're meaningless for new players
                    existing_players.append(
                        {k: v for k, v in incoming.items() if k not in _MUTATION_KEYS}
                    )

        # turn_order: a dict with "order" replaces the whole thing (a new fight);
        # one without it ({"current": …} / {"round": …}) updates those fields and
        # keeps the order, so a screen that reconnects mid-fight still has every
        # name. None clears. Also ticks round-based effects.
        _effect_expire_events: list[dict] = []
        if "turn_order" in data:
            sent_to = data["turn_order"]
            old_to = _current_stats.get("turn_order")
            new_to = sent_to
            if isinstance(sent_to, dict) and "order" not in sent_to and isinstance(old_to, dict):
                new_to = {**old_to, **sent_to}
            _current_stats["turn_order"] = new_to
            if _combat_on(old_to) != _combat_on(new_to):
                _combat_flipped = True
            # Decrement round-based effects for the actor whose turn just started
            # (only when this update names whose turn it is)
            if new_to and isinstance(sent_to, dict) and sent_to.get("current"):
                actor = sent_to["current"].lower()
                for p in _current_stats.get("players", []):
                    if p.get("name", "").lower() != actor:
                        continue
                    kept, expired = [], []
                    for eff in p.get("effects", []):
                        if eff.get("duration_type") == "rounds":
                            eff = dict(eff)  # don't mutate in-place
                            eff["duration_remaining"] = max(0, eff.get("duration_remaining", 1) - 1)
                            if eff["duration_remaining"] <= 0:
                                expired.append(eff)
                            else:
                                kept.append(eff)
                        else:
                            kept.append(eff)
                    p["effects"] = kept
                    for eff in expired:
                        was_conc = eff.get("concentration", False)
                        if was_conc and p.get("concentration", "").lower() == eff["name"].lower():
                            p["concentration"] = None
                        _effect_expire_events.append({
                            "owner": p["name"],
                            "name": eff["name"],
                            "was_concentration": was_conc,
                        })

        # world_time replaces entirely
        if "world_time" in data:
            _current_stats["world_time"] = data["world_time"]

        # factions replaces entirely ([] clears)
        # Validate: default missing standing to "Neutral" and warn so the root
        # cause (DM omitting the field when building JSON from state.md prose)
        # is surfaced in logs without silently showing "—" in the sidebar.
        if "factions" in data:
            validated_factions = []
            for f in (data["factions"] or []):
                if not isinstance(f, dict):
                    continue
                if f.get("name") and not f.get("standing"):
                    print(
                        f"[WARN] faction '{f['name']}' missing standing field — "
                        "defaulting to Neutral. Push with standing: Allied/Friendly/"
                        "Neutral/Suspicious/Hostile to show correct colour.",
                        file=sys.stderr,
                    )
                    f = dict(f)
                    f["standing"] = "Neutral"
                validated_factions.append(f)
            _current_stats["factions"] = validated_factions

        # Battle map: "map" replaces it (None clears); "map_moves" moves tokens
        # in the stored map — [{"name": "Havoc", "x": 4, "y": 2}, ...].
        if "map" in data:
            _current_stats["map"] = _clean_map(data["map"])
        if data.get("map_moves") and isinstance(_current_stats.get("map"), dict):
            toks = {t.get("name", "").lower(): t for t in _current_stats["map"].get("tokens", [])}
            for mv in data["map_moves"] if isinstance(data["map_moves"], list) else []:
                t = toks.get(str(mv.get("name", "")).lower()) if isinstance(mv, dict) else None
                if t is not None:
                    try:
                        t["x"], t["y"] = int(mv["x"]), int(mv["y"])
                    except (KeyError, TypeError, ValueError):
                        pass

        # quests replaces entirely ([] clears)
        if "quests" in data:
            _current_stats["quests"] = data["quests"]

        current = dict(_current_stats)

    # autorun_waiting / autorun_cycle — display-only signals, not stored in stats
    if "autorun_waiting" in data:
        if not data["autorun_waiting"]:
            # Turn resolved — clear stored cycle so reconnecting clients don't see stale pie
            global _autorun_cycle
            with _autorun_cycle_lock:
                _autorun_cycle = None
        _broadcast({"autorun_waiting": bool(data["autorun_waiting"])})
        if not any(k in data for k in ("players", "turn_order", "world_time", "factions",
                                        "quests", "replace_players", "sheet", "autorun_cycle")):
            return "", 204

    if "autorun_cycle" in data:
        with _autorun_cycle_lock:
            _autorun_cycle = data["autorun_cycle"]
        _broadcast({"autorun_cycle": data["autorun_cycle"]})
        if not any(k in data for k in ("players", "turn_order", "world_time", "factions",
                                        "replace_players", "sheet", "autorun_threshold")):
            return "", 204

    if "autorun_threshold" in data:
        global _autorun_threshold
        val = data["autorun_threshold"]
        _autorun_threshold = int(val) if val is not None else None
        _broadcast({"autorun_threshold": _autorun_threshold})
        if not any(k in data for k in ("players", "turn_order", "world_time", "factions",
                                        "replace_players", "sheet")):
            return "", 204

    # Write active campaign name so dm_help.py always reads the current campaign.
    # Also reload the tail buffer from the new campaign's session_tail.json so
    # display replay at /dnd load shows the correct campaign's last session.
    if "campaign" in data:
        try:
            with open(CAMP_FILE, "w", encoding="utf-8") as f:
                f.write(str(data["campaign"]).strip())
            _load_tail()
        except Exception:
            pass
        # Open screens switch to the new campaign's layout and voice settings
        # (the cache reloads because the active campaign name changed).
        try:
            _broadcast(_display_payload())
        except Exception:
            pass
        # Resolve and stash the ruleset for this campaign so the sidebar badge
        # can render. Defaults to '2014' for legacy campaigns predating the
        # ruleset field. Wrapped in try/except so a missing paths import or
        # malformed state.md never breaks the stats endpoint.
        try:
            from paths import campaign_ruleset as _campaign_ruleset
            _rs = _campaign_ruleset(str(data["campaign"]).strip())
            with _stats_lock:
                _current_stats["ruleset"] = _rs
            current = dict(_current_stats)
        except Exception:
            pass

    # Explicit ruleset override (e.g. push_stats.py --ruleset 2024)
    if "ruleset" in data:
        rs_in = str(data.get("ruleset") or "").strip()
        if rs_in in ("2014", "2024"):
            with _stats_lock:
                _current_stats["ruleset"] = rs_in
            current = dict(_current_stats)

    _persist_stats()
    _broadcast({"stats": current})
    # Broadcast any round-based effect expiries after the stats update
    for evt in _effect_expire_events:
        _broadcast({"effect_expired": evt})

    # Combat started or ended: a manual combat/story choice from the laptop
    # lasts only until then, so the TV goes back to switching by itself (D9).
    if _combat_flipped:
        global _mode_override
        _mode_override = None
        _broadcast(_display_payload())

    # Update expected player count for staged-input auto-trigger
    global _expected_count
    with _stats_lock:
        players = _current_stats.get("players", [])
    _expected_count = max(1, len(players))

    return "", 204


@app.route("/effects/expire", methods=["POST"])
def effects_expire():
    """Called by browser when a time-based effect countdown reaches zero.
    Removes the effect from stats, clears concentration if applicable,
    and broadcasts effect_expired to all connected clients.
    """
    if not _token_ok():
        return "Forbidden", 403
    data  = request.get_json(silent=True) or {}
    owner = data.get("owner", "").strip()
    name  = data.get("name", "").strip()
    if not owner or not name:
        return "", 400

    expire_evt = None
    with _stats_lock:
        for p in _current_stats.get("players", []):
            if p.get("name", "").lower() != owner.lower():
                continue
            was_conc   = False
            new_effects = []
            for e in p.get("effects", []):
                if e.get("name", "").lower() == name.lower():
                    was_conc = e.get("concentration", False)
                    if was_conc and p.get("concentration", "").lower() == name.lower():
                        p["concentration"] = None
                else:
                    new_effects.append(e)
            p["effects"] = new_effects
            expire_evt = {"owner": p["name"], "name": name, "was_concentration": was_conc}
            break
        current = dict(_current_stats)

    if expire_evt:
        _broadcast({"effect_expired": expire_evt})
    _broadcast({"stats": current})
    _persist_stats()
    return "", 204


@app.route("/audio-toggle", methods=["POST"])
def audio_toggle():
    """Enable/disable ambient or SFX from the browser toggle switches.

    Body: {"ambient": true|false, "sfx": true|false}  (either or both keys)
    Response: {"ambient": bool, "sfx": bool, "available": bool}
    Broadcasts audio_state to all connected browsers so every device syncs.
    """
    data = request.get_json(silent=True) or {}
    if _audio:
        if "sfx" in data:
            _audio.set_sfx(bool(data["sfx"]))
        state = _audio.get_state()
    else:
        state = {"sfx": False, "available": False}
    return state, 200


@app.route("/narration-pref", methods=["POST"])
def narration_pref():
    """Set the narration-length target the DM aims for each turn.

    Body: {"target_words": int}.  0 clears the preference. Persisted to the
    runtime dir as a plain integer; check_input.py reads it and prepends a
    directive to queued player input so the DM honors it that turn — no
    separate file read required on the DM side.
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr or "?"):
        return "Rate limited", 429
    data = request.get_json(silent=True) or {}
    try:
        n = int(data.get("target_words", 0))
    except (TypeError, ValueError):
        n = 0
    n = max(0, min(5000, n))
    pref = rt("narration_target")
    try:
        if n:
            with open(pref, "w", encoding="utf-8") as f:
                f.write(str(n))
        elif os.path.exists(pref):
            os.remove(pref)
    except OSError:
        pass
    return {"target_words": n}, 200


@app.route("/roll-pref", methods=["POST"])
def roll_pref():
    """Per-character roll preference. Body: {"character": str, "mode": "auto"|"players"}.

    Persisted to runtime roll_prefs.json; check_input.py surfaces each override as a
    [[<Char> roll mode: …]] directive so the DM honors it for that character,
    overriding the campaign-wide roll_mode in state.md.

    The character name is validated against the active party via _char_ok before
    persistence — otherwise a crafted value could smuggle prompt text into the DM
    through the [[<Char> roll mode: …]] template that check_input.py emits.
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr or "?"):
        return "Rate limited", 429
    data = request.get_json(silent=True) or {}
    char = (data.get("character") or "").strip()
    mode = (data.get("mode") or "").strip().lower()
    if not char or mode not in ("auto", "players"):
        return {"ok": False}, 400
    with _stats_lock:
        known = {p["name"] for p in _current_stats.get("players", [])}
    if not _char_ok(char, known):
        return "Forbidden", 403
    pref = rt("roll_prefs.json")
    try:
        prefs = {}
        if os.path.exists(pref):
            with open(pref, encoding="utf-8") as f:
                prefs = json.load(f)
        prefs[char] = mode
        with open(pref, "w", encoding="utf-8") as f:
            json.dump(prefs, f)
    except (OSError, ValueError):
        pass
    return {"ok": True, "character": char, "mode": mode}, 200


# ─── Narrator voice (Gemini Flash TTS) ────────────────────────────────────────
# Voice selection persists per-campaign in state.md → ## Session Flags →
# `tts_voice: <name>`. Read at /index render, written by POST /voice.

_VOICE_PAT = re.compile(r"^\s*tts_voice:\s*([A-Za-z_]+)\s*$", re.MULTILINE)


def _valid_voice(v: str) -> bool:
    """A Gemini or a local Kokoro voice name."""
    f = getattr(_tts, "valid_voice", None)
    return f(v) if f else v in _tts.VALID_VOICES


def _clean_map(m):
    """Validate a battle-map payload; None clears the map."""
    if not isinstance(m, dict):
        return None
    g = m.get("grid") or {}
    try:
        w, h = int(g.get("w", 0)), int(g.get("h", 0))
    except (TypeError, ValueError):
        return None
    if not (1 <= w <= 80 and 1 <= h <= 80):
        return None
    cells = [str(r)[:w] for r in (m.get("cells") or [])][:h]
    tokens = []
    for t in (m.get("tokens") or [])[:60]:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        try:
            tok = {"name": str(t["name"])[:40], "x": int(t["x"]), "y": int(t["y"]),
                   "kind": "pc" if t.get("kind") == "pc" else "npc",
                   "size": max(1, min(4, int(t.get("size", 1))))}
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(t.get("hp"), (list, dict)):
            tok["hp"] = t["hp"]
        if isinstance(t.get("conditions"), list):
            tok["conditions"] = [str(c)[:24] for c in t["conditions"][:6]]
        tokens.append(tok)
    image = m.get("image")
    image = str(image) if isinstance(image, str) and re.fullmatch(r"[\w\-./ ]{1,120}", image) else None
    return {"grid": {"w": w, "h": h, "ft": 5}, "cells": cells, "image": image,
            "round": int(m.get("round") or 1), "tokens": tokens}


@app.route("/map-image/<path:name>")
def map_image(name):
    """A battle-map background from the active campaign's maps/ folder."""
    camp = _active_campaign_name()
    if not camp or not re.fullmatch(r"[\w\-./ ]{1,120}", name) or ".." in name:
        return "Not found", 404
    if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return "Not found", 404
    try:
        base = (_find_campaign(camp) / "maps").resolve()
        rel = name[5:] if name.startswith("maps/") else name
        path = (base / rel).resolve()
        path.relative_to(base)
    except (OSError, ValueError, TypeError):
        return "Not found", 404
    if not path.is_file():
        return "Not found", 404
    return send_from_directory(str(path.parent), path.name)


def _active_campaign_name() -> Optional[str]:
    try:
        return open(CAMP_FILE, encoding="utf-8").read().strip() or None
    except OSError:
        return None


_NPC_VOICE_PAT = re.compile(r"^\s*-?\s*\**voice:?\**:?\s*\**\s*([A-Za-z_]+)", re.IGNORECASE | re.MULTILINE)
_TUTOR_VOICE_PAT = re.compile(r"^\s*tutor_voice:\s*([A-Za-z_]+)\s*$", re.MULTILINE)
def _npc_voices(campaign: str, npc: str) -> tuple:
    """(voice, fallback) for an NPC block.

    Every NPC has its own Kokoro voice in npcs.md: one that speaks with none
    is given a free one, written back (D19). In Kokoro mode (the default) the
    block is read in it and Gemini is never asked. In Gemini mode the block
    carries the NPC's Gemini voice, with its own Kokoro voice as the fallback
    for when Gemini can't speak (D17, D21). The engine is read per line, so a
    change on the laptop applies from the next one."""
    if _cast is None or not str(npc or "").strip():
        return "", ""
    try:
        engine = _display_config().get("voice", {}).get("npc_engine", "kokoro")
        got = _cast.npc_voices(campaign, npc, engine=engine)
    except (OSError, ValueError, TypeError) as exc:
        print(f"[cast] could not resolve a voice for {npc!r}: {exc}", flush=True)
        return "", ""
    kokoro, gemini = got.get("voice", ""), got.get("gemini", "")
    if engine == "gemini" and gemini:
        return gemini, kokoro
    return kokoro, ""


def _block_voice(data: dict, is_npc: bool, is_tutor: bool) -> str:
    """The read-aloud voice for one block, or "" for the narrator voice."""
    return _block_voices(data, is_npc, is_tutor)[0]


def _block_voices(data: dict, is_npc: bool, is_tutor: bool) -> tuple:
    """(voice, fallback): the read-aloud voice for one block ("" for the
    narrator voice) and, for an NPC in Gemini mode, its own Kokoro voice."""
    if _tts is None:
        return "", ""
    v = str(data.get("voice") or "").strip()
    if _valid_voice(v):
        return v, ""
    name = _active_campaign_name()
    if not name:
        return "", ""
    if is_npc:
        return _npc_voices(name, str(data.get("npc", "")))
    return _block_voice_rest(name, is_tutor), ""


def _block_voice_rest(name: str, is_tutor: bool) -> str:
    if is_tutor:
        try:
            st = (_find_campaign(name) / "state.md").read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError, TypeError):
            return ""
        m = _TUTOR_VOICE_PAT.search(st)
        return m.group(1) if m and _valid_voice(m.group(1)) else ""
    return ""


# ─── Voice state: works fully or stays off ───────────────────────────────────
# One answer for every screen: the voice is on, or it is off and says why.
# There is no browser speech-synthesis fallback: it read narration in a flat
# robot voice whenever Gemini failed, which sounded broken rather than off.
#
# Two engines feed it. Kokoro (local, kokoro_server.py) reads the narration
# and stands in for Gemini when Gemini can't speak; Gemini voices NPC lines.
# The voice is on while either works. _voice_set records Gemini's own state
# (set by a check at start that spends no quota, by a quota reply mid-game and
# by the key watcher); _kokoro_ok is kept current by _kokoro_supervise.
_voice_lock = threading.Lock()
_voice = {"on": False, "reason": "checking the voice…"}
_gemini_state = {"on": False, "reason": "checking the voice…"}
_kokoro_ok = False          # the local Kokoro helper answers /health
_kokoro_info: dict = {}     # its last /health reply (provider: cuda | cpu)
_voice_next_check = 0.0     # time.time() after which Gemini is checked again
_voice_key_fp = None        # fingerprint of the key last checked


def _voice_payload() -> dict:
    with _voice_lock:
        return {"on": _voice["on"], "reason": _voice["reason"]}


def _voice_publish() -> None:
    """Combine both engines into the one state; tell every screen on change."""
    on = bool(_gemini_state["on"] or _kokoro_ok)
    reason = "" if on else _gemini_state["reason"]
    with _voice_lock:
        changed = (_voice["on"] != on) or (_voice["reason"] != reason)
        _voice["on"], _voice["reason"] = on, reason
    if changed:
        _broadcast({"voice": _voice_payload()})


def _voice_set(on: bool, reason: str = "", recheck_in: float = 0.0) -> None:
    """Record whether Gemini can speak, then publish the combined state."""
    global _voice_next_check
    _gemini_state["on"], _gemini_state["reason"] = on, ("" if on else reason)
    _voice_next_check = (_time.time() + recheck_in) if (not on and recheck_in) else 0.0
    _voice_publish()


def _kokoro_set(ok: bool, info: Optional[dict] = None) -> None:
    global _kokoro_ok, _kokoro_info
    _kokoro_info = dict(info or {})
    if ok != _kokoro_ok:
        _kokoro_ok = ok
        _voice_publish()


def _kokoro_supervise() -> None:
    """Keep the local Kokoro voice running for as long as the display runs.

    Starts kokoro_server.py in Kokoro's own Python environment when it's
    installed and not already answering, restarts it if it dies (backing off
    after repeated failures), and keeps _kokoro_ok current. The helper is
    given this process's pid and exits by itself when the display stops."""
    import subprocess as _sp
    import time as _t
    if _tts is None or not hasattr(_tts, "kokoro_installed"):
        return
    proc, fails = None, 0
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        h = _tts.kokoro_health()
        if h:
            _kokoro_set(True, h)
            fails = 0
            _t.sleep(10)
            continue
        _kokoro_set(False)
        if not _tts.kokoro_installed():
            _t.sleep(60)
            continue
        if proc is not None and proc.poll() is None:
            _t.sleep(2)        # still loading the model
            continue
        if fails >= 5:
            _t.sleep(300)
            fails = 0
        fails += 1
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = _sp.CREATE_NO_WINDOW
        try:
            log = open(os.path.join(here, "kokoro.log"), "a", encoding="utf-8")
            proc = _sp.Popen([str(_tts.kokoro_python()), os.path.join(here, "kokoro_server.py"),
                              "--port", str(_tts.KOKORO_PORT), "--models", str(_tts.kokoro_models()),
                              "--parent-pid", str(os.getpid())],
                             stdout=log, stderr=_sp.STDOUT, stdin=_sp.DEVNULL, cwd=here, **kw)
        except OSError as e:
            print(f"[kokoro] could not start the local voice: {e}", file=sys.stderr)
            proc = None
            _t.sleep(30)
            continue
        for _ in range(120):          # the model loads in a few seconds
            _t.sleep(1)
            if _tts.kokoro_health() or proc.poll() is not None:
                break


def _voice_check() -> None:
    """Work out whether the voice can run, and set the state from that."""
    global _voice_key_fp
    if _tts is None:
        _voice_set(False, "the voice module didn't load")
        return
    try:
        _voice_key_fp = _tts.key_fingerprint()
    except Exception:
        _voice_key_fp = ""
    if not _voice_key_fp:
        _voice_set(False, "no voice key is set up")
        return
    left = _tts_quota_until - _time.time()
    if left > 0:
        _voice_set(False, _quota_reason(left), recheck_in=left)
        return
    ok, why, retry = _tts.check_key()
    _voice_set(ok, why, recheck_in=300.0 if retry else 0.0)


def _quota_reason(secs: float) -> str:
    secs = max(60, int(secs))
    when = f"about {round(secs / 3600)} h" if secs >= 5400 else f"about {max(1, round(secs / 60))} min"
    return f"the Gemini voice quota is used up — back in {when}"


def _tts_key_watch(interval: float = 30.0) -> None:
    """Check the voice at start, then again when the key changes or a
    temporary failure (quota, network) has had time to clear."""
    import time as _t
    _voice_check()
    while True:
        _t.sleep(interval)
        try:
            fp = _tts.key_fingerprint() if _tts is not None else ""
        except Exception:
            fp = ""
        with _voice_lock:
            # Gemini's own state, not the combined one: with Kokoro up the
            # voice stays on while Gemini waits out a quota, and Gemini must
            # still be checked again when the wait is over.
            due = (not _gemini_state["on"]) and _voice_next_check and _time.time() >= _voice_next_check
        if fp != _voice_key_fp or due:
            _voice_check()


def _read_narrator_voice() -> str:
    """Return the active campaign's tts_voice, or the module default."""
    if _tts is None:
        return ""
    local = getattr(_tts, "kokoro_installed", lambda: False)()
    default = _tts.DEFAULT_NARRATOR if local else _tts.DEFAULT_VOICE
    name = _active_campaign_name()
    if not name:
        return default
    v = ""
    if _cast is not None:
        # display.json voice.narrator, then state.md tts_voice (D17, S11).
        try:
            v = _cast.narrator_voice(name, default="") or ""
        except (OSError, ValueError, TypeError):
            v = ""
    else:
        try:
            state = _find_campaign(name) / "state.md"
            if not state.exists():
                return default
            text = state.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            return default
        m = _VOICE_PAT.search(text)
        v = m.group(1).strip() if m else ""
    # Kokoro reads the narration when it's installed: a saved Gemini narrator
    # is from before that, so it gives way to the local default (Lewis).
    if local:
        return v if _tts.is_kokoro(v) else default
    return v if _valid_voice(v) else default


def _write_narrator_voice(voice: str) -> bool:
    """Persist tts_voice to the active campaign's state.md → ## Session Flags."""
    ok_voice = getattr(_tts, "valid_voice", None)
    if _tts is None or not (ok_voice(voice) if ok_voice else voice in _tts.VALID_VOICES):
        return False
    name = _active_campaign_name()
    if not name:
        return False
    try:
        state = _find_campaign(name) / "state.md"
        # utf8io transcodes legacy-GBK state.md losslessly (one-time migration);
        # raises ValueError on anything undecodable, so we never write U+FFFD
        # back over the file from the display UI.
        text = _read_text(state) if state.exists() else ""
    except (OSError, ValueError):
        return False

    # _read_text decodes raw bytes, so a CRLF file arrives with its "\r\n"
    # intact. Editing in "\n" form and writing back with newline="" keeps the
    # file's own line endings. Before this, write_text's newline translation on
    # Windows turned every "\r\n" into "\r\r\n" — one more "\r" per line on
    # every voice change — and the "\n"-only section regex below never matched
    # a CRLF file, so the chosen voice was never saved either.
    eol = "\r\n" if "\r\n" in text else "\n"
    text = text.replace("\r\n", "\n")

    new_line = f"tts_voice: {voice}"
    if _VOICE_PAT.search(text):
        text = _VOICE_PAT.sub(new_line, text, count=1)
    else:
        # Append under ## Session Flags. If the section is missing, append at EOF.
        if "## Session Flags" in text:
            # Insert right after the header line. Keep the existing template
            # comment if present, but place the flag immediately under it.
            text = re.sub(
                r"(## Session Flags\n(?:\*\(.*?\)\*\n)?)",
                r"\1" + new_line + "\n",
                text,
                count=1,
            )
        else:
            sep = "" if text.endswith("\n") else "\n"
            text = f"{text}{sep}\n## Session Flags\n{new_line}\n"

    if eol != "\n":
        text = text.replace("\n", eol)
    try:
        with open(state, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return True
    except OSError:
        return False


_tts_quota_until = 0.0   # time.time() before which Gemini is known to be out of quota


def _tts_quota_response():
    left = max(0, int(_tts_quota_until - _time.time()))
    return Response(f"TTS quota used up; resets in about {left // 60} min", status=429,
                    headers={"Retry-After": str(left), "X-TTS-Quota": "exhausted"})


@app.route("/tts", methods=["POST"])
def tts_synthesize():
    """Synthesize a narrator/NPC line to L16 PCM.

    Body: {"text": str, "voice": str (optional), "speed": float (optional)}
    Response: raw L16 PCM bytes, Content-Type: audio/L16;codec=pcm;rate=24000;
    X-Audio-Engine says who spoke (kokoro | gemini).

    A Kokoro voice (narration) is read locally. A Gemini voice (an NPC) goes
    to Gemini; when Gemini fails or is out of quota and Kokoro is running, a
    matching Kokoro voice reads the line instead, so the table never loses
    the voice to Gemini's 10-a-day free quota.
    Failures: 503 (voice off / module unavailable), 400 (bad input), 502
    (upstream, no stand-in), 429 (Gemini quota used up and no local voice).
    """
    global _tts_quota_until
    if _tts is None:
        return "TTS module unavailable", 503
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    voice = (data.get("voice") or "").strip() or _read_narrator_voice() or _tts.DEFAULT_VOICE
    # The local narrator is read a sentence at a time (a long turn is 20+
    # requests a minute) and costs nothing, so it has its own, larger bucket.
    local_req = getattr(_tts, "is_kokoro", lambda v: False)(voice) and _kokoro_ok
    ip = request.remote_addr or "?"
    if not (_rate_ok(ip + "|local", 600) if local_req else _rate_ok(ip)):
        return "Rate limited", 429
    try:
        speed = max(0.5, min(2.0, float(data.get("speed") or 1.0)))
    except (TypeError, ValueError):
        speed = 1.0
    if not text:
        return "empty text", 400
    if len(text) > _tts.MAX_TEXT_CHARS:
        text = text[: _tts.MAX_TEXT_CHARS]
    if not _valid_voice(voice):
        voice = _tts.DEFAULT_VOICE

    def _pcm(pcm, engine, used):
        return Response(pcm, mimetype="audio/L16;codec=pcm;rate=24000",
                        headers={"X-Audio-Chars": str(len(text)), "X-Audio-Voice": used,
                                 "X-Audio-Engine": engine, "Cache-Control": "no-store"})

    # An NPC block in Gemini mode carries its own Kokoro voice (D21); without
    # one (the old page) the shared stand-in table picks a similar voice.
    fallback = str(data.get("fallback") or "").strip()
    if not getattr(_tts, "is_kokoro", lambda v: False)(fallback):
        fallback = ""

    def _local(v):
        v = fallback or _tts.kokoro_standin(v)
        return _pcm(_tts.kokoro_synthesize(text, v, speed=speed), "kokoro", v)

    is_local = getattr(_tts, "is_kokoro", lambda v: False)(voice)
    # The TV reads narration a sentence at a time and asks for the local voice
    # only: routed to Gemini, one turn would use up the day's quota. Refused,
    # it types the text without voice.
    if data.get("local_only") and not (is_local and _kokoro_ok):
        return "Local voice unavailable", 503
    if is_local:
        if _kokoro_ok:
            try:
                return _local(voice)
            except _tts.TtsError as e:
                return f"Local voice failed: {e}", 502
        # Kokoro is down: read the narration with Gemini while it can.
        voice = _tts.DEFAULT_VOICE

    # ── Gemini ──
    if _time.time() < _tts_quota_until:
        # A cached clip still plays: only a new line would hit the quota.
        cached = _tts.cache_get(_tts.cache_key(voice, text))
        if cached is not None:
            return _pcm(cached, "gemini", voice)
        if _kokoro_ok:
            return _local(voice)
        return _tts_quota_response()
    if not _voice_payload()["on"]:
        return f"Voice is off: {_voice_payload()['reason']}", 503
    if _kokoro_ok and (_tts.key_source() == "unset" or not _gemini_state["on"]):
        return _local(voice)
    if _tts.key_source() == "unset":
        return "TTS not configured (see docs/SKILL-tts.md)", 503
    try:
        # With a local stand-in ready, don't keep the table waiting on Gemini.
        kw = {"timeout": 12.0} if _kokoro_ok else {}
        pcm = _tts.synthesize_strict(text, voice, **kw)
    except _tts.TtsError as e:
        if getattr(e, "status", 0) == 429:
            # Out of Gemini quota. The free tier allows 10 voice requests a
            # day for this model, so a few turns use it up. Stop calling
            # until it resets.
            wait = e.retry_after or (3600 if e.daily_quota else 60)
            _tts_quota_until = _time.time() + wait
            if _kokoro_ok:
                global _voice_next_check
                _gemini_state["on"], _gemini_state["reason"] = False, _quota_reason(wait)
                _voice_next_check = _time.time() + wait
                # For the laptop page only (D17): the TV shows nothing. A
                # per-minute limit clears in seconds and gets no note.
                if e.daily_quota:
                    _voice_note_set(wait)
                return _local(voice)
            _broadcast({"tts_quota": {"daily": e.daily_quota, "retry_after": int(wait),
                                      "quota": e.quota_id}})
            _voice_set(False, _quota_reason(wait), recheck_in=wait)
            return _tts_quota_response()
        if _kokoro_ok:
            return _local(voice)
        return f"TTS upstream: {e}", 502
    return _pcm(pcm, "gemini", voice)


@app.route("/voice", methods=["POST"])
def tts_voice():
    """Persist narrator voice selection for the active campaign.

    Body: {"voice": str}
    Response: {"voice": str, "persisted": bool}
    """
    if _tts is None:
        return jsonify({"voice": "", "persisted": False}), 503
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    voice = (data.get("voice") or "").strip()
    if not _valid_voice(voice):
        return jsonify({"error": "invalid voice"}), 400
    ok = _write_narrator_voice(voice)
    name = _active_campaign_name()
    if _cast is not None and name:
        # display.json is where the narrator is read from first; an NPC that
        # held this voice is given another (no NPC sounds like the narrator).
        try:
            _cast.set_voice(name, "narrator", None, voice)
            ok = True
        except (OSError, ValueError) as exc:
            print(f"[cast] narrator voice not saved to display.json: {exc}", flush=True)
        _display_reload()
    return jsonify({"voice": voice, "persisted": ok}), 200


@app.route("/cast", methods=["GET"])
def cast_get():
    """Everyone and their voice, for the laptop page's cast list (D18)."""
    name = _active_campaign_name()
    if _cast is None or not name:
        return jsonify(narrator=_read_narrator_voice(), pcs=[], npcs=[], voices=[])
    try:
        first = _cast.cast_list(name)
        for pc in first["pcs"]:
            if not pc["voice"]:
                _cast.pc_voice(name, pc["name"])       # D22: every PC has a voice
        # Show the voice each NPC will really be read in: an NPC with none, or
        # an old Gemini Voice: line, is settled now rather than when it speaks.
        engine = _display_config().get("voice", {}).get("npc_engine", "kokoro")
        for npc in first["npcs"]:
            if not _tts.is_kokoro(npc["voice"]) or (engine == "gemini" and not npc.get("gemini")):
                _cast.npc_voices(name, npc["name"], engine=engine)
        data = _cast.cast_list(name)
    except (OSError, ValueError) as exc:
        return jsonify(error=str(exc)), 500
    data["voices"] = [{"id": v, "label": _tts.KOKORO_VOICES[v], "kind": _cast.describe(v)}
                      for v in _cast.kokoro_voices()] if _tts is not None else []
    return jsonify(data)


@app.route("/cast", methods=["POST"])
def cast_post():
    """Change one voice: {"kind": "npc"|"pc"|"narrator", "name", "voice"}."""
    if not _host_only():
        return jsonify(error="Open this on the computer running the game."), 403
    name = _active_campaign_name()
    if _cast is None or not name:
        return jsonify(error="No campaign is loaded."), 409
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Send {kind, name, voice} as JSON."), 400
    kind = str(data.get("kind") or "")
    voice = str(data.get("voice") or "").strip()
    try:
        _cast.set_voice(name, kind, data.get("name"), voice)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except OSError as exc:
        return jsonify(error=f"Could not save: {exc}"), 500
    if kind == "narrator":
        _write_narrator_voice(voice)
        _display_reload()
    return jsonify(_cast.cast_list(name))


@app.route("/audio/sfx/<name>")
def audio_sfx(name):
    """Serve a synthesized SFX WAV for the given effect name."""
    if not _audio:
        return "Audio not available", 503
    wav = _audio.get_sfx_wav(name)
    if wav is None:
        return "Not found", 404
    return Response(wav, mimetype="audio/wav",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.route("/clear", methods=["POST"])
def clear():
    """Wipe text log AND stats, broadcast clear to all connected browsers.

    Called on /dnd new (fresh campaign). Ensures sidebar shows no stale characters.
    """
    if not _token_ok():
        return "Forbidden", 403
    global _scene_buffer, _current_stats
    with _text_log_lock:
        _text_log.clear()
    with _stats_lock:
        _current_stats = {}
    _scene_buffer = []
    for path in (LOG_FILE, STATS_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    _broadcast({"clear": True})
    return "", 204


@app.route("/help-request", methods=["POST"])
def help_request():
    """Spawn dm_help.py to generate and send an on-demand DM hint.

    Protected by an O_EXCL lock file — concurrent requests return 409
    so multiple players clicking the button never duplicates execution.
    Lock is released by dm_help.py in its finally block.
    """
    if not _token_ok():
        return "Forbidden", 403

    # Atomic lock: O_EXCL fails if file already exists — no race condition
    try:
        fd = os.open(HELP_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return "Already running", 409

    # Read active campaign name
    try:
        campaign = open(CAMP_FILE, encoding="utf-8").read().strip()
    except FileNotFoundError:
        os.unlink(HELP_LOCK)
        return "No active campaign", 400

    if not campaign:
        os.unlink(HELP_LOCK)
        return "No active campaign", 400

    dm_help_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dm_help.py")
    subprocess.Popen(
        [sys.executable, dm_help_py, "--campaign", campaign],
        close_fds=True,
        start_new_session=True,
    )
    return "", 202


@app.route("/player-input", methods=["POST"])
def player_input():
    """Queue a player action submitted from the display companion.

    Body: {"character": "Mira", "text": "I draw my rapier", "hold": false}
    Broadcasts pending_input event to all connected browsers.
    """
    if not _token_ok():
        return "Forbidden", 403

    import time
    data = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", "Party"))[:50]
    text = str(data.get("text", ""))[:500]
    hold = bool(data.get("hold", False))

    # Strip shell metacharacters — input is player dialogue/action, not commands
    text = re.sub(r"[`\\$]", "", text).strip()
    if not text:
        return "empty", 400

    entry = {
        "character": character,
        "text": text,
        "hold": hold,
        "timestamp": time.time(),
    }

    with _input_lock:
        _input_queue.append(entry)
        current = list(_input_queue)

    _persist_input_queue()
    _broadcast({"pending_input": current})
    return "", 204


@app.route("/player-input/dice", methods=["POST"])
def player_dice():
    """Server-side dice roll submitted from a player's phone.

    Body: {"character": "Piper", "spec": "1d20", "modifier": 5,
           "advantage": "normal" | "advantage" | "disadvantage",
           "label": "Stealth check"  (optional)}

    Rolls server-side (secrets.randbelow → uniform, non-spoofable), broadcasts
    a dice-typed entry on the feed, and returns the result so the phone can
    finish its slot-machine animation on the real value.

    `"faces": [14]` (or two faces for a 1d20 with advantage / disadvantage)
    records a PHYSICAL roll instead: the player rolled a real die and typed
    the face; the modifier is added here, so nobody has to guess whether "14"
    was the face or the total. It goes through the same request correlation
    as a virtual roll, so `send.py --wait` reports both alike.
    """
    if not _token_ok():
        return "Forbidden", 403

    data = request.get_json(force=True, silent=True) or {}
    character = re.sub(r"[`\\$]", "", str(data.get("character", "Player"))[:50]).strip() or "Player"
    spec      = str(data.get("spec", "1d20")).strip().lower()
    modifier  = int(data.get("modifier", 0) or 0)
    adv       = str(data.get("advantage", "normal")).strip().lower()
    label     = re.sub(r"[`\\$]", "", str(data.get("label", ""))[:60]).strip()
    req_id    = str(data.get("request_id", "")).strip()[:24]

    m = re.fullmatch(r"(\d{1,2})d(\d{1,3})", spec)
    if not m:
        return jsonify({"error": "bad spec"}), 400
    n_dice, n_sides = int(m.group(1)), int(m.group(2))
    if not (1 <= n_dice <= 20 and 2 <= n_sides <= 100):
        return jsonify({"error": "out of range"}), 400
    modifier = max(-100, min(100, modifier))

    faces_in = data.get("faces")
    physical = faces_in is not None
    if physical:
        try:
            faces = [int(f) for f in (faces_in if isinstance(faces_in, list) else [faces_in])]
        except (TypeError, ValueError):
            return jsonify({"error": "faces must be integers"}), 400
        want = 2 if (adv in ("advantage", "disadvantage") and spec == "1d20") else n_dice
        if len(faces) == 1 and want == 2:
            want = 1          # one physical d20 typed with advantage on: take it as the kept die
            adv = "normal"
        if len(faces) != want or any(not (1 <= f <= n_sides) for f in faces):
            return jsonify({"error": f"expected {want} face(s) between 1 and {n_sides}"}), 400
        _faces_iter = iter([[f] for f in faces] if want == 2 else [faces])

    def _roll_once() -> list[int]:
        if physical:
            return next(_faces_iter)
        return [secrets.randbelow(n_sides) + 1 for _ in range(n_dice)]

    if adv in ("advantage", "disadvantage") and spec == "1d20":
        r1, r2 = _roll_once(), _roll_once()
        chosen = max(r1[0], r2[0]) if adv == "advantage" else min(r1[0], r2[0])
        rolls  = [chosen]
        kept   = [chosen]
        both   = [r1[0], r2[0]]
    else:
        rolls = _roll_once()
        kept  = rolls
        both  = None

    subtotal = sum(kept)
    total    = subtotal + modifier
    mod_str  = (f"+{modifier}" if modifier > 0 else (str(modifier) if modifier < 0 else ""))
    breakdown = f"[{', '.join(str(r) for r in (both or rolls))}]"
    if both is not None:
        breakdown += f" → keep {kept[0]} ({adv})"
    if modifier:
        breakdown += f" {mod_str}"
    suffix = f" — {label}" if label else ""
    how    = " (rolled at the table)" if physical else ""
    text   = f"{character} rolls {spec}{mod_str}{how}: {breakdown} = {total}{suffix}"

    payload   = {"text": text, "dice": True}
    log_entry = {"text": text, "dice": True}
    try:
        _camp_stamp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if _camp_stamp:
            log_entry["_camp"] = _camp_stamp
    except Exception:
        pass

    _log_append(log_entry, payload)
    _persist_log()
    _persist_tail()
    _broadcast(payload)

    # Correlate against any pending DM request. Case-insensitive match on the
    # character name — drop them from the request's expected-rollers set.
    pending_changed = False
    nat = kept[0] if spec == "1d20" else None
    to_queue: list = []       # lines for the DM's inbox (no live waiter to hand them over)
    asked = False             # this roll answered a DM request
    if req_id:
        with _dice_pending_lock:
            entry = _dice_pending.get(req_id)
            if entry is not None:
                ci = character.lower()
                matched = next((c for c in entry["chars"] if c.lower() == ci), None)
                if matched is not None:
                    asked = True
                    waiter_alive = (_time.time() - entry.get("polled_at", 0.0)) < _DICE_WAITER_ALIVE
                    entry["chars"].discard(matched)
                    dc_v = entry["meta"].get("dc")
                    entry.setdefault("results", []).append({
                        "character": matched, "rolls": both or rolls, "kept": kept,
                        "modifier": modifier, "total": total, "physical": physical,
                        "natural": nat,
                        "success": (total >= dc_v) if isinstance(dc_v, int) else None,
                        "text": text,
                        "dm_line": _roll_line(matched, label or entry["meta"].get("label", ""), spec,
                                              mod_str, total, nat, dc_v, physical, True),
                        "queued": False,
                    })
                    if not waiter_alive:
                        # Nobody is polling: this roll, and any earlier one of the
                        # same request that a now-dead wait never printed.
                        for r in entry["results"]:
                            if not r.get("queued"):
                                to_queue.append(r["dm_line"])
                                r["queued"] = True
                    pending_changed = True
                    if not entry["chars"]:
                        _dice_finish(req_id, entry)
    if pending_changed:
        _broadcast({"dice_pending": _dice_pending_snapshot()})

    # A roll nobody asked for, or one whose DM wait was cut off, goes to the
    # DM's inbox, so the next autorun_wait / check_input delivers it and the
    # game moves on instead of the roll sitting on the TV unseen.
    if not asked:
        to_queue.append(_roll_line(character, label, spec, mod_str, total, nat,
                                   None, physical, False))
    if to_queue:
        _queue_roll_for_dm("\n".join(to_queue), character)

    return jsonify({
        "character": character,
        "spec": spec,
        "modifier": modifier,
        "advantage": adv,
        "rolls": rolls,
        "kept": kept,
        "both": both,
        "subtotal": subtotal,
        "total": total,
        "text": text,
        "physical": physical,
        "request_id": req_id or None,
    }), 200


@app.route("/dice-request", methods=["POST"])
def dice_request():
    """DM-initiated dice request — broadcast to player phones (no persistence).

    Body: {"character": "Piper" | "any",
           "spec": "1d20", "modifier": 5,
           "advantage": "normal" | "advantage" | "disadvantage",
           "label": "Stealth check"  (optional),
           "dc": 15  (optional, informational)}

    Phones bound to ?character=<name> match case-insensitively. "any" / ""
    targets every phone. No state stored — late-joining phones will not see
    requests issued before they connected.
    """
    if not _token_ok():
        return "Forbidden", 403

    import time
    data = request.get_json(force=True, silent=True) or {}
    raw_char  = data.get("characters") if "characters" in data else data.get("character", "any")
    if isinstance(raw_char, list):
        chars = [str(c).strip() for c in raw_char if str(c).strip()]
    else:
        chars = [c.strip() for c in re.sub(r"[`\\$]", "", str(raw_char))[:200].split(",") if c.strip()]
    if not chars:
        chars = ["any"]

    spec      = str(data.get("spec", "1d20")).strip().lower()
    modifier  = int(data.get("modifier", 0) or 0)
    adv       = str(data.get("advantage", "normal")).strip().lower()
    label     = re.sub(r"[`\\$]", "", str(data.get("label", ""))[:60]).strip()
    dc        = data.get("dc")

    if not re.fullmatch(r"\d{1,2}d\d{1,3}", spec):
        return jsonify({"error": "bad spec"}), 400
    if adv not in ("normal", "advantage", "disadvantage"):
        adv = "normal"
    modifier = max(-100, min(100, modifier))
    dc_val   = int(dc) if isinstance(dc, (int, float)) else None

    request_id = secrets.token_hex(6)

    # Only register pending entries for explicit named targets. "any" is fire-and-forget.
    trackable = [c for c in chars if c.lower() != "any"]
    if trackable:
        with _dice_pending_lock:
            _dice_pending[request_id] = {
                "chars": set(trackable),
                "meta": {"spec": spec, "modifier": modifier, "advantage": adv, "label": label, "dc": dc_val},
                "started_at": time.time(),
                "results": [],
            }
        _broadcast({"dice_pending": _dice_pending_snapshot()})

    # Targets with no live phone bound → the main display should roll on-screen.
    onscreen_targets = [c for c in chars if c.lower() != "any" and not _phone_present(c)]
    payload = {
        "dice_request": {
            "request_id": request_id,
            "characters": chars,
            "character": chars[0] if len(chars) == 1 else "any",   # legacy single-target field
            "onscreen_targets": onscreen_targets,
            "spec": spec,
            "modifier": modifier,
            "advantage": adv,
            "label": label,
            "dc": dc_val,
        }
    }
    _broadcast(payload)
    return jsonify({
        "request_id": request_id,
        "pending": sorted(trackable),
        "complete": not trackable,
    }), 200


@app.route("/dice-request/<request_id>", methods=["GET"])
def dice_request_status(request_id):
    """Poll a dice request's completion state.

    Returns 200 with {complete, pending, label, started_at}. A request that
    never existed (or has already fully drained) reports complete=True with
    an empty pending list — send.py --wait treats both identically.
    """
    if not _token_ok():
        return "Forbidden", 403
    # ?delivered=1: the caller (send.py --wait giving up) is printing these
    # results now, so they must not also go to the DM's inbox later.
    delivered = request.args.get("delivered") == "1"

    def _public(results):
        if delivered:
            for r in results:
                r["queued"] = True
        return [{k: v for k, v in r.items() if k not in ("dm_line", "queued")} for r in results]

    with _dice_pending_lock:
        entry = _dice_pending.get(request_id)
        if entry is not None:
            entry["polled_at"] = _time.time()
        if entry is None or not entry["chars"]:
            done = _dice_done.get(request_id) or {}
            meta = done.get("meta") or {}
            return jsonify({"complete": True, "pending": [],
                            "results": _public(done.get("results", [])),
                            "label": meta.get("label", ""), "dc": meta.get("dc")}), 200
        return jsonify({
            "complete": False,
            "pending": sorted(entry["chars"]),
            "results": _public(entry.get("results", [])),
            "label": entry["meta"].get("label", ""),
            "dc": entry["meta"].get("dc"),
            "started_at": entry["started_at"],
        }), 200


@app.route("/dice-request/<request_id>", methods=["DELETE"])
def dice_request_cancel(request_id):
    """Cancel a pending dice request (DM gave up waiting / moved on)."""
    if not _token_ok():
        return "Forbidden", 403
    with _dice_pending_lock:
        _dice_pending.pop(request_id, None)
    _broadcast({"dice_pending": _dice_pending_snapshot(), "dice_request_cancelled": request_id})
    return "", 204


@app.route("/character/<character>", methods=["GET"])
def get_character_sheet(character):
    """Return the markdown content of a PC sheet for the active campaign.

    Used by the phone's Character tab. Resolves the active campaign from
    CAMP_FILE, then reads:
        <DND_CAMPAIGN_ROOT>/campaigns/<campaign>/characters/<character>.md

    Falls back to the global roster at ~/.claude/dnd/characters/<character>.md
    if the campaign-side file is missing — useful when the character was just
    imported but not yet replicated.

    Returns text/markdown so the phone can render in JS without server-side
    dependencies (no `markdown` lib required).
    """
    if not _token_ok():
        return "Forbidden", 403

    safe = re.sub(r"[^A-Za-z0-9 _-]", "", character).strip()[:50]
    if not safe:
        return "Bad character name", 400

    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except Exception:
        camp = ""
    # Sanitise the campaign name with the same allowlist + length cap as the
    # character argument. CAMP_FILE is writable by anyone inside the LAN+token
    # trust boundary (via push_stats.py --set-campaign), so a malicious value
    # here could pivot to arbitrary `<name>.md` reads via os.path.join.
    camp = re.sub(r"[^A-Za-z0-9_-]", "", camp)[:50]

    root = os.environ.get("DND_CAMPAIGN_ROOT", os.path.expanduser("~/.claude/dnd"))
    candidates = []
    if camp:
        candidates.append(os.path.join(root, "campaigns", camp, "characters", f"{safe}.md"))
    candidates.append(os.path.expanduser(f"~/.claude/dnd/characters/{safe}.md"))

    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    body = f.read()
            except Exception as e:
                return f"Read error: {e}", 500
            return Response(body, mimetype="text/markdown; charset=utf-8")

    return f"No sheet found for '{safe}' in campaign '{camp}'", 404


@app.route("/device/approve", methods=["POST"])
def device_approve():
    """DM approves a pending device. Body: {"id": "<device_id>"}"""
    if not _token_ok():
        return "Forbidden", 403
    device_id = str((request.get_json(force=True, silent=True) or {}).get("id", ""))
    with _devices_lock:
        _pending_devices.pop(device_id, None)
        _approved_devices.add(device_id)
    _persist_approved_devices()
    _persist_pending_devices()
    _broadcast({"device_approved": device_id})
    return "", 204


@app.route("/device/deny", methods=["POST"])
def device_deny():
    """DM denies a pending device. Body: {"id": "<device_id>"}"""
    if not _token_ok():
        return "Forbidden", 403
    device_id = str((request.get_json(force=True, silent=True) or {}).get("id", ""))
    with _devices_lock:
        _pending_devices.pop(device_id, None)
        _denied_devices.add(device_id)
    _persist_pending_devices()
    _broadcast({"device_denied": device_id})
    return "", 204


@app.route("/player-input/stage", methods=["POST"])
def stage_input():
    """Stage a player action for review. Broadcasts staged_inputs to all displays.

    Body: {"character": "Mira", "text": "draws her rapier"}
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429

    device_id = request.headers.get("X-DND-Device", "")
    status    = _device_ok(device_id, request.remote_addr)
    if status == "denied":
        return "Forbidden", 403
    if status == "pending":
        return jsonify({"status": "pending"}), 202

    data      = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", ""))[:50].strip()
    text      = _sanitize_input(str(data.get("text", "")))

    if not character or not text:
        return "Bad Request", 400

    with _stats_lock:
        known = {p["name"] for p in _current_stats.get("players", [])}
    if not _char_ok(character, known):
        return "Forbidden", 403

    # In solo mode (1 expected player), skip the manual Ready step and auto-trigger.
    # The same when the DM is waiting on exactly this character (their turn).
    awaited = _awaited_names()
    solo = (_expected_count == 1) or bool(
        awaited and len(awaited) == 1 and awaited[0].lower() == character.lower())

    with _staged_lock:
        _staged[character] = {
            "text":      text,
            "ready":     solo,
            "timestamp": _time.time(),
        }
        snap = _staged_snapshot()

    _broadcast({"staged_inputs": snap})

    if solo:
        _check_auto_trigger()

    return "", 204


# ─── Pass the mic: one speaker at a time, transcribed on this PC ────────────
# A phone claims the mic, records, uploads the clip to /stt, and the text comes
# back into that player's action box to check and send. Only the holder can
# upload; the mic frees itself after _MIC_HOLD_MAX seconds in case a phone
# drops mid-sentence.
_mic_lock = threading.Lock()
_mic = {"holder": "", "device": "", "since": 0.0}
_MIC_HOLD_MAX = 75.0


def _mic_expire_locked() -> None:
    if _mic["holder"] and _time.time() - _mic["since"] > _MIC_HOLD_MAX:
        _mic.update(holder="", device="", since=0.0)


def _stt_state() -> dict:
    if _stt is None:
        return {"on": False, "reason": "speech-to-text module didn't load"}
    return _stt.state()


def _mic_payload() -> dict:
    with _mic_lock:
        _mic_expire_locked()
        holder = _mic["holder"]
    st = _stt_state()
    return {"holder": holder, "on": bool(st.get("on")), "reason": st.get("reason", "")}


def _mic_request_checks():
    """Common gate for the mic routes: token, device approval, party member.

    Returns (character, device_id, None) or (None, None, response)."""
    if not _token_ok():
        return None, None, ("Forbidden", 403)
    device_id = request.headers.get("X-DND-Device", "")
    status = _device_ok(device_id, request.remote_addr)
    if status == "denied":
        return None, None, ("Forbidden", 403)
    if status == "pending":
        return None, None, (jsonify({"status": "pending"}), 202)
    data = request.get_json(force=True, silent=True) if request.is_json else None
    character = str((data or {}).get("character") or request.form.get("character") or "")[:50].strip()
    with _stats_lock:
        known = {p["name"] for p in _current_stats.get("players", [])}
    if not character or character == "Everybody" or not _char_ok(character, known):
        return None, None, ("Pick your character first", 400)
    return character, device_id, None


def _mic_release(character: str) -> bool:
    with _mic_lock:
        if _mic["holder"] and _mic["holder"].lower() == character.lower():
            _mic.update(holder="", device="", since=0.0)
            return True
    return False


@app.route("/mic/claim", methods=["POST"])
def mic_claim():
    """Take the mic. 200 {"holder"} when it is yours, 409 {"holder"} when
    someone else is talking, 503 when speech-to-text is off."""
    if not _rate_ok(request.remote_addr or "?"):
        return "Too Many Requests", 429
    character, device_id, err = _mic_request_checks()
    if err:
        return err
    st = _stt_state()
    if not st.get("on"):
        return jsonify({"error": st.get("reason", "speech-to-text is off")}), 503
    with _mic_lock:
        _mic_expire_locked()
        holder = _mic["holder"]
        if holder and holder.lower() != character.lower():
            return jsonify({"holder": holder}), 409
        _mic.update(holder=character, device=device_id, since=_time.time())
    _broadcast({"mic": _mic_payload()})
    return jsonify({"holder": character}), 200


@app.route("/mic/release", methods=["POST"])
def mic_release():
    character, _device, err = _mic_request_checks()
    if err:
        return err
    if _mic_release(character):
        _broadcast({"mic": _mic_payload()})
    return "", 204


def _stt_hint_words() -> list:
    """Party, turn-order and map names, so Whisper spells them the table's way."""
    with _stats_lock:
        st = dict(_current_stats)
    names = [p.get("name", "") for p in st.get("players", []) if isinstance(p, dict)]
    to = st.get("turn_order") or {}
    names += [n for n in (to.get("order") or []) if isinstance(n, str)]
    m = st.get("map") or {}
    names += [t.get("name", "") for t in (m.get("tokens") or []) if isinstance(t, dict)]
    return [n for n in names if n][:40]


@app.route("/stt", methods=["POST"])
def stt_transcribe():
    """Transcribe the mic holder's clip. Form: audio=<file>, character=<name>.
    Returns {"text": "..."} and frees the mic."""
    character, _device, err = _mic_request_checks()
    if err:
        return err
    with _mic_lock:
        _mic_expire_locked()
        holder = _mic["holder"]
    if not holder or holder.lower() != character.lower():
        return jsonify({"error": "you don't have the mic", "holder": holder}), 409
    try:
        if (request.content_length or 0) > (_stt.MAX_CLIP_BYTES if _stt else 0) + 64 * 1024:
            return jsonify({"error": "that recording is too long"}), 413
        f = request.files.get("audio")
        audio = f.read() if f else b""
        if not audio:
            return jsonify({"error": "no audio arrived"}), 400
        try:
            text = _stt.transcribe(audio, _stt_hint_words())
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 503
        except Exception as e:
            return jsonify({"error": f"couldn't understand that recording ({type(e).__name__})"}), 422
        said = _sanitize_input(text)
        if said.strip():
            with _stt_said_lock:
                _stt_said[character.lower()] = (said.strip(), _time.time())
        return jsonify({"text": said}), 200
    finally:
        if _mic_release(character):
            _broadcast({"mic": _mic_payload()})


def _stt_start() -> None:
    """Load the speech model in the background, then tell every phone."""
    if _stt is not None:
        _stt.load()
    _broadcast({"mic": _mic_payload()})


@app.route("/player-input/ready", methods=["POST"])
def ready_input():
    """Toggle the ready flag for a staged character.

    Body: {"character": "Mira", "ready": true}
    Triggers auto-fire when all expected players are ready.
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429

    device_id = request.headers.get("X-DND-Device", "")
    status    = _device_ok(device_id, request.remote_addr)
    if status == "denied":
        return "Forbidden", 403
    if status == "pending":
        return jsonify({"status": "pending"}), 202

    data      = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", ""))[:50].strip()
    ready     = bool(data.get("ready", True))

    with _staged_lock:
        if character not in _staged:
            return "Not Found", 404
        _staged[character]["ready"] = ready
        snap = _staged_snapshot()

    _broadcast({"staged_inputs": snap})

    if ready:
        _check_auto_trigger()

    return "", 204


@app.route("/player-input/unstage", methods=["POST"])
def unstage_input():
    """Remove a character's staged action (e.g. player wants to edit it).

    Body: {"character": "Mira"}
    """
    if not _token_ok():
        return "Forbidden", 403

    device_id = request.headers.get("X-DND-Device", "")
    if _device_ok(device_id, request.remote_addr) != "approved":
        return "Forbidden", 403

    data      = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", ""))[:50].strip()

    with _staged_lock:
        _staged.pop(character, None)
        snap = _staged_snapshot()

    _broadcast({"staged_inputs": snap})
    return "", 204


@app.route("/player-input/skip", methods=["POST"])
def skip_input():
    """Skip a character's turn — stages a 'skips their turn' entry marked ready.

    Counts toward the auto-trigger threshold and fires auto-trigger if threshold met.
    Body: {"character": "Mira"}
    """
    if not _token_ok():
        return "Forbidden", 403

    device_id = request.headers.get("X-DND-Device", "")
    if _device_ok(device_id, request.remote_addr) != "approved":
        return "Forbidden", 403

    data      = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", ""))[:50].strip()
    if not character:
        return "Bad Request", 400

    with _stats_lock:
        known = {p["name"] for p in _current_stats.get("players", [])}
    if not _char_ok(character, known):
        return "Forbidden", 403

    with _staged_lock:
        _staged[character] = {
            "text":      "skips their turn",
            "ready":     True,
            "timestamp": _time.time(),
        }
        snap = _staged_snapshot()

    _broadcast({"staged_inputs": snap})
    _check_auto_trigger()
    return "", 204


def _rewind_mark(inputs: list) -> None:
    """Snapshot the moment these player actions reach the DM (see rewind.py)."""
    camp = _active_campaign_name()
    if _rewind is None or not camp:
        return
    try:
        with _text_log_lock:
            log = list(_text_log)
        with _tail_lock:
            tail = list(_tail_buffer)
        with _stats_lock:
            stats = json.loads(json.dumps(_current_stats))
        _rewind.snapshot(os.path.dirname(QUEUE_FILE), camp, _find_campaign(camp), inputs,
                         {"log": log, "tail": tail, "stats": stats})
    except Exception as e:   # a failed snapshot must never block the turn
        print(f"[rewind] snapshot failed: {e}", file=sys.stderr)


@app.route("/rewind", methods=["POST"])
def rewind_turn():
    """Undo the last turn: campaign files, TV feed and sidebar go back to the
    moment the last player actions reached the DM. The DM is told with a note
    in front of the next input; those players' phones get their old action
    back in the input box to correct and resend."""
    global _current_stats
    if not _token_ok():
        return "Forbidden", 403
    camp = _active_campaign_name()
    if _rewind is None or not camp:
        return jsonify({"error": "no campaign is running"}), 404
    snap, meta = _rewind.latest(os.path.dirname(QUEUE_FILE), camp)
    if snap is None:
        return jsonify({"error": "there's no turn to rewind"}), 404
    try:
        state = _rewind.restore(snap, _find_campaign(camp))
    except Exception as e:
        return jsonify({"error": f"rewind failed: {e}"}), 500
    with _text_log_lock:
        _text_log.clear()
        _text_log.extend(state.get("log") or [])
    _persist_log()
    with _tail_lock:
        _tail_buffer.clear()
        _tail_buffer.extend(state.get("tail") or [])
    _persist_tail()
    with _stats_lock:
        _current_stats = state.get("stats") or {}
    _persist_stats()
    # Moves and rolls sent for the undone turn go with it.
    with _queue_file_lock:
        try:
            os.remove(QUEUE_FILE)
        except OSError:
            pass
    with _queue_status_lock:
        _queue_status.clear()
    with _dice_pending_lock:
        _dice_pending.clear()
    _rewind.write_note(os.path.dirname(QUEUE_FILE), _rewind.dm_note(meta))
    with _text_log_lock:
        recent = list(_text_log)[-200:]
    with _stats_lock:
        stats = dict(_current_stats)
    _broadcast({"queue_status": [], "dice_pending": {}})
    _broadcast({"rewind": {"inputs": meta.get("inputs", []), "log": recent, "stats": stats}})
    return jsonify({"ok": True, "inputs": meta.get("inputs", [])}), 200


@app.route("/queue/consumed", methods=["POST"])
def queue_consumed():
    """Called by wrapper.py after it injects .input_queue into the PTY.

    Clears the server-side queue_status and broadcasts to all clients so
    the 'Queued — fires on DM Enter' indicator disappears on every display.
    Token required (called from localhost by the wrapper, but checked for
    consistency).
    """
    if not _token_ok():
        return "Forbidden", 403
    with _queue_status_lock:
        _queue_status.clear()
    _broadcast({"queue_status": [], "dm_processing": True})
    return "", 204


@app.route("/player-input/submit-now", methods=["POST"])
def submit_now():
    """Promote .input_queue → .input_trigger for immediate injection.

    Called by the DM or Claude when they want to process queued player actions
    right now rather than waiting for the DM's next CLI Enter press.
    Token required (DM-only action).
    """
    if not _token_ok():
        return "Forbidden", 403
    try:
        content = open(QUEUE_FILE, encoding="utf-8").read()
        os.unlink(QUEUE_FILE)
    except FileNotFoundError:
        return "No queue", 204
    except Exception:
        return "Error", 500
    try:
        with open(TRIGGER_FILE, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        return "Error", 500
    return "", 204


@app.route("/player-input/drain", methods=["POST"])
def drain_player_input():
    """Read and clear the player input queue. Called by check_input.py at turn start.

    Returns the drained entries as JSON, then broadcasts pending_input: [] to
    clear the indicator on all connected displays.
    """
    if not _token_ok():
        return "Forbidden", 403

    with _input_lock:
        drained = list(_input_queue)
        _input_queue.clear()

    _persist_input_queue()
    _broadcast({"pending_input": []})
    return jsonify(drained), 200


@app.route("/stream")
def stream():
    q: queue.Queue = queue.Queue(maxsize=256)
    with _clients_lock:
        _clients.append(q)
        # Register this client's bound character (phones pass ?character=/?char=);
        # the main display passes neither. Drives dice-request phone-vs-screen routing.
        _ch = (request.args.get("character") or request.args.get("char") or "").strip().lower()[:48]
        if _ch:
            _client_chars[q] = _ch

    # First: which server boot this is, so a reconnecting screen knows whether
    # the replay below continues what it has (same epoch) or starts over.
    q.put_nowait({"hello": {"epoch": _epoch}})

    # Send the current scene immediately on connect so the browser
    # starts with the right background even mid-session.
    initial_scene = SCENES[_current_scene_name] | {"name": _current_scene_name}
    q.put_nowait({"scene": initial_scene})

    # Replay recent entries so late-connecting / reconnecting browsers catch up.
    # _text_log is the durable session record (maxlen=2000); replay only the
    # last 200 chunks — the browser renders this batch on join, not the whole
    # log, so a late joiner isn't shown sessions 1..N rendered at them.
    with _text_log_lock:
        recent = list(_text_log)[-200:]
    if recent:
        q.put_nowait({"replay_batch": recent})

    # The TV's layout and voice settings for the active campaign.
    q.put_nowait(_display_payload())

    # Send current stats so the sidebar is populated immediately on (re)connect.
    with _stats_lock:
        if _current_stats:
            q.put_nowait({"stats": dict(_current_stats)})

    # Send current input queue so the pending indicator is accurate on reconnect.
    with _input_lock:
        if _input_queue:
            q.put_nowait({"pending_input": list(_input_queue)})

    # Send current staged inputs so the panel reflects live state on reconnect.
    with _staged_lock:
        if _staged:
            q.put_nowait({"staged_inputs": _staged_snapshot()})

    # Whether the mic works and who has it, so a phone shows the right button.
    q.put_nowait({"mic": _mic_payload()})

    # Send current queue status so the 'Queued' indicator survives page reload.
    with _queue_status_lock:
        if _queue_status:
            q.put_nowait({"queue_status": list(_queue_status)})

    # Send current pending dice requests so the "Waiting on…" badge survives reload.
    snap = _dice_pending_snapshot()
    if snap:
        q.put_nowait({"dice_pending": snap})

    # Replay every active dice_request so phones that connected *after* a DM
    # broadcast still pre-fill their pad and store the request_id. Without this,
    # a late-joining or reloaded phone rolls without a request_id, the roll logs
    # but the pending set never drains, and the "Waiting on…" banner gets stuck.
    with _dice_pending_lock:
        active = [(rid, dict(e["meta"]), sorted(e["chars"])) for rid, e in _dice_pending.items() if e["chars"]]
    for rid, meta, chars in active:
        q.put_nowait({"dice_request": {
            "request_id": rid,
            "characters": chars,
            "character": chars[0] if len(chars) == 1 else "any",
            "onscreen_targets": [c for c in chars if c.lower() != "any" and not _phone_present(c)],
            "spec": meta.get("spec", "1d20"),
            "modifier": meta.get("modifier", 0),
            "advantage": meta.get("advantage", "normal"),
            "label": meta.get("label", ""),
            "dc": meta.get("dc"),
        }})

    # Replay autorun cycle so reconnecting clients resume the countdown from correct elapsed position.
    with _autorun_cycle_lock:
        if _autorun_cycle:
            q.put_nowait({"autorun_cycle": dict(_autorun_cycle)})

    # Replay threshold so the ready counter reflects the correct target on reconnect.
    if _autorun_threshold is not None:
        q.put_nowait({"autorun_threshold": _autorun_threshold})
    if _awaiting:
        q.put_nowait({"awaiting": list(_awaiting)})

    # Send any pending device approval requests so the DM sees them on reconnect.
    with _devices_lock:
        for dev in list(_pending_devices.values()):
            q.put_nowait({"device_request": {"id": dev["id"], "ip": dev["ip"]}})

    def generate():
        try:
            while True:
                try:
                    payload = q.get(timeout=5)
                    yield f"data: {json.dumps(payload)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"   # prevent proxy timeout
        except GeneratorExit:
            with _clients_lock:
                try:
                    _clients.remove(q)
                except ValueError:
                    pass
                _client_chars.pop(q, None)

    resp = Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Transfer-Encoding": "chunked",
        },
    )
    # Force a single authoritative Connection header — Werkzeug otherwise
    # emits both keep-alive (ours) and close (its default), which confuses
    # transparent proxies (e.g. eero mesh routing) into buffering the stream.
    resp.headers["Connection"] = "keep-alive"
    return resp


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Wire audio SFX broadcast now that _broadcast is defined
    if _audio:
        _audio.set_broadcast(_broadcast)
    # Always: with no module or no key it still sets the "off, because…" state.
    threading.Thread(target=_tts_key_watch, daemon=True, name="tts-key-watch").start()
    threading.Thread(target=_kokoro_supervise, daemon=True, name="kokoro").start()
    threading.Thread(target=_stt_start, daemon=True, name="stt-load").start()

    # 127.0.0.1, not "localhost": say exactly which address is bound (the
    # scripts connect there; see display_url.py).
    host = "0.0.0.0" if _LAN_MODE else "127.0.0.1"
    # TLS — only enabled when --tls is explicitly passed; HTTP is the default.
    # Certs are runtime state (persist across plugin updates) → rt(); .scheme is a
    # launch-time marker read by simple shell commands → stays in the code dir.
    _display_dir = os.path.dirname(os.path.abspath(__file__))
    _cert = rt("cert.pem")
    _key  = rt("key.pem")
    ssl_ctx = (_cert, _key) if (_TLS_MODE and os.path.exists(_cert) and os.path.exists(_key)) else None
    scheme  = "https" if ssl_ctx else "http"

    # Write .scheme so push_stats.py / send.py / autorun_wait.py know which to use
    try:
        with open(os.path.join(_display_dir, ".scheme"), "w", encoding="utf-8") as _sf:
            _sf.write(scheme)
    except OSError:
        pass

    if _LAN_MODE:
        print(f"DnD DM Display — LAN mode (0.0.0.0:5001) [{scheme.upper()}]")
        print(f"  Local:  {scheme}://localhost:5001")
        print("  Token stored at:", TOKEN_FILE)
        print("  POST endpoints require X-DND-Token header (send.py/push_stats.py handle this automatically)")
        print()
    else:
        print(f"DnD DM Display — Flask server starting on {scheme}://localhost:5001")
        print(f"Open {scheme}://localhost:5001 in your browser, then Chromecast the tab.")
        print()
    app.run(host=host, port=5001, threaded=True, debug=False, ssl_context=ssl_ctx)
