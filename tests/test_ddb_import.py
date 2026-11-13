"""ddb_import.py flags the sheet problems that surfaced mid-session in the live test.

The fixture follows the shape of D&D Beyond's character-service v5 JSON
(only the fields the script reads). It has not been checked against a live
character in this test; the script skips missing fields rather than guessing.
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ddb_import as ddb  # noqa: E402


def _char(**over):
    d = {
        "name": "Havoc",
        "race": {"fullName": "Human"},
        "background": {"definition": {"name": "Soldier", "grantedFeats": [{"name": "Savage Attacker"}]}},
        "classes": [{"level": 1, "definition": {"name": "Fighter"}, "subclassDefinition": None}],
        "stats": [{"id": i, "value": v} for i, v in zip(range(1, 7), (15, 13, 14, 8, 12, 10))],
        "bonusStats": [{"id": i, "value": None} for i in range(1, 7)],
        "overrideStats": [{"id": i, "value": None} for i in range(1, 7)],
        "modifiers": {"background": [{"type": "bonus", "subType": "strength-score", "value": 2},
                                     {"type": "bonus", "subType": "constitution-score", "value": 1}],
                      "class": []},
        "baseHitPoints": 10, "bonusHitPoints": None, "overrideHitPoints": None,
        "inventory": [
            {"equipped": True, "quantity": 1, "definition": {"name": "Longsword", "filterType": "Weapon", "canEquip": True}},
            {"equipped": True, "quantity": 1, "definition": {"name": "Chain Mail", "filterType": "Armor", "armorClass": 16, "canEquip": True}},
        ],
        "classSpells": [],
        "feats": [{"definition": {"name": "Savage Attacker"}}],
    }
    d.update(over)
    return d


class TestReading(unittest.TestCase):

    def test_clean_sheet(self):
        r = ddb.check(_char(), "2024")
        self.assertEqual(r["issues"], [])
        self.assertEqual(r["abilities"]["STR"], 17)       # 15 + 2 background
        self.assertEqual(r["abilities"]["CON"], 15)
        self.assertEqual(r["hp_max"], 12)                 # 10 + CON +2
        self.assertEqual(r["level"], 1)

    def test_unequipped_armor_flagged(self):
        inv = _char()["inventory"]
        inv[1]["equipped"] = False
        r = ddb.check(_char(inventory=inv))
        self.assertTrue(any("Chain Mail" in i["issue"] and "not equipped" in i["issue"] for i in r["issues"]))

    def test_no_spells_prepared(self):
        c = _char(classes=[{"level": 1, "definition": {"name": "Cleric"}}],
                  classSpells=[{"spells": [
                      {"prepared": False, "definition": {"name": "Bless", "level": 1}},
                      {"prepared": False, "definition": {"name": "Guidance", "level": 0}}]}])
        r = ddb.check(c, "2014")
        self.assertTrue(any("none are prepared" in i["issue"] for i in r["issues"]))

    def test_2024_sorcerer_prepares_too(self):
        c = _char(classes=[{"level": 1, "definition": {"name": "Sorcerer"}}],
                  classSpells=[{"spells": [{"prepared": False, "definition": {"name": "Shield", "level": 1}}]}])
        self.assertTrue(ddb.check(c, "2024")["issues"])
        self.assertFalse(ddb.check(c, "2014")["issues"])

    def test_mastery_for_missing_weapon(self):
        c = _char(modifiers={"class": [{"type": "weapon-mastery", "subType": "battleaxe",
                                        "friendlySubtypeName": "Battleaxe"}]})
        r = ddb.check(c, "2024")
        self.assertTrue(any("Battleaxe" in i["issue"] for i in r["issues"]))

    def test_2014_background_on_2024(self):
        c = _char(background={"definition": {"name": "Acolyte", "isLegacy": True}}, feats=[])
        r = ddb.check(c, "2024")
        self.assertTrue(any("2014 background" in i["issue"] for i in r["issues"]))

    def test_unfinished_sheet(self):
        r = ddb.check({"name": "", "classes": [], "stats": []})
        text = " ".join(i["issue"] for i in r["issues"])
        self.assertIn("no name", text)
        self.assertIn("Character Not Ready", text)
        self.assertIn("no ability scores", text)

    def test_ids(self):
        self.assertEqual(ddb.character_id("https://www.dndbeyond.com/characters/123456789"), "123456789")
        self.assertEqual(ddb.character_id("https://www.dndbeyond.com/characters/123456789/builder"), "123456789")
        self.assertEqual(ddb.character_id("98765432"), "98765432")
        self.assertIsNone(ddb.character_id("havoc"))

    def test_service_wrapper_unwrapped(self):
        self.assertEqual(ddb.unwrap({"success": True, "data": {"name": "X"}})["name"], "X")
        with self.assertRaises(ddb.ImportFailed):
            ddb.unwrap({"success": False, "message": "private", "data": {}})

    def test_cli_with_saved_file(self):
        with tempfile.TemporaryDirectory() as t:
            f = pathlib.Path(t) / "havoc.json"
            f.write_text(json.dumps({"success": True, "data": _char()}), encoding="utf-8")
            r = subprocess.run([sys.executable, str(SCRIPTS / "ddb_import.py"), str(f), "--ruleset", "2024"],
                               capture_output=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No problems found", r.stdout)


if __name__ == "__main__":
    unittest.main()
