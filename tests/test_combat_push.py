"""Starting combat starts combat mode (display foundation S5).

Covers AC10 and D12.

D12  — the command that rolls initiative pushes the turn order itself, and
       the command that ends combat clears it.
AC10 — rolling initiative pushes the turn order; ending combat clears it,
       with no separate push command.

Plan S5:
  * `combat.py init` pushes {"turn_order": {"order": [...], "current": first,
    "round": 1}} through `push_stats.py --json`; `--no-push` opts out.
  * New `combat.py end [--clear-map]` pushes {"turn_order": null}, and also
    {"map": null} with --clear-map.
  * Finding F1 (S5 verify), builder's decision: `end --clear-map` takes
    -c/--campaign (default $CAMPAIGN), deletes <campaign>/battlemap.json like
    `map clear`, and pushes ONE payload {"turn_order": null, "map": null}.
    With no campaign it exits non-zero naming -c, pushes nothing and prints
    no confirmation. Plain `end` needs no campaign and never touches
    battlemap.json. `end` prints exactly "Combat ended." (plain) or
    "Combat ended. Battle map cleared." (--clear-map).
  * Like push_map, a push does nothing harmful when no display is running.

The seam: a module-level helper `combat.push_stats_json(payload: dict)` that
runs `push_stats.py --json <payload>`. Most tests mock it. The
TestPushGoesThroughPushStats class instead patches subprocess.run, so the
real helper (whatever its name) must invoke push_stats.py with --json.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


combat = _load("combat_push", SCRIPTS / "combat.py")
import battlemap  # noqa: E402  (SCRIPTS is on sys.path)

END_LINE = "Combat ended.\n"
END_CLEAR_LINE = "Combat ended. Battle map cleared.\n"


def _no_campaign_env():
    """os.environ without $CAMPAIGN, for patch.dict(..., clear=True)."""
    env = dict(os.environ)
    env.pop("CAMPAIGN", None)
    return env


_camp_seq = [0]


def _make_campaign_with_map():
    """A throwaway campaign under the sandboxed DND_CAMPAIGN_ROOT holding a
    battlemap.json. Returns (name, dir, map file)."""
    _camp_seq[0] += 1
    name = f"f1-camp-{os.getpid()}-{_camp_seq[0]}"
    camp = pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"]) / "campaigns" / name
    camp.mkdir(parents=True, exist_ok=True)
    battlemap.save(camp, battlemap.new_map(5, 5))
    f = battlemap.map_path(camp)
    assert f.exists(), f"fixture: {f} was not written"
    return name, camp, f

# Five combatants. With d20 faces [10, 10, 15, 12, 3]:
#   Havoc 12 (dex 2), Goblin 1 12 (dex 2), Mira 16, Zed 12 (dex 0), Goblin 2 5.
# Havoc / Goblin 1 tie on total AND dex: input order holds (stable sort).
# Zed ties on total but has lower dex, so goes after them.
COMBATANTS = [
    {"name": "Havoc", "dex_mod": 2, "hp": 24, "ac": 16, "type": "pc"},
    {"name": "Goblin 1", "dex_mod": 2, "hp": 7, "ac": 15, "type": "npc"},
    {"name": "Mira", "dex_mod": 1, "hp": 18, "ac": 13, "type": "pc"},
    {"name": "Zed", "dex_mod": 0, "hp": 9, "ac": 12, "type": "pc"},
    {"name": "Goblin 2", "dex_mod": 2, "hp": 7, "ac": 15, "type": "npc"},
]
FACES = [10, 10, 15, 12, 3]
ORDER = ["Mira", "Havoc", "Goblin 1", "Zed", "Goblin 2"]

# combat.py init's stdout for COMBATANTS and FACES, captured from the code
# before S5 (commit b7da31a). S5 must not change it.
GOLDEN_INIT_STDOUT = (
    '\n====================================================================\n'
    '  COMBAT — Round 1\n'
    '====================================================================\n'
    '  #   Name                Init       HP   AC  Conditions\n'
    '  --------------------------------------------------------------\n'
    '  ► 1  Mira                  16    18/18   13  —\n'
    '    2  Havoc                 12    24/24   16  —\n'
    '    3  Goblin 1              12      7/7   15  —\n'
    '    4  Zed                   12      9/9   12  —\n'
    '    5  Goblin 2               5      7/7   15  —\n'
    '====================================================================\n'
    '\n'
    'Initiative rolls:\n'
    '  Mira: d20(15) + 1 = 16\n'
    '  Havoc: d20(10) + 2 = 12\n'
    '  Goblin 1: d20(10) + 2 = 12\n'
    '  Zed: d20(12) + 0 = 12\n'
    '  Goblin 2: d20(3) + 2 = 5\n'
    '\n'
    'STATE_JSON: [{"name": "Mira", "dex_mod": 1, "hp": 18, "ac": 13, "type": "pc", "max_hp": 18, '
    '"initiative_roll": 15, "initiative": 16, "conditions": [], "temp_hp": 0}, '
    '{"name": "Havoc", "dex_mod": 2, "hp": 24, "ac": 16, "type": "pc", "max_hp": 24, '
    '"initiative_roll": 10, "initiative": 12, "conditions": [], "temp_hp": 0}, '
    '{"name": "Goblin 1", "dex_mod": 2, "hp": 7, "ac": 15, "type": "npc", "max_hp": 7, '
    '"initiative_roll": 10, "initiative": 12, "conditions": [], "temp_hp": 0}, '
    '{"name": "Zed", "dex_mod": 0, "hp": 9, "ac": 12, "type": "pc", "max_hp": 9, '
    '"initiative_roll": 12, "initiative": 12, "conditions": [], "temp_hp": 0}, '
    '{"name": "Goblin 2", "dex_mod": 2, "hp": 7, "ac": 15, "type": "npc", "max_hp": 7, '
    '"initiative_roll": 3, "initiative": 5, "conditions": [], "temp_hp": 0}]\n'
)

EXPECTED_TURN_ORDER = {"turn_order": {"order": ORDER, "current": "Mira", "round": 1}}


def _payload_of(call):
    """The dict passed to push_stats_json(payload), positional or keyword."""
    if call.args:
        return call.args[0]
    return call.kwargs.get("payload")


def _run_main(argv, faces=None, push=None, env=None, stderr=None):
    """Run combat.main(argv) with dice fixed and push_stats_json mocked.

    Returns (exit_code, stdout, the mock). create=True so the test runs (and
    fails on the missing behavior) before the helper exists.
    """
    push = push if push is not None else mock.MagicMock(return_value=None)
    buf = io.StringIO()
    with contextlib.ExitStack() as st:
        if env is not None:
            st.enter_context(mock.patch.dict(os.environ, env, clear=True))
        if stderr is not None:
            st.enter_context(contextlib.redirect_stderr(stderr))
        if faces is not None:
            st.enter_context(mock.patch.object(combat, "d", side_effect=list(faces)))
        st.enter_context(mock.patch.object(combat, "push_stats_json", push, create=True))
        # Belt and braces: nothing in these tests may reach a real process.
        st.enter_context(mock.patch("subprocess.run",
                                    side_effect=AssertionError("push must go through push_stats_json")))
        st.enter_context(contextlib.redirect_stdout(buf))
        try:
            rc = combat.main(argv)
        except SystemExit as exc:  # argparse
            rc = exc.code
    return rc, buf.getvalue(), push


class TestInitPushesTurnOrder(unittest.TestCase):
    """AC10 / D12: rolling initiative pushes the turn order."""

    def test_golden_is_todays_output(self):
        # Guards the golden itself: with the push mocked, stdout is unchanged.
        rc, out, _ = _run_main(["init", json.dumps(COMBATANTS)], faces=FACES)
        self.assertEqual(rc, 0)
        self.assertEqual(out, GOLDEN_INIT_STDOUT)

    def test_init_pushes_exactly_one_turn_order_payload(self):
        rc, _, push = _run_main(["init", json.dumps(COMBATANTS)], faces=FACES)
        self.assertEqual(rc, 0)
        self.assertEqual(push.call_count, 1,
                         f"init should push once, pushed {push.call_count} times")
        self.assertEqual(_payload_of(push.call_args), EXPECTED_TURN_ORDER)

    def test_order_matches_printed_order_including_ties(self):
        rc, out, push = _run_main(["init", json.dumps(COMBATANTS)], faces=FACES)
        self.assertEqual(rc, 0)
        state = json.loads(out.split("STATE_JSON:", 1)[1])
        printed = [c["name"] for c in state]
        self.assertTrue(push.called, "init pushed nothing")
        pushed = _payload_of(push.call_args)["turn_order"]
        self.assertEqual(pushed["order"], printed)
        self.assertEqual(pushed["current"], printed[0])
        self.assertEqual(pushed["round"], 1)

    def test_payload_is_json_serialisable(self):
        _, _, push = _run_main(["init", json.dumps(COMBATANTS)], faces=FACES)
        self.assertTrue(push.called, "init pushed nothing")
        json.dumps(_payload_of(push.call_args))

    def test_no_push_pushes_nothing_same_stdout(self):
        rc, out, push = _run_main(["init", json.dumps(COMBATANTS), "--no-push"], faces=FACES)
        self.assertEqual(rc, 0, "init --no-push should be accepted")
        push.assert_not_called()
        self.assertEqual(out, GOLDEN_INIT_STDOUT)


class TestEndClearsTurnOrder(unittest.TestCase):
    """AC10 / D12: ending combat clears the turn order."""

    def _pushed(self, argv):
        rc, out, push = _run_main(argv)
        merged = {}
        for c in push.call_args_list:
            p = _payload_of(c)
            self.assertIsInstance(p, dict)
            merged.update(p)
        return rc, out, push, merged

    def test_end_pushes_turn_order_none(self):
        rc, _, push, merged = self._pushed(["end"])
        self.assertEqual(rc, 0)
        self.assertTrue(push.called, "end pushed nothing")
        self.assertIn("turn_order", merged)
        self.assertIsNone(merged["turn_order"])

    def test_end_without_clear_map_keeps_the_map(self):
        rc, _, push, merged = self._pushed(["end"])
        self.assertEqual(rc, 0)
        self.assertTrue(push.called, "end pushed nothing")
        self.assertNotIn("map", merged, "plain `end` must not clear the map")

    def test_end_clear_map_pushes_one_payload_and_deletes_the_map(self):
        name, _, f = _make_campaign_with_map()
        rc, out, push = _run_main(["end", "--clear-map", "-c", name], env=_no_campaign_env())
        self.assertEqual(rc, 0, out)
        self.assertEqual(push.call_count, 1,
                         f"end --clear-map should push once, pushed {push.call_count} times")
        self.assertEqual(_payload_of(push.call_args), {"turn_order": None, "map": None})
        self.assertFalse(f.exists(), "end --clear-map left battlemap.json behind")

    def test_end_clear_map_long_campaign_option(self):
        name, _, f = _make_campaign_with_map()
        rc, out, _ = _run_main(["end", "--clear-map", "--campaign", name], env=_no_campaign_env())
        self.assertEqual(rc, 0, out)
        self.assertFalse(f.exists(), "end --clear-map --campaign left battlemap.json behind")

    def test_end_clear_map_uses_campaign_env(self):
        name, _, f = _make_campaign_with_map()
        env = _no_campaign_env()
        env["CAMPAIGN"] = name
        rc, out, push = _run_main(["end", "--clear-map"], env=env)
        self.assertEqual(rc, 0, out)
        self.assertEqual(push.call_count, 1)
        self.assertEqual(_payload_of(push.call_args), {"turn_order": None, "map": None})
        self.assertFalse(f.exists(), "end --clear-map with $CAMPAIGN left battlemap.json behind")

    def test_end_clear_map_with_no_map_file_still_ends(self):
        # Like `map clear`: a missing battlemap.json is not an error.
        name, _, f = _make_campaign_with_map()
        f.unlink()
        rc, out, push = _run_main(["end", "--clear-map", "-c", name], env=_no_campaign_env())
        self.assertEqual(rc, 0, out)
        self.assertEqual(push.call_count, 1)
        self.assertEqual(_payload_of(push.call_args), {"turn_order": None, "map": None})
        self.assertEqual(out, END_CLEAR_LINE)

    def test_end_clear_map_without_campaign_fails_and_pushes_nothing(self):
        err = io.StringIO()
        rc, out, push = _run_main(["end", "--clear-map"], env=_no_campaign_env(), stderr=err)
        self.assertNotIn(rc, (0, None), "end --clear-map with no campaign must exit non-zero")
        push.assert_not_called()
        self.assertNotIn("Combat ended", out, "combat must not be half-ended")
        # SystemExit("msg") carries the message as its code; argparse writes stderr.
        msg = (rc if isinstance(rc, str) else "") + err.getvalue()
        self.assertIn("-c", msg, f"the error should name -c, got: {msg!r}")

    def test_plain_end_never_touches_the_map_file(self):
        name, _, f = _make_campaign_with_map()
        before = f.read_bytes()
        env = _no_campaign_env()
        env["CAMPAIGN"] = name
        rc, out, push = _run_main(["end"], env=env)
        self.assertEqual(rc, 0, out)
        self.assertTrue(f.exists(), "plain `end` deleted battlemap.json")
        self.assertEqual(f.read_bytes(), before)
        self.assertEqual(_payload_of(push.call_args), {"turn_order": None})

    def test_plain_end_with_explicit_campaign_never_touches_the_map_file(self):
        name, _, f = _make_campaign_with_map()
        rc, out, push = _run_main(["end", "-c", name], env=_no_campaign_env())
        self.assertEqual(rc, 0, out)
        self.assertTrue(f.exists(), "plain `end -c` deleted battlemap.json")
        self.assertEqual(_payload_of(push.call_args), {"turn_order": None})

    def test_plain_end_needs_no_campaign(self):
        rc, out, push = _run_main(["end"], env=_no_campaign_env())
        self.assertEqual(rc, 0, out)
        self.assertEqual(push.call_count, 1)

    def test_plain_end_prints_exactly_one_line(self):
        rc, out, _ = _run_main(["end"], env=_no_campaign_env())
        self.assertEqual(rc, 0)
        self.assertEqual(out, END_LINE)

    def test_end_clear_map_prints_exactly_one_line(self):
        name, _, _ = _make_campaign_with_map()
        rc, out, _ = _run_main(["end", "--clear-map", "-c", name], env=_no_campaign_env())
        self.assertEqual(rc, 0)
        self.assertEqual(out, END_CLEAR_LINE)


class TestStrictCli(unittest.TestCase):
    """tests/test_strict_cli.py pattern: unknown options exit 2."""

    def _run(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / "combat.py"), *args],
                              capture_output=True, encoding="utf-8", timeout=60)

    def test_end_bogus_exits_2_naming_the_option(self):
        r = self._run("end", "--bogus")
        self.assertEqual(r.returncode, 2)
        # Today `end` is not a subcommand at all ("invalid choice"); the exit
        # must come from `end` rejecting --bogus.
        self.assertNotIn("invalid choice", r.stderr)
        self.assertIn("--bogus", r.stderr)

    def test_end_clear_map_no_campaign_real_process(self):
        r = subprocess.run([sys.executable, str(SCRIPTS / "combat.py"), "end", "--clear-map"],
                           capture_output=True, encoding="utf-8", timeout=60, env=_no_campaign_env())
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("-c", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn("Combat ended", r.stdout)

    def test_init_bogus_exits_2(self):
        r = self._run("init", json.dumps(COMBATANTS), "--bogus")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--bogus", r.stderr)


class TestPushFailureIsHarmless(unittest.TestCase):
    """No display running must not break init or end."""

    def test_init_survives_helper_raising(self):
        push = mock.MagicMock(side_effect=ConnectionRefusedError("no display"))
        rc, out, push = _run_main(["init", json.dumps(COMBATANTS)], faces=FACES, push=push)
        self.assertEqual(rc, 0)
        self.assertEqual(out, GOLDEN_INIT_STDOUT)
        self.assertTrue(push.called, "init never tried to push")

    def test_end_survives_helper_raising(self):
        push = mock.MagicMock(side_effect=ConnectionRefusedError("no display"))
        rc, out, push = _run_main(["end"], push=push, env=_no_campaign_env())
        self.assertEqual(rc, 0)
        self.assertTrue(push.called, "end never tried to push")
        self.assertEqual(out, END_LINE)

    def test_end_clear_map_survives_helper_raising(self):
        name, _, f = _make_campaign_with_map()
        push = mock.MagicMock(side_effect=ConnectionRefusedError("no display"))
        rc, out, push = _run_main(["end", "--clear-map", "-c", name], push=push, env=_no_campaign_env())
        self.assertEqual(rc, 0)
        self.assertTrue(push.called, "end --clear-map never tried to push")
        self.assertFalse(f.exists(), "a dead display must not stop the map file being cleared")
        self.assertEqual(out, END_CLEAR_LINE)

    def test_end_survives_push_stats_exit_nonzero(self):
        run = mock.MagicMock(return_value=subprocess.CompletedProcess([], 1, "", "connection refused"))
        with mock.patch.dict(os.environ, _no_campaign_env(), clear=True):
            rc, out = self._real_helper_run(["end"], run)
        self.assertEqual(rc, 0)
        self.assertTrue(run.called, "end never ran push_stats.py")
        self.assertEqual(out, END_LINE)

    def _real_helper_run(self, argv, run_mock):
        buf = io.StringIO()
        with mock.patch.object(combat, "d", side_effect=list(FACES)), \
             mock.patch("subprocess.run", run_mock), \
             contextlib.redirect_stdout(buf):
            try:
                rc = combat.main(argv)
            except SystemExit as exc:  # argparse
                rc = exc.code
        return rc, buf.getvalue()

    def test_init_survives_push_stats_exit_nonzero(self):
        run = mock.MagicMock(return_value=subprocess.CompletedProcess([], 1, "", "connection refused"))
        rc, out = self._real_helper_run(["init", json.dumps(COMBATANTS)], run)
        self.assertEqual(rc, 0)
        self.assertEqual(out, GOLDEN_INIT_STDOUT)
        self.assertTrue(run.called, "init never ran push_stats.py")

    def test_init_survives_push_stats_timeout(self):
        run = mock.MagicMock(side_effect=subprocess.TimeoutExpired("push_stats.py", 20))
        rc, out = self._real_helper_run(["init", json.dumps(COMBATANTS)], run)
        self.assertEqual(rc, 0)
        self.assertEqual(out, GOLDEN_INIT_STDOUT)
        self.assertTrue(run.called, "init never ran push_stats.py")

    def test_real_process_no_display(self):
        # The test sandbox points DND_DISPLAY_URL at a dead port, so the real
        # push_stats.py finds no display. init and end still exit 0.
        env = dict(os.environ)
        r = subprocess.run([sys.executable, str(SCRIPTS / "combat.py"), "init", json.dumps(COMBATANTS)],
                           capture_output=True, encoding="utf-8", timeout=90, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("STATE_JSON:", r.stdout)
        r = subprocess.run([sys.executable, str(SCRIPTS / "combat.py"), "end"],
                           capture_output=True, encoding="utf-8", timeout=90, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, END_LINE)

    def test_real_process_no_display_clear_map(self):
        name, _, f = _make_campaign_with_map()
        r = subprocess.run([sys.executable, str(SCRIPTS / "combat.py"), "end", "--clear-map", "-c", name],
                           capture_output=True, encoding="utf-8", timeout=90, env=_no_campaign_env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, END_CLEAR_LINE)
        self.assertFalse(f.exists(), "end --clear-map left battlemap.json behind")


class TestPushGoesThroughPushStats(unittest.TestCase):
    """Whatever the helper is called, it runs push_stats.py --json <payload>."""

    def _payloads(self, argv, faces=None):
        run = mock.MagicMock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        buf = io.StringIO()
        with contextlib.ExitStack() as st:
            if faces:
                st.enter_context(mock.patch.object(combat, "d", side_effect=list(faces)))
            st.enter_context(mock.patch("subprocess.run", run))
            st.enter_context(contextlib.redirect_stdout(buf))
            try:
                rc = combat.main(argv)
            except SystemExit as exc:  # argparse
                rc = exc.code
        self.assertEqual(rc, 0, f"combat.py {argv[0]} exited {rc}")
        out = []
        for c in run.call_args_list:
            cmd = [str(x) for x in (c.args[0] if c.args else c.kwargs["args"])]
            self.assertTrue(any(x.endswith("push_stats.py") for x in cmd), cmd)
            self.assertIn("--json", cmd)
            out.append(json.loads(cmd[cmd.index("--json") + 1]))
        return out

    def test_init_runs_push_stats_json(self):
        payloads = self._payloads(["init", json.dumps(COMBATANTS)], faces=FACES)
        self.assertEqual(payloads, [EXPECTED_TURN_ORDER])

    def test_end_runs_push_stats_json_null(self):
        payloads = self._payloads(["end"])
        merged = {}
        for p in payloads:
            merged.update(p)
        self.assertIn("turn_order", merged)
        self.assertIsNone(merged["turn_order"])

    def test_end_clear_map_runs_push_stats_once(self):
        name, _, _ = _make_campaign_with_map()
        with mock.patch.dict(os.environ, _no_campaign_env(), clear=True):
            payloads = self._payloads(["end", "--clear-map", "-c", name])
        self.assertEqual(payloads, [{"turn_order": None, "map": None}])

    def test_no_push_runs_no_process(self):
        payloads = self._payloads(["init", json.dumps(COMBATANTS), "--no-push"], faces=FACES)
        self.assertEqual(payloads, [])


if __name__ == "__main__":
    unittest.main()
