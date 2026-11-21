"""Reviewer findings on the battle-map branch (battle-map spec D9, D10, D13,
D15, D18; AC7, AC9, AC12, AC15).

1. A creature that moves into view and attacks in the same block must get
   its whole attack animation: attacks are planned from what is visible
   after that block's moves (fx.js passes the moved tokens to isVisible).
2. A doorway only diagonally next to a lit square, across a wall corner,
   stays dark (no peeking through corners).
5. Turn-level moves ride the first *text* block, never an award block.
6. With no battle map, attacks and spell areas are dropped quietly instead
   of rejecting the turn (moves still need a map).
7. tracker.py announcements about a creature hidden on the map are not sent.
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

from tests.test_battlemap import CELLAR, _fresh, _import_app, bm, paths  # noqa: F401

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "skills" / "dnd" / "display"
SCRIPTS = ROOT / "skills" / "dnd" / "scripts"
NODE = shutil.which("node")


def _node(tc, script):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
        path = f.name
    r = subprocess.run([NODE, path], capture_output=True, encoding="utf-8", timeout=60)
    tc.assertEqual(r.returncode, 0, r.stderr)
    return json.loads(r.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "node is not installed")
class TestMoveThenAttack(unittest.TestCase):

    def test_is_visible_sees_the_moved_tokens(self):
        r = _node(self, """
globalThis.window = { TV: {} };
window.TV.sprites = require(%s);
const fxm = require(%s);
const seenWith = [];
const fx = fxm.makeFx({ now: () => 0, setTimeout: () => 0, clearTimeout: () => {},
  isVisible: (name, toks) => { seenWith.push(JSON.stringify((toks || []).map((t) => [t.name, t.x]))); return true; } });
fx.setTarget({ grid: { w: 9, h: 3 }, cells: ['.........', '.........', '.........'],
  tokens: [{ name: 'Havoc', x: 0, y: 1, kind: 'pc' }, { name: 'Goblin', x: 8, y: 1, kind: 'npc' }] });
fx.arrive(2, { moves: [{ name: 'Goblin', x: 2, y: 1, path: [[8, 1], [2, 1]] }],
               attacks: [{ from: 'Goblin', to: 'Havoc', kind: 'ranged', hit: false }] });
fx.start(2);
const a = fx.shownState().attacks;
console.log(JSON.stringify({ seen: seenWith, from: a[0] && a[0].from, sprite: a[0] && a[0].sprite }));
""" % (json.dumps(str(DISPLAY / "static/map/sprites.js")), json.dumps(str(DISPLAY / "static/map/fx.js"))))
        self.assertTrue(r["seen"], "isVisible was never asked")
        self.assertIn('["Goblin",2]', r["seen"][0], "visibility must be judged after the block's move")
        self.assertEqual(r["from"], [2, 1])
        self.assertEqual(r["sprite"], "arrow")


@unittest.skipUnless(NODE, "node is not installed")
class TestDoorCorner(unittest.TestCase):

    def test_door_across_a_wall_corner_stays_dark(self):
        r = _node(self, """
const L = require(%s);
const cells = ['#####', '#...#', '#...#', '####D', '    .'];
const v = L.visibleFrom(cells, 1, 1);
console.log(JSON.stringify([v.has('4,3'), v.has('3,3')]));
""" % json.dumps(str(DISPLAY / "static/map/los.js")))
        self.assertEqual(r, [False, True])


class TestTurnEffectsEdges(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()
        sys.argv = ["send.py"]
        import send
        cls.send = send

    def setUp(self):
        self.posts = []
        self._b = mock.patch.object(self.app, "_broadcast")
        self._b.start()

    def tearDown(self):
        self._b.stop()

    def _fake_post(self, url, data, token):
        self.posts.append((urllib.parse.urlparse(url).path, json.loads(data)))
        r = self.client.post(urllib.parse.urlparse(url).path, data=data, content_type="application/json")
        return r.status_code < 300

    def _camp(self, name, with_map=True):
        camp = paths.campaigns_dir() / name
        camp.mkdir(parents=True, exist_ok=True)
        if with_map:
            bm.save(camp, _fresh())
        elif bm.map_path(camp).exists():
            bm.map_path(camp).unlink()
        return camp

    def test_turn_level_moves_skip_an_award_block(self):
        self._camp("bm-review-award")
        turn = {"campaign": "bm-review-award",
                "blocks": [{"inspiration_award": "Havoc", "reason": "bold"},
                           {"text": "The rat darts along the wall."}],
                "moves": [{"name": "Giant Rat", "x": 8, "y": 1}]}
        with mock.patch.object(self.send, "_post", side_effect=self._fake_post):
            self.assertEqual(self.send.send_turn(turn), 0)
        chunks = [p for path, p in self.posts if path == "/chunk"]
        self.assertNotIn("map_fx", chunks[0])
        self.assertEqual(chunks[1].get("map_fx", {}).get("moves", [{}])[0].get("name"), "Giant Rat")

    def test_attacks_and_areas_without_a_map_are_dropped(self):
        self._camp("bm-review-nomap", with_map=False)
        turn = {"campaign": "bm-review-nomap",
                "blocks": [{"text": "Havoc swings.", "attack": {"from": "Havoc", "to": "Orc", "kind": "melee", "hit": True}},
                           {"text": "Fire blooms.", "area": {"shape": "sphere", "size": 20, "at": [3, 3]}}]}
        with mock.patch.object(self.send, "_post", side_effect=self._fake_post):
            self.assertEqual(self.send.send_turn(turn), 0)
        chunks = [p for path, p in self.posts if path == "/chunk"]
        self.assertEqual(len(chunks), 2)
        self.assertFalse(any("map_fx" in c for c in chunks))

    def test_moves_without_a_map_still_refused(self):
        self._camp("bm-review-nomap2", with_map=False)
        turn = {"campaign": "bm-review-nomap2",
                "blocks": [{"text": "Havoc runs.", "moves": [{"name": "Havoc", "x": 2, "y": 2}]}]}
        with mock.patch.object(self.send, "_post") as p:
            self.assertEqual(self.send.send_turn(turn), 2)
        p.assert_not_called()


class TestHiddenAnnounce(unittest.TestCase):

    def test_announce_about_hidden_creature_not_sent(self):
        sys.path.insert(0, str(SCRIPTS))
        import tracker
        camp = paths.campaigns_dir() / "bm-review-announce"
        camp.mkdir(parents=True, exist_ok=True)
        m = bm.new_map(6, 3)
        bm.place(m, "Goblin 2", 1, 1, hidden=True)
        bm.place(m, "Orc", 3, 1)
        bm.save(camp, m)
        calls = []
        with mock.patch.object(tracker, "_CAMPAIGN", "bm-review-announce", create=True), \
                mock.patch.object(tracker.subprocess, "Popen", side_effect=lambda *a, **k: calls.append(a[0]) or mock.MagicMock()):
            tracker._send_announce("Goblin 2 → Invisible")
            tracker._send_announce("Orc → Prone")
        self.assertEqual(len(calls), 1, "only the Orc's announcement goes out")


if __name__ == "__main__":
    unittest.main()
