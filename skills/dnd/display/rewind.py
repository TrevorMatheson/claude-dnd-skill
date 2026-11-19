"""rewind.py — undo the last turn.

When the players' actions go to the DM (the display writes them to the DM's
inbox), the display takes a snapshot: the campaign's files (state.md, the
character sheets, tracker, battle map, logs) plus the TV feed and the sidebar
stats as they were at that moment. Rewinding puts all of that back, so the turn
the DM misunderstood never happened — its narration, its rolls, its HP changes —
and the player can send a corrected action.

Nothing is thrown away: the files the rewind replaces are moved to an
"abandoned" folder beside the snapshot, so the old branch can still be read.

Only the latest few snapshots are kept (KEEP). One rewind goes back one turn;
the snapshot it used is then spent.

The DM (Claude) can't have its memory rolled back, so a rewind also leaves a
note (NOTE_FILE) that autorun_wait.py / check_input.py put in front of the next
player input: the DM reads that the turn was undone before resolving the
corrected action.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

KEEP = 5
NOTE_NAME = ".rewind_note"
_EXTS = {".md", ".json", ".jsonl"}


def _campaign_files(camp_dir: Path) -> list:
    """The campaign's own small text files: top level + characters/.
    Backups, maps and anything else are left alone."""
    out = []
    for p in sorted(camp_dir.iterdir()):
        if p.is_file() and p.suffix in _EXTS and ".backup" not in p.name:
            out.append(p)
    chars = camp_dir / "characters"
    if chars.is_dir():
        out += sorted(p for p in chars.iterdir() if p.is_file() and p.suffix == ".md")
    return out


def _root(rt_dir: Path, campaign: str) -> Path:
    return Path(rt_dir) / "rewind" / campaign


def snapshot(rt_dir: Path, campaign: str, camp_dir: Path, inputs: list, app_state: dict) -> str:
    """Save the moment before a turn. `inputs` is [(character, text), ...]."""
    sid = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    base = _root(rt_dir, campaign) / sid
    files = base / "files"
    files.mkdir(parents=True, exist_ok=True)
    for p in _campaign_files(Path(camp_dir)):
        rel = p.relative_to(camp_dir)
        (files / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, files / rel)
    meta = {"id": sid, "campaign": campaign, "time": time.time(),
            "inputs": [{"character": c, "text": t} for c, t in inputs]}
    (base / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (base / "app_state.json").write_text(json.dumps(app_state), encoding="utf-8")
    # Keep only the newest KEEP live snapshots (spent ones keep their abandoned
    # branch until they age out too).
    snaps = sorted(d for d in _root(rt_dir, campaign).iterdir() if d.is_dir())
    for old in snaps[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    return sid


def latest(rt_dir: Path, campaign: str):
    """(path, meta) of the newest unspent snapshot, or (None, None)."""
    root = _root(rt_dir, campaign)
    if not root.is_dir():
        return None, None
    for d in sorted((d for d in root.iterdir() if d.is_dir()), reverse=True):
        if (d / "meta.json").exists() and not (d / "spent").exists():
            try:
                return d, json.loads((d / "meta.json").read_text(encoding="utf-8"))
            except ValueError:
                continue
    return None, None


def restore(snap: Path, camp_dir: Path) -> dict:
    """Put the campaign files back as they were and return the app state.

    Files changed or created since the snapshot are moved to snap/abandoned/."""
    camp_dir = Path(camp_dir)
    files = snap / "files"
    abandoned = snap / "abandoned"
    saved = {p.relative_to(files) for p in files.rglob("*") if p.is_file()}
    current = {p.relative_to(camp_dir) for p in _campaign_files(camp_dir)}
    for rel in sorted(current | saved):
        cur = camp_dir / rel
        if cur.exists():
            (abandoned / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cur, abandoned / rel)
        if rel in saved:
            cur.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(files / rel, cur)
        elif cur.exists():
            cur.unlink()          # created during the undone turn
    (snap / "spent").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    try:
        return json.loads((snap / "app_state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def dm_note(meta: dict) -> str:
    """What the DM reads in front of the next input after a rewind."""
    acts = "; ".join(f'{i["character"]}: "{i["text"]}"' for i in meta.get("inputs", []))
    return ("[[REWIND: the table undid the last turn. Everything after these player "
            f"actions was discarded — narration, rolls and changes — and the campaign "
            f"files and the TV were restored to just before them: {acts}. Re-read "
            "state.md and the character sheets before relying on anything from that "
            "turn. The input below replaces it; resolve it fresh.]]")


def write_note(rt_dir: Path, text: str) -> None:
    (Path(rt_dir) / NOTE_NAME).write_text(text, encoding="utf-8")


def take_note(rt_dir: Path) -> str:
    """Read and clear the pending rewind note ("" when there is none)."""
    p = Path(rt_dir) / NOTE_NAME
    try:
        text = p.read_text(encoding="utf-8")
        p.unlink()
        return text.strip()
    except OSError:
        return ""
