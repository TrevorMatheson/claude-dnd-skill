"""QA verify S5: a rewind must not put exact monster HP or hidden tokens back
on the screens. Snapshots taken before the v2 map cleaning hold the raw map
(AC3, AC9, AC12). Also: hp wins over a contradicting hp_state."""
import importlib.util
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(REPO / "skills" / "dnd" / "scripts"))

RAW_MAP = {"grid": {"w": 6, "h": 4}, "cells": ["......"] * 4, "image": None, "round": 1,
           "tokens": [{"name": "Havoc", "x": 1, "y": 1, "kind": "pc", "hp": [20, 30]},
                      {"name": "Orc", "x": 3, "y": 1, "kind": "npc", "hp": [3, 7]},
                      {"name": "Goblin", "x": 4, "y": 1, "kind": "npc", "hidden": True, "hp": [7, 7]}]}


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_rwc", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RewindCleansMap(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def test_rewind_restores_a_cleaned_map(self):
        a = self.app
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            camp = root / "camp"
            camp.mkdir()
            sent = []
            with mock.patch.object(a, "QUEUE_FILE", str(root / ".input_queue")), \
                    mock.patch.object(a, "_active_campaign_name", return_value="camp"), \
                    mock.patch.object(a, "_find_campaign", return_value=camp), \
                    mock.patch.object(a, "_broadcast", side_effect=sent.append), \
                    mock.patch.object(a, "_persist_log"), mock.patch.object(a, "_persist_tail"), \
                    mock.patch.object(a, "_persist_stats"):
                a._current_stats = {"map": dict(RAW_MAP)}     # as stored before the v2 cleaning
                a._rewind_mark([("Havoc", "I charge")])
                a._current_stats = {}
                self.assertEqual(self.client.post("/rewind").status_code, 200)
                msg = [m["rewind"] for m in sent if "rewind" in m][0]
                for where, mp in (("broadcast", msg["stats"]["map"]), ("stored", a._current_stats["map"])):
                    names = {t["name"]: t for t in mp["tokens"]}
                    self.assertNotIn("Goblin", names, where)
                    self.assertNotIn("hp", names["Orc"], where)
                    self.assertEqual(names["Orc"].get("hp_state"), "bloodied", where)

    def test_hp_beats_contradicting_hp_state(self):
        m = dict(RAW_MAP, tokens=[{"name": "Orc", "x": 1, "y": 1, "kind": "npc",
                                   "hp": [2, 7], "hp_state": "healthy"}])
        tok = self.app._clean_map(m)["tokens"][0]
        self.assertEqual(tok.get("hp_state"), "bloodied")


class OldPageReadsHpState(unittest.TestCase):
    """The old page (index.html) draws a monster's ring from hp_state now
    that monsters carry no numbers."""

    def test_bm_hp_reads_hp_state(self):
        html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        start = html.index("function _bmHp(t)")
        body = html[start:html.index("\n}\n", start)]
        self.assertIn("hp_state", body)


if __name__ == "__main__":
    unittest.main()
