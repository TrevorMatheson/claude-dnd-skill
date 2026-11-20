"""The laptop page's Voices section (display foundation S15; D17, D18, AC14).

* /layout serves the section and its script.
* POST /display-config with only {"voice": {...}} changes only what it
  names: the layout and the other voice settings (the narrator) stay.
* voices.js labels blends and keeps a current non-menu voice selectable.
The browser checks (settings take effect, the quota note, the cast list
writing npcs.md) are QA verify's.
"""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
VOICES_JS = DISPLAY / "static" / "layout" / "voices.js"
NODE = shutil.which("node")
sys.path.insert(0, str(DISPLAY))


def _import_app():
    spec = importlib.util.spec_from_file_location(f"dnd_display_app_lv_{uuid.uuid4().hex[:6]}",
                                                  str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.client = cls.app.app.test_client()

    def test_layout_page_has_voice_section(self):
        body = self.client.get("/layout").get_data(as_text=True)
        for needle in ('id="lp-voice"', 'id="lp-narrator"', 'id="lp-engine"', 'id="lp-volume"',
                       'id="lp-speed"', 'id="lp-read-actions"', 'id="lp-cast"', 'id="lp-voice-note"',
                       "/static/layout/voices.js"):
            self.assertIn(needle, body)

    def test_voices_js_is_served(self):
        r = self.client.get("/static/layout/voices.js")
        self.assertEqual(r.status_code, 200)
        r.close()


class TestPartialPost(unittest.TestCase):

    def setUp(self):
        self.app = _import_app()
        self.app._token_ok = lambda: True
        self.client = self.app.app.test_client()
        self.name = f"lv-{uuid.uuid4().hex[:8]}"
        self.camp = pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"]) / "campaigns" / self.name
        self.camp.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, str(self.camp), True)
        (self.camp / "display.json").write_text(json.dumps({
            "version": 1, "layout": {"preset": "tactical", "left": ["party"], "right": ["turns", "map"]},
            "voice": {"narrator": "bm_george", "npc_engine": "kokoro", "volume": 0.5, "speed": 1.0,
                      "read_player_actions": False}}), encoding="utf-8")
        for p in (mock.patch.object(self.app, "_broadcast"),
                  mock.patch.object(self.app, "_active_campaign_name", return_value=self.name)):
            p.start()
            self.addCleanup(p.stop)

    def post(self, body):
        return self.client.post("/display-config", json=body, environ_base={"REMOTE_ADDR": "127.0.0.1"})

    def test_voice_only_post_keeps_layout_and_narrator(self):
        r = self.post({"voice": {"read_player_actions": True}})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        saved = json.loads((self.camp / "display.json").read_text(encoding="utf-8"))
        self.assertTrue(saved["voice"]["read_player_actions"])
        self.assertEqual(saved["voice"]["narrator"], "bm_george")
        self.assertEqual(saved["voice"]["volume"], 0.5)
        self.assertEqual(saved["layout"]["preset"], "tactical")

    def test_layout_only_post_keeps_voice(self):
        r = self.post({"layout": {"left": [], "right": []}})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        saved = json.loads((self.camp / "display.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["layout"]["preset"], "minimal")
        self.assertEqual(saved["voice"]["narrator"], "bm_george")

    def test_out_of_range_value_still_clamped(self):
        self.post({"voice": {"speed": 9}})
        saved = json.loads((self.camp / "display.json").read_text(encoding="utf-8"))
        self.assertLessEqual(saved["voice"]["speed"], 2.0)


@unittest.skipUnless(NODE, "node is not installed")
class TestVoicesJs(unittest.TestCase):

    def run_node(self, script):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(f"const V = require({json.dumps(str(VOICES_JS))});\n{script}\n")
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        if r.returncode != 0:
            self.fail("node failed:\n" + (r.stderr or r.stdout)[-1500:])
        return json.loads(r.stdout)

    VOICES = [{"id": "am_onyx", "label": "Onyx", "kind": "American male"},
              {"id": "am_adam", "label": "Adam", "kind": "American male"},
              {"id": "bf_emma", "label": "Emma", "kind": "British female"}]

    def test_blend_label(self):
        out = self.run_node(f"console.log(JSON.stringify(V.voiceLabel('am_onyx+am_adam', {json.dumps(self.VOICES)})))")
        self.assertEqual(out, "Onyx + Adam")

    def test_current_blend_stays_selectable(self):
        out = self.run_node(f"console.log(JSON.stringify(V.voiceOptions({json.dumps(self.VOICES)}, 'am_onyx+am_adam')))")
        self.assertEqual(out[0]["id"], "am_onyx+am_adam")
        self.assertEqual({o["id"] for o in out[1:]}, {"am_onyx", "am_adam", "bf_emma"})

    def test_options_grouped_by_kind(self):
        out = self.run_node(f"console.log(JSON.stringify(V.voiceOptions({json.dumps(self.VOICES)}, 'am_onyx')))")
        self.assertEqual([o["group"] for o in out], ["American male", "American male", "British female"])


if __name__ == "__main__":
    unittest.main()
