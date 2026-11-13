"""The 2024 SRD dataset is built under the data root and a broken build fails.

build_srd.py --ruleset 2024 wrote into the plugin's data/ directory, which a
reinstall replaces, and without PyYAML it printed one line and wrote a file
with 0 spells and 0 features. It now writes to <data root>/data, refuses to
build without PyYAML (unless --no-fvtt), and refuses to write a dataset whose
FoundryVTT categories came out empty.
"""
import importlib.util
import os
import pathlib
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import paths  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build_srd = _load("build_srd_loc", SCRIPTS / "build_srd.py")


class TestLocation(unittest.TestCase):

    def test_2024_out_file_under_data_root(self):
        out = pathlib.Path(build_srd._out_file("2024"))
        self.assertEqual(out.parent, paths.built_data_dir())
        self.assertNotEqual(out.parent, paths.data_dir())

    def test_2014_stays_bundled(self):
        self.assertEqual(pathlib.Path(build_srd._out_file("2014")).parent, paths.data_dir())

    def test_dataset_file_prefers_built_copy(self):
        built = paths.built_data_dir()
        built.mkdir(parents=True, exist_ok=True)
        f = built / "dnd5e_srd_2024.json"
        f.write_text("{}", encoding="utf-8")
        try:
            self.assertEqual(paths.srd_path("2024"), f)
        finally:
            f.unlink()
        self.assertEqual(paths.srd_path("2024"), paths.data_dir() / "dnd5e_srd_2024.json")


class TestFailsLoudly(unittest.TestCase):

    def test_no_yaml_refuses(self):
        with mock.patch.object(build_srd, "yaml", None):
            with self.assertRaises(build_srd.BuildIncomplete) as cm:
                build_srd.cmd_build(ruleset="2024")
        self.assertIn("pyyaml", str(cm.exception).lower())

    def test_empty_spells_refuses_and_writes_nothing(self):
        out = pathlib.Path(build_srd._out_file("2024"))
        with mock.patch.object(build_srd, "yaml", object()), \
                mock.patch.object(build_srd, "_latest_sha", return_value="x"), \
                mock.patch.object(build_srd, "_build_5ebits", return_value=({"monsters": [1]}, [])), \
                mock.patch.object(build_srd, "_build_fvtt_spells_2024", return_value=[]), \
                mock.patch.object(build_srd, "_build_fvtt", return_value=([{"f": 1}], None)):
            with self.assertRaises(build_srd.BuildIncomplete) as cm:
                build_srd.cmd_build(ruleset="2024")
        self.assertIn("spells", str(cm.exception))
        self.assertFalse(out.exists())


class TestRequirements(unittest.TestCase):

    def test_pyyaml_listed(self):
        req = (REPO / "skills" / "dnd" / "display" / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("pyyaml", req.lower())


if __name__ == "__main__":
    unittest.main()
