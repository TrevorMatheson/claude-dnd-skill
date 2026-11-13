"""All five version strings must match VERSION.

They drifted once (VERSION 2.7.0, plugin.json 2.4.0), which left installs in
cache/.../2.4.0 where /plugin update could not see new releases. Bump with
scripts/bump_version.py.
"""
import json
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent


class TestVersionSync(unittest.TestCase):

    def setUp(self):
        self.version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(self.version, r"^\d+\.\d+\.\d+$")

    def test_plugin_json(self):
        data = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(data["version"], self.version)

    def test_marketplace_json(self):
        data = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        dm = [p for p in data["plugins"] if p["name"] == "dm"]
        self.assertEqual(len(dm), 1)
        self.assertEqual(dm[0]["version"], self.version)

    def test_skill_md_prefix(self):
        text = (REPO / "skills" / "dnd" / "SKILL.md").read_text(encoding="utf-8")
        m = re.search(r'^description:\s*"v(\d+\.\d+\.\d+) ·', text, re.MULTILINE)
        self.assertIsNotNone(m, "SKILL.md description lacks the vX.Y.Z prefix")
        self.assertEqual(m.group(1), self.version)

    def test_changelog_has_release(self):
        text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"[{self.version}]", text)


if __name__ == "__main__":
    unittest.main()
