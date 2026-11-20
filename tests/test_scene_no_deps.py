"""Scene backgrounds AC10: no new dependency and no new port.

Spec: docs/specs/2026-11-18-scene-backgrounds.md (AC10, D4, D14).
Plan: step S1, re-run at every later step.

The Gemini image call uses the standard library, as tts.py does, so
requirements.txt keeps its baseline set. Every skills/dnd/display/scene_*.py
imports only the standard library or local display/scripts modules, opens no
listening socket, and names no port. The display stays on 5001.
"""
import ast
import pathlib
import re
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
if str(DISPLAY) not in sys.path:
    sys.path.insert(0, str(DISPLAY))

BASELINE = {"flask", "flask-cors", "pygame", "numpy", "pyyaml"}

# Written in S1; later steps add scene_images.py and are picked up by the glob.
REQUIRED = {"scene_gen.py", "scene_trial.py"}


def _scene_files():
    return sorted(DISPLAY.glob("scene_*.py"))


def _local_modules():
    names = set()
    for folder in (DISPLAY, SCRIPTS):
        for p in folder.glob("*.py"):
            names.add(p.stem)
        for p in folder.iterdir():
            if p.is_dir() and (p / "__init__.py").exists():
                names.add(p.name)
    return names


def _requirement_names(text):
    names = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~;\[\s]", line, 1)[0]
        names.add(name.lower().replace("_", "-"))
    return names


class TestNoNewDependency(unittest.TestCase):

    def test_requirements_equal_baseline(self):
        req = DISPLAY / "requirements.txt"
        self.assertEqual(_requirement_names(req.read_text(encoding="utf-8")), BASELINE)

    def test_no_other_requirements_file_appears(self):
        skip = {".git", "node_modules", ".claude", ".venv", "venv"}
        found = {p.relative_to(REPO).as_posix() for p in REPO.glob("**/requirements*.txt")
                 if not skip & set(p.relative_to(REPO).parts)}
        self.assertEqual(found, {"skills/dnd/display/requirements.txt"})

    def test_scene_files_exist(self):
        names = {p.name for p in _scene_files()}
        self.assertTrue(REQUIRED <= names, f"missing {sorted(REQUIRED - names)}")

    def test_scene_imports_are_stdlib_or_local(self):
        files = _scene_files()
        self.assertTrue(files, "no skills/dnd/display/scene_*.py found")
        stdlib = set(sys.stdlib_module_names)
        local = _local_modules()
        bad = []
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:  # relative import of a sibling module
                        continue
                    mods = [node.module or ""]
                elif (isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", ""))
                      in ("import_module", "__import__") and node.args
                      and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                    mods = [node.args[0].value]
                for m in mods:
                    top = m.split(".", 1)[0]
                    if top not in stdlib and top not in local:
                        bad.append(f"{path.name}: {m}")
        self.assertEqual(bad, [], "scene modules must use only the stdlib and local modules")


class TestNoNewPort(unittest.TestCase):

    PATTERNS = [
        (re.compile(r"\bapp\.run\("), "app.run("),
        (re.compile(r"\.bind\("), ".bind("),
        (re.compile(r"\b5001\b"), "the display port 5001"),
        (re.compile(r"(?i)\bport\s*[=:]\s*\d+"), "port=<number>"),
        (re.compile(r"(?i)(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1?\])\s*:\s*\d{2,5}"), "host:port"),
    ]

    def test_scene_files_open_no_port(self):
        files = _scene_files()
        self.assertTrue(files, "no skills/dnd/display/scene_*.py found")
        hits = []
        for path in files:
            text = path.read_text(encoding="utf-8")
            for rx, what in self.PATTERNS:
                for m in rx.finditer(text):
                    line = text.count("\n", 0, m.start()) + 1
                    hits.append(f"{path.name}:{line}: {what}")
        self.assertEqual(hits, [])

    def test_display_port_unchanged(self):
        import display_url
        self.assertEqual(display_url.PORT, 5001)


if __name__ == "__main__":
    unittest.main()
