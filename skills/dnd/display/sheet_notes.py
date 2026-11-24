#!/usr/bin/env python3
"""Hand edits reach Claude with the next input (phone sheet spec D9, AC7).

When a player changes their sheet by hand, the server records a short line
("[Sheet] Havoc: −7 HP (12→5)"). check_input.py and autorun_wait.py print the
pending lines in front of the next player input and clear them. A note alone
never wakes Claude: the lines wait for input.

Repeated taps on one field merge into one line (−1, −1 → "−2 HP (12→10)"),
and edits that cancel out leave no line. Stdlib only: the input scripts import
this without Flask.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

NOTES_NAME = ".sheet_notes.json"
_tlock = threading.Lock()


class _Lock:
    """One reader/writer at a time across processes: the display server adds
    notes while check_input.py / autorun_wait.py take them. A lock file next
    to the notes; a stale one (a crashed process) is broken after 5 s."""

    def __init__(self, rt_dir):
        self.path = Path(rt_dir) / (NOTES_NAME + ".lock")
        self.fd = None

    def __enter__(self):
        _tlock.acquire()
        deadline = time.time() + 3.0
        while True:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except (FileExistsError, PermissionError):
                try:
                    if time.time() - self.path.stat().st_mtime > 5.0:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    return self               # go ahead rather than hang the table
                time.sleep(0.02)
            except OSError:
                return self                   # read-only dir: thread lock only

    def __exit__(self, *exc):
        try:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
                try:
                    self.path.unlink()
                except OSError:
                    pass
        finally:
            _tlock.release()
        return False
MINUS = "−"
ARROW = "→"


def _path(rt_dir) -> Path:
    return Path(rt_dir) / NOTES_NAME


def _load(rt_dir) -> list:
    try:
        data = json.loads(_path(rt_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(rt_dir, entries: list) -> None:
    p = _path(rt_dir)
    if not entries:
        try:
            p.unlink()
        except OSError:
            pass
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".sheet_notes.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)
    os.replace(tmp, str(p))


def _signed(n: int) -> str:
    return ("+" if n > 0 else MINUS) + str(abs(n))


def _key(name: str, r: dict) -> str:
    field = r.get("field")
    sub = r.get("level") or r.get("kind") or r.get("condition") or r.get("item") or r.get("note") or ""
    return f"{name.lower()}|{field}|{str(sub).lower()}"


def _entries_from(name: str, r: dict) -> list:
    """One stored entry per value a result changed (gold: one per coin)."""
    field = r.get("field")
    if not r.get("ok") or field is None:
        return []
    if field == "gold":
        out = []
        before, after = r.get("before") or {}, r.get("after") or {}
        for coin in (r.get("delta") or {}):
            out.append({"key": f"{name.lower()}|gold|{coin}", "name": name, "field": "gold",
                        "sub": coin, "before": before.get(coin, 0), "after": after.get(coin, 0)})
        return out
    sub = r.get("level") or r.get("kind") or r.get("condition") or r.get("item") or r.get("note") or ""
    return [{"key": _key(name, r), "name": name, "field": field, "sub": sub,
             "before": r.get("before"), "after": r.get("after")}]


def add(rt_dir, name: str, results: list) -> None:
    """Record a player's applied changes (results from sheet.apply)."""
    with _Lock(rt_dir):
        entries = _load(rt_dir)
        for r in results or []:
            for e in _entries_from(name, r):
                old = next((x for x in entries if x["key"] == e["key"]), None)
                if old is None:
                    entries.append(e)
                else:
                    old["after"] = e["after"]
        entries = [x for x in entries if x["before"] != x["after"]]
        _save(rt_dir, entries)


def line(e: dict) -> str:
    """The short line Claude reads for one entry."""
    n, f, b, a, sub = e["name"], e["field"], e["before"], e["after"], e.get("sub")
    try:
        d = int(a) - int(b)
    except (TypeError, ValueError):
        d = 0
    if f == "hp":
        text = f"{_signed(d)} HP ({b}{ARROW}{a})"
    elif f == "temp_hp":
        text = f"{_signed(d)} temp HP ({b}{ARROW}{a})"
    elif f == "slot":
        text = f"{_signed(d)} level-{sub} slot ({b}{ARROW}{a} left)"
    elif f == "hit_dice":
        text = f"{_signed(d)} hit {'die' if abs(d) == 1 else 'dice'} ({b}{ARROW}{a} left)"
    elif f == "death_saves":
        kind = "success" if str(sub).startswith("success") else "failure"
        text = f"{_signed(d)} death save {kind}{'' if abs(d) == 1 else 's'} ({b}{ARROW}{a})"
    elif f == "inspiration":
        text = "gained inspiration" if a else "used inspiration"
    elif f == "condition":
        text = f"{sub} {'added' if a else 'ended'}"
    elif f == "item":
        text = f"{_signed(d)} {sub} ({b}{ARROW}{a})"
    elif f == "gold":
        text = f"{_signed(d)} {sub} ({b}{ARROW}{a})"
    elif f == "note":
        text = f"note {'added' if a else 'removed'}: {sub}"
    else:
        text = f"{f} {b}{ARROW}{a}"
    return f"[Sheet] {n}: {text}"


def peek(rt_dir) -> list:
    """The pending lines, without clearing them."""
    with _Lock(rt_dir):
        return [line(e) for e in _load(rt_dir)]


def take_lines(rt_dir) -> list:
    """The pending lines, cleared (read once, by the next input)."""
    with _Lock(rt_dir):
        entries = _load(rt_dir)
        _save(rt_dir, [])
    return [line(e) for e in entries]


def clear(rt_dir) -> None:
    """Forget pending notes (a rewind restored the files they describe)."""
    with _Lock(rt_dir):
        _save(rt_dir, [])
