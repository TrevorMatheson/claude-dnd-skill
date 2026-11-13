"""Windows: nothing the skill runs may depend on `python3` or bash-only launchers.

Found re-checking the release on Windows, where `python3` is the Microsoft
Store stub: /dm:dnd new and load still called start-display.sh (python3,
pkill, nohup, macOS ipconfig), /dm:dnd save ran verify_tail.sh (python3, and
the wrong path under DND_CAMPAIGN_ROOT), and the autosave hook was installed
as `python3 "<script>"`, which would fail on every turn.
"""
import json
import pathlib
import subprocess
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd"
sys.path.insert(0, str(SKILL / "scripts"))


class TestProcedures(unittest.TestCase):

    def test_no_bash_launcher_except_tls(self):
        for f in SKILL.glob("SKILL*.md"):
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if "start-display.sh" in line:
                    self.assertIn("tls", line.lower(), f"{f.name}:{n} uses the bash launcher: {line.strip()}")
                self.assertNotIn("verify_tail.sh", line, f"{f.name}:{n}")


class TestHookCommand(unittest.TestCase):

    def test_hook_uses_running_interpreter(self):
        import install_autosave_hook as h
        cmd = h.checkpoint_command()
        self.assertTrue(cmd.startswith(f'"{sys.executable}"'), cmd)
        self.assertNotIn("python3 ", cmd.replace(sys.executable, ""))


class TestVerifyTail(unittest.TestCase):

    def _run(self, name):
        return subprocess.run([sys.executable, str(SKILL / "display" / "verify_tail.py"), name],
                              capture_output=True, encoding="utf-8")

    def test_healthy_missing_and_empty(self):
        import paths
        d = paths.campaigns_dir() / "tail-check"
        d.mkdir(parents=True, exist_ok=True)
        tail = d / "session_tail.json"
        if tail.exists():
            tail.unlink()
        self.assertEqual(self._run("tail-check").returncode, 1)          # missing
        tail.write_text("[]", encoding="utf-8")
        self.assertEqual(self._run("tail-check").returncode, 1)          # too small
        tail.write_text(json.dumps([{"text": "The cellar door bursts open.", "_camp": "x"}] * 3), encoding="utf-8")
        r = self._run("tail-check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("HEALTHY (3 entries)", r.stdout)


if __name__ == "__main__":
    unittest.main()
