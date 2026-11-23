"""Phone sheet, plan step S2: the sheet parser and GET /sheet/<name>.

AC4 (server part) — The sheet shows every section of a real imported
       character. An unreadable section shows as plain text.
D5   — Read from the existing character file. A section the server can't read
       is shown as plain text, not left out. Older characters keep working with
       no migration.

Fixtures in tests/fixtures/sheets/ are fictional characters:
  template_filled.md  skills/dnd/templates/character-sheet.md filled in (a wizard)
  imported.md         the layout of a D&D Beyond-imported sheet, invented content
  broken.md           ragged Skills table, Attacks with missing cells, HP "lots / ??"
  crlf.md             template_filled.md with CRLF line ends
  gbk.md              template_filled.md with CJK text, stored as GBK bytes
(.gitattributes in that folder keeps the bytes exact.)

Interface pinned (module skills/dnd/display/sheet.py):
  sheet.parse(text) -> dict with name, identity, abilities, combat, saves,
  skills, attacks, slots, spellcasting, spells, features, inventory, notes,
  plain, sections. Every `## ` section is either a parsed key or a
  {title, text} entry in `plain`; a known section that fails to parse is in
  `plain` and its key is absent or None.

Route (dnd-display-app.py):
  GET /sheet/<name> -> 200 {"sheet", "editable", "path_ok"} from
  <active campaign>/characters/<name>.md (found via cast._pc_path); 403 without
  X-DND-Token when a LAN token is set; 404 JSON with a reason when there is no
  file; 400 for a name failing _CHAR_NAME_RE; global roster fallback
  <DND_CAMPAIGN_ROOT>/characters/<name>.md -> 200 with path_ok false (read-only,
  so nothing editable). `editable` holds only D7 sections.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import pathlib
import shutil
import sys
import unittest
import uuid

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "sheets"
for _p in (str(DISPLAY), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

ABILS = ("STR", "DEX", "CON", "INT", "WIS", "CHA")
EDITABLE = {"combat", "slots", "inventory", "notes"}
BUILD_KEYS = {"abilities", "identity", "features", "saves", "skills", "attacks"}

# `## ` title -> the parsed key it feeds (None: plain text by design).
SECTION_KEY = {
    "Identity": "identity",
    "Character Pillar": None,
    "Campaign History": None,
    "Ability Scores": "abilities",
    "Combat Stats": "combat",
    "Saving Throws": "saves",
    "Skills": "skills",
    "Attacks": "attacks",
    "Spell Slots (if applicable)": "slots",
    "Known Spells / Cantrips": "spells",
    "Features & Traits": "features",
    "Equipment & Inventory": "inventory",
    "Backstory & Notes": "notes",
    "Session Log": None,
}


def _fixture_text(name: str) -> str:
    return (FIX / name).read_bytes().decode("utf-8")


def _sheet_mod():
    try:
        return importlib.import_module("sheet")
    except ImportError as exc:
        raise AssertionError(f"skills/dnd/display/sheet.py is missing or does not import: {exc}")


def _parse(text: str) -> dict:
    return _sheet_mod().parse(text)


def _headings(text: str) -> list:
    return [l[3:].strip() for l in text.replace("\r\n", "\n").split("\n") if l.startswith("## ")]


def _plain(sheet: dict) -> dict:
    return {p["title"]: p["text"] for p in sheet.get("plain") or []}


def _by(items, key, value):
    hits = [i for i in items if i.get(key) == value]
    return hits[0] if hits else None


def _items(sheet: dict) -> dict:
    """{item name: (qty, group label)} over every inventory group."""
    out = {}
    for g in sheet["inventory"]["groups"]:
        for it in g["items"]:
            out[it["name"]] = (it["qty"], g["label"])
    return out


class _SectionsMixin:
    def assert_nothing_dropped(self, text: str, sheet: dict):
        heads = _headings(text)
        self.assertEqual(sheet.get("sections"), heads, "sections is not the ordered list of `## ` titles")
        plain = _plain(sheet)
        for title in heads:
            key = SECTION_KEY.get(title)
            parsed = key is not None and sheet.get(key) is not None
            self.assertTrue(parsed or title in plain,
                            f"section {title!r} is neither parsed (key {key!r}) nor in plain: dropped")
            if key is None:
                self.assertIn(title, plain, f"section {title!r} has no parsed key and is not in plain")


# ── template_filled.md ────────────────────────────────────────────────────

class TestParseTemplateFilled(_SectionsMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _fixture_text("template_filled.md")

    def setUp(self):
        self.s = _parse(self.text)

    def test_every_section_present(self):
        self.assert_nothing_dropped(self.text, self.s)

    def test_name_and_identity(self):
        self.assertEqual(self.s["name"], "Ysolde Varn")
        idn = self.s["identity"]
        self.assertEqual(idn["race"], "Half-Elf")
        self.assertEqual(idn["class"], "Wizard")
        self.assertEqual(str(idn["level"]).strip(), "3")
        self.assertEqual(idn["background"], "Sage")
        self.assertEqual(idn["alignment"], "Neutral Good")
        self.assertIn("900", str(idn["xp"]))

    def test_abilities(self):
        want = {"STR": (8, -1), "DEX": (14, 2), "CON": (13, 1), "INT": (17, 3), "WIS": (12, 1), "CHA": (10, 0)}
        self.assertEqual({k: (v["score"], v["mod"]) for k, v in self.s["abilities"].items()}, want)

    def test_combat(self):
        c = self.s["combat"]
        self.assertEqual(c["hp"], {"current": 14, "max": 17})
        self.assertEqual(c["temp_hp"], 3)
        self.assertEqual(c["ac"], 12)
        self.assertEqual(c["ac_note"], "")
        self.assertEqual(c["initiative"], 2)
        self.assertIn("30", c["speed"])
        self.assertEqual(c["hit_dice"], {"die": "3d6", "total": 3, "remaining": 2})
        self.assertEqual(c["death_saves"], {"successes": 1, "failures": 2})
        self.assertIs(c["inspiration"], False, "no Inspiration line must read as False")
        self.assertEqual(c["conditions"], [], "no Conditions line must read as []")

    def test_saves(self):
        sv = self.s["saves"]
        self.assertEqual(set(sv), set(ABILS))
        self.assertEqual(sv["INT"], {"bonus": 5, "proficient": True})
        self.assertEqual(sv["WIS"], {"bonus": 3, "proficient": True})
        self.assertEqual(sv["STR"], {"bonus": -1, "proficient": False})
        self.assertEqual(sv["CHA"], {"bonus": 0, "proficient": False})

    def test_skills(self):
        sk = self.s["skills"]
        self.assertEqual(len(sk), 18)
        arc = _by(sk, "name", "Arcana")
        self.assertEqual((arc["ability"], arc["bonus"], arc["proficient"], arc["note"]), ("INT", 5, True, ""))
        ath = _by(sk, "name", "Athletics")
        self.assertEqual((ath["ability"], ath["bonus"], ath["proficient"]), ("STR", -1, False))
        self.assertEqual(_by(sk, "name", "Sleight of Hand")["bonus"], 2)

    def test_attacks(self):
        at = self.s["attacks"]
        self.assertEqual([a["name"] for a in at], ["Fire Bolt", "Dagger"])
        fb = at[0]
        self.assertEqual((fb["bonus"], fb["damage"], fb["type"]), (5, "1d10", "Fire"))
        self.assertIn("cantrip", fb["notes"])
        self.assertEqual((at[1]["bonus"], at[1]["damage"]), (4, "1d4+2"))

    def test_slots(self):
        self.assertEqual(self.s["slots"], [
            {"level": 1, "total": 4, "used": 1, "label": "1st"},
            {"level": 2, "total": 2, "used": 0, "label": "2nd"},
        ])

    def test_spells(self):
        groups = {g["label"]: g for g in self.s["spells"]}
        self.assertEqual(list(groups), ["Cantrips", "1st level", "2nd level"])
        self.assertEqual(groups["Cantrips"]["names"], ["Fire Bolt", "Mage Hand", "Light"])
        self.assertEqual(groups["1st level"]["names"], ["Magic Missile", "Shield", "Sleep", "Detect Magic"])
        self.assertEqual(groups["2nd level"]["names"], ["Misty Step", "Hold Person"])
        self.assertIn("Misty Step, Hold Person", groups["2nd level"]["text"])

    def test_features(self):
        f = self.s["features"]
        self.assertEqual([g["title"] for g in f], ["Wizard", "Half-Elf Racial", "Sage Background"])
        self.assertIn("Arcane Recovery", f[0]["text"])
        self.assertIn("Researcher", f[2]["text"])

    def test_inventory(self):
        inv = self.s["inventory"]
        self.assertEqual([g["label"] for g in inv["groups"]], ["Weapons", "Armour", "Adventuring Gear"])
        items = _items(self.s)
        self.assertEqual(items["Potion of Healing"], (2, "Adventuring Gear"))
        self.assertEqual(items["Rations"], (5, "Adventuring Gear"))
        self.assertEqual(items["Dagger"], (1, "Weapons"))
        self.assertEqual(items["Spellbook"][0], 1)
        self.assertEqual(inv["currency"], {"gp": 15, "sp": 4, "cp": 0})
        self.assertEqual(inv["currency_text"], "15gp 4sp 0cp")

    def test_notes_and_plain(self):
        self.assertIn("Owes the ferryman a favour", self.s["notes"])
        plain = _plain(self.s)
        self.assertIn("She is looking for the library that burned", plain["Character Pillar"])
        self.assertIn("Origin campaign", plain["Campaign History"])

    def test_result_is_json_serialisable(self):
        json.dumps(self.s)


# ── imported.md (D&D Beyond import layout) ────────────────────────────────

class TestParseImported(_SectionsMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _fixture_text("imported.md")

    def setUp(self):
        self.s = _parse(self.text)

    def test_every_section_present(self):
        self.assert_nothing_dropped(self.text, self.s)

    def test_known_sections_all_parsed_not_plain(self):
        plain = _plain(self.s)
        for title, key in SECTION_KEY.items():
            if key and title in self.s["sections"]:
                self.assertNotIn(title, plain, f"{title!r} of an imported sheet fell back to plain text")
                self.assertIsNotNone(self.s.get(key), f"{key!r} not parsed")

    def test_identity(self):
        self.assertEqual(self.s["name"], "Brakka")
        idn = self.s["identity"]
        self.assertEqual(idn["class"], "Paladin 1 / Warlock 1")
        self.assertTrue(idn["race"].startswith("Goliath"), idn["race"])
        self.assertEqual(idn["background"], "Soldier (legacy 2014)")

    def test_combat(self):
        c = self.s["combat"]
        self.assertEqual(c["hp"], {"current": 20, "max": 20})
        self.assertEqual(c["temp_hp"], 0)
        self.assertEqual(c["ac"], 18)
        self.assertEqual(c["ac_note"], "Chain Mail 16 + Shield 2")
        self.assertEqual(c["initiative"], 0)
        self.assertIn("35", c["speed"])
        self.assertEqual(c["hit_dice"], {"die": "2d10", "total": 2, "remaining": 2})
        self.assertEqual(c["death_saves"], {"successes": 0, "failures": 0})
        self.assertIs(c["inspiration"], True)
        self.assertEqual(c["conditions"], ["Poisoned"])

    def test_saves_star_marks_proficient(self):
        sv = self.s["saves"]
        self.assertEqual(sv["WIS"], {"bonus": 3, "proficient": True})
        self.assertEqual(sv["CHA"], {"bonus": 4, "proficient": True})
        self.assertEqual(sv["STR"], {"bonus": 3, "proficient": False})
        self.assertEqual(sv["INT"], {"bonus": -1, "proficient": False})

    def test_skills_fifth_column_note(self):
        sk = self.s["skills"]
        self.assertEqual(len(sk), 18)
        st = _by(sk, "name", "Stealth")
        self.assertEqual((st["bonus"], st["proficient"]), (0, False))
        self.assertIn("Disadvantage in Chain Mail", st["note"])
        self.assertTrue(_by(sk, "name", "Athletics")["proficient"])
        self.assertEqual(_by(sk, "name", "Athletics")["note"], "")

    def test_attacks(self):
        at = {a["name"]: a for a in self.s["attacks"]}
        self.assertEqual(list(at), ["Longsword", "Javelin", "Eldritch Blast", "Unarmed Strike"])
        ls = at["Longsword"]
        self.assertEqual((ls["bonus"], ls["damage"], ls["type"]), (5, "1d8+3 (1d10+3 two-handed)", "Slashing"))
        self.assertIn("Versatile", ls["notes"])
        self.assertEqual(at["Unarmed Strike"]["damage"], "4")
        self.assertEqual(at["Eldritch Blast"]["bonus"], 4)

    def test_pact_magic_slot_row(self):
        self.assertEqual(self.s["slots"], [
            {"level": 1, "total": 2, "used": 1, "label": "1st"},
            {"level": 1, "total": 1, "used": 0, "label": "1st (Pact Magic, short rest)"},
        ])

    def test_spellcasting_line(self):
        self.assertEqual(self.s["spellcasting"], {"ability": "CHA", "attack": 4, "dc": 12})

    def test_spell_groups(self):
        g = self.s["spells"]
        self.assertEqual(len(g), 3)
        self.assertTrue(g[0]["label"].startswith("Warlock cantrips"), g[0]["label"])
        self.assertEqual(g[0]["names"], ["Eldritch Blast", "Minor Illusion"])
        self.assertIn("Eldritch Blast, Minor Illusion", g[0]["text"])
        self.assertEqual(g[1]["names"], ["Divine Smite", "Bless"],
                         "parenthetical prose must be stripped from spell names")
        self.assertIn("picked at the table", g[1]["text"], "text must keep the raw bullet")
        self.assertEqual(g[2]["names"], ["Hex", "Protection from Evil and Good"])

    def test_features_subsections(self):
        f = self.s["features"]
        self.assertEqual([x["title"] for x in f], ["Paladin", "Warlock", "Goliath Racial", "Soldier Background"])
        self.assertIn("Lay On Hands", f[0]["text"])

    def test_inventory_bullets_and_inline_lists(self):
        items = _items(self.s)
        self.assertEqual(items["Javelin"], (6, "Weapons"))
        self.assertEqual(items["Longsword"], (1, "Weapons"))
        self.assertEqual(items["Rations"], (7, "Adventuring Gear"))
        self.assertEqual(items["Tinderbox"], (1, "Adventuring Gear"))
        self.assertTrue(any(n.startswith("Arcane focus") for n in items), sorted(items))
        self.assertEqual(items["Sickle"], (1, "Trinkets"))
        self.assertEqual(items["Dagger"], (2, "Trinkets"))
        self.assertTrue(any(n.startswith("Chain Mail") for n in items), sorted(items))
        self.assertIn("Shield", items)

    def test_currency_with_prose(self):
        inv = self.s["inventory"]
        self.assertEqual(inv["currency_text"], "23 gp (owes a 2 sp fine)")
        self.assertEqual(inv["currency"].get("gp"), 23)
        # The 2 sp is a debt in a note, not money she has. Counting it would let
        # S4 spend coins that do not exist.
        self.assertEqual(inv["currency"].get("sp", 0), 0, inv["currency"])

    def test_plain_holds_pillar_history_and_unknown_section(self):
        plain = _plain(self.s)
        self.assertIn("patron I have never seen", plain["Character Pillar"])
        self.assertIn("Origin campaign", plain["Campaign History"])
        self.assertIn("escorted a grain cart", plain["Session Log"])

    def test_notes(self):
        self.assertIn("Personality", self.s["notes"])
        self.assertIn("Import notes", self.s["notes"])


class TestParseLayoutVariants(unittest.TestCase):
    """Other layouts seen in imported sheets, as small inline sheets."""

    def test_spellcasting_bullet_form(self):
        s = _parse("# X\n## Spell Slots (if applicable)\n| Level | Total | Used |\n|---|---|---|\n| 1st | 2 | 0 |\n\n"
                   "- **Spellcasting ability:** Charisma | **Spell save DC:** 12 | **Spell attack:** +4\n")
        self.assertEqual(s["spellcasting"], {"ability": "CHA", "attack": 4, "dc": 12})

    def test_inline_weapons_line(self):
        s = _parse("# X\n## Equipment & Inventory\n**Weapons:** Sickle, Dagger ×2\n**Armour:** Leather\n"
                   "**Currency:** 23 gp\n")
        items = _items(s)
        self.assertEqual(items["Sickle"], (1, "Weapons"))
        self.assertEqual(items["Dagger"], (2, "Weapons"))
        self.assertEqual(items["Leather"], (1, "Armour"))
        self.assertEqual(s["inventory"]["currency"], {"gp": 23})

    def test_missing_temp_hp_reads_zero(self):
        s = _parse("# X\n## Combat Stats\n- **HP:** 9 / 12\n- **AC:** 13 | **Initiative:** -1 | **Speed:** 25 ft.\n")
        c = s["combat"]
        self.assertEqual(c["hp"], {"current": 9, "max": 12})
        self.assertEqual(c["temp_hp"], 0)
        self.assertEqual(c["initiative"], -1)
        self.assertIs(c["inspiration"], False)
        self.assertEqual(c["conditions"], [])

    def test_never_raises_on_junk(self):
        for junk in ("", "no headings at all", "## \n|||\n| |", "# A\n## Skills\n|\n|-|\n| x |",
                     "## Combat Stats\n- **HP:**\n- **AC:** ?", "\x00\x01## Attacks\n| a |"):
            s = _parse(junk)
            self.assertIsInstance(s, dict, repr(junk))
            json.dumps(s)


# ── broken.md ─────────────────────────────────────────────────────────────

class TestParseBroken(_SectionsMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _fixture_text("broken.md")

    def setUp(self):
        self.s = _parse(self.text)

    def test_every_section_present(self):
        self.assert_nothing_dropped(self.text, self.s)

    def test_ragged_skills_fall_back_to_plain_with_text_intact(self):
        plain = _plain(self.s)
        self.assertIn("Skills", plain, "a ragged Skills table must show as plain text")
        self.assertIsNone(self.s.get("skills"), "skills parsed from a ragged table and also in plain")
        for frag in ("| Acrobatics | DEX | +3 |", "| ??? |", "| Stealth | DEX | lots | ✓✓ |", "Sleight of Hand"):
            self.assertIn(frag, plain["Skills"])

    def test_non_numeric_hp_is_never_a_number(self):
        plain = _plain(self.s)
        c = self.s.get("combat")
        if c is None:
            self.assertIn("lots / ??", plain.get("Combat Stats", ""), "unreadable combat dropped")
        else:
            self.assertEqual(c["hp"], {"current": None, "max": None})

    def test_attacks_with_missing_cells_kept(self):
        at = self.s.get("attacks")
        if at is None:
            self.assertIn("Shortsword", _plain(self.s).get("Attacks", ""))
        else:
            sw = _by(at, "name", "Shortsword")
            self.assertIsNotNone(sw, at)
            self.assertEqual(sw["bonus"], 5)
            self.assertEqual(sw["damage"], "")

    def test_rest_of_the_sheet_parses(self):
        s = self.s
        self.assertEqual(s["name"], "Grimble")
        self.assertEqual(s["identity"]["class"], "Rogue")
        self.assertEqual(s["abilities"]["DEX"], {"score": 16, "mod": 3})
        self.assertEqual(s["saves"]["DEX"], {"bonus": 5, "proficient": True})
        self.assertEqual(s["slots"], [], "the template's empty slot row is no slot")
        self.assertEqual(s["spells"], [])
        self.assertEqual([f["title"] for f in s["features"]], ["Rogue"])
        self.assertEqual(_items(s)["Dagger"], (2, "Weapons"))
        self.assertEqual(s["inventory"]["currency"], {"gp": 3, "sp": 7})
        self.assertIn("bridge market", s["notes"])


# ── CRLF and GBK ──────────────────────────────────────────────────────────

class TestParseEncodings(unittest.TestCase):
    def test_crlf_parses_like_lf(self):
        raw = (FIX / "crlf.md").read_bytes()
        self.assertIn(b"\r\n", raw, "crlf.md lost its CRLF line ends (check .gitattributes)")
        lf = _parse(_fixture_text("template_filled.md"))
        crlf = _parse(raw.decode("utf-8"))
        self.assertEqual(crlf, lf)
        self.assertNotIn("\\r", json.dumps(crlf))

    def test_gbk_text_parses(self):
        raw = (FIX / "gbk.md").read_bytes()
        with self.assertRaises(UnicodeDecodeError, msg="gbk.md is not GBK bytes any more"):
            raw.decode("utf-8")
        s = _parse(raw.decode("gbk"))
        self.assertEqual(s["name"], "Lin Yue")
        items = _items(s)
        self.assertEqual(items["玉佩"], (2, "Adventuring Gear"))
        self.assertEqual(items["青铜镜"][0], 1)
        self.assertIn("家住长安", s["notes"])
        self.assertEqual(s["combat"]["hp"], {"current": 14, "max": 17})


# ── GET /sheet/<name> ─────────────────────────────────────────────────────

def _import_app():
    spec = importlib.util.spec_from_file_location(f"dnd_display_app_sheet_{uuid.uuid4().hex[:6]}",
                                                  str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LAN = {"REMOTE_ADDR": "192.168.1.50"}
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}


class TestSheetRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.client = cls.app.app.test_client()

    def setUp(self):
        root = pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"])
        self.name = f"sheet-{uuid.uuid4().hex[:8]}"
        self.camp = root / "campaigns" / self.name
        self.chars = self.camp / "characters"
        self.chars.mkdir(parents=True)
        (self.camp / "state.md").write_text("# State\n\n## Session Flags\n", encoding="utf-8")
        self.addCleanup(shutil.rmtree, str(self.camp), True)
        self.roster = root / "characters"
        camp_file = pathlib.Path(self.app.CAMP_FILE)
        camp_file.parent.mkdir(parents=True, exist_ok=True)
        old = camp_file.read_bytes() if camp_file.exists() else None
        camp_file.write_text(self.name, encoding="utf-8")
        self.addCleanup(lambda: camp_file.write_bytes(old) if old is not None else camp_file.unlink())
        old_tok = self.app._lan_token
        self.addCleanup(setattr, self.app, "_lan_token", old_tok)
        self.app._lan_token = None

    def put(self, fixture: str, filename: str, where: pathlib.Path = None) -> pathlib.Path:
        where = where or self.chars
        where.mkdir(parents=True, exist_ok=True)
        p = where / filename
        p.write_bytes((FIX / fixture).read_bytes())
        if where != self.chars:
            self.addCleanup(lambda: p.unlink() if p.exists() else None)
        return p

    def get(self, path, env=LOCAL, headers=None):
        return self.client.get(path, environ_base=env, headers=headers or {})

    def assert_route_exists(self, r):
        if r.status_code == 404 and not (r.is_json and r.get_json()):
            self.fail("GET /sheet/<name> is not routed (plain 404 from Flask)")

    def test_returns_parsed_sheet(self):
        self.put("template_filled.md", "Ysolde.md")
        r = self.get("/sheet/Ysolde")
        self.assert_route_exists(r)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        self.assertTrue({"sheet", "editable", "path_ok"} <= set(body), sorted(body))
        self.assertIs(body["path_ok"], True)
        self.assertEqual(body["sheet"]["name"], "Ysolde Varn")
        self.assertEqual(body["sheet"], _parse(_fixture_text("template_filled.md")))

    def test_editable_is_exactly_the_d7_sections(self):
        for fixture in ("template_filled.md", "imported.md"):
            with self.subTest(fixture=fixture):
                self.put(fixture, "Pcx.md")
                r = self.get("/sheet/Pcx")
                self.assert_route_exists(r)
                self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
                ed = r.get_json()["editable"]
                self.assertEqual(len(ed), len(set(ed)), ed)
                self.assertEqual(set(ed), EDITABLE)
                self.assertFalse(set(ed) & BUILD_KEYS, f"build sections editable: {ed}")

    def test_lan_token_required(self):
        self.put("template_filled.md", "Ysolde.md")
        self.app._lan_token = "tok-sheet-s2"
        r = self.get("/sheet/Ysolde", env=LAN)
        self.assert_route_exists(r)
        self.assertEqual(r.status_code, 403, "a sheet was served without X-DND-Token in LAN mode")
        self.assertNotIn("Ysolde Varn", r.get_data(as_text=True))
        r = self.get("/sheet/Ysolde", env=LAN, headers={"X-DND-Token": "wrong"})
        self.assertEqual(r.status_code, 403)
        r = self.get("/sheet/Ysolde", env=LAN, headers={"X-DND-Token": "tok-sheet-s2"})
        self.assertEqual(r.status_code, 200, "a phone with the token cannot read its sheet")

    def test_missing_file_is_404_with_reason(self):
        r = self.get("/sheet/Nobody")
        self.assertEqual(r.status_code, 404)
        body = r.get_json(silent=True)
        self.assertIsInstance(body, dict, "404 must be JSON with a reason")
        reason = body.get("error") or body.get("reason")
        self.assertTrue(isinstance(reason, str) and reason.strip(), body)

    def test_bad_names_are_400(self):
        self.put("template_filled.md", "Ysolde.md")
        for bad in ("bad.name", "x;y", "%2E%2E", "a$b", "-lead"):
            with self.subTest(name=bad):
                r = self.get(f"/sheet/{bad}")
                self.assertEqual(r.status_code, 400, r.get_data(as_text=True))

    def test_path_traversal_never_reads_outside_characters(self):
        secret = "SECRET-SHEET-MARKER"
        (self.camp / "x.md").write_text(f"# {secret}\n## Identity\n- **Race:** Elf\n", encoding="utf-8")
        (self.camp / "a").mkdir()
        (self.camp / "a" / "b.md").write_text(f"# {secret}\n", encoding="utf-8")
        (self.chars / "a").mkdir()
        (self.chars / "a" / "b.md").write_text(f"# {secret}\n", encoding="utf-8")
        # The task asks for 400 here; a name with a slash may never reach a
        # `<name>` route (Flask answers 404 first). Either way: never served.
        for bad in ("../x", "..%2Fx", "a/b", "a%2Fb", "..%5Cx"):
            with self.subTest(name=bad):
                r = self.get(f"/sheet/{bad}")
                self.assertIn(r.status_code, (400, 404), r.get_data(as_text=True))
                self.assertNotIn(secret, r.get_data(as_text=True))
        r = self.get("/sheet/..%2Fx")
        self.assertNotEqual(r.status_code, 200)

    def test_apostrophe_name_works(self):
        p = self.chars / "Dra'kan.md"
        p.write_bytes(_fixture_text("template_filled.md").replace("# Ysolde Varn", "# Dra'kan").encode("utf-8"))
        r = self.get("/sheet/Dra'kan")
        self.assert_route_exists(r)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["sheet"]["name"], "Dra'kan")

    def test_roster_fallback_is_read_only(self):
        who = f"Roster{uuid.uuid4().hex[:6]}"
        self.put("imported.md", f"{who}.md", where=self.roster)
        r = self.get(f"/sheet/{who}")
        self.assert_route_exists(r)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        self.assertIs(body["path_ok"], False)
        self.assertEqual(body["sheet"]["name"], "Brakka")
        self.assertEqual(body["editable"], [], "a roster-only sheet is read-only (plan: roster fallback is read-only)")

    def test_campaign_file_wins_over_roster(self):
        who = f"Both{uuid.uuid4().hex[:6]}"
        self.put("imported.md", f"{who}.md", where=self.roster)
        self.put("template_filled.md", f"{who}.md")
        r = self.get(f"/sheet/{who}")
        self.assert_route_exists(r)
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertIs(body["path_ok"], True)
        self.assertEqual(body["sheet"]["name"], "Ysolde Varn")

    def test_crlf_file(self):
        self.put("crlf.md", "Ysolde.md")
        r = self.get("/sheet/Ysolde")
        self.assert_route_exists(r)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["sheet"], _parse(_fixture_text("template_filled.md")))

    def test_gbk_file(self):
        self.put("gbk.md", "Lin.md")
        r = self.get("/sheet/Lin")
        self.assert_route_exists(r)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        s = r.get_json()["sheet"]
        self.assertEqual(s["name"], "Lin Yue")
        self.assertIn("玉佩", _items(s))
        self.assertIn("家住长安", s["notes"])

    def test_get_never_writes_the_file(self):
        files = [self.put(f, n) for f, n in (("crlf.md", "Ysolde.md"), ("gbk.md", "Lin.md"),
                                              ("broken.md", "Grimble.md"))]
        before = [p.read_bytes() for p in files]
        for who in ("Ysolde", "Lin", "Grimble"):
            r = self.get(f"/sheet/{who}")
            self.assert_route_exists(r)
            self.assertEqual(r.status_code, 200, who)
        self.assertEqual([p.read_bytes() for p in files], before, "GET /sheet changed a character file")

    def test_broken_sheet_still_served(self):
        self.put("broken.md", "Grimble.md")
        r = self.get("/sheet/Grimble")
        self.assert_route_exists(r)
        self.assertEqual(r.status_code, 200)
        s = r.get_json()["sheet"]
        self.assertIn("Skills", {p["title"] for p in s["plain"]})


if __name__ == "__main__":
    unittest.main()
