"""Scene backgrounds S3: the turn call names the place.

A `send.py --turn` call may carry a turn-level `location`:
`{"name": str <= 80, "look": str <= 400}` or `null`. It goes out as `place`
in the single /stats post that follows every chunk post. Any other shape
rejects the whole turn (exit 2) before anything is posted, like a bad block.

`_post` is replaced with a recorder, so nothing touches the network.
"""
import json
import pathlib
import re
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SKILL_MD = REPO / "skills" / "dnd" / "SKILL.md"
sys.path.insert(0, str(DISPLAY))

sys.argv = ["send.py"]
import send  # noqa: E402

TAVERN = {"name": "The Rusty Flagon",
          "look": "A low smoky taproom, a long oak bar, lanterns swinging from black beams."}


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, url, data, token):
        self.calls.append((url, json.loads(data.decode("utf-8"))))
        return True

    def chunks(self):
        return [p for u, p in self.calls if u == send.FLASK_URL]

    def stats(self):
        return [p for u, p in self.calls if u == send.STATS_URL]


class TurnLocation(unittest.TestCase):

    def setUp(self):
        send._SEND_LOG.clear()
        self.rec = _Recorder()

    def _send(self, turn):
        with mock.patch.object(send, "_post", side_effect=self.rec), \
                mock.patch.object(send, "_read_token", return_value="t"), \
                mock.patch.object(send, "dice_request", return_value=0):
            return send.send_turn(turn)

    def _turn(self, **extra):
        t = {"blocks": [{"text": "You push through the door."},
                        {"text": "Warmth and noise wash over you.\n\nA dwarf looks up from the bar."}]}
        t.update(extra)
        return t

    # -- accepted ---------------------------------------------------------

    def test_location_sent_as_place_in_one_stats_post_after_chunks(self):
        rc = self._send(self._turn(location=dict(TAVERN)))
        self.assertEqual(rc, 0)
        urls = [u for u, _ in self.rec.calls]
        self.assertEqual(urls.count(send.STATS_URL), 1, urls)
        self.assertGreaterEqual(urls.count(send.FLASK_URL), 2)
        self.assertEqual(urls[-1], send.STATS_URL, "stats post must come after every chunk post")
        self.assertEqual(self.rec.stats()[0]["place"],
                         {"name": TAVERN["name"], "look": TAVERN["look"]})

    def test_place_rides_with_other_stats_in_the_same_post(self):
        rc = self._send(self._turn(location=dict(TAVERN), turn="Anony", awaiting=["Anony"],
                                   stats={"players": [{"name": "Havoc"}]}))
        self.assertEqual(rc, 0)
        stats = self.rec.stats()
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["place"]["name"], TAVERN["name"])
        self.assertEqual(stats[0]["awaiting"], ["Anony"])
        self.assertEqual(stats[0]["turn_order"]["current"], "Anony")
        self.assertIn("players", stats[0])

    def test_location_only_turn_still_posts_place(self):
        rc = self._send({"blocks": [{"text": "The road bends north."}], "location": dict(TAVERN)})
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.rec.stats()), 1)
        self.assertIn("place", self.rec.stats()[0])

    def test_limits_are_inclusive(self):
        loc = {"name": "N" * 80, "look": "L" * 400}
        self.assertEqual(self._send(self._turn(location=loc)), 0)
        self.assertEqual(self.rec.stats()[0]["place"], loc)

    def test_null_location_sends_place_null(self):
        rc = self._send(self._turn(location=None))
        self.assertEqual(rc, 0)
        stats = self.rec.stats()
        self.assertEqual(len(stats), 1)
        self.assertIn("place", stats[0])
        self.assertIsNone(stats[0]["place"])

    def test_no_location_sends_no_place_key(self):
        rc = self._send(self._turn(turn="Havoc"))
        self.assertEqual(rc, 0)
        for p in self.rec.stats():
            self.assertNotIn("place", p)
        # and a turn with nothing for stats sends no stats post at all
        self.rec.calls.clear()
        self.assertEqual(self._send(self._turn()), 0)
        self.assertEqual(self.rec.stats(), [])

    # -- rejected ---------------------------------------------------------

    def _assert_rejected(self, loc):
        rc = self._send(self._turn(location=loc))
        self.assertEqual(rc, 2, f"location {loc!r} should be rejected")
        self.assertEqual(self.rec.calls, [], f"location {loc!r} must post nothing")

    def test_missing_name_rejected(self):
        self._assert_rejected({"look": TAVERN["look"]})

    def test_empty_name_rejected(self):
        self._assert_rejected({"name": "", "look": TAVERN["look"]})

    def test_non_dict_rejected(self):
        for loc in ("The Rusty Flagon", ["The Rusty Flagon"], 7, True):
            with self.subTest(loc=loc):
                self.rec.calls.clear()
                self._assert_rejected(loc)

    def test_long_look_rejected(self):
        self._assert_rejected({"name": "Cave", "look": "L" * 401})

    def test_long_name_rejected(self):
        self._assert_rejected({"name": "N" * 81, "look": "A cave."})

    def test_numeric_name_rejected(self):
        self._assert_rejected({"name": 42, "look": "A cave."})

    def test_non_string_look_rejected(self):
        self._assert_rejected({"name": "Cave", "look": 42})

    def test_validator_raises_value_error(self):
        """Assumed interface (plan S3): send._turn_location(turn)."""
        self.assertTrue(hasattr(send, "_turn_location"), "send._turn_location missing")
        with self.assertRaises(ValueError):
            send._turn_location({"location": {"look": "x"}})
        self.assertEqual(send._turn_location({"location": dict(TAVERN)}),
                         {"name": TAVERN["name"], "look": TAVERN["look"]})
        self.assertIsNone(send._turn_location({"location": None}))


class SkillDocExample(unittest.TestCase):

    def _example(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        start = text.find("**Display sync")
        self.assertNotEqual(start, -1, "Display sync section not found in SKILL.md")
        nxt = re.search(r"^---\s*$", text[start:], re.M)
        section = text[start:start + nxt.start()] if nxt else text[start:]
        blocks = re.findall(r"```json\s*\n(.*?)```", section, re.S)
        with_loc = [b for b in blocks if '"location"' in b]
        self.assertTrue(with_loc, "no JSON example with \"location\" in SKILL.md Display sync")
        return json.loads(with_loc[0])

    def test_example_turn_parses_and_validates(self):
        turn = self._example()
        self.assertIn("location", turn)
        loc = turn["location"]
        self.assertIsInstance(loc, dict)
        send._SEND_LOG.clear()
        rec = _Recorder()
        with mock.patch.object(send, "_post", side_effect=rec), \
                mock.patch.object(send, "_read_token", return_value="t"), \
                mock.patch.object(send, "dice_request", return_value=0):
            rc = send.send_turn(turn)
        self.assertEqual(rc, 0)
        stats = rec.stats()
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["place"], {"name": loc["name"], "look": loc["look"]})


if __name__ == "__main__":
    unittest.main()
