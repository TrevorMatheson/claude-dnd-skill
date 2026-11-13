"""Changing the narrator voice must not corrupt state.md's line endings.

WHY
===
`_write_narrator_voice` read state.md as raw bytes (so a CRLF file kept its
"\\r\\n") and wrote it back with `Path.write_text`, whose newline translation
on Windows turns every "\\n" into "\\r\\n". Each voice change therefore turned
"\\r\\n" into "\\r\\r\\n": after a few clicks of the voice menu a real campaign
file had eight carriage returns per line, and later edits that searched for
"## Active Combat\\n*(none)*" no longer matched. The "\\n"-only Session Flags
regex also never matched a CRLF file, so the voice was never actually saved.

The writer now edits in "\\n" form, restores the file's own line ending and
writes with newline="" so no platform translation happens.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "skills" / "dnd" / "display"
SCRIPTS = ROOT / "skills" / "dnd" / "scripts"

STATE = (
    "# Campaign: Test\n"
    "## Session Flags\n"
    "*(tutor_mode, autorun, tts_voice — session-scoped flags)*\n"
    "*(autosave: on|off — default on.)*\n"
    "autorun: true\n"
    "\n"
    "## DM Notes (hidden from players)\n"
)


def _load_utf8io():
    spec = importlib.util.spec_from_file_location("utf8io", SCRIPTS / "utf8io.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class VoicePersistLineEndings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.camp = pathlib.Path(self.tmp.name) / "test-camp"
        self.camp.mkdir()
        self.state = self.camp / "state.md"

        src = (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8")
        pat = re.search(r'^_VOICE_PAT = .*$', src, re.M).group(0)
        start = src.index("def _write_narrator_voice")
        end = src.index("\n@app.route", start)

        class _TTS:
            VALID_VOICES = frozenset({"Charon", "Kore", "Enceladus"})

        self.ns = {
            "re": re,
            "_tts": _TTS,
            "_active_campaign_name": lambda: "test-camp",
            "_find_campaign": lambda name: self.camp,
            # the same decoder the app uses (raw bytes → str, CRLF kept)
            "_read_text": getattr(_load_utf8io(), "read_" + "text"),
        }
        exec(pat + "\n" + src[start:end], self.ns)

    def tearDown(self):
        self.tmp.cleanup()

    def _set(self, voice):
        self.assertTrue(self.ns["_write_narrator_voice"](voice))

    def test_crlf_file_survives_repeated_voice_changes(self):
        self.state.write_bytes(STATE.replace("\n", "\r\n").encode("utf-8"))
        for v in ("Charon", "Kore", "Enceladus", "Charon"):
            self._set(v)
        raw = self.state.read_bytes()
        self.assertNotIn(b"\r\r", raw, "carriage returns piled up")
        self.assertEqual(raw.count(b"\r\n"), raw.count(b"\n"), "file is no longer pure CRLF")
        self.assertIn(b"tts_voice: Charon\r\n", raw, "voice was not saved in a CRLF file")
        self.assertEqual(raw.count(b"tts_voice:"), 1)

    def test_lf_file_stays_lf(self):
        self.state.write_bytes(STATE.encode("utf-8"))
        for v in ("Kore", "Charon"):
            self._set(v)
        raw = self.state.read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertIn(b"tts_voice: Charon\n", raw)
        self.assertEqual(raw.count(b"tts_voice:"), 1)


if __name__ == "__main__":
    unittest.main()
