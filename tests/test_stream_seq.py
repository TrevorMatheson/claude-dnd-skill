"""Stream sequence numbers and the boot epoch (display foundation S2, AC12 server side).

WHY
===
When the TV loses the server and reconnects, `/stream` re-sends the last 200
feed entries. The old page appended them again, so a dropped connection
duplicated the feed. The new TV de-duplicates by `seq` and starts over when the
server's boot `epoch` changes. For that to work the server must:

  * stamp every feed entry with an integer `seq`, the same number in the live
    broadcast and in `_text_log`, strictly increasing;
  * number old `text_log.json` entries that have none when the log is loaded,
    and carry on above the highest number;
  * open every `/stream` with `{"hello": {"epoch": ...}}`, before the scene and
    the replay, and replay entries that carry their `seq`;
  * never reset the counter, not even on `/rewind`.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(REPO / "skills" / "dnd" / "scripts"))


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_seq", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError as exc:                    # flask/deps absent
        raise unittest.SkipTest(f"display app not importable: {exc}")
    return mod


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


class _AppCase(unittest.TestCase):
    """Shared app instance; broadcasts captured, nothing written to disk."""

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()

    def setUp(self):
        a = self.app
        self.sent = []
        self._p = [mock.patch.object(a, "_broadcast", side_effect=self.sent.append),
                   mock.patch.object(a, "_persist_log"),
                   mock.patch.object(a, "_persist_tail")]
        for p in self._p:
            p.start()
        a._text_log.clear()
        a._tail_buffer.clear()

    def tearDown(self):
        for p in self._p:
            p.stop()
        self.app._text_log.clear()
        self.app._tail_buffer.clear()

    def post(self, body: dict) -> dict:
        """POST /chunk and return the feed payload it broadcast."""
        before = len(self.sent)
        r = self.client.post("/chunk", json=body)
        self.assertEqual(r.status_code, 204)
        feed = [m for m in self.sent[before:] if "text" in m]
        self.assertEqual(len(feed), 1, f"expected one feed broadcast, got {self.sent[before:]}")
        return feed[0]

    def all_seqs(self) -> list:
        return [e.get("seq") for e in self.app._text_log]


class ChunkSeq(_AppCase):

    def test_two_chunks_get_increasing_seq_in_payload_and_log(self):
        p1 = self.post({"text": "The door creaks open.", "npc": "Narrator"})
        p2 = self.post({"text": "Havoc steps inside.", "action": "Havoc"})
        for p in (p1, p2):
            self.assertIn("seq", p, f"broadcast payload has no seq: {p}")
            self.assertTrue(_is_int(p["seq"]), f"seq is not an integer: {p['seq']!r}")
        self.assertGreater(p2["seq"], p1["seq"])
        log = list(self.app._text_log)
        self.assertEqual(len(log), 2)
        self.assertEqual([e.get("seq") for e in log], [p1["seq"], p2["seq"]],
                         "the _text_log entries do not carry the same seq as the broadcast")

    def test_dm_narration_is_stamped_too(self):
        p1 = self.post({"text": "Rain hammers the shutters of the inn."})
        p2 = self.post({"text": "A stranger walks in out of the storm."})
        self.assertTrue(_is_int(p1.get("seq")) and _is_int(p2.get("seq")), (p1, p2))
        self.assertGreater(p2["seq"], p1["seq"])
        self.assertEqual(self.all_seqs(), [p1["seq"], p2["seq"]])

    def test_award_paths_are_stamped(self):
        bodies = [
            {"text": "x", "inspiration_award": "Havoc", "reason": "a fine speech"},
            {"text": "x", "milestone_award": "Anony", "label": "Milestone"},
            {"text": "x", "xp_award": {"summary": "+50 XP each", "each": 50}},
            {"text": "Plain narration after the awards."},
        ]
        seqs = []
        for b in bodies:
            p = self.post(b)
            self.assertTrue(_is_int(p.get("seq")), f"no integer seq on {b}: {p}")
            seqs.append(p["seq"])
        self.assertEqual(seqs, sorted(set(seqs)), f"seq not strictly increasing: {seqs}")
        self.assertEqual(self.all_seqs(), seqs)


class LoadLogNumbers(_AppCase):
    """`_load_log()` runs at import and reads LOG_FILE; drive it the same way."""

    def _load(self, entries) -> None:
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "text_log.json"
            f.write_text(json.dumps(entries), encoding="utf-8")
            with mock.patch.object(self.app, "LOG_FILE", str(f)):
                self.app._load_log()

    def test_seqless_entries_are_numbered_and_next_post_continues_above(self):
        self._load(["an old plain-string entry",
                    {"text": "Session one ends."},
                    {"text": "Havoc waves.", "player": "Havoc"}])
        seqs = self.all_seqs()
        self.assertEqual(len(seqs), 3)
        self.assertTrue(all(_is_int(s) for s in seqs), f"loaded entries not numbered: {seqs}")
        self.assertEqual(seqs, sorted(set(seqs)), f"loaded seqs not unique and increasing: {seqs}")
        p = self.post({"text": "Session two begins."})
        self.assertTrue(_is_int(p.get("seq")), p)
        self.assertGreater(p["seq"], max(seqs))

    def test_next_post_continues_above_the_highest_existing_seq(self):
        self._load([{"text": "a", "seq": 40}, {"text": "b"}, {"text": "c", "seq": 7}])
        seqs = self.all_seqs()
        self.assertTrue(all(_is_int(s) for s in seqs), seqs)
        self.assertEqual(len(set(seqs)), 3, f"loaded seqs not unique: {seqs}")
        p = self.post({"text": "after the reload"})
        self.assertGreater(p.get("seq", -1), max(seqs))


class StreamHello(_AppCase):

    def _first_events(self, n: int) -> list:
        """Open /stream and read the first n events already queued on connect."""
        resp = self.client.get("/stream")
        out = []
        buf = b""
        it = resp.response if hasattr(resp, "response") else resp.iter_encoded()
        try:
            for piece in it:
                buf += piece if isinstance(piece, bytes) else piece.encode("utf-8")
                while b"\n\n" in buf and len(out) < n:
                    frame, buf = buf.split(b"\n\n", 1)
                    line = frame.decode("utf-8")
                    if line.startswith("data: "):
                        out.append(json.loads(line[len("data: "):]))
                    else:
                        out.append({"_comment": line})
                if len(out) >= n:
                    break
        finally:
            resp.close()
        return out

    def test_first_item_is_hello_with_epoch_then_scene_and_replay_carry_seq(self):
        p1 = self.post({"text": "The bridge sways."})
        p2 = self.post({"text": "Anony grips the rope.", "action": "Anony"})
        events = self._first_events(3)
        first = events[0]
        self.assertEqual(list(first.keys()), ["hello"], f"first /stream item is not hello: {first}")
        epoch = first["hello"].get("epoch") if isinstance(first["hello"], dict) else None
        self.assertIsInstance(epoch, str, f"hello has no string epoch: {first}")
        self.assertTrue(epoch.strip(), "epoch is empty")
        self.assertIn("scene", events[1], f"scene does not follow hello: {events[:3]}")
        self.assertIn("replay_batch", events[2], f"replay does not follow the scene: {events[:3]}")
        replay = events[2]["replay_batch"]
        self.assertEqual([e.get("seq") for e in replay], [p1["seq"], p2["seq"]],
                         "replayed entries do not carry their seq")


class RewindKeepsCounting(_AppCase):

    def test_seq_after_rewind_is_above_every_seq_issued_before(self):
        a = self.app
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            camp = root / "camp"
            camp.mkdir()
            (camp / "state.md").write_text("before", encoding="utf-8")
            with mock.patch.object(a, "QUEUE_FILE", str(root / ".input_queue")), \
                    mock.patch.object(a, "_active_campaign_name", return_value="camp"), \
                    mock.patch.object(a, "_find_campaign", return_value=camp), \
                    mock.patch.object(a, "_persist_stats"):
                issued = [self.post({"text": "The story so far."}).get("seq")]
                a._rewind_mark([("Havoc", "I hug the owlbear")])
                issued.append(self.post({"text": "Havoc hugs the owlbear.", "action": "Havoc"}).get("seq"))
                issued.append(self.post({"text": "The owlbear is not amused."}).get("seq"))

                r = self.client.post("/rewind")
                self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
                self.assertEqual([e.get("text") for e in a._text_log], ["The story so far."],
                                 "rewind did not restore the feed")

                after = self.post({"text": "Havoc thinks better of it."})
        self.assertTrue(all(_is_int(x) for x in issued), f"seqs before the rewind: {issued}")
        self.assertTrue(_is_int(after.get("seq")), after)
        self.assertGreater(after["seq"], max(issued),
                           f"seq after rewind ({after['seq']}) reuses a number issued before it ({issued})")


if __name__ == "__main__":
    unittest.main()
