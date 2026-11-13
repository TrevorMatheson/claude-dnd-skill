"""DM Help reports why it failed instead of silently doing nothing.

dm_help.py called `claude -p --model claude-sonnet-4-6` and returned "SKIP" on
any failure, so a signed-out CLI or a retired model id made the button look
broken. It now uses the CLI's default model (DND_HELP_MODEL overrides), finds
claude.cmd on Windows through shutil.which, and sends a visible
"DM Help unavailable" tutor block with the reason.
"""
import importlib.util
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SCRIPTS))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dm_help = _load("dm_help_visible", DISPLAY / "dm_help.py")


class TestDmHelp(unittest.TestCase):

    def test_no_hardcoded_model_ids(self):
        for f in (DISPLAY / "dm_help.py", SCRIPTS / "campaign_graph.py"):
            src = f.read_text(encoding="utf-8")
            self.assertNotRegex(src, r"claude-(sonnet|haiku|opus)-\d", f.name)

    def test_default_has_no_model_flag(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="Try the door.", stderr="")
        with mock.patch.object(dm_help.subprocess, "run", fake_run), \
                mock.patch.dict("os.environ", {"DND_HELP_MODEL": ""}):
            self.assertEqual(dm_help.call_claude("d", "s", "x", "a"), "Try the door.")
        self.assertNotIn("--model", seen["cmd"])

    def test_failure_raises_with_reason(self):
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Invalid API key · Please run /login")
        with mock.patch.object(dm_help.subprocess, "run", fake_run):
            with self.assertRaises(dm_help.HelpUnavailable) as cm:
                dm_help.call_claude("d", "s", "x", "a")
        self.assertIn("/login", str(cm.exception))

    def test_missing_cli(self):
        def fake_run(cmd, **kw):
            raise FileNotFoundError(cmd[0])
        with mock.patch.object(dm_help.subprocess, "run", fake_run):
            with self.assertRaises(dm_help.HelpUnavailable):
                dm_help.call_claude("d", "s", "x", "a")

    def test_main_sends_visible_block(self):
        sent = []
        with mock.patch.object(dm_help, "get_recent_display", return_value="narration"), \
                mock.patch.object(dm_help, "get_campaign_state", return_value=""), \
                mock.patch.object(dm_help, "get_session_context", return_value=""), \
                mock.patch.object(dm_help, "get_arc_context", return_value=""), \
                mock.patch.object(dm_help, "get_graph_context", return_value=""), \
                mock.patch.object(dm_help, "call_claude",
                                  side_effect=dm_help.HelpUnavailable("not signed in")), \
                mock.patch.object(dm_help, "send_tutor", side_effect=sent.append), \
                mock.patch.object(dm_help, "release_lock"), \
                mock.patch.object(sys, "argv", ["dm_help.py", "--campaign", "x"]):
            dm_help.main()
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0].startswith("DM Help unavailable: not signed in"))


if __name__ == "__main__":
    unittest.main()
