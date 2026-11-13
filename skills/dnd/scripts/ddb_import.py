#!/usr/bin/env python3
"""
ddb_import.py — read a D&D Beyond character and flag what will go wrong at the table.

Reading a sheet by clicking through the D&D Beyond page took about ten browser
steps per character in the first live test, and the problems it hid (armor in
the bag but not equipped, so D&D Beyond showed AC 10; no spells prepared) only
surfaced mid-fight. This reads the character's JSON once and reports them.

    python ddb_import.py https://www.dndbeyond.com/characters/12345678 [--ruleset 2024]
    python ddb_import.py 12345678
    python ddb_import.py saved-character.json          # a JSON file saved from the browser
    python ddb_import.py <...> --json                  # machine-readable summary + issues

A URL or id is fetched from D&D Beyond's character service, which answers only
for characters whose sharing is set to Public (Character → Manage → Sharing).
A private character, or a network that blocks it, exits 2 with that advice;
the fallback is to open the JSON URL this prints in a signed-in browser, save
it, and pass the file.

Flags (each says what to fix on D&D Beyond before the session):
  * armor, a shield or weapons carried but not equipped
  * a spellcaster that prepares spells with none prepared
  * Weapon Mastery chosen for a weapon the character doesn't carry
  * a 2014 background on a 2024 character (no origin feat)
  * an unfinished sheet: no class, no ability scores, or no name

Field names follow D&D Beyond's character-service v5 JSON. Anything missing or
shaped differently is skipped and reported as "not in the data", never guessed.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

# Windows: piped stdout defaults to the system codepage; the report has — and →.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

SERVICE = "https://character-service.dndbeyond.com/character/v5/character/{id}"
ABILITIES = {1: "STR", 2: "DEX", 3: "CON", 4: "INT", 5: "WIS", 6: "CHA"}
_SCORE_SUBTYPES = {f"{n}-score": a for n, a in (("strength", "STR"), ("dexterity", "DEX"),
                                                ("constitution", "CON"), ("intelligence", "INT"),
                                                ("wisdom", "WIS"), ("charisma", "CHA"))}
# Classes that prepare spells from a list each day (2014: cleric, druid,
# paladin, wizard, artificer; 2024 adds the rest — every caster prepares).
_PREPARERS_2014 = {"cleric", "druid", "paladin", "wizard", "artificer"}
_CASTERS = _PREPARERS_2014 | {"bard", "ranger", "sorcerer", "warlock"}


class ImportFailed(RuntimeError):
    pass


def character_id(src: str) -> str | None:
    m = re.search(r"(?:characters?/|^)(\d{4,12})(?:\b|/|$)", src.strip())
    return m.group(1) if m else None


def fetch(cid: str, timeout: float = 20) -> dict:
    url = SERVICE.format(id=cid)
    req = urllib.request.Request(url, headers={"User-Agent": "dnd-dm-skill/ddb-import",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            raise ImportFailed(
                f"D&D Beyond refused character {cid} (HTTP {e.code}). Set its sharing to Public "
                f"(Character → Manage → Sharing), or open {url} in a signed-in browser, save the "
                "page as a .json file and pass that file instead.")
        raise ImportFailed(f"D&D Beyond answered HTTP {e.code} for {url}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ImportFailed(f"couldn't fetch {url}: {e}. Save it from a browser and pass the file.")
    return unwrap(body)


def unwrap(body) -> dict:
    """The service wraps the character in {"success", "data"}; a saved file may not."""
    if isinstance(body, dict) and isinstance(body.get("data"), dict) and "success" in body:
        if not body.get("success", True):
            raise ImportFailed(f"D&D Beyond said: {body.get('message') or 'not available'}")
        return body["data"]
    if isinstance(body, dict):
        return body
    raise ImportFailed("not a D&D Beyond character JSON object")


# ── reading ──────────────────────────────────────────────────────────────────

def _all_modifiers(d: dict) -> list:
    mods = d.get("modifiers") or {}
    out = []
    if isinstance(mods, dict):
        for lst in mods.values():
            if isinstance(lst, list):
                out.extend(m for m in lst if isinstance(m, dict))
    return out


def ability_scores(d: dict) -> dict | None:
    base = {s.get("id"): s.get("value") for s in d.get("stats") or [] if isinstance(s, dict)}
    if not any(isinstance(v, int) and v > 0 for v in base.values()):
        return None
    bonus = {s.get("id"): s.get("value") or 0 for s in d.get("bonusStats") or [] if isinstance(s, dict)}
    over = {s.get("id"): s.get("value") for s in d.get("overrideStats") or [] if isinstance(s, dict)}
    scores = {}
    for i, ab in ABILITIES.items():
        v = (base.get(i) or 0) + (bonus.get(i) or 0)
        scores[ab] = v
    for m in _all_modifiers(d):
        ab = _SCORE_SUBTYPES.get(str(m.get("subType", "")))
        if ab and m.get("type") == "bonus" and isinstance(m.get("value"), int):
            scores[ab] += m["value"]
    for i, ab in ABILITIES.items():
        if isinstance(over.get(i), int) and over[i] > 0:
            scores[ab] = over[i]
    return scores


def classes(d: dict) -> list:
    out = []
    for c in d.get("classes") or []:
        if not isinstance(c, dict):
            continue
        name = ((c.get("definition") or {}).get("name") or "?")
        sub = (c.get("subclassDefinition") or {}).get("name")
        out.append({"name": name, "level": c.get("level") or 0, "subclass": sub})
    return out


def inventory(d: dict) -> list:
    out = []
    for it in d.get("inventory") or []:
        if not isinstance(it, dict):
            continue
        df = it.get("definition") or {}
        out.append({"name": df.get("name") or "?", "equipped": bool(it.get("equipped")),
                    "type": df.get("filterType") or df.get("type") or "",
                    "armor_type": df.get("armorTypeId"), "ac": df.get("armorClass"),
                    "can_equip": df.get("canEquip", True), "qty": it.get("quantity", 1)})
    return out


def prepared_spells(d: dict) -> tuple:
    """(prepared names, always-prepared names, total spells listed) from classSpells."""
    prepared, always, total = [], [], 0
    for cs in d.get("classSpells") or []:
        for sp in (cs or {}).get("spells") or []:
            df = sp.get("definition") or {}
            total += 1
            if (df.get("level") or 0) == 0:
                continue                      # cantrips are always ready
            if sp.get("alwaysPrepared"):
                always.append(df.get("name", "?"))
            elif sp.get("prepared"):
                prepared.append(df.get("name", "?"))
    return prepared, always, total


def mastery_weapons(d: dict) -> list:
    """Weapons chosen for Weapon Mastery, from modifiers or choices that mention mastery."""
    names = []
    for m in _all_modifiers(d):
        tags = " ".join(str(m.get(k, "")) for k in ("type", "subType", "friendlyTypeName",
                                                     "friendlySubtypeName")).lower()
        if "mastery" in tags:
            n = m.get("friendlySubtypeName") or m.get("subType")
            if n and "mastery" not in str(n).lower():
                names.append(str(n).replace("-", " ").title())
    return sorted(set(names))


def hp_max(d: dict, scores: dict | None, level: int) -> int | None:
    if isinstance(d.get("overrideHitPoints"), int):
        return d["overrideHitPoints"]
    base = d.get("baseHitPoints")
    if not isinstance(base, int):
        return None
    con = ((scores or {}).get("CON", 10) - 10) // 2
    return base + (d.get("bonusHitPoints") or 0) + con * max(level, 1)


# ── checks ───────────────────────────────────────────────────────────────────

def check(d: dict, ruleset: str | None = None) -> dict:
    name = (d.get("name") or "").strip()
    cls = classes(d)
    level = sum(c["level"] for c in cls)
    scores = ability_scores(d)
    inv = inventory(d)
    issues = []

    def issue(what, fix):
        issues.append({"issue": what, "fix": fix})

    if not name:
        issue("the character has no name", "finish the sheet on D&D Beyond")
    if not cls:
        issue("no class chosen — the sheet isn't finished (D&D Beyond shows 'Character Not Ready')",
              "finish the Class step in the builder")
    if scores is None:
        issue("no ability scores set", "finish the Ability Scores step")

    for it in inv:
        t = str(it["type"]).lower()
        if it["equipped"] or not it["can_equip"]:
            continue
        if t == "armor":
            issue(f"{it['name']} is carried but not equipped — D&D Beyond leaves AC at the "
                  "unarmored value", f"equip {it['name']} in the Inventory tab")
        elif t == "weapon" and not any(o["equipped"] and str(o["type"]).lower() == "weapon" for o in inv):
            issue(f"{it['name']} is carried but no weapon is equipped — attacks won't show",
                  f"equip {it['name']} in the Inventory tab")

    prepared, always, total = prepared_spells(d)
    cls_names = {c["name"].lower() for c in cls}
    preparers = (_CASTERS if ruleset == "2024" else _PREPARERS_2014) & cls_names
    leveled_listed = total
    if preparers and not prepared:
        issue(f"{'/'.join(sorted(n.title() for n in preparers))} prepares spells, but none are "
              "prepared" + (" (only always-prepared ones)" if always else ""),
              "prepare spells in the Spells tab (Manage Spells)")

    carried = {it["name"].lower() for it in inv if str(it["type"]).lower() == "weapon"}
    for w in mastery_weapons(d):
        if not any(w.lower() in c or c in w.lower() for c in carried):
            issue(f"Weapon Mastery is set for {w}, which the character doesn't carry",
                  "pick a mastery for a weapon in the inventory, or add the weapon")

    bg = (d.get("background") or {}).get("definition") or {}
    if ruleset == "2024":
        legacy = bg.get("isLegacy")
        feats = [((f.get("definition") or {}).get("name") or "") for f in d.get("feats") or []]
        granted = bg.get("grantedFeats") or bg.get("featList")
        if legacy or (bg and not granted and not feats):
            issue(f"background {bg.get('name') or '?'} looks like a 2014 background — a 2024 "
                  "character gets an origin feat from it", "choose a 2024 background (2024 PHB)")

    ac_armor = [it for it in inv if it["equipped"] and str(it["type"]).lower() == "armor"]
    return {
        "name": name or "(unnamed)",
        "species": ((d.get("race") or {}).get("fullName")),
        "background": bg.get("name"),
        "classes": cls,
        "level": level,
        "abilities": scores,
        "hp_max": hp_max(d, scores, level),
        "equipped": [it["name"] for it in inv if it["equipped"]],
        "armor_equipped": [it["name"] for it in ac_armor],
        "prepared_spells": prepared,
        "always_prepared": always,
        "spells_listed": leveled_listed,
        "weapon_mastery": mastery_weapons(d),
        "issues": issues,
    }


def render(r: dict) -> str:
    cls = " / ".join(f"{c['name']} {c['level']}" + (f" ({c['subclass']})" if c.get("subclass") else "")
                     for c in r["classes"]) or "no class"
    lines = [f"{r['name']} — {r.get('species') or '?'} {cls}; background {r.get('background') or '?'}"]
    if r["abilities"]:
        lines.append("  " + "  ".join(f"{k} {v}" for k, v in r["abilities"].items()))
    lines.append(f"  HP max: {r['hp_max'] if r['hp_max'] is not None else 'not in the data'}")
    lines.append(f"  Equipped: {', '.join(r['equipped']) or 'nothing'}")
    if r["prepared_spells"] or r["always_prepared"]:
        lines.append(f"  Prepared: {', '.join(r['prepared_spells'] + r['always_prepared'])}")
    if r["weapon_mastery"]:
        lines.append(f"  Weapon Mastery: {', '.join(r['weapon_mastery'])}")
    if r["issues"]:
        lines.append("")
        lines.append(f"  {len(r['issues'])} thing(s) to fix on D&D Beyond before play:")
        for i in r["issues"]:
            lines.append(f"   ! {i['issue']}")
            lines.append(f"     → {i['fix']}")
    else:
        lines.append("  No problems found.")
    return "\n".join(lines)


def load_source(src: str) -> dict:
    p = pathlib.Path(src)
    if p.suffix.lower() == ".json" and p.exists():
        return unwrap(json.loads(p.read_text(encoding="utf-8")))
    cid = character_id(src)
    if not cid:
        raise ImportFailed(f"{src!r} is not a D&D Beyond character URL, id or .json file")
    return fetch(cid)


def main() -> int:
    ap = argparse.ArgumentParser(description="Read a D&D Beyond character and flag problems.")
    ap.add_argument("source", help="character URL, numeric id, or a saved .json file")
    ap.add_argument("--ruleset", choices=["2014", "2024"], help="the campaign's ruleset")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    try:
        d = load_source(a.source)
    except ImportFailed as exc:
        print(f"ddb_import.py: {exc}", file=sys.stderr)
        return 2
    r = check(d, a.ruleset)
    print(json.dumps(r, indent=1, ensure_ascii=False) if a.json else render(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
