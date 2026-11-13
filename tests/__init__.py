"""Test package setup: keep every test away from the real data root.

Importing dnd-display-app.py (or any script that resolves paths.runtime_dir())
writes to the runtime directory. Before this existed, running the suite during
a game replaced the TV's live party with the fixture character "Aldric".

This module is imported before any test module whenever the suite is run from
the repository root — `python -m pytest tests/`, `python -m unittest discover`,
or `python -m unittest tests.test_x`. It points the data root, runtime dir,
TTS cache and home directory at a throwaway temp directory, inherited by every
subprocess a test starts. tests/test_test_isolation.py fails if it is not in
effect (for example `python -m unittest discover -s tests`, which skips this
package file — run from the repo root instead).
"""
import atexit
import os
import pathlib
import shutil
import tempfile

REAL_HOME = os.path.expanduser("~")

if not os.environ.get("DND_TEST_SANDBOX"):
    _sandbox = pathlib.Path(tempfile.mkdtemp(prefix="dnd-tests-"))
    _home = _sandbox / "home"
    _home.mkdir()
    os.environ["DND_TEST_SANDBOX"] = str(_sandbox)
    os.environ["DND_TEST_REAL_HOME"] = REAL_HOME
    os.environ["DND_CAMPAIGN_ROOT"] = str(_sandbox / "dnd")
    os.environ["DND_RUNTIME_DIR"] = str(_sandbox / "dnd" / ".runtime")
    os.environ["DND_TTS_CACHE_DIR"] = str(_sandbox / "tts-cache")
    # Nothing listens on the discard port: a script that would post to the
    # live TV on :5001 gets "connection refused" instead.
    os.environ["DND_DISPLAY_URL"] = "http://127.0.0.1:9"
    os.environ["HOME"] = str(_home)
    os.environ["USERPROFILE"] = str(_home)
    atexit.register(shutil.rmtree, str(_sandbox), ignore_errors=True)
