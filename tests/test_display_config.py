"""Saved display settings: display.json and /display-config (display foundation S3).

Covers AC5, and the server side of AC4 and AC6.

AC4 — a layout change on the laptop page reaches the TV within 1 s without a
      reload. Server side: POST /display-config broadcasts {"display": ...} and
      /stream opens with the current {"display": ...}.
AC5 — the layout is saved to <campaign>/display.json and survives a server
      restart; a campaign without the file opens in the Story preset.
AC6 — each panel takes 1-3 of the five modules; the Story, Tactical and
      Minimal presets match D7.

Two layers:
  A) skills/dnd/display/display_config.py, the pure module
     (PRESETS, MODULES, default(), validate(cfg), load(dir), save(dir, cfg)).
  B) the app's /display-config and /stream, through the Flask test client,
     with a sandboxed active campaign.

Interface assumption for B: the POST body is the settings object itself, in
the same shape as display.json and as GET's "config"
({"version":1, "layout":{...}, "voice":{...}}).
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest
import uuid
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
CONFIG_PY = DISPLAY / "display_config.py"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(REPO / "skills" / "dnd" / "scripts"))

D7 = {
    "story":    {"left": ["party"], "right": ["quests", "clock"]},
    "tactical": {"left": ["party"], "right": ["turns", "map"]},
    "minimal":  {"left": [],        "right": []},
}
MODULE_IDS = {"party", "turns", "map", "quests", "clock"}
VOICE_DEFAULTS = {"npc_engine": "kokoro", "volume": 1.0, "speed": 1.0,
                  "read_player_actions": False}

_dc_cache = {}


def _load_display_config(case: unittest.TestCase):
    """Import display_config.py by path; a missing module is a test failure."""
    if "mod" in _dc_cache:
        return _dc_cache["mod"]
    if not CONFIG_PY.exists():
        case.fail(f"{CONFIG_PY} does not exist (S3 not built)")
    spec = importlib.util.spec_from_file_location("display_config_s3", str(CONFIG_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _dc_cache["mod"] = mod
    return mod


def _layout(cfg: dict) -> dict:
    return cfg.get("layout") or {}


def _panels(cfg: dict):
    lay = _layout(cfg)
    return list(lay.get("left") or []), list(lay.get("right") or [])


def _cfg(left, right, preset="custom") -> dict:
    return {"version": 1,
            "layout": {"preset": preset, "left": list(left), "right": list(right)},
            "voice": dict(VOICE_DEFAULTS)}


# ─── A) the pure module ──────────────────────────────────────────────────────

class _ModuleCase(unittest.TestCase):
    def setUp(self):
        self.dc = _load_display_config(self)
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name) / "campaign"
        self.dir.mkdir()

    def tearDown(self):
        self._tmp.cleanup()


class ModulesAndPresets(_ModuleCase):

    # AC6
    def test_modules_are_exactly_the_five_ids(self):
        mods = list(self.dc.MODULES)
        self.assertEqual(set(mods), MODULE_IDS)
        self.assertEqual(len(mods), len(MODULE_IDS), f"MODULES has duplicates: {mods}")

    # AC6
    def test_presets_match_d7(self):
        presets = self.dc.PRESETS
        for name, want in D7.items():
            self.assertIn(name, presets, f"preset {name!r} missing: {list(presets)}")
            got = presets[name]
            got = got.get("layout", got) if isinstance(got, dict) else got
            self.assertEqual(list(got.get("left") or []), want["left"], f"{name} left")
            self.assertEqual(list(got.get("right") or []), want["right"], f"{name} right")

    # AC5
    def test_default_is_story_with_voice_defaults(self):
        d = self.dc.default()
        self.assertEqual(d.get("version"), 1)
        self.assertEqual(_layout(d).get("preset"), "story")
        self.assertEqual(_panels(d), (D7["story"]["left"], D7["story"]["right"]))
        voice = d.get("voice") or {}
        for k, v in VOICE_DEFAULTS.items():
            self.assertIn(k, voice, f"voice default {k!r} missing: {voice}")
            self.assertEqual(voice[k], v, f"voice default {k}")

    def test_default_returns_a_fresh_copy(self):
        d = self.dc.default()
        d["layout"]["left"].append("map")
        self.assertEqual(_panels(self.dc.default())[0], ["party"],
                         "mutating default() changed the next default()")


class Validate(_ModuleCase):

    # AC6
    def test_four_modules_in_a_panel_never_come_back_as_more_than_three(self):
        bad = _cfg(["party", "turns", "map", "quests"], ["clock"])
        try:
            out = self.dc.validate(bad)
        except (ValueError, TypeError, KeyError):
            return  # rejected: acceptable
        left, right = _panels(out)
        self.assertLessEqual(len(left), 3, f"left panel kept 4 modules: {out}")
        self.assertLessEqual(len(right), 3, f"right panel too big: {out}")
        self.assertEqual(out.get("version"), 1)

    # AC6
    def test_unknown_ids_are_dropped(self):
        out = self.dc.validate(_cfg(["party", "dragon"], ["quests", "", 42, "clock"]))
        left, right = _panels(out)
        self.assertEqual(left, ["party"])
        self.assertEqual(right, ["quests", "clock"])
        self.assertEqual(out.get("version"), 1)

    # AC6
    def test_module_used_twice_appears_once_across_both_panels(self):
        out = self.dc.validate(_cfg(["party", "map"], ["map", "party", "clock"]))
        left, right = _panels(out)
        both = left + right
        self.assertEqual(len(both), len(set(both)), f"duplicate module survived: {out}")
        self.assertEqual(set(both), {"party", "map", "clock"})

    # AC6
    def test_duplicate_inside_one_panel_is_removed(self):
        out = self.dc.validate(_cfg(["party", "party"], []))
        self.assertEqual(_panels(out)[0], ["party"])

    # AC6
    def test_every_preset_validates_to_itself(self):
        for name, want in D7.items():
            out = self.dc.validate(_cfg(want["left"], want["right"], preset=name))
            self.assertEqual(_panels(out), (want["left"], want["right"]), name)
            self.assertEqual(out.get("version"), 1)

    def test_garbage_and_missing_version_still_give_version_1(self):
        for raw in ({}, {"layout": "nope"}, {"version": 7, "layout": {"left": ["party"]}}):
            out = self.dc.validate(raw)
            self.assertEqual(out.get("version"), 1, f"validate({raw!r}) -> {out}")
            left, right = _panels(out)
            for p in (left, right):
                self.assertLessEqual(len(p), 3)
                self.assertTrue(set(p) <= MODULE_IDS, out)


class LoadSave(_ModuleCase):

    # AC5
    def test_no_file_loads_story(self):
        self.assertFalse((self.dir / "display.json").exists())
        cfg = self.dc.load(self.dir)
        self.assertEqual(_layout(cfg).get("preset"), "story")
        self.assertEqual(_panels(cfg), (D7["story"]["left"], D7["story"]["right"]))
        self.assertFalse((self.dir / "display.json").exists(),
                         "load() wrote display.json; it must be written only when the host changes something")

    # AC5
    def test_save_then_fresh_load_returns_same_settings(self):
        cfg = _cfg(D7["tactical"]["left"], D7["tactical"]["right"], preset="tactical")
        cfg["voice"].update({"volume": 0.5, "speed": 1.25, "read_player_actions": True})
        self.dc.save(self.dir, copy.deepcopy(cfg))
        self.assertTrue((self.dir / "display.json").is_file())
        on_disk = json.loads((self.dir / "display.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk.get("version"), 1)
        self.assertEqual(_panels(on_disk), (["party"], ["turns", "map"]))

        # Simulate a restart: a fresh import of the module, then load.
        spec = importlib.util.spec_from_file_location("display_config_restart", str(CONFIG_PY))
        fresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fresh)
        back = fresh.load(self.dir)
        self.assertEqual(_layout(back).get("preset"), "tactical")
        self.assertEqual(_panels(back), (["party"], ["turns", "map"]))
        for k in ("volume", "speed", "read_player_actions", "npc_engine"):
            self.assertEqual(back["voice"][k], cfg["voice"][k], k)

    # AC5
    def test_save_leaves_no_temp_files(self):
        self.dc.save(self.dir, _cfg(["party"], ["quests"]))
        self.dc.save(self.dir, _cfg([], []))
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["display.json"])

    # AC5
    def test_save_is_atomic_via_os_replace(self):
        real = os.replace
        calls = []

        def spy(src, dst, *a, **kw):
            calls.append((str(src), str(dst)))
            return real(src, dst, *a, **kw)

        with mock.patch("os.replace", side_effect=spy):
            self.dc.save(self.dir, _cfg(["party"], ["clock"]))
        self.assertTrue(any(pathlib.Path(d) == self.dir / "display.json" for _, d in calls),
                        f"display.json was not written with os.replace: {calls}")

    # AC5
    def test_save_validates_before_writing(self):
        self.dc.save(self.dir, _cfg(["party", "party", "dragon"], ["map"]))
        on_disk = json.loads((self.dir / "display.json").read_text(encoding="utf-8"))
        self.assertEqual(_panels(on_disk), (["party"], ["map"]))

    # AC5
    def test_corrupt_file_loads_default_instead_of_raising(self):
        for junk in ("{not json", "", "[1, 2, 3]", '"a string"'):
            (self.dir / "display.json").write_text(junk, encoding="utf-8")
            cfg = self.dc.load(self.dir)
            self.assertEqual(_layout(cfg).get("preset"), "story", f"junk {junk!r} -> {cfg}")
            self.assertEqual(cfg.get("version"), 1)

    # AC6
    def test_load_cleans_a_hand_edited_file(self):
        (self.dir / "display.json").write_text(json.dumps(
            _cfg(["party", "turns", "map", "quests", "clock"], ["party"])), encoding="utf-8")
        cfg = self.dc.load(self.dir)
        left, right = _panels(cfg)
        self.assertLessEqual(len(left), 3)
        self.assertLessEqual(len(right), 3)
        self.assertEqual(len(left + right), len(set(left + right)), cfg)


# ─── B) the app ──────────────────────────────────────────────────────────────

def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_cfg", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError as exc:                    # flask/deps absent
        raise unittest.SkipTest(f"display app not importable: {exc}")
    return mod


class _AppCase(unittest.TestCase):
    """Fresh app; broadcasts captured; the active campaign is a temp dir."""

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def setUp(self):
        a = self.app
        self._tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self._tmp.name)
        self.name = f"cfgtest-{uuid.uuid4().hex[:8]}"
        # A real campaign under the sandboxed campaign root, so paths.find_campaign
        # resolves it too, plus patches for code that goes through the app helpers.
        self.camp = pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"]) / "campaigns" / self.name
        self.camp.mkdir(parents=True)
        (self.camp / "state.md").write_text("# State\n", encoding="utf-8")
        camp_file = root / ".campaign"
        camp_file.write_text(self.name, encoding="utf-8")
        self.sent = []
        camp = self.camp
        self._p = [mock.patch.object(a, "_broadcast", side_effect=self.sent.append),
                   mock.patch.object(a, "_persist_log"),
                   mock.patch.object(a, "_persist_tail"),
                   mock.patch.object(a, "CAMP_FILE", str(camp_file)),
                   mock.patch.object(a, "_active_campaign_name", return_value=self.name),
                   mock.patch.object(a, "_find_campaign",
                                     side_effect=lambda n: camp if n == self.name
                                     else pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"]) / "campaigns" / n)]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        import shutil
        shutil.rmtree(self.camp, ignore_errors=True)
        self._tmp.cleanup()

    def _first_events(self, limit: int = 40) -> list:
        """Open /stream and read what is queued on connect (stops at the first keepalive)."""
        resp = self.client.get("/stream")
        out, buf = [], b""
        it = resp.response if hasattr(resp, "response") else resp.iter_encoded()
        try:
            for piece in it:
                buf += piece if isinstance(piece, bytes) else piece.encode("utf-8")
                done = False
                while b"\n\n" in buf:
                    frame, buf = buf.split(b"\n\n", 1)
                    line = frame.decode("utf-8")
                    if not line.startswith("data: "):
                        done = True       # keepalive: the connect burst is over
                        break
                    ev = json.loads(line[len("data: "):])
                    out.append(ev)
                    if "display" in ev or len(out) >= limit:
                        done = True
                        break
                if done:
                    break
        finally:
            resp.close()
        return out


class GetDisplayConfig(_AppCase):

    # AC4 / AC6 (server side)
    def test_get_returns_config_presets_modules_mode_override_status(self):
        r = self.client.get("/display-config")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        body = r.get_json()
        self.assertIsInstance(body, dict)
        for k in ("config", "presets", "modules", "mode_override", "status"):
            self.assertIn(k, body, f"/display-config has no {k!r}: {list(body)}")
        self.assertEqual(set(body["modules"]), MODULE_IDS)
        for name in D7:
            self.assertIn(name, body["presets"])

    # AC5
    def test_campaign_without_file_gets_story(self):
        self.assertFalse((self.camp / "display.json").exists())
        r = self.client.get("/display-config")
        self.assertEqual(r.status_code, 200)
        cfg = r.get_json()["config"]
        self.assertEqual(_layout(cfg).get("preset"), "story")
        self.assertEqual(_panels(cfg), (D7["story"]["left"], D7["story"]["right"]))


class PostDisplayConfig(_AppCase):

    # AC4 (server side), AC5
    def test_loopback_post_saves_and_broadcasts_display(self):
        cfg = _cfg(D7["tactical"]["left"], D7["tactical"]["right"], preset="tactical")
        r = self.client.post("/display-config", json=cfg)
        self.assertTrue(200 <= r.status_code < 300, f"{r.status_code} {r.get_data(as_text=True)[:200]}")
        f = self.camp / "display.json"
        self.assertTrue(f.is_file(), "POST did not write <campaign>/display.json")
        on_disk = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(_panels(on_disk), (["party"], ["turns", "map"]))
        disp = [m for m in self.sent if isinstance(m, dict) and "display" in m]
        self.assertTrue(disp, f"no {{'display': ...}} broadcast; sent: {self.sent}")
        payload = disp[-1]["display"]
        self.assertIn("config", payload, payload)
        self.assertEqual(_panels(payload["config"]), (["party"], ["turns", "map"]))
        # GET reflects the change.
        got = self.client.get("/display-config").get_json()["config"]
        self.assertEqual(_panels(got), (["party"], ["turns", "map"]))

    # AC6 (server side)
    def test_post_cleans_bad_panels_before_saving(self):
        r = self.client.post("/display-config",
                             json=_cfg(["party", "dragon", "party"], ["map", "turns", "quests", "clock"]))
        if r.status_code >= 400:
            self.assertEqual(r.status_code, 400, r.get_data(as_text=True)[:200])
            return  # rejected outright: acceptable
        on_disk = json.loads((self.camp / "display.json").read_text(encoding="utf-8"))
        left, right = _panels(on_disk)
        self.assertLessEqual(len(right), 3, on_disk)
        self.assertNotIn("dragon", left)
        self.assertEqual(len(left + right), len(set(left + right)), on_disk)

    # AC4 (server side): loopback rule
    def test_non_loopback_post_is_403_and_nothing_written(self):
        cfg = _cfg(D7["minimal"]["left"], D7["minimal"]["right"], preset="minimal")
        r = self.client.post("/display-config", json=cfg,
                             environ_base={"REMOTE_ADDR": "192.168.1.50"})
        self.assertEqual(r.status_code, 403, r.get_data(as_text=True)[:200])
        self.assertFalse((self.camp / "display.json").exists(), "a refused POST wrote display.json")
        self.assertFalse([m for m in self.sent if isinstance(m, dict) and "display" in m],
                         "a refused POST still broadcast display")

    # AC5: survives a restart (fresh app import reads the saved file)
    def test_saved_layout_survives_a_restart(self):
        cfg = _cfg(D7["minimal"]["left"], D7["minimal"]["right"], preset="minimal")
        r = self.client.post("/display-config", json=cfg)
        self.assertTrue(200 <= r.status_code < 300, r.status_code)
        dc = _load_display_config(self)
        back = dc.load(self.camp)
        self.assertEqual(_layout(back).get("preset"), "minimal")
        self.assertEqual(_panels(back), ([], []))


class StreamDisplay(_AppCase):

    # AC4 (server side)
    def test_stream_sends_display_after_hello(self):
        events = self._first_events()
        self.assertTrue(events, "stream sent nothing")
        self.assertIn("hello", events[0], f"first item is not hello: {events[0]}")
        disp = [e for e in events if "display" in e]
        self.assertTrue(disp, f"no {{'display': ...}} item on connect; keys: {[list(e) for e in events]}")
        payload = disp[0]["display"]
        self.assertIsInstance(payload, dict)
        self.assertIn("config", payload, payload)
        self.assertIn("mode_override", payload, payload)
        self.assertEqual(_panels(payload["config"]), (D7["story"]["left"], D7["story"]["right"]))


class CampaignSwitchBroadcastsDisplay(unittest.TestCase):
    """Plan S3: settings "are re-read when the active campaign changes
    (/stats with campaign)". An open TV must get the new campaign's layout
    without reconnecting, so the switch itself has to broadcast {"display": ...}.

    The switch is real: CAMP_FILE points at a temp file and POST /stats
    {"campaign": name} writes it; _active_campaign_name and find_campaign are
    not patched, so they resolve the two campaigns under the sandboxed root.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def setUp(self):
        a = self.app
        self._tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"]) / "campaigns"
        tag = uuid.uuid4().hex[:8]
        self.tactical_name = f"cfgswitch-tac-{tag}"
        self.story_name = f"cfgswitch-story-{tag}"
        self.camps = []
        for n in (self.tactical_name, self.story_name):
            d = root / n
            d.mkdir(parents=True)
            (d / "state.md").write_text("# State\n", encoding="utf-8")
            self.camps.append(d)
        dc = _load_display_config(self)
        dc.save(self.camps[0], _cfg(D7["tactical"]["left"], D7["tactical"]["right"],
                                    preset="tactical"))
        self.assertFalse((self.camps[1] / "display.json").exists())

        camp_file = pathlib.Path(self._tmp.name) / ".campaign"
        camp_file.write_text(self.tactical_name, encoding="utf-8")
        self.sent = []
        self._p = [mock.patch.object(a, "_broadcast", side_effect=self.sent.append),
                   mock.patch.object(a, "_persist_log"),
                   mock.patch.object(a, "_persist_tail"),
                   mock.patch.object(a, "CAMP_FILE", str(camp_file))]
        for p in self._p:
            p.start()
        # Prime the cache with the Tactical campaign, as an open TV would have.
        with a._display_lock:
            a._display_cache.update(campaign=None, config=None)
        got = self.client.get("/display-config").get_json()["config"]
        self.assertEqual(_panels(got), (["party"], ["turns", "map"]),
                         "sandbox setup: Tactical campaign did not load its display.json")

    def tearDown(self):
        for p in self._p:
            p.stop()
        import shutil
        for d in self.camps:
            shutil.rmtree(d, ignore_errors=True)
        self._tmp.cleanup()

    def _switch(self, name):
        self.sent.clear()
        r = self.client.post("/stats", json={"campaign": name})
        self.assertTrue(200 <= r.status_code < 300,
                        f"/stats campaign switch: {r.status_code} {r.get_data(as_text=True)[:200]}")
        self.assertEqual(self.app._active_campaign_name(), name, "switch did not reach CAMP_FILE")
        disp = [m for m in self.sent if isinstance(m, dict) and "display" in m]
        self.assertTrue(disp, f"switching to {name!r} broadcast no {{'display': ...}}; "
                              f"sent keys: {[list(m) for m in self.sent if isinstance(m, dict)]}")
        return disp[-1]["display"]

    # AC4 / AC5 (plan S3: re-read on campaign change)
    def test_switching_campaign_via_stats_broadcasts_that_campaigns_display(self):
        payload = self._switch(self.story_name)
        self.assertIn("config", payload, payload)
        self.assertEqual(_layout(payload["config"]).get("preset"), "story", payload)
        self.assertEqual(_panels(payload["config"]),
                         (D7["story"]["left"], D7["story"]["right"]))

        payload = self._switch(self.tactical_name)
        self.assertEqual(_layout(payload["config"]).get("preset"), "tactical", payload)
        self.assertEqual(_panels(payload["config"]), (["party"], ["turns", "map"]))


if __name__ == "__main__":
    unittest.main()
