#!/usr/bin/env python3
"""The cast: who speaks in which voice (display foundation D18, D19, D21).

Voices live with the characters, not in display.json:
  * NPCs:      `- **Voice:** <kokoro>` in the NPC's `### Name` section of
               <campaign>/npcs.md, plus `- **Gemini voice:** <name>` when the
               NPC also has a Gemini voice.
  * PCs:       `- **Voice:** <kokoro>` under `## Identity` in
               <campaign>/characters/<name>.md.
  * Narrator:  display.json voice.narrator, else state.md `tts_voice:`, else
               tts.DEFAULT_NARRATOR.

Every NPC that speaks gets its own Kokoro voice: the DM picks one when it
creates the NPC (`cast.py voices --free`, then `cast.py set --npc`), and the
display server assigns a free one to any NPC that speaks without one. No NPC
is ever given the narrator's or a player character's voice.

CLI:
  python cast.py voices --free -c <campaign>
  python cast.py set -c <campaign> --npc|--pc <name> <voice>
  python cast.py set -c <campaign> --narrator <voice>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.normpath(os.path.join(_HERE, "..", "scripts"))
for _p in (_HERE, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tts                      # noqa: E402  voice tables only; no network on import
import display_config           # noqa: E402
from paths import find_campaign  # noqa: E402

_lock = threading.RLock()

# Subheadings that templates/npcs.md puts under each NPC: not cast members.
_NOT_NPCS = {"personality", "relationships", "notes"}

_HEADING = re.compile(r"^(#{1,3})[ \t]+(.*?)[ \t]*$")
# `- **Voice:** x`, `Voice: x`, `**Voice**: x` … but not `Gemini voice:`.
_VOICE_LINE = re.compile(r"^([ \t]*(?:[-*+][ \t]*)?\**[ \t]*)voice[ \t]*\**[ \t]*:[ \t]*\**[ \t]*:?\**[ \t]*(.*)$",
                         re.IGNORECASE)
_GEMINI_LINE = re.compile(r"^[ \t]*(?:[-*+][ \t]*)?\**[ \t]*gemini[ \t]+voice[ \t]*\**[ \t]*:", re.IGNORECASE)
_TOKEN = re.compile(r"^[A-Za-z][\w.+:-]*")
_STATE_VOICE = re.compile(r"^\s*tts_voice:\s*([A-Za-z_]+)\s*$", re.MULTILINE)


# ── voices ──────────────────────────────────────────────────────────────────

def kokoro_voices() -> list:
    return list(tts.KOKORO_VOICES)


def is_kokoro(v) -> bool:
    """A Kokoro voice or an "a+b" blend of two (D20)."""
    return isinstance(v, str) and tts.is_kokoro(v)


def is_gemini(v) -> bool:
    return isinstance(v, str) and v in tts.VALID_VOICES and v not in tts.KOKORO_VOICES


def same_voice(a, b) -> bool:
    """"a+b" and "b+a" are one voice: same parts, same 50/50 mix."""
    if not a or not b:
        return False
    pa, pb = tts.blend_parts(a), tts.blend_parts(b)
    if pa and pb:
        return frozenset(pa) == frozenset(pb)
    return a == b


def parts_of(v) -> tuple:
    return tts.blend_parts(v) or ((v,) if v else ())


def describe(v: str) -> str:
    """'British female' for bf_emma."""
    accent = {"b": "British", "a": "American"}.get(v[:1], "Other")
    sex = {"f": "female", "m": "male"}.get(v[1:2], "")
    return f"{accent} {sex}".strip()


# ── file helpers ────────────────────────────────────────────────────────────

def _camp(campaign) -> Path:
    return find_campaign(str(campaign))


def _read(path: Path):
    """(lines without EOLs, eol) or (None, "\\n") when missing/unreadable."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None, "\n"
    try:
        text = raw.decode("utf-8")      # a BOM stays on line 1 and is written back
    except UnicodeDecodeError:
        print(f"[cast] {path.name} is not UTF-8: left unchanged, voices not saved",
              file=sys.stderr, flush=True)
        return None, "\n"
    eol = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n").split("\n"), eol


def _write(path: Path, lines: list, eol: str) -> None:
    data = eol.join(lines).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_BOM = "\ufeff"


def _is_voice_value(raw: str) -> bool:
    """A Voice: line holds a voice id, a placeholder `<...>` or nothing. Prose
    such as `Voice: gravelly baritone` is a description, not ours to touch."""
    v = raw.strip().strip("*").strip()
    return not v or v.startswith("<") or len(v.split()) == 1


def _value(raw: str) -> str:
    m = _TOKEN.match(raw.strip().strip("*").strip())
    return m.group(0) if m else ""


# ── npcs.md ─────────────────────────────────────────────────────────────────

def _npc_sections(lines: list) -> list:
    """[{name, start, end, voice_idx, voice, gemini_idx}] — `start` is the
    heading line; the section runs to the next heading of any level."""
    heads = []
    for i, line in enumerate(lines):
        m = _HEADING.match(line.lstrip(_BOM))
        if m:
            heads.append((i, len(m.group(1)), m.group(2)))
    out = []
    for n, (i, level, name) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        if level != 3 or not name:
            continue
        sec = {"name": name, "start": i, "end": end, "voice_idx": None, "voice": "",
               "gemini_idx": None}
        for j in range(i + 1, end):
            if sec["gemini_idx"] is None and _GEMINI_LINE.match(lines[j]):
                sec["gemini_idx"] = j
                continue
            vm = _VOICE_LINE.match(lines[j])
            if vm and sec["voice_idx"] is None and _is_voice_value(vm.group(2)):
                sec["voice_idx"] = j
                sec["voice"] = _value(vm.group(2))
        # The template's own subheadings (### Personality / Relationships /
        # Notes) and its `### <Name>` placeholder are not cast members, unless
        # the section carries a real voice: then an NPC really has that name.
        odd = name.startswith("<") or name.lower() in _NOT_NPCS
        if odd and not tts.valid_voice(sec["voice"]):
            continue
        out.append(sec)
    return out


def _find(sections: list, name: str):
    """Exact (case-insensitive) heading first, then a unique first-word match."""
    key = " ".join(str(name).split()).lower()
    if not key:
        return None
    for s in sections:
        if " ".join(s["name"].split()).lower() == key:
            return s
    if len(key.split()) != 1:
        # "Captain Vex" is not "Captain Harrow": a full name that only shares
        # a first word (often a title) is a different NPC.
        return None
    hits = [s for s in sections if s["name"].split()[0].lower() == key]
    return hits[0] if len(hits) == 1 else None


def _npcs_path(campaign) -> Path:
    return _camp(campaign) / "npcs.md"


def _npc_lines(campaign):
    lines, eol = _read(_npcs_path(campaign))
    return (lines if lines is not None else []), eol, lines is not None


# ── player characters ───────────────────────────────────────────────────────

def _pc_files(campaign) -> list:
    d = _camp(campaign) / "characters"
    try:
        return sorted(p for p in d.glob("*.md") if p.is_file())
    except OSError:
        return []


def _pc_info(path: Path):
    lines, _ = _read(path)
    if lines is None:
        return None
    name = path.stem
    for line in lines:
        m = _HEADING.match(line)
        if m and len(m.group(1)) == 1:
            name = m.group(2)
            break
    idx = _pc_voice_idx(lines)
    voice = _value(_VOICE_LINE.match(lines[idx]).group(2)) if idx is not None else ""
    return {"name": name, "voice": voice, "path": path}


def _pc_voice_idx(lines: list):
    """The character's voice line: under `## Identity` when the sheet has one
    (a `Voice:` description elsewhere on the sheet is prose), else anywhere."""
    ident = next((i for i, l in enumerate(lines)
                  if re.match(r"^##[ \t]+Identity\b", l, re.IGNORECASE)), None)
    if ident is not None:
        end = next((j for j in range(ident + 1, len(lines))
                    if re.match(r"^#{1,2}[ \t]", lines[j])), len(lines))
        rng = range(ident + 1, end)
    else:
        rng = range(len(lines))
    for i in rng:
        vm = _VOICE_LINE.match(lines[i])
        if vm and _is_voice_value(vm.group(2)):
            return i
    return None


def _pcs(campaign) -> list:
    return [i for i in (_pc_info(p) for p in _pc_files(campaign)) if i]


def pc_voices(campaign) -> set:
    return {p["voice"] for p in _pcs(campaign) if p["voice"]}


# ── narrator ────────────────────────────────────────────────────────────────

def narrator_voice(campaign, default: Optional[str] = tts.DEFAULT_NARRATOR) -> Optional[str]:
    """display.json voice.narrator, else state.md tts_voice, else `default`."""
    folder = _camp(campaign)
    try:
        with open(folder / display_config.FILENAME, encoding="utf-8") as f:
            v = ((json.load(f) or {}).get("voice") or {}).get("narrator")
        if isinstance(v, str) and v.strip():
            return v.strip()
    except (OSError, ValueError, AttributeError):
        pass
    lines, _ = _read(folder / "state.md")
    if lines:
        m = _STATE_VOICE.search("\n".join(lines))
        if m:
            return m.group(1)
    return default


# ── the pool ────────────────────────────────────────────────────────────────

def heard_narrator(campaign) -> str:
    """The Kokoro voice that actually reads the narration. A saved Gemini
    narrator (from before Kokoro) gives way to the default, as the display
    server does, so that default is the one to keep away from NPCs."""
    v = narrator_voice(campaign)
    return v if is_kokoro(v) else tts.DEFAULT_NARRATOR


def _reserved(campaign) -> set:
    """Voices no NPC may have: the narrator's and every player character's."""
    return {narrator_voice(campaign), heard_narrator(campaign)} | pc_voices(campaign)


def _npc_held(sections: list) -> list:
    return [s["voice"] for s in sections if is_kokoro(s["voice"])]


def free_voices(campaign) -> list:
    lines, _, _ = _npc_lines(campaign)
    taken = _reserved(campaign) | set(_npc_held(_npc_sections(lines)))
    return [v for v in kokoro_voices() if v not in taken]


def _pick(campaign, sections: list, prefer: Optional[str] = None, avoid=()) -> str:
    """The voice for a new NPC: `prefer` when it is free, else the first free
    voice; once the pool is used up, the least-used NPC voice (never the
    narrator's or a player character's)."""
    reserved = _reserved(campaign) | set(avoid)
    held = _npc_held(sections)
    free = [v for v in kokoro_voices() if v not in reserved and v not in held]
    if prefer in free:
        return prefer
    if free:
        return free[0]
    allowed = [v for v in kokoro_voices() if v not in reserved]
    if not allowed:                       # every voice reserved: should never happen
        allowed = [v for v in kokoro_voices() if v != narrator_voice(campaign)] or kokoro_voices()
    if tts.kokoro_blend_supported() is True:
        blend = _new_blend(allowed, held)
        if blend:
            return blend
    return min(allowed, key=lambda v: (held.count(v), allowed.index(v)))


def _new_blend(allowed: list, held: list) -> str:
    """A blend "a+b" no NPC has yet: two allowed voices of the same group
    (af / am / bf / bm), preferring parts that appear in the fewest blends so
    the new voice sounds least like the others. "" when none is left."""
    used_pairs = {frozenset(p) for p in (tts.blend_parts(v) for v in held) if p}
    parts_used: dict = {}
    for pair in used_pairs:
        for v in pair:
            parts_used[v] = parts_used.get(v, 0) + 1
    best = None
    for i, a in enumerate(allowed):
        for b in allowed[i + 1:]:
            if a[:2] != b[:2] or frozenset((a, b)) in used_pairs:
                continue
            score = (parts_used.get(a, 0) + parts_used.get(b, 0), i)
            if best is None or score < best[0]:
                best = (score, f"{a}+{b}")
    return best[1] if best else ""


def _voice_line(indent_from: str, voice: str) -> str:
    indent = re.match(r"^[ \t]*", indent_from or "").group(0)
    return f"{indent}- **Voice:** {voice}"


def _put_voice(lines: list, sec: dict, voice: str) -> None:
    """Set the section's Voice: line (insert one after its first block of
    lines when it has none)."""
    if sec["voice_idx"] is not None:
        lines[sec["voice_idx"]] = _voice_line(lines[sec["voice_idx"]], voice)
        return
    j = sec["start"] + 1
    while j < sec["end"] and lines[j].strip():
        j += 1
    lines.insert(j, f"- **Voice:** {voice}")


def _append_stub(lines: list, name: str, voice: str) -> None:
    while lines and not lines[-1].strip():
        lines.pop()
    if lines:
        lines.append("")
    lines += [f"### {name}", f"- **Voice:** {voice}", ""]


# ── public API ──────────────────────────────────────────────────────────────

def npc_voices(campaign, name: str, engine: str = "kokoro") -> dict:
    """Make sure the NPC has its own Kokoro voice (writing npcs.md when it
    doesn't) and return {"voice": kokoro, "gemini": gemini-or-""}.

    With engine "gemini" the NPC also gets a Gemini voice (the least-used one,
    never a Gemini narrator's), written as `- **Gemini voice:**` (D21)."""
    name = " ".join(str(name or "").split())
    if not name:
        return {"voice": "", "gemini": ""}
    with _lock:
        res = _npc_kokoro(campaign, name)
        if engine == "gemini" and res.get("voice"):
            res["gemini"] = _npc_gemini(campaign, name, res.get("gemini", ""))
        return res


def _gemini_of(lines: list, sec: dict) -> str:
    if sec is None or sec["gemini_idx"] is None:
        return ""
    v = _value(lines[sec["gemini_idx"]].split(":", 1)[-1])
    return v if is_gemini(v) else ""


def _npc_gemini(campaign, name: str, current: str) -> str:
    """The NPC's Gemini voice, assigned and written back when it has none or
    holds the narrator's."""
    narrator = narrator_voice(campaign)
    if is_gemini(current) and current != narrator:
        return current
    lines, eol, exists = _npc_lines(campaign)
    if not exists:
        return ""
    sections = _npc_sections(lines)
    sec = _find(sections, name)
    if sec is None:
        return ""
    held = [_gemini_of(lines, s) for s in sections if s is not sec]
    pool = sorted(v for v in tts.VALID_VOICES if is_gemini(v) and v != narrator)
    if not pool:
        return ""
    voice = min(pool, key=lambda v: (held.count(v), pool.index(v)))
    line = f"- **Gemini voice:** {voice}"
    if sec["gemini_idx"] is not None:
        lines[sec["gemini_idx"]] = line
    elif sec["voice_idx"] is not None:
        lines.insert(sec["voice_idx"] + 1, line)
    else:
        lines.insert(sec["start"] + 1, line)
    _write(_npcs_path(campaign), lines, eol)
    return voice


def _npc_kokoro(campaign, name: str) -> dict:
    """npc_voices' Kokoro half (D19): see npc_voices."""
    with _lock:
        path = _npcs_path(campaign)
        lines, eol, exists = _npc_lines(campaign)
        if not exists and path.exists():
            # Present but unreadable (not UTF-8): never overwrite it.
            return {"voice": _pick(campaign, []), "gemini": ""}
        sections = _npc_sections(lines)
        sec = _find(sections, name)
        gemini = ""
        if sec is not None and sec["gemini_idx"] is not None:
            gemini = _value(lines[sec["gemini_idx"]].split(":", 1)[-1])
        if sec is not None and is_kokoro(sec["voice"]) and not _must_move(campaign, sections, sec):
            return {"voice": sec["voice"], "gemini": gemini}
        if sec is not None and is_gemini(sec["voice"]):
            old = sec["voice"]
            others = [s for s in sections if s is not sec]
            voice = _pick(campaign, others, prefer=tts.GEMINI_TO_KOKORO.get(old))
            _put_voice(lines, sec, voice)
            if sec["gemini_idx"] is None:
                lines.insert(sec["voice_idx"] + 1, _voice_line(lines[sec["voice_idx"]], old)
                             .replace("**Voice:**", "**Gemini voice:**"))
            gemini = gemini or old
        elif sec is not None:
            # No voice, a placeholder, or one it may not keep (the narrator's,
            # a player character's, or another NPC's while free voices remain).
            avoid = (sec["voice"],) if is_kokoro(sec["voice"]) else ()
            voice = _pick(campaign, [s for s in sections if s is not sec], avoid=avoid)
            _put_voice(lines, sec, voice)
        else:
            voice = _pick(campaign, sections)
            _append_stub(lines, name, voice)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write(path, lines, eol)
        return {"voice": voice, "gemini": gemini}


def _must_move(campaign, sections: list, sec: dict) -> bool:
    """An NPC's Kokoro voice it may not keep: reserved for the narrator or a
    player character, or shared with another NPC while free voices remain."""
    v = sec["voice"]
    reserved = _reserved(campaign)
    if v in reserved or any(p in reserved for p in (tts.blend_parts(v) or ())):
        return True
    if any(s is not sec and same_voice(s["voice"], v) for s in sections):
        held = set(_npc_held(sections))
        return any(f not in held and f not in reserved for f in kokoro_voices())
    return False


def _reassign_npcs_holding(campaign, voice: str) -> list:
    """Give a new voice to every NPC that holds `voice` (now reserved)."""
    moved = []
    with _lock:
        lines, eol, exists = _npc_lines(campaign)
        if not exists:
            return moved
        changed = False
        while True:
            sections = _npc_sections(lines)
            hit = next((s for s in sections if voice in parts_of(s["voice"])), None)
            if hit is None:
                break
            new = _pick(campaign, [s for s in sections if s is not hit], avoid=(voice,))
            _put_voice(lines, hit, new)
            moved.append((hit["name"], new))
            changed = True
        if changed:
            _write(_npcs_path(campaign), lines, eol)
    return moved


def _set_npc(campaign, name: str, voice: str) -> None:
    if not is_kokoro(voice):
        raise ValueError(f"{voice!r} is not a Kokoro voice (see: cast.py voices --free)")
    bp = tts.blend_parts(voice)
    if bp and bp[0][:2] != bp[1][:2]:
        raise ValueError(f"{voice}: a blend mixes two voices of one group (af, am, bf or bm)")
    parts = set(bp or (voice,))
    if parts & {narrator_voice(campaign), heard_narrator(campaign)}:
        raise ValueError(f"{voice} is or contains the narrator's voice")
    if parts & pc_voices(campaign):
        raise ValueError(f"{voice} is or contains a player character's voice")
    with _lock:
        lines, eol, exists = _npc_lines(campaign)
        if not exists and _npcs_path(campaign).exists():
            raise ValueError("npcs.md is not readable as UTF-8")
        sections = _npc_sections(lines)
        sec = _find(sections, name)
        holder = next((s for s in sections if same_voice(s["voice"], voice) and s is not sec), None)
        if holder is not None:
            raise ValueError(f"{voice} already belongs to {holder['name']}")
        if sec is None:
            _append_stub(lines, " ".join(str(name).split()), voice)
        else:
            old = sec["voice"]
            _put_voice(lines, sec, voice)
            if is_gemini(old) and sec["gemini_idx"] is None:
                lines.insert(sec["voice_idx"] + 1, f"- **Gemini voice:** {old}")
        _npcs_path(campaign).parent.mkdir(parents=True, exist_ok=True)
        _write(_npcs_path(campaign), lines, eol)


def _pc_path(campaign, name: str) -> Optional[Path]:
    key = " ".join(str(name).split()).lower()
    infos = _pcs(campaign)
    for i in infos:
        if " ".join(i["name"].split()).lower() == key or i["path"].stem.lower() == key:
            return i["path"]
    first = [i for i in infos if i["name"].split() and i["name"].split()[0].lower() == key]
    return first[0]["path"] if len(first) == 1 else None


def _set_pc(campaign, name: str, voice: str) -> None:
    if voice not in tts.KOKORO_VOICES:
        raise ValueError(f"{voice!r} is not a Kokoro voice (player characters get a single voice)")
    if voice == narrator_voice(campaign):
        raise ValueError(f"{voice} is the narrator's voice")
    path = _pc_path(campaign, name)
    if path is None:
        raise ValueError(f"no character file for {name!r}")
    other = next((p for p in _pcs(campaign) if p["voice"] == voice and p["path"] != path), None)
    if other is not None:
        raise ValueError(f"{voice} already belongs to {other['name']}")
    with _lock:
        lines, eol = _read(path)
        if lines is None:
            raise ValueError(f"{path.name} is not readable as UTF-8")
        idx = _pc_voice_idx(lines)
        if idx is not None:
            lines[idx] = _voice_line(lines[idx], voice)
        else:
            ident = next((i for i, l in enumerate(lines)
                          if re.match(r"^##[ \t]+Identity\b", l, re.IGNORECASE)), None)
            if ident is None:
                ident = next((i for i, l in enumerate(lines) if _HEADING.match(l)), -1)
            j = ident + 1
            while j < len(lines) and lines[j].strip() and not _HEADING.match(lines[j]):
                j += 1
            lines.insert(j, f"- **Voice:** {voice}")
        _write(path, lines, eol)
    # D22: a player character's voice is never an NPC's.
    _reassign_npcs_holding(campaign, voice)


def _set_narrator(campaign, voice: str) -> None:
    if not tts.valid_voice(voice) or tts.blend_parts(voice):
        raise ValueError(f"{voice!r} is not a narrator voice")
    folder = _camp(campaign)
    with _lock:
        cfg = display_config.load(folder)
        cfg.setdefault("voice", {})["narrator"] = voice
        display_config.save(folder, cfg)
    _reassign_npcs_holding(campaign, voice)
    heard = heard_narrator(campaign)       # a Gemini narrator is read by the default
    if heard != voice:
        _reassign_npcs_holding(campaign, heard)


def set_voice(campaign, kind: str, name: Optional[str], voice: str) -> None:
    """kind: "npc" | "pc" | "narrator". Raises ValueError when not allowed."""
    voice = str(voice or "").strip()
    if kind == "npc":
        if not str(name or "").strip():
            raise ValueError("an NPC name is needed")
        _set_npc(campaign, name, voice)
    elif kind == "pc":
        if not str(name or "").strip():
            raise ValueError("a character name is needed")
        _set_pc(campaign, name, voice)
    elif kind == "narrator":
        _set_narrator(campaign, voice)
    else:
        raise ValueError(f"unknown kind {kind!r}")


def cast_list(campaign) -> dict:
    """Everyone and their voice, for the laptop page's cast list. Read-only."""
    lines, _, _ = _npc_lines(campaign)
    npcs = []
    for s in _npc_sections(lines):
        g = ""
        if s["gemini_idx"] is not None:
            g = _value(lines[s["gemini_idx"]].split(":", 1)[-1])
        npcs.append({"name": s["name"], "voice": s["voice"] if tts.valid_voice(s["voice"]) else "",
                     "gemini": g})
    return {
        "narrator": narrator_voice(campaign),
        "narrator_heard": heard_narrator(campaign),
        "pcs": [{"name": p["name"], "voice": p["voice"]} for p in _pcs(campaign)],
        "npcs": npcs,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────

def _cli_voices(args) -> int:
    voices = free_voices(args.campaign) if args.free else kokoro_voices()
    if not voices:
        print("No free voices left: a new NPC gets a blend of two voices when the Kokoro "
              "helper supports it, else the least-used NPC voice.")
        return 0
    groups: dict = {}
    for v in voices:
        groups.setdefault(describe(v), []).append(v)
    for label in sorted(groups):
        names = ", ".join(f"{v} ({tts.KOKORO_VOICES[v]})" for v in groups[label])
        print(f"{label}: {names}")
    return 0


def _cli_set(args) -> int:
    if args.npc is not None:
        kind, name = "npc", args.npc
    elif args.pc is not None:
        kind, name = "pc", args.pc
    else:
        kind, name = "narrator", None
    try:
        set_voice(args.campaign, kind, name, args.voice)
    except ValueError as exc:
        print(f"cast.py: {exc}", file=sys.stderr)
        return 2
    who = name if name else "the narrator"
    print(f"{who}: {args.voice}")
    return 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    p = argparse.ArgumentParser(prog="cast.py", description="Voices for the narrator, PCs and NPCs.")
    sub = p.add_subparsers(dest="cmd", required=True)
    pv = sub.add_parser("voices", help="list Kokoro voices by accent and sex")
    pv.add_argument("--free", action="store_true", help="only voices nobody holds")
    pv.add_argument("-c", "--campaign", required=True)
    ps = sub.add_parser("set", help="give a character a voice")
    ps.add_argument("-c", "--campaign", required=True)
    who = ps.add_mutually_exclusive_group(required=True)
    who.add_argument("--npc", metavar="NAME")
    who.add_argument("--pc", metavar="NAME")
    who.add_argument("--narrator", action="store_true")
    ps.add_argument("voice")
    args = p.parse_args(argv)
    return _cli_voices(args) if args.cmd == "voices" else _cli_set(args)


if __name__ == "__main__":
    sys.exit(main())
