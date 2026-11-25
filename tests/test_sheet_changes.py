"""Phone sheet, plan step S4: differences applied to the file, with a live update.

AC5 (server half) — A player's change to their own sheet lands in the file and
       the TV's Party panel shows it.
AC6 (server half) — A player can't change build fields (ability scores, level,
       max HP) and can't send `damage`; each entry names one resource.
AC8  — A heal and damage that arrive at the same moment both land.
D6   — Claude's turn call sends differences (source "dm", game computer only).
D7   — What a player may change by hand.
D10  — Every change is a difference, applied under the cast lock; all or
       nothing; a repeated op applies once.

Interface pinned (skills/dnd/display/sheet.py):
  RESOURCE_KEYS, PLAYER_FIELDS, DM_FIELDS, validate(entry, fields),
  apply(text, changes, fields) -> (new_text, results)  results: [{field,
      before, after, ok, reason?, ...}]
  change(campaign, name, changes, source, dry_run) -> {ok, results, reason?, sheet?}
Route (dnd-display-app.py):
  POST /sheet/<name>/change  body {op, source, changes, dry_run}
  200 ok / 409 refused (reason, nothing written) / 400 bad shape or another
  character / 403 token, or source dm off the game computer / 202 pending
  device / 429 on the `sheet:<ip>` bucket (120 per minute, separate).
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import pathlib
import shutil
import sys
import threading
import unittest
import uuid
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "sheets"
for _p in (str(DISPLAY), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

LAN = {"REMOTE_ADDR": "192.168.1.5"}
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}


def _sheet():
    return importlib.import_module("sheet")


def _fixture_text(name: str) -> str:
    return (FIX / name).read_bytes().decode("utf-8")


TEMPLATE = None


def _template() -> str:
    global TEMPLATE
    if TEMPLATE is None:
        TEMPLATE = _fixture_text("template_filled.md")
    return TEMPLATE


def _apply(changes, text=None, fields=None):
    s = _sheet()
    args = (text if text is not None else _template(), changes)
    return s.apply(*args, fields) if fields is not None else s.apply(*args)


def _parse(text):
    return _sheet().parse(text)


def _items(sheet: dict) -> dict:
    out = {}
    for g in sheet["inventory"]["groups"]:
        for it in g["items"]:
            out[it["name"]] = (it["qty"], g["label"])
    return out


def _with_hp(text: str, cur: int, temp: int) -> str:
    old = "- **HP:** 14 / 17 | **Temp HP:** 3"
    assert old in text, "fixture HP line moved"
    return text.replace(old, f"- **HP:** {cur} / 17 | **Temp HP:** {temp}")


def _one(results, field):
    hits = [r for r in results if r.get("field") == field]
    assert hits, f"no result for {field}: {results}"
    return hits[-1]


# ── apply(): each field ────────────────────────────────────────────────────

class TestApplyFields(unittest.TestCase):
    """Start: HP 14/17, temp 3, slots 1st 4 total 1 used, 2nd 2/0, hit dice
    3d6 remaining 2, death saves 1 success 2 failures, no Inspiration line, no
    Conditions line, Potion of Healing ×2, 15gp 4sp 0cp."""

    def ok(self, results):
        self.assertTrue(results and all(r.get("ok") for r in results), results)

    def test_damage_takes_temp_first(self):
        new, res = _apply([{"damage": 7}])
        self.ok(res)
        c = _parse(new)["combat"]
        self.assertEqual(c["temp_hp"], 0)
        self.assertEqual(c["hp"], {"current": 10, "max": 17})
        hp = _one(res, "hp")
        self.assertEqual((hp["before"], hp["after"]), (14, 10))
        t = _one(res, "temp_hp")
        self.assertEqual((t["before"], t["after"]), (3, 0))

    def test_damage_smaller_than_temp_leaves_hp(self):
        new, res = _apply([{"damage": 2}])
        self.ok(res)
        c = _parse(new)["combat"]
        self.assertEqual((c["hp"]["current"], c["temp_hp"]), (14, 1))

    def test_damage_clamps_at_zero(self):
        new, res = _apply([{"damage": 99}])
        self.ok(res)
        self.assertEqual(_parse(new)["combat"]["hp"]["current"], 0)
        self.assertEqual(_one(res, "hp")["after"], 0)

    def test_heal_clamps_at_max(self):
        new, res = _apply([{"heal": 10}])
        self.ok(res)
        self.assertEqual(_parse(new)["combat"]["hp"]["current"], 17)
        hp = _one(res, "hp")
        self.assertEqual((hp["before"], hp["after"]), (14, 17))

    def test_hp_delta_both_ways_and_clamped(self):
        new, res = _apply([{"hp": -4}])
        self.ok(res)
        self.assertEqual(_parse(new)["combat"]["hp"]["current"], 10)
        self.assertEqual((_one(res, "hp")["before"], _one(res, "hp")["after"]), (14, 10))
        new, res = _apply([{"hp": 2}])
        self.assertEqual(_parse(new)["combat"]["hp"]["current"], 16)
        new, _ = _apply([{"hp": 50}])
        self.assertEqual(_parse(new)["combat"]["hp"]["current"], 17)
        new, _ = _apply([{"hp": -50}])
        self.assertEqual(_parse(new)["combat"]["hp"]["current"], 0)

    def test_hp_delta_does_not_touch_temp(self):
        new, _ = _apply([{"hp": -5}])
        self.assertEqual(_parse(new)["combat"]["temp_hp"], 3)

    def test_temp_hp_delta_and_floor(self):
        new, res = _apply([{"temp_hp": 5}])
        self.ok(res)
        self.assertEqual(_parse(new)["combat"]["temp_hp"], 8)
        self.assertEqual((_one(res, "temp_hp")["before"], _one(res, "temp_hp")["after"]), (3, 8))
        new, _ = _apply([{"temp_hp": -10}])
        self.assertEqual(_parse(new)["combat"]["temp_hp"], 0)

    def test_slot_spend_and_restore_clamped(self):
        new, res = _apply([{"slot": 1, "delta": -1}])
        self.ok(res)
        self.assertEqual(_parse(new)["slots"][0], {"level": 1, "total": 4, "used": 2, "label": "1st"})
        r = _one(res, "slot")
        self.assertEqual((r["before"], r["after"]), (3, 2), "slot before/after count slots left")
        new, _ = _apply([{"slot": 1, "delta": 5}])
        self.assertEqual(_parse(new)["slots"][0]["used"], 0, "restoring past full must clamp")
        new, _ = _apply([{"slot": 2, "delta": -9}])
        self.assertEqual(_parse(new)["slots"][1]["used"], 2, "spending past empty must clamp")

    def test_slot_missing_level_refused(self):
        new, res = _apply([{"slot": 5, "delta": -1}])
        self.assertFalse(res[0]["ok"])
        self.assertEqual(new.replace("\r\n", "\n"), _template().replace("\r\n", "\n"))

    def test_hit_dice_clamped(self):
        new, res = _apply([{"hit_dice": -1}])
        self.ok(res)
        self.assertEqual(_parse(new)["combat"]["hit_dice"]["remaining"], 1)
        self.assertEqual((_one(res, "hit_dice")["before"], _one(res, "hit_dice")["after"]), (2, 1))
        new, _ = _apply([{"hit_dice": 9}])
        self.assertEqual(_parse(new)["combat"]["hit_dice"]["remaining"], 3)
        new, _ = _apply([{"hit_dice": -9}])
        self.assertEqual(_parse(new)["combat"]["hit_dice"]["remaining"], 0)

    def test_item_decrement(self):
        new, res = _apply([{"item": "Potion of Healing", "delta": -1}])
        self.ok(res)
        self.assertEqual(_items(_parse(new))["Potion of Healing"][0], 1)
        self.assertEqual((_one(res, "item")["before"], _one(res, "item")["after"]), (2, 1))

    def test_item_removed_at_zero(self):
        new, res = _apply([{"item": "Potion of Healing", "delta": -2}])
        self.ok(res)
        items = _items(_parse(new))
        self.assertNotIn("Potion of Healing", items)
        self.assertNotIn("Potion of Healing", new)
        self.assertIn("Ink and quill", items, "neighbouring items must survive")

    def test_item_new_goes_under_adventuring_gear(self):
        new, res = _apply([{"item": "Silver Mirror", "delta": 1}])
        self.ok(res)
        items = _items(_parse(new))
        self.assertEqual(items.get("Silver Mirror"), (1, "Adventuring Gear"), items)
        new, _ = _apply([{"item": "Caltrops", "delta": 3}])
        self.assertEqual(_items(_parse(new)).get("Caltrops"), (3, "Adventuring Gear"))

    def test_item_absent_with_negative_delta_refused(self):
        new, res = _apply([{"item": "Bag of Holding", "delta": -1}])
        self.assertFalse(res[0]["ok"])
        self.assertTrue(res[0].get("reason"))

    def test_item_name_prefix_never_leaves_a_fragment(self):
        # Claude's turn call may say "Potion" for "Potion of Healing ×2", and
        # an imported sheet lists "Arcane focus (rod)". A shorter name must
        # either be refused or change the whole item; it must never cut the
        # name in two and leave "of Healing ×2" or "(rod)" behind.
        cases = (("template_filled.md", "Potion"), ("imported.md", "Arcane focus"))
        for fixture, short in cases:
            with self.subTest(item=short):
                text = _fixture_text(fixture)
                was = set(_items(_parse(text)))
                new, res = _apply([{"item": short, "delta": -1}], text=text)
                now = set(_items(_parse(new)))
                self.assertLessEqual(now, was, f"a decrement made new item names: {sorted(now - was)}")

    def test_item_overspend_refused(self):
        _, res = _apply([{"item": "Potion of Healing", "delta": -3}])
        self.assertFalse(res[0]["ok"])

    def test_gold(self):
        new, res = _apply([{"gold": {"gp": -5, "sp": 2}}])
        self.ok(res)
        inv = _parse(new)["inventory"]
        self.assertEqual(_sheet().coins(new.split("**Currency:**", 1)[1].split("\n", 1)[0]),
                         {"gp": 10, "sp": 6, "cp": 0})
        r = _one(res, "gold")
        self.assertEqual(r["before"].get("gp"), 15)
        self.assertEqual(r["after"].get("gp"), 10)
        self.assertIsNotNone(inv)

    def test_condition_on_and_off(self):
        new, res = _apply([{"condition": "Poisoned", "on": True}])
        self.ok(res)
        self.assertEqual(_parse(new)["combat"]["conditions"], ["Poisoned"])
        r = _one(res, "condition")
        self.assertEqual((r["before"], r["after"]), (False, True))
        new2, res2 = _apply([{"condition": "poisoned", "on": False}], text=new)
        self.ok(res2)
        self.assertEqual(_parse(new2)["combat"]["conditions"], [])

    def test_death_saves_clamped(self):
        new, res = _apply([{"death_saves": "failure", "delta": 1}])
        self.ok(res)
        self.assertEqual(_parse(new)["combat"]["death_saves"], {"successes": 1, "failures": 3})
        self.assertEqual((_one(res, "death_saves")["before"], _one(res, "death_saves")["after"]), (2, 3))
        new, _ = _apply([{"death_saves": "failure", "delta": 5}])
        self.assertEqual(_parse(new)["combat"]["death_saves"]["failures"], 3)
        new, _ = _apply([{"death_saves": "success", "delta": -5}])
        self.assertEqual(_parse(new)["combat"]["death_saves"]["successes"], 0)

    def test_inspiration(self):
        new, res = _apply([{"inspiration": True}])
        self.ok(res)
        self.assertIs(_parse(new)["combat"]["inspiration"], True)
        new2, _ = _apply([{"inspiration": False}], text=new)
        self.assertIs(_parse(new2)["combat"]["inspiration"], False)

    def test_note_on_and_off(self):
        new, res = _apply([{"note": "owes Brann 5 gp", "on": True}])
        self.ok(res)
        self.assertIn("owes Brann 5 gp", _parse(new)["notes"])
        self.assertIn("Owes the ferryman", new, "the existing note must stay")
        new2, res2 = _apply([{"note": "owes Brann 5 gp", "on": False}], text=new)
        self.ok(res2)
        self.assertNotIn("owes Brann 5 gp", new2)

    def test_other_sections_untouched(self):
        new, _ = _apply([{"damage": 7}, {"slot": 1, "delta": -1}, {"item": "Rations", "delta": -1}])
        a, b = _parse(_template()), _parse(new)
        for k in ("abilities", "identity", "saves", "skills", "attacks", "features", "spells"):
            self.assertEqual(a[k], b[k], k)


# ── shape and permission (AC6, D7) ─────────────────────────────────────────

class TestShapeAndFields(unittest.TestCase):
    def test_player_cannot_send_build_fields_or_damage(self):
        s = _sheet()
        for entry in ({"max_hp": 5}, {"str": 18}, {"level": 4}, {"damage": 3}):
            with self.subTest(entry=entry):
                new, res = s.apply(_template(), [entry], s.PLAYER_FIELDS)
                self.assertFalse(res[0]["ok"], res)
                self.assertTrue(res[0].get("reason"))
                self.assertEqual(new, _template())

    def test_damage_not_in_player_fields_but_in_dm_fields(self):
        s = _sheet()
        self.assertNotIn("damage", s.PLAYER_FIELDS)
        self.assertIn("damage", s.DM_FIELDS)
        self.assertIn("hp", s.PLAYER_FIELDS)
        for build in ("max_hp", "str", "level"):
            self.assertNotIn(build, s.DM_FIELDS)

    def test_two_resource_keys_in_one_entry_refused(self):
        s = _sheet()
        with self.assertRaises(s.ChangeError):
            s.validate({"hp": -1, "temp_hp": 2})
        _, res = s.apply(_template(), [{"hp": -1, "temp_hp": 2}])
        self.assertFalse(res[0]["ok"])

    def test_negative_damage_and_heal_refused(self):
        _, res = _apply([{"damage": -3}])
        self.assertFalse(res[0]["ok"])
        _, res = _apply([{"heal": -3}])
        self.assertFalse(res[0]["ok"])


# ── change(): file on disk, all or nothing, concurrency ─────────────────────

class _CampaignMixin:
    def make_campaign(self):
        root = pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"])
        self.camp_name = f"chg-{uuid.uuid4().hex[:8]}"
        self.camp = root / "campaigns" / self.camp_name
        self.chars = self.camp / "characters"
        self.chars.mkdir(parents=True)
        (self.camp / "state.md").write_text("# State\n\n## Session Flags\n", encoding="utf-8")
        self.addCleanup(shutil.rmtree, str(self.camp), True)
        self.roster = root / "characters"

    def put(self, fixture: str, filename: str, text: str = None) -> pathlib.Path:
        p = self.chars / filename
        if text is not None:
            p.write_bytes(text.encode("utf-8"))
        else:
            p.write_bytes((FIX / fixture).read_bytes())
        return p


class TestChange(_CampaignMixin, unittest.TestCase):
    def setUp(self):
        self.make_campaign()

    def test_change_writes_and_returns_sheet(self):
        p = self.put("template_filled.md", "Ysolde.md")
        out = _sheet().change(self.camp_name, "Ysolde", [{"damage": 7}], source="dm")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["sheet"]["combat"]["hp"]["current"], 10)
        self.assertEqual(_parse(p.read_bytes().decode("utf-8"))["combat"]["hp"]["current"], 10)

    def test_short_gold_refused_file_byte_identical(self):
        p = self.put("template_filled.md", "Ysolde.md")
        before = p.read_bytes()
        out = _sheet().change(self.camp_name, "Ysolde",
                              [{"hp": -2}, {"gold": {"gp": -50}}], source="player")
        self.assertFalse(out["ok"], out)
        self.assertTrue(out.get("reason"))
        self.assertEqual(p.read_bytes(), before, "a refused change must leave the file byte-for-byte")

    def test_dry_run_writes_nothing(self):
        p = self.put("template_filled.md", "Ysolde.md")
        before = p.read_bytes()
        out = _sheet().change(self.camp_name, "Ysolde", [{"hp": -2}], source="player", dry_run=True)
        self.assertTrue(out["ok"], out)
        self.assertEqual(p.read_bytes(), before)

    def test_player_source_refuses_build_fields_on_disk(self):
        p = self.put("template_filled.md", "Ysolde.md")
        before = p.read_bytes()
        for entry in ({"max_hp": 5}, {"str": 18}, {"level": 4}, {"damage": 3}):
            with self.subTest(entry=entry):
                out = _sheet().change(self.camp_name, "Ysolde", [entry], source="player")
                self.assertFalse(out["ok"], out)
                self.assertTrue(out.get("reason"))
                self.assertEqual(p.read_bytes(), before)

    def test_crlf_stays_crlf(self):
        p = self.put("crlf.md", "Ysolde.md")
        out = _sheet().change(self.camp_name, "Ysolde", [{"hp": -1}, {"item": "Lantern", "delta": 1},
                                                         {"note": "met the ferryman", "on": True}],
                              source="dm")
        self.assertTrue(out["ok"], out)
        raw = p.read_bytes()
        self.assertIn(b"\r\n", raw)
        self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"), "a bare LF crept into a CRLF file")
        self.assertEqual(_parse(raw.decode("utf-8"))["combat"]["hp"]["current"], 13)

    def test_gbk_readable_after_change(self):
        p = self.put("gbk.md", "Lin.md")
        out = _sheet().change(self.camp_name, "Lin", [{"item": "玉佩", "delta": -1}], source="player")
        self.assertTrue(out["ok"], out)
        text = _sheet().read_file(p)
        s = _parse(text)
        self.assertEqual(s["name"], "Lin Yue")
        self.assertEqual(_items(s)["玉佩"][0], 1)
        self.assertIn("家住长安", s["notes"])

    def test_roster_only_character_refused(self):
        who = f"Roster{uuid.uuid4().hex[:6]}"
        self.roster.mkdir(parents=True, exist_ok=True)
        rp = self.roster / f"{who}.md"
        rp.write_bytes((FIX / "imported.md").read_bytes())
        self.addCleanup(lambda: rp.unlink() if rp.exists() else None)
        before = rp.read_bytes()
        out = _sheet().change(self.camp_name, who, [{"hp": -1}], source="player")
        self.assertFalse(out["ok"], out)
        self.assertTrue(out.get("reason"))
        self.assertEqual(rp.read_bytes(), before)

    def test_ac8_damage_and_heal_at_once_both_land(self):
        s = _sheet()
        for rnd in range(6):
            with self.subTest(round=rnd):
                text = _with_hp(_template(), 10, 0)
                p = self.put(None, "Ysolde.md", text=text)
                gate = threading.Barrier(2)
                outs = {}

                def run(key, changes, source):
                    gate.wait()
                    outs[key] = s.change(self.camp_name, "Ysolde", changes, source=source)

                t1 = threading.Thread(target=run, args=("dm", [{"damage": 7}], "dm"))
                t2 = threading.Thread(target=run, args=("pl", [{"hp": 5}], "player"))
                t1.start(); t2.start(); t1.join(10); t2.join(10)
                self.assertTrue(outs.get("dm", {}).get("ok"), outs)
                self.assertTrue(outs.get("pl", {}).get("ok"), outs)
                final = _parse(_sheet().read_file(p))
                self.assertEqual(final["combat"]["hp"], {"current": 8, "max": 17},
                                 "damage 7 and hp +5 from 10 must end at 8")
                self.assertEqual(final["name"], "Ysolde Varn", "file no longer parses")
                self.assertEqual(len(final["skills"]), 18, "file damaged by the race")


# ── route ──────────────────────────────────────────────────────────────────

def _import_app():
    spec = importlib.util.spec_from_file_location(f"dnd_display_app_chg_{uuid.uuid4().hex[:6]}",
                                                  str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestChangeRoute(_CampaignMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.client = cls.app.app.test_client()

    def setUp(self):
        self.make_campaign()
        camp_file = pathlib.Path(self.app.CAMP_FILE)
        camp_file.parent.mkdir(parents=True, exist_ok=True)
        old = camp_file.read_bytes() if camp_file.exists() else None
        camp_file.write_text(self.camp_name, encoding="utf-8")
        self.addCleanup(lambda: camp_file.write_bytes(old) if old is not None else camp_file.unlink())
        old_tok = self.app._lan_token
        self.addCleanup(setattr, self.app, "_lan_token", old_tok)
        self.app._lan_token = None
        self.app._rate_buckets.clear()
        self.calls = []
        patcher = mock.patch.object(self.app, "_broadcast", side_effect=lambda p: self.calls.append(p))
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.app._stats_lock:
            saved = dict(self.app._current_stats)
        def restore():
            with self.app._stats_lock:
                self.app._current_stats.clear()
                self.app._current_stats.update(saved)
        self.addCleanup(restore)
        self.device = f"dev-{uuid.uuid4().hex[:10]}"
        self.p = self.put("template_filled.md", "Ysolde.md")

    def post(self, name="Ysolde", body=None, env=LOCAL, headers=None):
        h = {"X-DND-Device": self.device}
        h.update(headers or {})
        return self.client.post(f"/sheet/{name}/change", json=body or {}, environ_base=env, headers=h)

    def assert_routed(self, r):
        if r.status_code in (404, 405) and not (r.is_json and r.get_json(silent=True)):
            self.fail("POST /sheet/<name>/change is not routed")

    def test_player_change_applies(self):
        r = self.post(body={"source": "player", "changes": [{"hp": -3}]}, env=LAN)
        self.assert_routed(r)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(_parse(self.p.read_bytes().decode("utf-8"))["combat"]["hp"]["current"], 11)

    def test_same_op_applies_once(self):
        body = {"op": f"op-{uuid.uuid4().hex}", "source": "player", "changes": [{"hp": -3}]}
        r1 = self.post(body=body, env=LAN)
        r2 = self.post(body=body, env=LAN)
        self.assertEqual((r1.status_code, r2.status_code), (200, 200), r2.get_data(as_text=True))
        self.assertEqual(_parse(self.p.read_bytes().decode("utf-8"))["combat"]["hp"]["current"], 11,
                         "a repeated op applied twice")

    def test_dm_source_from_lan_is_403(self):
        before = self.p.read_bytes()
        r = self.post(body={"source": "dm", "changes": [{"damage": 3}]}, env=LAN)
        self.assert_routed(r)
        self.assertEqual(r.status_code, 403, r.get_data(as_text=True))
        self.assertEqual(self.p.read_bytes(), before)
        r = self.post(body={"source": "dm", "changes": [{"damage": 3}]}, env=LOCAL)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def test_lan_token_required(self):
        self.app._lan_token = "tok-sheet-s4"
        before = self.p.read_bytes()
        body = {"source": "player", "changes": [{"hp": -1}]}
        r = self.post(body=body, env=LAN)
        self.assertEqual(r.status_code, 403)
        r = self.post(body=body, env=LAN, headers={"X-DND-Token": "wrong"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.p.read_bytes(), before)
        r = self.post(body=body, env=LAN, headers={"X-DND-Token": "tok-sheet-s4"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def test_pending_device_gets_202(self):
        before = self.p.read_bytes()
        with mock.patch.object(self.app, "_REQUIRE_APPROVAL", True):
            r = self.post(body={"source": "player", "changes": [{"hp": -1}]}, env=LAN)
        self.addCleanup(lambda: self.app._pending_devices.pop(self.device, None))
        self.assertEqual(r.status_code, 202, r.get_data(as_text=True))
        self.assertEqual(self.p.read_bytes(), before)

    def test_change_naming_another_character_is_400(self):
        before = self.p.read_bytes()
        r = self.post(body={"source": "player", "changes": [{"name": "Brakka", "hp": -1}]}, env=LAN)
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        self.assertEqual(self.p.read_bytes(), before)

    def test_refused_is_409_with_reason_and_writes_nothing(self):
        before = self.p.read_bytes()
        r = self.post(body={"source": "player", "changes": [{"hp": -1}, {"gold": {"gp": -500}}]}, env=LAN)
        self.assertEqual(r.status_code, 409, r.get_data(as_text=True))
        body = r.get_json()
        self.assertFalse(body["ok"])
        self.assertTrue(isinstance(body.get("reason"), str) and body["reason"].strip(), body)
        self.assertEqual(self.p.read_bytes(), before)
        self.assertFalse([c for c in self.calls if "stats" in c or "sheet" in c],
                         "a refused change broadcast an update")

    def test_player_build_fields_409(self):
        before = self.p.read_bytes()
        for entry in ({"max_hp": 5}, {"str": 18}, {"level": 4}, {"damage": 3}):
            with self.subTest(entry=entry):
                r = self.post(body={"source": "player", "changes": [entry]}, env=LAN)
                self.assertIn(r.status_code, (400, 409), r.get_data(as_text=True))
                self.assertEqual(self.p.read_bytes(), before)

    def test_dry_run_writes_nothing(self):
        before = self.p.read_bytes()
        r = self.post(body={"source": "player", "changes": [{"hp": -3}], "dry_run": True}, env=LAN)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.p.read_bytes(), before)
        self.assertFalse([c for c in self.calls if "stats" in c], "dry run broadcast stats")

    def test_success_updates_party_panel_and_broadcasts(self):
        with self.app._stats_lock:
            self.app._current_stats["players"] = [
                {"name": "Ysolde", "hp": {"current": 14, "max": 17}, "conditions": [],
                 "spell_slots": {"1": {"used": 1, "max": 4}}}]
        r = self.post(body={"source": "dm", "changes": [
            {"damage": 5}, {"condition": "Poisoned", "on": True}, {"slot": 1, "delta": -1}]}, env=LOCAL)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        with self.app._stats_lock:
            p = self.app._current_stats["players"][0]
        self.assertEqual(p["hp"]["current"], 12)
        self.assertEqual(p["hp"]["temp"], 0)
        self.assertEqual(p["conditions"], ["Poisoned"])
        self.assertEqual(p["spell_slots"]["1"], {"used": 2, "max": 4})
        stats = [c["stats"] for c in self.calls if "stats" in c]
        self.assertTrue(stats, f"no stats broadcast: {self.calls}")
        self.assertEqual(stats[-1]["players"][0]["hp"]["current"], 12)
        sheets = [c["sheet"] for c in self.calls if "sheet" in c]
        self.assertTrue(sheets, f"no sheet broadcast: {self.calls}")
        self.assertIn(sheets[-1].get("name"), ("Ysolde", "Ysolde Varn"))

    def test_sheet_bucket_separate_from_stage(self):
        ip = {"REMOTE_ADDR": "192.168.1.77"}
        for i in range(30):
            r = self.post(body={"source": "player", "changes": [{"temp_hp": 1}]}, env=ip)
            self.assertNotEqual(r.status_code, 429, f"sheet post {i} hit the limit")
        r = self.client.post("/player-input/stage", json={"character": "Ysolde", "text": "looks around"},
                             environ_base=ip, headers={"X-DND-Device": self.device})
        self.assertNotEqual(r.status_code, 429, "sheet posts used up the /player-input/stage bucket")

    def test_roster_only_character_refused(self):
        who = f"Roster{uuid.uuid4().hex[:6]}"
        self.roster.mkdir(parents=True, exist_ok=True)
        rp = self.roster / f"{who}.md"
        rp.write_bytes((FIX / "imported.md").read_bytes())
        self.addCleanup(lambda: rp.unlink() if rp.exists() else None)
        before = rp.read_bytes()
        r = self.post(name=who, body={"source": "player", "changes": [{"hp": -1}]}, env=LAN)
        self.assertEqual(r.status_code, 409, r.get_data(as_text=True))
        self.assertTrue(r.get_json().get("reason"))
        self.assertEqual(rp.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
