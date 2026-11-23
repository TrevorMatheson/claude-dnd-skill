"""Per-campaign display settings: the TV layout, the voice settings and the
scene pictures.

Stored as <campaign>/display.json (display foundation spec D6, D7, D17):

    {"version": 1,
     "layout": {"preset": "story", "left": ["party"], "right": ["quests", "clock"]},
     "voice":  {"npc_engine": "kokoro", "volume": 1.0, "speed": 1.0,
                "read_player_actions": false},
     "scenes": {"generate": true, "style": "painterly"}}

A campaign without the file gets the Story preset. The file is written only
when the host changes something, so older campaigns stay untouched.
"""
import copy
import json
import os
import tempfile

VERSION = 1
FILENAME = "display.json"

# Module ids a side panel can hold (D7).
MODULES = ("party", "turns", "map", "quests", "clock")
MAX_PER_PANEL = 3

PRESETS = {
    "story":    {"left": ["party"], "right": ["quests", "clock"]},
    "tactical": {"left": ["party"], "right": ["turns", "map"]},
    "minimal":  {"left": [],        "right": []},
}

NPC_ENGINES = ("kokoro", "gemini")
_VOICE_DEFAULTS = {"npc_engine": "kokoro", "volume": 1.0, "speed": 1.0,
                   "read_player_actions": False}
# Scene pictures (scene backgrounds spec D4, D6, D17): whether new places get
# a generated picture (on by default; it needs a Gemini key too), and the one
# art style added to every request so a campaign's pictures belong together.
SCENE_STYLES = {
    "painterly": {
        "label": "Painterly, muted",
        "prompt": "painterly fantasy illustration, muted colours, soft atmospheric light, dark edges",
    },
}
_SCENE_DEFAULTS = {"generate": True, "style": "painterly"}
_VOLUME_RANGE = (0.0, 1.0)
_SPEED_RANGE = (0.5, 2.0)


def default() -> dict:
    """The settings of a campaign that has none saved: the Story preset."""
    return {
        "version": VERSION,
        "layout": {"preset": "story", **copy.deepcopy(PRESETS["story"])},
        "voice": dict(_VOICE_DEFAULTS),
        "scenes": dict(_SCENE_DEFAULTS),
    }


def _clean_panel(value, used: set) -> list:
    out = []
    for mid in value if isinstance(value, list) else []:
        if isinstance(mid, str) and mid in MODULES and mid not in used:
            used.add(mid)
            out.append(mid)
        if len(out) == MAX_PER_PANEL:
            break
    return out


def _number(value, lo: float, hi: float, fallback: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    return float(min(hi, max(lo, value)))


def validate(cfg) -> dict:
    """A clean copy of `cfg`: known modules only, each at most once, at most
    three per panel, voice values in range. Anything unusable falls back to
    the defaults, so the result is always valid."""
    base = default()
    if not isinstance(cfg, dict):
        return base

    layout = cfg.get("layout") if isinstance(cfg.get("layout"), dict) else {}
    used: set = set()
    left = _clean_panel(layout.get("left"), used)
    right = _clean_panel(layout.get("right"), used)
    preset = layout.get("preset")
    if "left" not in layout and "right" not in layout and preset in PRESETS:
        left = list(PRESETS[preset]["left"])
        right = list(PRESETS[preset]["right"])
    if not layout:
        left, right = base["layout"]["left"], base["layout"]["right"]
    match = next((n for n, p in PRESETS.items() if p["left"] == left and p["right"] == right), None)
    base["layout"] = {"preset": match or "custom", "left": left, "right": right}

    voice = cfg.get("voice") if isinstance(cfg.get("voice"), dict) else {}
    v = base["voice"]
    if voice.get("npc_engine") in NPC_ENGINES:
        v["npc_engine"] = voice["npc_engine"]
    v["volume"] = _number(voice.get("volume"), *_VOLUME_RANGE, v["volume"])
    v["speed"] = _number(voice.get("speed"), *_SPEED_RANGE, v["speed"])
    if isinstance(voice.get("read_player_actions"), bool):
        v["read_player_actions"] = voice["read_player_actions"]
    if isinstance(voice.get("narrator"), str) and voice["narrator"].strip():
        v["narrator"] = voice["narrator"].strip()

    scenes = cfg.get("scenes") if isinstance(cfg.get("scenes"), dict) else {}
    sc = base["scenes"]
    if isinstance(scenes.get("generate"), bool):
        sc["generate"] = scenes["generate"]
    if isinstance(scenes.get("style"), str) and scenes["style"] in SCENE_STYLES:
        sc["style"] = scenes["style"]
    return base


def load(campaign_dir) -> dict:
    """The campaign's saved settings, cleaned; the defaults when there is no
    file or it can't be read. Never writes."""
    try:
        with open(os.path.join(str(campaign_dir), FILENAME), encoding="utf-8") as f:
            return validate(json.load(f))
    except (OSError, ValueError):
        return default()


def save(campaign_dir, cfg) -> dict:
    """Clean `cfg` and write it atomically (temp file, then os.replace).
    Returns what was written."""
    clean = validate(cfg)
    folder = str(campaign_dir)
    fd, tmp = tempfile.mkstemp(prefix=".display-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2)
        os.replace(tmp, os.path.join(folder, FILENAME))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return clean
