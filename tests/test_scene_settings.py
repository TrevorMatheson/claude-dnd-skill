"""Per-campaign scene settings and the style preset (scene backgrounds S8).

Covers D4 (generation on or off per campaign), D6 (style presets), D7 (the
setting the laptop note refers to), as decided in D17:

* one preset, "Painterly, muted", whose prompt is the trial's style:
  "painterly fantasy illustration, muted colours, soft atmospheric light,
  dark edges";
* generation is on by default (whenever a key exists; the key check is S9's).

Interface pinned by these tests (S8 plan step):

* display_config.SCENE_STYLES = {id: {"label", "prompt"}}, with the single
  preset id **"painterly"**.
* default() and validate() carry "scenes": {"generate": True, "style": "painterly"}.
* POST /display-config merges data["scenes"] like data["voice"]: a partial
  {"scenes": {...}} changes only what it names.
* GET /display-config adds "scene_styles": either the SCENE_STYLES dict, or a
  list of {"id", "label", ...}. Both are accepted.
* /layout has a "Scene pictures" section after Voices with a checkbox (the
  on/off toggle), a <select> (the style menu), <p id="lp-scene-note"> and a
  hint naming the scenes/ folder; it loads /static/layout/scenes.js.

No network: DND_TTS_KEY and GEMINI_API_KEY are cleared and urlopen raises.
"""
import copy
import importlib.util
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
CONFIG_PY = DISPLAY / "display_config.py"
sys.path.insert(0, str(DISPLAY))

PRESET_ID = "painterly"
PRESET_LABEL = "Painterly, muted"
PRESET_PROMPT = ("painterly fantasy illustration, muted colours, soft atmospheric "
                 "light, dark edges")
SCENE_DEFAULTS = {"generate": True, "style": PRESET_ID}


def _no_network(case: unittest.TestCase):
    for var in ("DND_TTS_KEY", "GEMINI_API_KEY"):
        old = os.environ.pop(var, None)
        if old is not None:
            case.addCleanup(os.environ.__setitem__, var, old)

    def _refuse(*a, **k):
        raise OSError("network is off in tests")

    p = mock.patch("urllib.request.urlopen", side_effect=_refuse)
    p.start()
    case.addCleanup(p.stop)


def _load_dc():
    spec = importlib.util.spec_from_file_location(
        f"display_config_s8_{uuid.uuid4().hex[:6]}", str(CONFIG_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_app():
    spec = importlib.util.spec_from_file_location(
        f"dnd_display_app_s8_{uuid.uuid4().hex[:6]}", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scenes(cfg) -> dict:
    s = cfg.get("scenes") if isinstance(cfg, dict) else None
    if not isinstance(s, dict):
        raise AssertionError(f"no 'scenes' object in {cfg!r}")
    return s


# ─── A) display_config.py ────────────────────────────────────────────────────

class StylePresets(unittest.TestCase):

    def setUp(self):
        _no_network(self)
        self.dc = _load_dc()

    # D6, D17
    def test_scene_styles_has_the_one_painterly_preset(self):
        styles = getattr(self.dc, "SCENE_STYLES", None)
        self.assertIsInstance(styles, dict, "display_config.SCENE_STYLES is missing")
        self.assertEqual(list(styles), [PRESET_ID], styles)
        p = styles[PRESET_ID]
        self.assertEqual(p.get("label"), PRESET_LABEL)
        self.assertEqual(p.get("prompt"), PRESET_PROMPT)

    # D4, D6, D17
    def test_default_has_scenes_generate_on_and_the_preset(self):
        self.assertEqual(_scenes(self.dc.default()), SCENE_DEFAULTS)

    def test_default_is_a_fresh_copy(self):
        a = self.dc.default()
        _scenes(a)["generate"] = False
        self.assertTrue(_scenes(self.dc.default())["generate"])


class Validation(unittest.TestCase):

    def setUp(self):
        _no_network(self)
        self.dc = _load_dc()

    def _v(self, scenes):
        return _scenes(self.dc.validate({"version": 1, "scenes": scenes}))

    def test_unknown_style_falls_back_to_default(self):
        self.assertEqual(self._v({"generate": True, "style": "x"})["style"], PRESET_ID)

    def test_non_string_style_falls_back_to_default(self):
        for bad in (None, 3, ["painterly"], {"id": "painterly"}):
            with self.subTest(style=bad):
                self.assertEqual(self._v({"style": bad})["style"], PRESET_ID)

    def test_known_style_is_kept(self):
        self.assertEqual(self._v({"style": PRESET_ID})["style"], PRESET_ID)

    def test_non_bool_generate_falls_back_to_true(self):
        for bad in ("false", "no", 0, 1, None, [], {}):
            with self.subTest(generate=bad):
                self.assertIs(self._v({"generate": bad})["generate"], True)

    def test_generate_false_is_kept(self):
        self.assertIs(self._v({"generate": False})["generate"], False)

    def test_scenes_not_a_dict_gives_defaults(self):
        for bad in (None, "on", 1, ["painterly"]):
            with self.subTest(scenes=bad):
                self.assertEqual(self._v(bad), SCENE_DEFAULTS)

    def test_missing_keys_take_defaults(self):
        self.assertEqual(self._v({}), SCENE_DEFAULTS)
        self.assertEqual(self._v({"generate": False}), {"generate": False, "style": PRESET_ID})

    def test_unknown_scene_keys_are_dropped(self):
        self.assertEqual(set(self._v({"generate": True, "style": PRESET_ID, "evil": 1})),
                         {"generate", "style"})

    def test_scenes_do_not_disturb_layout_or_voice(self):
        cfg = {"version": 1,
               "layout": {"preset": "tactical", "left": ["party"], "right": ["turns", "map"]},
               "voice": {"narrator": "bm_george", "npc_engine": "gemini", "volume": 0.5,
                         "speed": 1.2, "read_player_actions": True},
               "scenes": {"generate": False, "style": "x"}}
        out = self.dc.validate(copy.deepcopy(cfg))
        self.assertEqual(out["layout"]["preset"], "tactical")
        self.assertEqual(out["voice"]["narrator"], "bm_george")
        self.assertEqual(out["voice"]["npc_engine"], "gemini")
        self.assertEqual(out["scenes"], {"generate": False, "style": PRESET_ID})


class OlderCampaigns(unittest.TestCase):

    def setUp(self):
        _no_network(self)
        self.dc = _load_dc()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)
        self.file = self.dir / "display.json"
        self.raw = json.dumps({
            "version": 1,
            "layout": {"preset": "minimal", "left": [], "right": []},
            "voice": {"npc_engine": "kokoro", "volume": 0.7, "speed": 1.0,
                      "read_player_actions": False}})
        self.file.write_text(self.raw, encoding="utf-8")
        os.utime(self.file, (1_700_000_000, 1_700_000_000))

    def test_file_without_scenes_loads_defaults_and_is_not_rewritten(self):
        cfg = self.dc.load(self.dir)
        self.assertEqual(_scenes(cfg), SCENE_DEFAULTS)
        self.assertEqual(cfg["layout"]["preset"], "minimal")
        self.assertEqual(cfg["voice"]["volume"], 0.7)
        self.assertEqual(self.file.read_text(encoding="utf-8"), self.raw)
        self.assertEqual(os.path.getmtime(self.file), 1_700_000_000)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["display.json"])

    def test_save_round_trips_scenes(self):
        cfg = self.dc.load(self.dir)
        cfg["scenes"] = {"generate": False, "style": PRESET_ID}
        self.dc.save(self.dir, cfg)
        back = self.dc.load(self.dir)
        self.assertEqual(back["scenes"], {"generate": False, "style": PRESET_ID})
        on_disk = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["scenes"], {"generate": False, "style": PRESET_ID})


# ─── B) /display-config through the app ──────────────────────────────────────

class _AppCase(unittest.TestCase):
    SAVED = {
        "version": 1,
        "layout": {"preset": "tactical", "left": ["party"], "right": ["turns", "map"]},
        "voice": {"narrator": "bm_george", "npc_engine": "kokoro", "volume": 0.5,
                  "speed": 1.0, "read_player_actions": True},
    }
    SCENES = None   # what display.json holds under "scenes" (None = no key)

    def setUp(self):
        _no_network(self)
        self.app = _import_app()
        self.app._token_ok = lambda: True
        self.client = self.app.app.test_client()
        self.name = f"s8-{uuid.uuid4().hex[:8]}"
        self.camp = pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"]) / "campaigns" / self.name
        self.camp.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, str(self.camp), True)
        saved = copy.deepcopy(self.SAVED)
        if self.SCENES is not None:
            saved["scenes"] = copy.deepcopy(self.SCENES)
        self.file = self.camp / "display.json"
        self.raw = json.dumps(saved)
        self.file.write_text(self.raw, encoding="utf-8")
        os.utime(self.file, (1_700_000_000, 1_700_000_000))
        self.sent = []
        for p in (mock.patch.object(self.app, "_broadcast", side_effect=self.sent.append),
                  mock.patch.object(self.app, "_active_campaign_name", return_value=self.name)):
            p.start()
            self.addCleanup(p.stop)

    def post(self, body, addr="127.0.0.1"):
        return self.client.post("/display-config", json=body, environ_base={"REMOTE_ADDR": addr})

    def on_disk(self):
        return json.loads(self.file.read_text(encoding="utf-8"))


class PartialSave(_AppCase):
    SCENES = {"generate": False, "style": PRESET_ID}

    def test_style_only_post_keeps_layout_voice_and_generate(self):
        r = self.post({"scenes": {"style": "x"}})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        saved = self.on_disk()
        self.assertEqual(saved["scenes"], {"generate": False, "style": PRESET_ID})
        self.assertEqual(saved["layout"]["preset"], "tactical")
        self.assertEqual(saved["voice"]["narrator"], "bm_george")
        self.assertEqual(saved["voice"]["volume"], 0.5)
        self.assertIs(saved["voice"]["read_player_actions"], True)
        self.assertEqual(r.get_json()["config"]["scenes"], {"generate": False, "style": PRESET_ID})

    def test_generate_only_post_keeps_style_and_the_rest(self):
        r = self.post({"scenes": {"generate": True}})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        saved = self.on_disk()
        self.assertEqual(saved["scenes"], {"generate": True, "style": PRESET_ID})
        self.assertEqual(saved["layout"]["preset"], "tactical")
        self.assertEqual(saved["voice"]["narrator"], "bm_george")

    def test_voice_only_post_keeps_scenes(self):
        r = self.post({"voice": {"speed": 1.5}})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.on_disk()["scenes"], {"generate": False, "style": PRESET_ID})

    def test_layout_only_post_keeps_scenes(self):
        r = self.post({"layout": {"left": [], "right": []}})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.on_disk()["scenes"], {"generate": False, "style": PRESET_ID})

    def test_bad_generate_in_post_falls_back_to_default(self):
        r = self.post({"scenes": {"generate": "off"}})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertIs(self.on_disk()["scenes"]["generate"], True)

    def test_scenes_change_is_broadcast(self):
        self.post({"scenes": {"generate": True}})
        disp = [m for m in self.sent if isinstance(m, dict) and "display" in m]
        self.assertTrue(disp, f"no display broadcast: {self.sent}")
        self.assertEqual(disp[-1]["display"]["config"]["scenes"]["generate"], True)

    def test_non_localhost_post_is_403_and_nothing_written(self):
        r = self.post({"scenes": {"generate": True}}, addr="192.168.1.50")
        self.assertEqual(r.status_code, 403, r.get_data(as_text=True)[:200])
        self.assertEqual(self.file.read_text(encoding="utf-8"), self.raw)
        self.assertFalse([m for m in self.sent if isinstance(m, dict) and "display" in m])


class OlderCampaignThroughTheApp(_AppCase):
    SCENES = None

    def test_get_gives_default_scenes_and_does_not_rewrite(self):
        r = self.client.get("/display-config")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(_scenes(r.get_json()["config"]), SCENE_DEFAULTS)
        self.assertEqual(self.file.read_text(encoding="utf-8"), self.raw)
        self.assertEqual(os.path.getmtime(self.file), 1_700_000_000)

    def test_style_only_post_on_older_file_keeps_generate_default(self):
        r = self.post({"scenes": {"style": "x"}})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        saved = self.on_disk()
        self.assertEqual(saved["scenes"], SCENE_DEFAULTS)
        self.assertEqual(saved["layout"]["preset"], "tactical")
        self.assertEqual(saved["voice"]["narrator"], "bm_george")


class GetListsStyles(_AppCase):

    def test_get_lists_scene_styles(self):
        body = self.client.get("/display-config").get_json()
        self.assertIn("scene_styles", body, list(body))
        styles = body["scene_styles"]
        if isinstance(styles, dict):
            items = [{"id": k, **v} for k, v in styles.items()]
        else:
            self.assertIsInstance(styles, list, styles)
            items = styles
        self.assertEqual([s.get("id") for s in items], [PRESET_ID], styles)
        self.assertEqual(items[0].get("label"), PRESET_LABEL)
        if "prompt" in items[0]:
            self.assertEqual(items[0]["prompt"], PRESET_PROMPT)

    def test_get_keeps_existing_fields(self):
        body = self.client.get("/display-config").get_json()
        for k in ("config", "presets", "modules", "mode_override", "status"):
            self.assertIn(k, body)


# ─── C) the laptop page ──────────────────────────────────────────────────────

class LayoutPage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.client = cls.app.app.test_client()

    def setUp(self):
        _no_network(self)
        self.body = self.client.get("/layout").get_data(as_text=True)

    def _section(self) -> str:
        """The <section> whose heading reads "Scene pictures"."""
        m = re.search(r"<h2[^>]*>\s*Scene pictures\s*</h2>", self.body)
        self.assertIsNotNone(m, "no <h2>Scene pictures</h2> on /layout")
        start = self.body.rfind("<section", 0, m.start())
        end = self.body.find("</section>", m.end())
        self.assertTrue(start >= 0 and end > 0, "Scene pictures heading is not inside a <section>")
        return self.body[start:end]

    def test_section_comes_after_voices(self):
        sec = self._section()
        self.assertGreater(self.body.find(sec), self.body.find('id="lp-voice"'),
                           "Scene pictures must come after the Voices section")

    def test_section_has_toggle_menu_note_and_folder_hint(self):
        sec = self._section()
        self.assertRegex(sec, r'<input[^>]*type="checkbox"', "no on/off toggle")
        self.assertIn("<select", sec, "no style menu")
        self.assertIn('id="lp-scene-note"', sec)
        self.assertRegex(sec, r"<p[^>]*id=\"lp-scene-note\"")
        self.assertIn("scenes/", sec, "no hint naming the campaign's scenes/ folder")

    def test_page_loads_scenes_js(self):
        self.assertIn("/static/layout/scenes.js", self.body)

    def test_scenes_js_is_served(self):
        r = self.client.get("/static/layout/scenes.js")
        self.assertEqual(r.status_code, 200)
        r.close()


if __name__ == "__main__":
    unittest.main()
