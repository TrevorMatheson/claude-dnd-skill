"""Campaign-file writers keep each file's line ending.

pathlib's write_text translates "\n" to os.linesep, so on Windows xp.py,
migrate_ruleset.py, npc_rename.py, oracle.py and path_config.py turned LF
campaign files into CRLF, and utf8io.read_text (which keeps "\r\n") paired
with a text-mode write added one more "\r" per line per pass. They now go
through utf8io.write_text, and this test forbids a new pathlib .write_text(
in scripts/ outside the JSON / output writers listed below.
"""
import io
import pathlib
import sys
import tempfile
import tokenize
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import utf8io  # noqa: E402

# Files whose .write_text( writes freshly generated JSON or a user-chosen
# output file — never a read-modify-write of a campaign markdown file.
ALLOWED = {"autosave_checkpoint.py", "campaign_graph.py", "graph_extract_deterministic.py",
           "name_registry.py", "session_recap.py", "utf8io.py"}


class TestWriteText(unittest.TestCase):

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())

    def test_lf_file_stays_lf(self):
        p = self.d / "state.md"
        p.write_bytes(b"a\nb\n")
        utf8io.write_text(p, utf8io.read_text(p) + "c\n")
        self.assertEqual(p.read_bytes(), b"a\nb\nc\n")

    def test_crlf_file_stays_crlf_without_growing(self):
        p = self.d / "state.md"
        p.write_bytes(b"a\r\nb\r\n")
        for _ in range(3):
            utf8io.write_text(p, utf8io.read_text(p))
        self.assertEqual(p.read_bytes(), b"a\r\nb\r\n")

    def test_new_file_is_lf(self):
        p = self.d / "new.md"
        utf8io.write_text(p, "x\ny\n")
        self.assertEqual(p.read_bytes(), b"x\ny\n")

    def test_explicit_newline(self):
        p = self.d / "w.md"
        utf8io.write_text(p, "x\ny\n", newline="\r\n")
        self.assertEqual(p.read_bytes(), b"x\r\ny\r\n")

    def test_utf8_bytes(self):
        p = self.d / "zh.md"
        utf8io.write_text(p, "好的\n")
        self.assertEqual(p.read_bytes(), "好的\n".encode("utf-8"))


class TestNoBareWriteTextInScripts(unittest.TestCase):

    def test_scan(self):
        bad = []
        for f in sorted(SCRIPTS.glob("*.py")):
            if f.name in ALLOWED:
                continue
            src = f.read_text(encoding="utf-8")
            toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
            for i, t in enumerate(toks):
                if (t.type == tokenize.NAME and t.string == "write_text" and i
                        and toks[i - 1].string == "."
                        and not (i > 1 and toks[i - 2].string == "utf8io")):
                    bad.append(f"{f.name}:{t.start[0]}")
        self.assertEqual(bad, [], "use utf8io.write_text (keeps the file's line ending)")


if __name__ == "__main__":
    unittest.main()
