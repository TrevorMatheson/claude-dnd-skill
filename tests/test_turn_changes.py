"""Phone sheet, plan step S6: `changes` in the turn call (send.py --turn).

AC5 (Claude's path) — A turn call carrying HP, temp HP, a slot, ... updates the
       character file, the phone sheet and the TV Party panel.
D6   — Claude tracks resources by itself; the server applies them.
D10  — Every change is stored as a difference, never an overwrite.
Plan approval note 1 — display offline: send.py writes no file, prints
       `NOT APPLIED (display offline): ...` lines and exits 0.

Order pinned by the plan: shape check, then a dry run on the display (a
refusal rejects the whole turn, nothing sent), then the chunk posts, then
/stats, then the real POST /sheet/<name>/change last.

send.py's posts (_post for chunks and stats, _post_json for sheet changes)
are routed into the Flask test client, so the real server and sheet.py run
end to end against a scratch campaign.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import sys
import unittest
import urllib.parse
import uuid
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "sheets"
SKILL_MD = REPO / "skills" / "dnd" / "SKILL.md"
for _p in (str(DISPLAY), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_turn_changes",
                                                  str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.argv = ["send.py"]
import send  # noqa: E402

HP_LINE = "- **HP:** 14 / 17 | **Temp HP:** 3"


def _havoc_text() -> str:
    """template_filled.md renamed to Havoc, HP 12/17, no temp HP."""
    t = (FIX / "template_filled.md").read_bytes().decode("utf-8")
    assert t.startswith("# Ysolde Varn"), "fixture heading moved"
    assert HP_LINE in t, "fixture HP line moved"
    t = t.replace("# Ysolde Varn", "# Havoc", 1)
    return t.replace(HP_LINE, "- **HP:** 12 / 17 | **Temp HP:** 0")


class _Base(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def setUp(self):
        # Scratch campaign with Havoc's sheet, made active.
        root = pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"])
        self.camp_name = f"turnchg-{uuid.uuid4().hex[:8]}"
        self.camp = root / "campaigns" / self.camp_name
        chars = self.camp / "characters"
        chars.mkdir(parents=True)
        (self.camp / "state.md").write_text("# State\n\n## Session Flags\n", encoding="utf-8")
        self.addCleanup(shutil.rmtree, str(self.camp), True)
        self.sheet_path = chars / "Havoc.md"
        self.sheet_path.write_bytes(_havoc_text().encode("utf-8"))
        self.original = self.sheet_path.read_bytes()

        camp_file = pathlib.Path(self.app.CAMP_FILE)
        camp_file.parent.mkdir(parents=True, exist_ok=True)
        old = camp_file.read_bytes() if camp_file.exists() else None
        camp_file.write_text(self.camp_name, encoding="utf-8")
        self.addCleanup(lambda: camp_file.write_bytes(old) if old is not None else camp_file.unlink())

        old_tok = self.app._lan_token
        self.addCleanup(setattr, self.app, "_lan_token", old_tok)
        self.app._lan_token = None
        self.app._rate_buckets.clear()

        with self.app._stats_lock:
            saved = dict(self.app._current_stats)
            self.app._current_stats.clear()
            self.app._current_stats.update({"players": [{"name": "Havoc", "hp": {"current": 12, "max": 17}},
                                                        {"name": "Anony"}]})

        def restore():
            with self.app._stats_lock:
                self.app._current_stats.clear()
                self.app._current_stats.update(saved)
        self.addCleanup(restore)
        self.app._awaiting = None
        self.broadcasts = []
        bp = mock.patch.object(self.app, "_broadcast", side_effect=self.broadcasts.append)
        bp.start()
        self.addCleanup(bp.stop)
        send._SEND_LOG.clear()
        self.order: list = []      # ("chunk"|"stats"|"change-dry"|"change", body)

    # Routed into Flask ──────────────────────────────────────────────────────

    def _fake_post(self, url, data, token):
        path = urllib.parse.urlparse(url).path
        r = self.client.post(path, data=data, content_type="application/json")
        self.order.append(({"/chunk": "chunk", "/stats": "stats"}.get(path, path), json.loads(data)))
        send._SEND_LOG.append({"endpoint": path, "ok": r.status_code < 300})
        return r.status_code < 300

    def _fake_post_json(self, url, body, token):
        path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
        r = self.client.post(path, json=body, environ_base={"REMOTE_ADDR": "127.0.0.1"})
        kind = "change-dry" if body.get("dry_run") else "change"
        self.order.append((kind, body, path))
        return r.status_code, (r.get_json(silent=True) or {})

    def run_turn(self, turn, post_json=None):
        err = io.StringIO()
        with mock.patch.object(send, "_post", side_effect=self._fake_post), \
                mock.patch.object(send, "_post_json", side_effect=post_json or self._fake_post_json), \
                mock.patch.object(send, "dice_request", return_value=0), \
                contextlib.redirect_stderr(err):
            rc = send.send_turn(turn)
        return rc, err.getvalue()

    def kinds(self):
        return [o[0] for o in self.order]


TURN = {
    "blocks": [{"action": "Havoc", "text": "Havoc lunges."},
               {"text": "The rat bites back."}],
    "changes": [{"name": "Havoc", "damage": 7}],
    "turn": "Anony", "awaiting": ["Anony"],
}


class TestOrder(_Base):
    """AC5 / D6: dry run before any chunk; chunks, /stats, real change last."""

    def test_dry_run_then_chunks_then_stats_then_change(self):
        rc, err = self.run_turn(json.loads(json.dumps(TURN)))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.kinds(), ["change-dry", "chunk", "chunk", "stats", "change"], err)

    def test_change_posts_to_named_character(self):
        self.run_turn(json.loads(json.dumps(TURN)))
        change_paths = [o[2] for o in self.order if o[0].startswith("change")]
        self.assertEqual(change_paths, ["/sheet/Havoc/change"] * 2)

    def test_file_written_as_difference(self):
        rc, err = self.run_turn(json.loads(json.dumps(TURN)))
        self.assertEqual(rc, 0, err)
        text = self.sheet_path.read_bytes().decode("utf-8")
        self.assertIn("- **HP:** 5 / 17", text)

    def test_tv_record_from_file_wins_over_stats_total(self):
        # stats.players carries an absolute (stale) HP; the change lands last.
        turn = json.loads(json.dumps(TURN))
        turn["stats"] = {"players": [{"name": "Havoc", "hp": {"current": 12, "max": 17}}]}
        rc, err = self.run_turn(turn)
        self.assertEqual(rc, 0, err)
        with self.app._stats_lock:
            p = next(x for x in self.app._current_stats["players"] if x["name"] == "Havoc")
        self.assertEqual(p["hp"]["current"], 5)
        self.assertIn("temp", p["hp"])

    def test_no_changes_key_no_sheet_posts(self):
        turn = {k: v for k, v in TURN.items() if k != "changes"}
        rc, _ = self.run_turn(turn)
        self.assertEqual(rc, 0)
        self.assertEqual(self.kinds(), ["chunk", "chunk", "stats"])


class TestRealPostBody(_Base):
    """D10: the real post carries an op id (repeat-safe) and source "dm"."""

    def test_op_and_source(self):
        self.run_turn(json.loads(json.dumps(TURN)))
        real = [o[1] for o in self.order if o[0] == "change"]
        self.assertEqual(len(real), 1)
        body = real[0]
        self.assertEqual(body.get("source"), "dm")
        self.assertTrue(str(body.get("op") or "").strip(), body)
        self.assertFalse(body.get("dry_run"), body)
        self.assertEqual(body.get("changes"), [{"name": "Havoc", "damage": 7}])

    def test_dry_run_body(self):
        self.run_turn(json.loads(json.dumps(TURN)))
        dry = [o[1] for o in self.order if o[0] == "change-dry"]
        self.assertEqual(len(dry), 1)
        self.assertEqual(dry[0].get("source"), "dm")
        self.assertTrue(dry[0].get("dry_run"))
        # The dry run must not have written the file before the chunks.
        self.assertEqual(self.kinds()[0], "change-dry")

    def test_op_differs_between_turns(self):
        self.run_turn(json.loads(json.dumps(TURN)))
        self.run_turn(json.loads(json.dumps(TURN)))
        ops = [o[1]["op"] for o in self.order if o[0] == "change"]
        self.assertEqual(len(ops), 2)
        self.assertNotEqual(ops[0], ops[1])


class TestResultLines(_Base):
    """Results print on stderr like `Havoc: HP 12→5`."""

    def test_hp_line(self):
        rc, err = self.run_turn(json.loads(json.dumps(TURN)))
        self.assertEqual(rc, 0, err)
        self.assertIn("Havoc: HP 12→5", err)

    def test_temp_hp_line(self):
        turn = json.loads(json.dumps(TURN))
        turn["changes"] = [{"name": "Havoc", "temp_hp": 5}]
        rc, err = self.run_turn(turn)
        self.assertEqual(rc, 0, err)
        self.assertIn("Havoc: temp HP 0→5", err)


class TestBadEntries(_Base):
    """A bad entry exits 2 and posts nothing (no chunk, no dry run)."""

    BAD = [
        ("two resource keys", [{"name": "Havoc", "damage": 3, "heal": 2}]),
        ("build field str", [{"name": "Havoc", "str": 2}]),
        ("no name", [{"damage": 3}]),
        ("blank name", [{"name": "  ", "damage": 3}]),
        ("not a list", {"name": "Havoc", "damage": 3}),
        ("entry not a dict", ["Havoc damage 3"]),
    ]

    def test_each_bad_entry(self):
        for label, changes in self.BAD:
            with self.subTest(label):
                self.order.clear()
                turn = json.loads(json.dumps(TURN))
                turn["changes"] = changes
                with mock.patch.object(send, "_post") as p, \
                        mock.patch.object(send, "_post_json") as pj, \
                        contextlib.redirect_stderr(io.StringIO()) as err:
                    rc = send.send_turn(turn)
                self.assertEqual(rc, 2, f"{label}: {err.getvalue()}")
                p.assert_not_called()
                pj.assert_not_called()
                self.assertEqual(self.sheet_path.read_bytes(), self.original)

    def test_bad_second_entry_rejects_whole_turn(self):
        turn = json.loads(json.dumps(TURN))
        turn["changes"] = [{"name": "Havoc", "damage": 3}, {"name": "Havoc", "str": 2}]
        with mock.patch.object(send, "_post") as p, mock.patch.object(send, "_post_json") as pj, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(send.send_turn(turn), 2)
        p.assert_not_called()
        pj.assert_not_called()


class TestDryRunRefusal(_Base):
    """A dry-run refusal exits 2, reason on stderr, no chunk posted."""

    def test_fake_409(self):
        def refuse(url, body, token):
            self.order.append(("change-dry" if body.get("dry_run") else "change", body, url))
            return 409, {"ok": False, "reason": "has 3 gp"}
        turn = json.loads(json.dumps(TURN))
        turn["changes"] = [{"name": "Havoc", "gold": {"gp": -5}}]
        rc, err = self.run_turn(turn, post_json=refuse)
        self.assertEqual(rc, 2, err)
        self.assertIn("has 3 gp", err)
        self.assertEqual(self.kinds(), ["change-dry"], "nothing but the dry run is posted")
        self.assertEqual(self.sheet_path.read_bytes(), self.original)

    def test_real_server_refusal(self):
        # Fixture has 15 gp; spending 500 must be refused by the real dry run.
        turn = json.loads(json.dumps(TURN))
        turn["changes"] = [{"name": "Havoc", "gold": {"gp": -500}}]
        rc, err = self.run_turn(turn)
        self.assertEqual(rc, 2, err)
        self.assertEqual(self.kinds(), ["change-dry"])
        self.assertEqual(self.sheet_path.read_bytes(), self.original)

    def test_second_character_refused_sends_nothing(self):
        # Mira has no sheet: the dry run for her refuses, Havoc's is not applied.
        turn = json.loads(json.dumps(TURN))
        turn["changes"] = [{"name": "Havoc", "damage": 3}, {"name": "Mira", "damage": 1}]
        rc, err = self.run_turn(turn)
        self.assertEqual(rc, 2, err)
        self.assertNotIn("chunk", self.kinds())
        self.assertNotIn("change", self.kinds())
        self.assertEqual(self.sheet_path.read_bytes(), self.original)


class TestDisplayOffline(_Base):
    """Approval note 1: NOT APPLIED lines on stderr, exit 0, no file written."""

    def test_offline(self):
        turn = json.loads(json.dumps(TURN))
        turn["changes"] = [{"name": "Havoc", "damage": 7}, {"name": "Havoc", "temp_hp": 5}]
        err = io.StringIO()
        with mock.patch.object(send, "_display_up", return_value=False), \
                mock.patch.object(send.urllib.request, "urlopen",
                                  side_effect=AssertionError("no network when offline")), \
                mock.patch.object(send, "dice_request", return_value=0), \
                contextlib.redirect_stderr(err):
            rc = send.send_turn(turn)
        out = err.getvalue()
        self.assertEqual(rc, 0, out)
        lines = [ln for ln in out.splitlines() if "NOT APPLIED (display offline)" in ln]
        self.assertEqual(len(lines), 2, out)
        self.assertIn("Havoc", lines[0])
        self.assertIn("damage", lines[0])
        self.assertIn("7", lines[0])
        self.assertIn("temp_hp", lines[1])
        self.assertEqual(self.sheet_path.read_bytes(), self.original, "send.py must not write the file")


class TestSkillExample(unittest.TestCase):
    """The SKILL.md turn example parses and its `changes` validate."""

    def _example(self) -> dict:
        md = SKILL_MD.read_text(encoding="utf-8")
        at = md.find("send.py --turn")
        self.assertGreaterEqual(at, 0, "SKILL.md has no send.py --turn")
        m = re.compile(r"```json\s*\n(.*?)\n```", re.S).search(md, at)
        self.assertIsNotNone(m, "no ```json block after send.py --turn")
        return json.loads(m.group(1))

    def test_example_has_changes_and_validates(self):
        turn = self._example()
        self.assertIn("changes", turn)
        by_name = send._turn_changes(turn)
        self.assertTrue(by_name)
        self.assertNotIn("hp", json.dumps((turn.get("stats") or {}).get("players") or []),
                         "the example must not put absolute hp in stats.players")

    def test_example_accepted_by_send_turn(self):
        turn = self._example()
        send._SEND_LOG.clear()
        with mock.patch.object(send, "_post", return_value=True) as p, \
                mock.patch.object(send, "_post_json", return_value=(200, {"ok": True, "results": []})), \
                mock.patch.object(send, "dice_request", return_value=0), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            rc = send.send_turn(turn)
        self.assertEqual(rc, 0, err.getvalue())
        self.assertTrue(p.called)

    def test_every_documented_entry_validates(self):
        md = SKILL_MD.read_text(encoding="utf-8")
        para = next((ln for ln in md.splitlines() if ln.startswith("**Resources go in `changes`")), None)
        self.assertIsNotNone(para, "SKILL.md lost the 'Resources go in `changes`' paragraph")
        entries = [json.loads(s) for s in re.findall(r"`(\{\"name\"[^`]*\})`", para)]
        self.assertGreaterEqual(len(entries), 8, entries)
        for e in entries:
            with self.subTest(e):
                send._turn_changes({"changes": [e]})
