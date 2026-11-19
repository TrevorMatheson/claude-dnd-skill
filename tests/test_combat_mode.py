"""Combat mode on the server (display foundation S4).

Covers the server side of AC7 and D9.

D9 — combat mode turns on when the turn order is set and goes back to the
     saved layout when it is cleared; the host can flip it by hand from the
     laptop at any time.

Plan S4:
  * /stats: a turn_order dict without "order" merges into the stored one, so
    "current" and "round" update and "order" is kept. None clears.
    (Today /stats replaces turn_order wholesale: plan fact 3.)
  * On the empty <-> set transition of the turn order, the manual override
    (_mode_override) is cleared and {"display": ...} is broadcast.
  * POST /display-mode {"combat": true|false|null}, token plus loopback.

Same app-loading pattern as tests/test_display_config.py: a fresh app per
class, _broadcast captured, _persist_* stubbed, the active campaign a
throwaway folder under the sandboxed campaign root.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import sys
import unittest
import uuid
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(REPO / "skills" / "dnd" / "scripts"))

ORDER = ["Havoc", "Goblin 1", "Mira", "Goblin 2"]


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_combat", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError as exc:                    # flask/deps absent
        raise unittest.SkipTest(f"display app not importable: {exc}")
    return mod


class _AppCase(unittest.TestCase):
    """Fresh app; broadcasts captured; persistence stubbed; no turn order, no override."""

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def setUp(self):
        a = self.app
        self.name = f"combattest-{uuid.uuid4().hex[:8]}"
        self.camp = pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"]) / "campaigns" / self.name
        self.camp.mkdir(parents=True)
        (self.camp / "state.md").write_text("# State\n", encoding="utf-8")
        camp = self.camp
        self.sent = []
        self._p = [mock.patch.object(a, "_broadcast", side_effect=self.sent.append),
                   mock.patch.object(a, "_persist_log"),
                   mock.patch.object(a, "_persist_tail"),
                   mock.patch.object(a, "_persist_stats"),
                   mock.patch.object(a, "_active_campaign_name", return_value=self.name),
                   mock.patch.object(a, "_find_campaign",
                                     side_effect=lambda n: camp if n == self.name
                                     else pathlib.Path(os.environ["DND_CAMPAIGN_ROOT"]) / "campaigns" / n)]
        for p in self._p:
            p.start()
        a._current_stats.clear()
        a._current_stats.update({"players": [{"name": "Havoc"}, {"name": "Mira"}]})
        a._mode_override = None
        a._awaiting = None

    def tearDown(self):
        for p in self._p:
            p.stop()
        self.app._current_stats.clear()
        self.app._mode_override = None
        shutil.rmtree(self.camp, ignore_errors=True)

    # helpers
    def _stats(self, body: dict):
        r = self.client.post("/stats", json=body)
        self.assertTrue(200 <= r.status_code < 300, f"/stats {r.status_code} {r.get_data(as_text=True)[:200]}")
        return r

    def _mode(self, value, remote="127.0.0.1"):
        return self.client.post("/display-mode", json={"combat": value},
                                environ_base={"REMOTE_ADDR": remote})

    def _stored_to(self):
        return self.app._current_stats.get("turn_order")

    def _displays(self) -> list:
        return [m["display"] for m in self.sent if isinstance(m, dict) and "display" in m]

    def _start_combat(self):
        self._stats({"turn_order": {"order": list(ORDER), "current": "Havoc", "round": 1}})


# ─── turn_order merges on the server ─────────────────────────────────────────

class TurnOrderMerge(_AppCase):

    # AC7 (server side): plan fact 3, a reconnecting TV needs the names
    def test_current_only_update_keeps_order_and_round(self):
        self._start_combat()
        self._stats({"turn_order": {"current": "Goblin 1"}})
        to = self._stored_to()
        self.assertIsInstance(to, dict, f"turn_order after partial update: {to!r}")
        self.assertEqual(to.get("order"), ORDER, f"order dropped by a current-only update: {to}")
        self.assertEqual(to.get("current"), "Goblin 1", to)
        self.assertEqual(to.get("round"), 1, f"round dropped by a current-only update: {to}")

    # AC7 (server side)
    def test_round_only_update_keeps_order_and_current(self):
        self._start_combat()
        self._stats({"turn_order": {"current": "Mira"}})
        self._stats({"turn_order": {"round": 2}})
        to = self._stored_to()
        self.assertIsInstance(to, dict, f"turn_order after round update: {to!r}")
        self.assertEqual(to.get("order"), ORDER, to)
        self.assertEqual(to.get("current"), "Mira", to)
        self.assertEqual(to.get("round"), 2, to)

    # AC7 (server side): the broadcast carries the merged order too
    def test_stats_broadcast_after_partial_update_carries_full_order(self):
        self._start_combat()
        self.sent.clear()
        self._stats({"turn_order": {"current": "Mira"}})
        stats = [m["stats"] for m in self.sent if isinstance(m, dict) and "stats" in m]
        self.assertTrue(stats, f"no stats broadcast: {self.sent}")
        to = stats[-1].get("turn_order") or {}
        self.assertEqual(to.get("order"), ORDER, f"broadcast turn_order: {to}")
        self.assertEqual(to.get("current"), "Mira")

    # AC7 (server side)
    def test_null_clears_turn_order(self):
        self._start_combat()
        self._stats({"turn_order": None})
        self.assertFalse(self._stored_to(), f"turn_order not cleared: {self._stored_to()!r}")

    # A new full order replaces the old one (a new fight).
    def test_full_order_replaces_previous_order(self):
        self._start_combat()
        self._stats({"turn_order": {"order": ["Mira", "Ogre"], "current": "Mira", "round": 1}})
        to = self._stored_to()
        self.assertEqual(to.get("order"), ["Mira", "Ogre"], to)
        self.assertEqual(to.get("current"), "Mira", to)


# ─── manual override: POST /display-mode ─────────────────────────────────────

class DisplayModeOverride(_AppCase):

    def _check_value(self, value):
        self.sent.clear()
        r = self._mode(value)
        self.assertTrue(200 <= r.status_code < 300,
                        f"/display-mode {value!r}: {r.status_code} {r.get_data(as_text=True)[:200]}")
        self.assertIs(self.app._mode_override, value, f"stored override for {value!r}")
        got = self.client.get("/display-config").get_json()
        self.assertIs(got.get("mode_override"), value, f"GET /display-config mode_override: {got.get('mode_override')!r}")
        disp = self._displays()
        self.assertTrue(disp, f"no {{'display': ...}} broadcast for {value!r}; sent: {self.sent}")
        self.assertIn("mode_override", disp[-1], disp[-1])
        self.assertIs(disp[-1]["mode_override"], value, disp[-1])
        self.assertIn("config", disp[-1], "display broadcast must carry config as in S3")

    # AC7 / D9: host flips combat on by hand
    def test_true_is_stored_and_broadcast(self):
        self._check_value(True)

    # AC7 / D9
    def test_false_is_stored_and_broadcast(self):
        self._check_value(False)

    # AC7 / D9: back to automatic
    def test_null_is_stored_and_broadcast(self):
        self._check_value(True)
        self._check_value(None)

    # Host only: the LAN token is on every phone too
    def test_non_loopback_is_403_and_override_unchanged(self):
        self._check_value(True)
        self.sent.clear()
        r = self._mode(False, remote="192.168.1.50")
        self.assertEqual(r.status_code, 403, f"{r.status_code} {r.get_data(as_text=True)[:200]}")
        self.assertIs(self.app._mode_override, True, "a refused request changed the override")
        self.assertFalse(self._displays(), "a refused request still broadcast display")

    def test_ipv6_loopback_is_allowed(self):
        r = self._mode(True, remote="::1")
        self.assertTrue(200 <= r.status_code < 300, f"{r.status_code} {r.get_data(as_text=True)[:200]}")
        self.assertIs(self.app._mode_override, True)

    def test_bad_token_is_refused(self):
        with mock.patch.object(self.app, "_token_ok", return_value=False):
            r = self._mode(True)
        self.assertIn(r.status_code, (401, 403), f"{r.status_code} {r.get_data(as_text=True)[:200]}")
        self.assertIsNone(self.app._mode_override)

    def test_non_boolean_values_are_400(self):
        for bad in ("true", "combat", 1, 0, [], {}):
            with self.subTest(value=bad):
                self.sent.clear()
                r = self._mode(bad)
                self.assertEqual(r.status_code, 400,
                                 f"/display-mode {bad!r}: {r.status_code} {r.get_data(as_text=True)[:200]}")
                self.assertIsNone(self.app._mode_override, f"{bad!r} changed the override")
                self.assertFalse(self._displays(), f"{bad!r} still broadcast display")

    def test_non_object_body_is_400(self):
        r = self.client.post("/display-mode", data="true", content_type="application/json")
        self.assertEqual(r.status_code, 400, f"{r.status_code} {r.get_data(as_text=True)[:200]}")
        self.assertIsNone(self.app._mode_override)


# ─── the automatic switch resumes on the empty <-> set transition ────────────

class AutomaticSwitchResumes(_AppCase):

    def _set_override(self, value):
        r = self._mode(value)
        self.assertTrue(200 <= r.status_code < 300, f"/display-mode {r.status_code}")
        self.assertIs(self.app._mode_override, value)
        self.sent.clear()

    # D9: host forced story, then initiative is rolled -> combat takes over
    def test_setting_turn_order_clears_false_override_and_broadcasts(self):
        self._set_override(False)
        self._start_combat()
        self.assertIsNone(self.app._mode_override,
                          f"starting combat kept the manual override {self.app._mode_override!r}")
        disp = self._displays()
        self.assertTrue(disp, f"no display broadcast on combat start; sent keys: {[list(m) for m in self.sent]}")
        self.assertIsNone(disp[-1].get("mode_override", "missing"), disp[-1])

    # D9: host forced combat, then combat ends -> back to the saved layout
    def test_clearing_turn_order_clears_true_override_and_broadcasts(self):
        self._start_combat()
        self._set_override(True)
        self._stats({"turn_order": None})
        self.assertIsNone(self.app._mode_override,
                          f"ending combat kept the manual override {self.app._mode_override!r}")
        disp = self._displays()
        self.assertTrue(disp, f"no display broadcast on combat end; sent keys: {[list(m) for m in self.sent]}")
        self.assertIsNone(disp[-1].get("mode_override", "missing"), disp[-1])

    # D9: the host's choice holds until the next transition
    def test_partial_update_during_combat_keeps_override(self):
        self._start_combat()
        self._set_override(False)
        self._stats({"turn_order": {"current": "Goblin 1"}})
        self._stats({"turn_order": {"round": 2}})
        self.assertIs(self.app._mode_override, False,
                      "a current/round update mid-combat cleared the host's override")

    def test_clearing_when_already_empty_keeps_override(self):
        self._set_override(True)
        self._stats({"turn_order": None})
        self.assertIs(self.app._mode_override, True,
                      "clearing an already-empty turn order cleared the override (no transition)")

    def test_unrelated_stats_keep_override(self):
        self._start_combat()
        self._set_override(False)
        self._stats({"players": [{"name": "Havoc", "hp": 7}]})
        self.assertIs(self.app._mode_override, False)


# ─── combat is "on" only when the stored order has names ─────────────────────
#
# Builder decision after the S4 verify edge case: combat counts as on only
# when the stored turn_order has a non-empty "order" list. A dict that is
# truthy but nameless ({"current": ...}, {"round": ...}, {"order": []}) is not
# combat, so reaching it from nothing is not a start, and reaching it from a
# real order is an end (same as null).

class CombatNeedsNamedOrder(_AppCase):

    def _set_override(self, value):
        r = self._mode(value)
        self.assertTrue(200 <= r.status_code < 300, f"/display-mode {r.status_code}")
        self.assertIs(self.app._mode_override, value)
        self.sent.clear()

    def _assert_no_start(self, body, override):
        self._set_override(override)
        self._stats({"turn_order": body})
        self.assertIs(self.app._mode_override, override,
                      f"{body!r} with no stored order cleared the override (counted as combat start)")
        self.assertFalse(self._displays(),
                         f"{body!r} with no stored order broadcast display: {self._displays()}")

    def _assert_end(self, body):
        self._start_combat()
        self._set_override(True)
        self._stats({"turn_order": body})
        self.assertIsNone(self.app._mode_override,
                          f"{body!r} after a real order kept the override {self.app._mode_override!r} "
                          "(should count as combat ending)")
        disp = self._displays()
        self.assertTrue(disp, f"no display broadcast when {body!r} ended combat; "
                              f"sent keys: {[list(m) for m in self.sent]}")
        self.assertIsNone(disp[-1].get("mode_override", "missing"), disp[-1])

    # Partial updates with nothing stored are not a combat start
    def test_current_only_with_nothing_stored_is_not_a_start(self):
        self._assert_no_start({"current": "Mira"}, False)

    def test_round_only_with_nothing_stored_is_not_a_start(self):
        self._assert_no_start({"round": 2}, True)

    def test_empty_order_with_nothing_stored_is_not_a_start(self):
        self._assert_no_start({"order": []}, False)

    # A nameless/empty order after a real one is a combat end
    def test_empty_order_after_real_order_ends_combat(self):
        self._assert_end({"order": []})

    def test_empty_order_with_current_after_real_order_ends_combat(self):
        self._assert_end({"order": [], "current": None, "round": 0})

    # Following a nameless order, a real order is a start
    def test_real_order_after_empty_order_starts_combat(self):
        self._stats({"turn_order": {"order": []}})
        self._set_override(False)
        self._start_combat()
        self.assertIsNone(self.app._mode_override,
                          "a real order after {'order': []} kept the override (not counted as start)")
        disp = self._displays()
        self.assertTrue(disp, f"no display broadcast on combat start; sent keys: {[list(m) for m in self.sent]}")
        self.assertIsNone(disp[-1].get("mode_override", "missing"), disp[-1])

    def test_real_order_after_orphan_current_starts_combat(self):
        self._stats({"turn_order": {"current": "Mira"}})
        self._set_override(False)
        self._start_combat()
        self.assertIsNone(self.app._mode_override,
                          "a real order after a nameless {'current': ...} kept the override")
        self.assertTrue(self._displays(), "no display broadcast on combat start")


if __name__ == "__main__":
    unittest.main()
