#!/usr/bin/env python3
"""The character sheet as the phone shows it (phone sheet spec D5).

parse(text) reads a character's .md file into sections. Every `## ` section
is either parsed into its own key or, when it is unknown or can't be read,
kept as plain text in `plain`, so nothing in the file is ever left out and
older characters work with no migration.
"""
from __future__ import annotations

import re
from typing import Optional

ABILS = ("STR", "DEX", "CON", "INT", "WIS", "CHA")
_ABIL_NAMES = {
    "strength": "STR", "dexterity": "DEX", "constitution": "CON",
    "intelligence": "INT", "wisdom": "WIS", "charisma": "CHA",
}

# Which parsed key each `## ` heading feeds, by keyword (first match wins).
_SECTION_KEYS = (
    ("identity", "identity"),
    ("ability", "abilities"),
    ("combat", "combat"),
    ("saving", "saves"),
    ("skill", "skills"),
    ("attack", "attacks"),
    ("slot", "slots"),
    ("spell", "spells"),
    ("feature", "features"),
    ("trait", "features"),
    ("equipment", "inventory"),
    ("inventory", "inventory"),
    ("backstory", "notes"),
    ("notes", "notes"),
)

# Sections whose fields a player may change by hand (D7). Ability scores,
# level, features, proficiencies and max HP are not among them.
EDITABLE_SECTIONS = ("combat", "slots", "inventory", "notes")

# `**Key:** value`; the value runs to the next `| **Key:**` or `**Key:**`, so a
# value with its own bar ("Successes: 1 | Failures: 2") stays whole.
_BOLD = re.compile(r"\*\*\s*([^*:]+?)\s*:\s*\*\*\s*(.*?)(?=\s*\|?\s*\*\*[^*]+?:\*\*|\s*\|\s*$|$)")
_INT = re.compile(r"[+\-−]?\d+")
_COINS = ("pp", "gp", "ep", "sp", "cp")
_COIN_RE = re.compile(r"(\d[\d,]*)\s*(pp|gp|ep|sp|cp)\b", re.IGNORECASE)
_QTY_RE = re.compile(r"^(.*?)\s*(?:[×x]\s*(\d+)|\((\d+)\))\s*$")
_LEAD_QTY = re.compile(r"^(\d+)\s+(.+)$")


def _int(s) -> Optional[int]:
    if s is None:
        return None
    m = _INT.search(str(s).replace("−", "-"))
    return int(m.group(0).replace("−", "-")) if m else None


def _strip_md(s: str) -> str:
    s = re.sub(r"\*\*|__|`", "", s)
    s = re.sub(r"(?<!\w)[*_](.+?)[*_](?!\w)", r"\1", s)
    return s.strip()


# ── splitting ───────────────────────────────────────────────────────────────

def _split(text: str):
    """(name, preamble lines, [(title, [lines])]) from the markdown."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    name, pre, sections, cur = "", [], [], None
    for line in text.split("\n"):
        if line.startswith("# ") and not name and cur is None:
            name = line[2:].strip()
            continue
        if line.startswith("## "):
            cur = (line[3:].strip(), [])
            sections.append(cur)
            continue
        (cur[1] if cur is not None else pre).append(line)
    return name, pre, sections


def _key_for(title: str) -> Optional[str]:
    t = title.lower()
    for word, key in _SECTION_KEYS:
        if word in t:
            return key
    return None


def _table(lines):
    """Rows of a markdown table (header first), separator rows dropped."""
    rows = []
    for line in lines:
        s = line.strip()
        if not s.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) or c == "" for c in cells) and any(cells):
            continue
        rows.append(cells)
    return rows


def _bold_pairs(lines) -> dict:
    out = {}
    for line in lines:
        for m in _BOLD.finditer(line):
            key = m.group(1).strip().lower()
            if key not in out:
                out[key] = m.group(2).strip().strip("|").strip()
    return out


# ── section parsers ─────────────────────────────────────────────────────────

def _identity(lines):
    pairs = _bold_pairs(lines)
    if not pairs:
        raise ValueError("no fields")
    return pairs


def _abilities(lines):
    rows = _table(lines)
    if len(rows) < 2:
        raise ValueError("no table")
    head = [h.upper()[:3] for h in rows[0]]
    out = {}
    for h, cell in zip(head, rows[1]):
        if h in ABILS:
            m = re.match(r"\s*(\d+)\s*(?:\(\s*([+\-−]?\d+)\s*\))?", cell)
            if not m:
                raise ValueError(f"bad score {cell!r}")
            score = int(m.group(1))
            mod = _int(m.group(2)) if m.group(2) else (score - 10) // 2
            out[h] = {"score": score, "mod": mod}
    if len(out) != 6:
        raise ValueError("missing abilities")
    return out


def _combat(lines):
    pairs = _bold_pairs(lines)
    if "hp" not in pairs:
        raise ValueError("no HP line")
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)", pairs["hp"])
    if not m:
        raise ValueError(f"bad HP {pairs['hp']!r}")
    out = {"hp": {"current": int(m.group(1)), "max": int(m.group(2))}}
    out["temp_hp"] = _int(pairs.get("temp hp")) or 0
    ac = pairs.get("ac", "")
    out["ac"] = _int(ac)
    note = re.search(r"\((.*)\)", ac)
    out["ac_note"] = note.group(1).strip() if note else ""
    out["initiative"] = _int(pairs.get("initiative"))
    out["speed"] = pairs.get("speed", "").strip()
    hd = pairs.get("hit dice", "")
    hm = re.search(r"(\d+)\s*d\s*(\d+)", hd)
    rem = re.search(r"remaining\s*:?\s*(\d+)", hd, re.IGNORECASE)
    if hm:
        total = int(hm.group(1))
        out["hit_dice"] = {"die": f"{total}d{hm.group(2)}", "total": total,
                           "remaining": int(rem.group(1)) if rem else total}
    else:
        out["hit_dice"] = None
    ds = pairs.get("death saves", "")
    sm = re.search(r"success\w*\s*:?\s*(\d+)", ds, re.IGNORECASE)
    fm = re.search(r"fail\w*\s*:?\s*(\d+)", ds, re.IGNORECASE)
    out["death_saves"] = {"successes": int(sm.group(1)) if sm else 0,
                          "failures": int(fm.group(1)) if fm else 0}
    insp = pairs.get("inspiration", "").strip().lower()
    out["inspiration"] = insp.startswith(("yes", "y", "true", "1", "✓", "heroic")) if insp else False
    conds = pairs.get("conditions", "").strip()
    out["conditions"] = ([c.strip() for c in re.split(r"[,;]", conds)
                          if c.strip() and c.strip().lower() not in ("none", "—", "-")]
                         if conds else [])
    return out


def _saves(lines):
    rows = _table(lines)
    if len(rows) < 2:
        raise ValueError("no table")
    out = {}
    for h, cell in zip(rows[0], rows[1]):
        k = h.upper()[:3]
        if k in ABILS:
            b = _int(cell)
            if b is None:
                raise ValueError(f"bad save {cell!r}")
            out[k] = {"bonus": b, "proficient": "*" in cell or "✓" in cell}
    if not out:
        raise ValueError("no saves")
    return out


def _skills(lines):
    rows = _table(lines)
    if len(rows) < 2:
        raise ValueError("no table")
    out = []
    for r in rows[1:]:
        if len(r) < 3 or not r[0]:
            raise ValueError(f"bad skill row {r!r}")
        bonus = _int(r[2])
        if bonus is None:
            raise ValueError(f"bad bonus {r!r}")
        prof_cell = r[3] if len(r) > 3 else ""
        note = ""
        nm = re.search(r"\((.*)\)", prof_cell)
        if nm:
            note = nm.group(1).strip()
        if len(r) > 4 and r[4]:
            note = (note + " " + r[4]).strip()
        prof = bool(re.search(r"✓|yes|\bx\b|expert", prof_cell, re.IGNORECASE))
        out.append({"name": _strip_md(r[0]), "ability": r[1].upper()[:3], "bonus": bonus,
                    "proficient": prof, "note": note})
    return out


def _attacks(lines):
    rows = _table(lines)
    if not rows:
        return []
    out = []
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        if len(r) < 3 or not r[0].strip():
            raise ValueError(f"bad attack row {r!r}")
        out.append({"name": _strip_md(r[0]), "bonus": _int(r[1]) if len(r) > 1 else None,
                    "damage": r[2].strip() if len(r) > 2 else "",
                    "type": r[3].strip() if len(r) > 3 else "",
                    "notes": _strip_md(r[4]) if len(r) > 4 else ""})
    return out


def _slots(lines):
    rows = _table(lines)
    out = []
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        lvl = _int(r[0])
        total = _int(r[1]) if len(r) > 1 else None
        if lvl is None:
            raise ValueError(f"bad slot row {r!r}")
        if total is None:
            continue               # the template's empty "1st | | |" row
        used = _int(r[2]) if len(r) > 2 else 0
        out.append({"level": lvl, "total": total, "used": used or 0, "label": r[0].strip()})
    return out


_CAST_ABIL = re.compile(r"spellcasting ability\**\s*:?\**\s*([A-Za-z]+)", re.IGNORECASE)
_CAST_ATK = re.compile(r"spell attack\**\s*:?\**\s*(?:bonus\s*)?([+\-−]?\d+)", re.IGNORECASE)
_CAST_DC = re.compile(r"(?:spell\s+)?save DC\**\s*:?\**\s*(\d+)", re.IGNORECASE)


def _spellcasting(text: str):
    a = _CAST_ABIL.search(text)
    k = _CAST_ATK.search(text)
    d = _CAST_DC.search(text)
    if not (a or k or d):
        return None
    ab = a.group(1) if a else ""
    ab = _ABIL_NAMES.get(ab.lower(), ab.upper()[:3]) if ab else ""
    return {"ability": ab, "attack": _int(k.group(1)) if k else None,
            "dc": int(d.group(1)) if d else None}


def _spell_names(rest: str) -> list:
    rest = re.sub(r"\([^)]*\)", "", rest)          # (cantrips), (1/Long Rest …)
    names = []
    for part in re.split(r"[,;]", rest):
        p = _strip_md(part).strip(" .")
        p = re.split(r"\s+(?:—|–|-)\s+|\s+at level\b|\s+once\b|\s+on self\b|:", p)[0].strip(" .")
        p = re.sub(r"^(?:and|or)\s+", "", p)
        if p.lower() in _ABIL_NAMES or p.upper() in ABILS:
            continue                           # "…, Charisma" is the casting ability
        if p and len(p) <= 40 and re.match(r"^[A-Z]", p) and not re.search(r"\b(?:more|choose|yet)\b", p):
            names.append(p)
    return names


def _spells(lines):
    out = []
    for line in lines:
        s = line.strip()
        if not _BULLET.match(s):
            continue                       # prose or an italic aside
        body = _BULLET.sub("", s, count=1).strip()
        m = re.match(r"\*\*(.+?)\*\*\s*(.*)$", body)
        if m:
            label = m.group(1).strip().rstrip(":").strip()
            rest = m.group(2).lstrip(":").strip()
        else:
            label, rest = "", body
        out.append({"label": _strip_md(label), "names": _spell_names(rest), "text": _strip_md(body)})
    return out


def _features(lines):
    groups, cur = [], None
    for line in lines:
        if line.startswith("### "):
            cur = {"title": line[4:].strip(), "text": ""}
            groups.append(cur)
            continue
        if cur is None:
            if not line.strip():
                continue
            cur = {"title": "", "text": ""}
            groups.append(cur)
        cur["text"] += line + "\n"
    for g in groups:
        g["text"] = g["text"].strip()
    return [g for g in groups if g["title"] or g["text"]]


def _item(text: str) -> Optional[dict]:
    t = _strip_md(text).strip(" .")
    if not t or t in ("-", "—"):
        return None
    m = _QTY_RE.match(t)
    if m and m.group(1):
        return {"name": m.group(1).strip(), "qty": int(m.group(2) or m.group(3))}
    m = _LEAD_QTY.match(t)
    if m and not re.match(r"\d+\s*(?:gp|sp|cp|pp|ep)\b", t, re.IGNORECASE):
        return {"name": m.group(2).strip(), "qty": int(m.group(1))}
    return {"name": t, "qty": 1}


_BULLET = re.compile(r"^[-*+]\s+")


def _is_list(text: str, parts: list) -> bool:
    """A comma list of items, not a sentence that happens to have commas."""
    if re.search(r"\s[—–]\s|[.!?]\s", re.sub(r"\([^)]*\)", "", text)):
        return False
    return all(len(p) <= 60 for p in parts)


def _split_items(s: str) -> list:
    # Split on commas that are not inside parentheses.
    parts, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [p for p in (x.strip() for x in parts) if p]


def coins(text: str) -> dict:
    out = {}
    for n, c in _COIN_RE.findall(text or ""):
        c = c.lower()
        out[c] = out.get(c, 0) + int(n.replace(",", ""))
    return out


def _inventory(lines):
    groups, cur = [], None
    currency_text = ""
    for line in lines:
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^\*\*(.+?):\*\*\s*(.*)$", s) or re.match(r"^\*\*(.+?)\*\*:\s*(.*)$", s)
        if m:
            label, rest = m.group(1).strip(), m.group(2).strip()
            if label.lower().startswith("currency") or label.lower() in ("gold", "money", "coins"):
                currency_text = rest
                cur = None
                continue
            cur = {"label": label, "items": []}
            groups.append(cur)
            if rest:
                parts = _split_items(rest)
                if len(parts) > 1 and not _is_list(rest, parts):
                    parts = [rest]
                cur["items"].extend(i for i in (_item(x) for x in parts) if i)
            continue
        if _BULLET.match(s):
            body = _BULLET.sub("", s, count=1).strip()
            if cur is None:
                cur = {"label": "", "items": []}
                groups.append(cur)
            sub = re.match(r"^(\w[\w' ]{0,24}):\s+(.*)$", body)   # "Weapons: Sickle, 2 Daggers"
            listed = sub.group(2) if sub else body
            parts = _split_items(listed)
            if len(parts) > 1 and _is_list(listed, parts):
                cur["items"].extend(i for i in (_item(x) for x in parts) if i)
            else:
                it = _item(body)
                if it:
                    cur["items"].append(it)
    return {"groups": [g for g in groups if g["items"] or g["label"]],
            # Coins in parentheses are notes ("owes a 2 sp fine"), not coins held.
            "currency": coins(re.sub(r"\([^)]*\)", "", currency_text)), "currency_text": currency_text}


_PARSERS = {
    "identity": _identity, "abilities": _abilities, "combat": _combat,
    "saves": _saves, "skills": _skills, "attacks": _attacks, "slots": _slots,
    "spells": _spells, "features": _features, "inventory": _inventory,
}


def parse(text: str) -> dict:
    """The sheet as a dict. See the module docstring; never raises."""
    name, pre, sections = _split(text or "")
    out: dict = {"name": name, "sections": [t for t, _ in sections], "plain": []}
    pre_pairs = _bold_pairs(pre)
    if pre_pairs.get("player"):
        out["player"] = pre_pairs["player"]
    full = "\n".join(text.replace("\r\n", "\n").split("\n")) if text else ""
    for title, lines in sections:
        key = _key_for(title)
        raw = "\n".join(lines).strip()
        if key == "notes":
            if "notes" in out:
                out["notes"] += "\n\n" + raw
            else:
                out["notes"] = raw
            continue
        if key is None or key in out:
            out["plain"].append({"title": title, "text": raw})
            continue
        try:
            out[key] = _PARSERS[key](lines)
        except Exception:
            out["plain"].append({"title": title, "text": raw})
            continue
        if key == "slots" or key == "spells":
            sc = _spellcasting(raw)
            if sc and not out.get("spellcasting"):
                out["spellcasting"] = sc
    if "spellcasting" not in out:
        out["spellcasting"] = _spellcasting(full)
    return out


def editable(sheet: dict) -> list:
    """The D7 sections this sheet has that a player may change by hand."""
    return [k for k in EDITABLE_SECTIONS if sheet.get(k) is not None]


# ── files ───────────────────────────────────────────────────────────────────

def _cast():
    import cast            # noqa: WPS433  (imported late: cast pulls in tts)
    return cast


def find(campaign: Optional[str], name: str):
    """(path, path_ok): the campaign's file for `name` (path_ok True), else the
    global roster copy (path_ok False, read-only), else (None, False)."""
    if campaign:
        try:
            p = _cast()._pc_path(campaign, name)
        except Exception:
            p = None
        if p is not None and p.is_file():
            return p, True
    try:
        from paths import characters_dir
        roster = characters_dir()
        key = " ".join(str(name).split()).lower()
        for p in sorted(roster.glob("*.md")) if roster.is_dir() else []:
            if p.stem.lower() == key:
                return p, False
    except Exception:
        pass
    return None, False


def read_text(path) -> str:
    """The file as text: UTF-8, or legacy GBK read losslessly."""
    import utf8io
    return utf8io.read_text(path)


def load(campaign: Optional[str], name: str):
    """(sheet dict, path_ok) or (None, reason) when there's no readable file."""
    path, ok = find(campaign, name)
    if path is None:
        where = f"campaign {campaign!r}" if campaign else "the roster (no campaign loaded)"
        return None, f"No character sheet for {name!r} in {where}"
    try:
        text = read_text(path)
    except (OSError, ValueError) as e:
        return None, f"{path.name} can't be read: {e}"
    return parse(text), ok


# ── changes (D6, D7, D10) ───────────────────────────────────────────────────
# Every change is a difference, never a new total, so a heal and damage that
# arrive together both land. One entry names one character and one resource:
#   {"name":"Havoc","damage":7}  {"heal":5}  {"hp":-3}  {"temp_hp":5}
#   {"slot":2,"delta":-1}  (delta counts slots left: -1 spends one)
#   {"hit_dice":-1}  {"item":"Potion of Healing","delta":-1}  {"gold":{"gp":-5}}
#   {"condition":"Poisoned","on":true}  {"death_saves":"failure","delta":1}
#   {"inspiration":false}  {"note":"owes Brann 5 gp","on":true}

RESOURCE_KEYS = ("damage", "heal", "hp", "temp_hp", "slot", "hit_dice", "item",
                 "gold", "condition", "death_saves", "inspiration", "note")
# What a player may change by hand (D7) and what Claude's turn call may (D6).
# Ability scores, level, features, proficiencies and max HP are in neither.
PLAYER_FIELDS = frozenset(("hp", "temp_hp", "slot", "hit_dice", "death_saves", "inspiration",
                           "condition", "item", "gold", "note"))
DM_FIELDS = frozenset(RESOURCE_KEYS)
_ENTRY_EXTRAS = ("name", "delta", "on")


class ChangeError(ValueError):
    """A change that can't be applied; the message says why."""


def resource_of(entry) -> str:
    """The one resource key an entry changes, or raise ChangeError."""
    if not isinstance(entry, dict):
        raise ChangeError("a change must be an object")
    other = [k for k in entry if k not in RESOURCE_KEYS and k not in _ENTRY_EXTRAS]
    if other:
        raise ChangeError(f"{other[0]} can't be changed this way")
    keys = [k for k in entry if k in RESOURCE_KEYS]
    if len(keys) != 1:
        raise ChangeError("each change names exactly one resource" if keys else "no resource named")
    return keys[0]


def _as_int(v, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
        raise ChangeError(f"{what} must be a whole number")
    if abs(v) > 100000:
        raise ChangeError(f"{what} is too large")
    return int(v)


def validate(entry, fields=DM_FIELDS) -> str:
    """Check an entry's shape and that `fields` allows it; return its key."""
    key = resource_of(entry)
    if key not in fields:
        raise ChangeError(f"{key} can't be changed by hand")
    v = entry[key]
    if key in ("damage", "heal"):
        if _as_int(v, key) < 0:
            raise ChangeError(f"{key} can't be negative")
    elif key in ("hp", "temp_hp", "hit_dice"):
        _as_int(v, key)
    elif key == "slot":
        lvl = _as_int(v, "slot level")
        if not 1 <= lvl <= 9:
            raise ChangeError("slot level must be 1 to 9")
        _as_int(entry.get("delta"), "delta")
    elif key == "item":
        if not isinstance(v, str) or not v.strip() or len(v) > 80:
            raise ChangeError("item must be a name")
        _as_int(entry.get("delta"), "delta")
    elif key == "gold":
        if not isinstance(v, dict) or not v or any(c not in _COINS for c in v):
            raise ChangeError('gold must be coins like {"gp": -5}')
        for c, n in v.items():
            _as_int(n, c)
    elif key == "condition":
        if not isinstance(v, str) or not v.strip() or len(v) > 40:
            raise ChangeError("condition must be a name")
        if not isinstance(entry.get("on"), bool):
            raise ChangeError("condition needs on: true or false")
    elif key == "death_saves":
        if v not in ("success", "failure", "successes", "failures"):
            raise ChangeError("death_saves is success or failure")
        _as_int(entry.get("delta"), "delta")
    elif key == "inspiration":
        if not isinstance(v, bool):
            raise ChangeError("inspiration is true or false")
    elif key == "note":
        if not isinstance(v, str) or not v.strip() or len(v) > 300:
            raise ChangeError("note must be text")
        if not isinstance(entry.get("on"), bool):
            raise ChangeError("note needs on: true or false")
    return key


# ── line editing ────────────────────────────────────────────────────────────

class _Doc:
    """The file as lines, with its sections found by their `## ` heading."""

    def __init__(self, text: str):
        self.bom = text.startswith("﻿")
        body = text[1:] if self.bom else text
        self.eol = "\r\n" if "\r\n" in body else "\n"
        self.trailing = body.endswith("\n")
        self.lines = body.replace("\r\n", "\n").split("\n")
        if self.trailing:
            self.lines.pop()

    def text(self) -> str:
        out = self.eol.join(self.lines) + (self.eol if self.trailing else "")
        return ("﻿" if self.bom else "") + out

    def section(self, key: str):
        """(start, end) line indexes of the body of the first section that
        feeds `key`, or None."""
        for i, ln in enumerate(self.lines):
            if ln.startswith("## ") and _key_for(ln[3:].strip()) == key:
                j = i + 1
                while j < len(self.lines) and not self.lines[j].startswith("## "):
                    j += 1
                return i + 1, j
        return None

    def need(self, key: str, title: str):
        sec = self.section(key)
        if sec is None:
            raise ChangeError(f"the sheet has no {title} section")
        return sec

    def find(self, key: str, pattern):
        sec = self.section(key)
        if sec is None:
            return None, None
        for i in range(*sec):
            m = pattern.search(self.lines[i])
            if m:
                return i, m
        return None, None

    def append_to(self, key: str, line: str) -> None:
        """Add a line after a section's last non-blank line."""
        start, end = self.section(key)
        at = end
        while at > start and not self.lines[at - 1].strip():
            at -= 1
        self.lines.insert(at, line)


def _bold_val(label: str):
    """`**Label:** <value>`: the value runs to a `|` or the line's end."""
    return re.compile(r"(\*\*\s*" + label + r"\s*:\s*\*\*\s*)([^|\n]*?)(\s*(?:\||$))", re.IGNORECASE)


_HP = re.compile(r"(\*\*\s*HP\s*:\s*\*\*\s*)(\d+)(\s*/\s*)(\d+)", re.IGNORECASE)
_TEMP = re.compile(r"(\*\*\s*Temp HP\s*:\s*\*\*\s*)(\d+)", re.IGNORECASE)
_HD = re.compile(r"(\*\*\s*Hit Dice\s*:\s*\*\*\s*)(\d+)\s*d\s*(\d+)([^\n]*)", re.IGNORECASE)
_HD_REM = re.compile(r"(remaining\s*:?\s*)(\d+)", re.IGNORECASE)
_DS = re.compile(r"\*\*\s*Death Saves\s*:\s*\*\*", re.IGNORECASE)
_INSP = _bold_val("Inspiration")
_CONDS = _bold_val("Conditions")
_CURRENCY = re.compile(r"^(\s*(?:[-*+]\s+)?\*\*\s*currency\s*:?\s*\*\*\s*:?\s*)(.*)$", re.IGNORECASE)
_YES = ("yes", "y", "true", "1", "✓", "heroic")


def _r(field, before, after, **kw):
    out = {"field": field, "before": before, "after": after, "ok": True}
    out.update(kw)
    return out


def _hp(doc):
    i, m = doc.find("combat", _HP)
    if i is None:
        raise ChangeError("the sheet has no readable HP line")
    return i, int(m.group(2)), int(m.group(4))


def _set_hp(doc, i, cur):
    doc.lines[i] = _HP.sub(lambda m: f"{m.group(1)}{cur}{m.group(3)}{m.group(4)}", doc.lines[i], count=1)


def _temp(doc):
    i, m = doc.find("combat", _TEMP)
    return (i, int(m.group(2))) if i is not None else (None, 0)


def _set_temp(doc, val):
    i, _ = _temp(doc)
    if i is not None:
        doc.lines[i] = _TEMP.sub(lambda m: f"{m.group(1)}{val}", doc.lines[i], count=1)
        return
    hi, _, _ = _hp(doc)
    doc.lines[hi] = doc.lines[hi].rstrip() + f" | **Temp HP:** {val}"


def _do_hp(doc, e, key):
    i, cur, mx = _hp(doc)
    if key == "damage":
        dmg = int(e["damage"])
        _, temp = _temp(doc)
        absorbed = min(temp, dmg)
        res = []
        if absorbed:
            _set_temp(doc, temp - absorbed)
            res.append(_r("temp_hp", temp, temp - absorbed, delta=-absorbed))
        new = max(0, cur - (dmg - absorbed))
        _set_hp(doc, i, new)
        res.append(_r("hp", cur, new, delta=new - cur, max=mx))
        return res
    delta = int(e["heal"]) if key == "heal" else int(e["hp"])
    new = max(0, min(mx, cur + delta))
    _set_hp(doc, i, new)
    return [_r("hp", cur, new, delta=new - cur, max=mx)]


def _do_temp(doc, e, key):
    _hp(doc)                                   # Temp HP sits beside the HP line
    _, temp = _temp(doc)
    new = max(0, temp + int(e["temp_hp"]))
    _set_temp(doc, new)
    return [_r("temp_hp", temp, new, delta=new - temp)]


def _do_slot(doc, e, key):
    lvl, delta = int(e["slot"]), int(e["delta"])
    start, end = doc.need("slots", "Spell Slots")
    for i in range(start, end):
        ln = doc.lines[i]
        if not ln.strip().startswith("|"):
            continue
        cells = ln.strip().strip("|").split("|")
        if len(cells) < 3 or _int(cells[0]) != lvl:
            continue
        total, used = _int(cells[1]), _int(cells[2]) or 0
        if total is None:
            continue
        new_used = max(0, min(total, used - delta))
        raw = ln.split("|")                  # raw[3] is the Used cell
        old = raw[3]
        lead = old[:len(old) - len(old.lstrip())] or " "
        trail = old[len(old.rstrip()):] or " "
        raw[3] = f"{lead}{new_used}{trail}"
        doc.lines[i] = "|".join(raw)
        return [_r("slot", total - used, total - new_used, delta=used - new_used, level=lvl, total=total)]
    raise ChangeError(f"no level-{lvl} spell slots on the sheet")


def _do_hd(doc, e, key):
    i, m = doc.find("combat", _HD)
    if i is None:
        raise ChangeError("the sheet has no Hit Dice line")
    total = int(m.group(2))
    rm = _HD_REM.search(m.group(4))
    rem = int(rm.group(2)) if rm else total
    new = max(0, min(total, rem + int(e["hit_dice"])))
    ln = doc.lines[i]
    if rm:
        tail = _HD_REM.sub(lambda x: f"{x.group(1)}{new}", m.group(4), count=1)
        doc.lines[i] = ln[:m.start(4)] + tail + ln[m.end(4):]
    else:
        doc.lines[i] = ln[:m.end(3)] + f" (remaining: {new})" + ln[m.end(3):]
    return [_r("hit_dice", rem, new, delta=new - rem, total=total)]


def _do_ds(doc, e, key):
    kind = "successes" if str(e["death_saves"]).startswith("success") else "failures"
    i, _ = doc.find("combat", _DS)
    if i is None:
        doc.need("combat", "Combat Stats")
        doc.append_to("combat", "- **Death Saves:** Successes: 0 | Failures: 0")
        i, _ = doc.find("combat", _DS)
    word = re.compile(r"(" + ("Success" if kind == "successes" else "Fail") + r"\w*\s*:?\s*)(\d+)", re.IGNORECASE)
    m = word.search(doc.lines[i])
    if not m:
        doc.lines[i] = doc.lines[i].rstrip() + f" | {kind.capitalize()}: 0"
        m = word.search(doc.lines[i])
    before = int(m.group(2))
    new = max(0, min(3, before + int(e["delta"])))
    doc.lines[i] = doc.lines[i][:m.start(2)] + str(new) + doc.lines[i][m.end(2):]
    return [_r("death_saves", before, new, delta=new - before, kind=kind)]


def _do_insp(doc, e, key):
    want = bool(e["inspiration"])
    i, m = doc.find("combat", _INSP)
    before = False
    if i is not None:
        before = m.group(2).strip().lower().startswith(_YES)
        doc.lines[i] = doc.lines[i][:m.start(2)] + ("yes" if want else "no") + doc.lines[i][m.end(2):]
    else:
        doc.need("combat", "Combat Stats")
        doc.append_to("combat", f"- **Inspiration:** {'yes' if want else 'no'}")
    return [_r("inspiration", before, want)]


def _do_cond(doc, e, key):
    name, on = str(e["condition"]).strip(), bool(e["on"])
    i, m = doc.find("combat", _CONDS)
    cur = []
    if i is not None:
        cur = [c.strip() for c in re.split(r"[,;]", m.group(2))
               if c.strip() and c.strip().lower() not in ("none", "—", "-")]
    has = any(c.lower() == name.lower() for c in cur)
    if on and not has:
        new = cur + [name]
    elif not on and has:
        new = [c for c in cur if c.lower() != name.lower()]
    else:
        new = cur
    val = ", ".join(new) if new else "none"
    if i is not None:
        doc.lines[i] = doc.lines[i][:m.start(2)] + val + doc.lines[i][m.end(2):]
    elif new:
        doc.need("combat", "Combat Stats")
        doc.append_to("combat", f"- **Conditions:** {val}")
    return [_r("condition", has, on, condition=name, changed=has != on)]


def _item_re(name: str):
    """One whole item on an inventory line: it starts after a bullet, a comma
    or a `**Label:**`, and ends at a comma or the line's end. Groups: 1 the
    name, 2 a parenthetical, 3 the quantity."""
    return re.compile(r"(?:(?<=^)|(?<=[-*+]\s)|(?<=,\s)|(?<=,)|(?<=\*\*\s)|(?<=\*\*))\s*("
                      + re.escape(name) + r")(\s*\([^)]*\))?(?:\s*[×x]\s*(\d+))?\s*(?=,|$)",
                      re.IGNORECASE)


def _inventory_names(doc, start, end) -> list:
    try:
        inv = _inventory(doc.lines[start:end])
    except Exception:
        return []
    names = []
    for g in inv["groups"]:
        for it in g["items"]:
            n = re.sub(r"\s*\([^)]*\)\s*$", "", it["name"]).strip()
            if n and n.lower() not in (x.lower() for x in names):
                names.append(n)
    return names


def _resolve_item(doc, start, end, name: str):
    """The inventory's own name for `name`: an exact match, else the one item
    whose name starts with it ("Potion" → "Potion of Healing"). None when
    there's no such item; ChangeError when it's ambiguous."""
    names = _inventory_names(doc, start, end)
    key = name.lower()
    exact = [n for n in names if n.lower() == key]
    if exact:
        return exact[0]
    pre = [n for n in names if n.lower().startswith(key + " ") or n.lower().startswith(key)]
    if len(pre) == 1:
        return pre[0]
    if len(pre) > 1:
        raise ChangeError(f"which {name}: " + ", ".join(pre[:4]) + "?")
    return None


def _remove_item(doc, i, m):
    ln = doc.lines[i]
    before, after = ln[:m.start()], ln[m.end():]
    if re.fullmatch(r"\s*[-*+]\s*", before) and not after.strip(" .,;"):
        del doc.lines[i]                       # the whole bullet was this item
        return
    if re.match(r"\s*,", after):              # first or middle of a comma list
        after = re.sub(r"^\s*,\s*", "", after, count=1)
        before = before.rstrip() + " " if before.strip() else before
    elif re.search(r",\s*$", before):         # last of a comma list
        before = re.sub(r",\s*$", "", before)
    doc.lines[i] = (before + after).rstrip()


def _do_item(doc, e, key):
    name, delta = str(e["item"]).strip(), int(e["delta"])
    start, end = doc.need("inventory", "Equipment & Inventory")
    real = _resolve_item(doc, start, end, name)
    if real is not None:
        pat = _item_re(real)
        for i in range(start, end):
            ln = doc.lines[i]
            if _CURRENCY.match(ln):
                continue
            m = pat.search(ln)
            if not m:
                continue
            qty = int(m.group(3)) if m.group(3) else 1
            new = qty + delta
            if new < 0:
                raise ChangeError(f"has only {qty} {m.group(1)}")
            if new == 0:
                _remove_item(doc, i, m)
            else:
                lead = ln[m.start():m.start(1)]
                doc.lines[i] = (ln[:m.start()] + lead + m.group(1) + (m.group(2) or "")
                                + (f" ×{new}" if new > 1 else "") + ln[m.end():])
            return [_r("item", qty, new, delta=delta, item=m.group(1))]
    if delta < 0:
        raise ChangeError(f"no {name} in the inventory")
    label = name + (f" ×{delta}" if delta > 1 else "")
    gear = None
    for i in range(start, end):
        if re.match(r"^\s*\*\*\s*adventuring gear\s*:?\s*\*\*", doc.lines[i], re.IGNORECASE):
            gear = i
            break
    if gear is not None and re.fullmatch(r"\s*\*\*[^*]+\*\*\s*:?\s*", doc.lines[gear]):
        j = gear                               # a bullet group: after its last bullet
        while j + 1 < end and _BULLET.match(doc.lines[j + 1].strip()):
            j += 1
        doc.lines.insert(j + 1, f"- {label}")
    elif gear is not None:
        doc.lines[gear] = doc.lines[gear].rstrip() + ", " + label
    else:
        cur = next((i for i in range(start, end) if _CURRENCY.match(doc.lines[i])), None)
        if cur is not None:
            doc.lines[cur:cur] = [f"- {label}", ""]
        else:
            doc.append_to("inventory", f"- {label}")
    return [_r("item", 0, delta, delta=delta, item=name)]


def _do_gold(doc, e, key):
    want = {c: int(n) for c, n in e["gold"].items()}
    i, m = doc.find("inventory", _CURRENCY)
    text = m.group(2) if i is not None else ""
    held = coins(re.sub(r"\([^)]*\)", "", text))
    new = dict(held)
    for c, n in want.items():
        if held.get(c, 0) + n < 0:
            raise ChangeError(f"has {held.get(c, 0)} {c}")
        new[c] = held.get(c, 0) + n
    notes = " ".join(re.findall(r"\([^)]*\)", text))
    val = (" ".join(f"{new[c]} {c}" for c in _COINS if c in new) + (" " + notes if notes else "")).strip()
    if i is not None:
        doc.lines[i] = m.group(1) + val
    else:
        doc.need("inventory", "Equipment & Inventory")
        doc.append_to("inventory", "")
        doc.append_to("inventory", f"**Currency:** {val}")
    return [_r("gold", held, new, delta=want)]


def _do_note(doc, e, key):
    text, on = " ".join(str(e["note"]).split()), bool(e["on"])
    sec = doc.section("notes")
    if sec is None:
        if not on:
            raise ChangeError("no such note")
        while doc.lines and not doc.lines[-1].strip():
            doc.lines.pop()
        doc.lines += ["", "## Backstory & Notes", f"- {text}"]
        return [_r("note", False, True, note=text)]
    for i in range(*sec):
        line = doc.lines[i].strip()
        if not _BULLET.match(line):
            continue
        # The phone shows notes without markdown bold, so compare that way.
        bare = " ".join(_BULLET.sub("", line, count=1).replace("**", "").split())
        if bare == text.replace("**", ""):
            if on:
                return [_r("note", True, True, note=text, changed=False)]
            del doc.lines[i]
            return [_r("note", True, False, note=text)]
    if not on:
        raise ChangeError("no such note")
    doc.append_to("notes", f"- {text}")
    return [_r("note", False, True, note=text)]


_APPLY = {
    "damage": _do_hp, "heal": _do_hp, "hp": _do_hp, "temp_hp": _do_temp,
    "slot": _do_slot, "hit_dice": _do_hd, "death_saves": _do_ds,
    "inspiration": _do_insp, "condition": _do_cond, "item": _do_item,
    "gold": _do_gold, "note": _do_note,
}


def apply(text: str, changes, fields=DM_FIELDS):
    """(new text, results). Changes apply in order. One that can't be applied
    changes nothing and comes back with ok False and the reason."""
    doc = _Doc(text or "")
    results = []
    for e in list(changes or []):
        key = None
        try:
            key = validate(e, fields)
            snapshot = list(doc.lines)
            try:
                res = _APPLY[key](doc, e, key)
            except ChangeError:
                doc.lines = snapshot
                raise
            for r in res:
                r["name"] = e.get("name", "")
                r["change"] = key
            results.extend(res)
        except ChangeError as err:
            results.append({"name": e.get("name", "") if isinstance(e, dict) else "",
                            "field": key, "change": key, "ok": False, "reason": str(err)})
    return doc.text(), results


def change(campaign: Optional[str], name: str, changes, source: str = "dm",
           dry_run: bool = False) -> dict:
    """Apply differences to one character's file, read and written under the
    cast lock (the display server and cast.py share it). All or nothing: if
    any change is refused, the file is left as it was.
    Returns {"ok", "results", "reason"?, "sheet"?}; never raises."""
    fields = PLAYER_FIELDS if source == "player" else DM_FIELDS
    try:
        path, writable = find(campaign, name)
        if path is None:
            return {"ok": False, "reason": f"no character sheet for {name}", "results": []}
        if not writable:
            return {"ok": False, "reason": f"{name} has only a roster copy (read-only)", "results": []}
        cast = _cast()
        with cast._lock:
            lines, eol = cast._read(path)
            if lines is None:
                return {"ok": False, "reason": f"{path.name} can't be read", "results": []}
            text = "\n".join(lines)
            new_text, results = apply(text, changes, fields)
            ok = all(r.get("ok") for r in results)
            if ok and not dry_run and new_text != text:
                cast._write(path, new_text.split("\n"), eol)
        out = {"ok": ok, "results": results}
        if not ok:
            out["reason"] = "; ".join(r["reason"] for r in results if not r.get("ok"))
        elif not dry_run:
            out["sheet"] = parse(new_text)
        return out
    except Exception as err:                    # never into the server
        return {"ok": False, "reason": f"couldn't change the sheet: {err}", "results": []}
