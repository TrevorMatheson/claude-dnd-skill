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
