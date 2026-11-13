"""Guard: the suite must never resolve paths into the real home directory.

See tests/__init__.py. If this fails, the run started without the package
setup (run from the repo root) or a module resolved a path before it ran.
"""
import os
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd"
sys.path.insert(0, str(SKILL / "scripts"))
sys.path.insert(0, str(SKILL / "display"))


def _under(child, parent) -> bool:
    try:
        pathlib.Path(child).resolve().relative_to(pathlib.Path(parent).resolve())
        return True
    except ValueError:
        return False


class TestIsolation(unittest.TestCase):

    def setUp(self):
        self.sandbox = os.environ.get("DND_TEST_SANDBOX")
        self.real_home = os.environ.get("DND_TEST_REAL_HOME")
        if not self.sandbox:
            self.fail("tests/__init__.py did not run: start the suite from the repo root "
                      "(python -m pytest tests/ or python -m unittest discover)")

    def test_paths_resolve_into_sandbox(self):
        import paths
        for fn in (paths._root, paths.runtime_dir, paths.campaigns_dir, paths.characters_dir):
            p = fn()
            self.assertTrue(_under(p, self.sandbox), f"{fn.__name__}() -> {p} is outside the sandbox")

    def test_display_runtime_dir_in_sandbox(self):
        import runtime_paths
        self.assertTrue(_under(runtime_paths.RT, self.sandbox), runtime_paths.RT)

    def test_tts_cache_and_home_in_sandbox(self):
        import tts
        self.assertTrue(_under(tts.cache_dir(), self.sandbox))
        self.assertTrue(_under(pathlib.Path.home(), self.sandbox))
        self.assertNotEqual(pathlib.Path.home().resolve(), pathlib.Path(self.real_home).resolve())


if __name__ == "__main__":
    unittest.main()
