"""Battle map S6: effects ride their narration blocks in the turn call.

Spec docs/specs/2026-11-17-battle-map.md (AC4 payload, AC5, AC9, AC11 log side,
D12, D13, D14, D15) and plan step S6 in the .plan.md.

- Any text block may carry "moves", "area", "attack" / "attacks". A turn-level
  "moves" belongs to the first block.
- send.py applies every move in block order and validates every area and
  attack before any _post; one failure rejects the whole call (exit 2) and
  leaves battlemap.json untouched.
- The first chunk of each block carries map_fx; the final stats.map goes in
  the one /stats post after the chunks.
- Hidden tokens never reach map_fx; a monster's hp becomes hp_state.
- The server forwards a cleaned map_fx on the /chunk broadcast and never
  writes it to _text_log.

Area geometry assumption (the plan says "a square is included when its centre
lies inside the shape"): a sphere's `at` is a square and the sphere is centred
on that square's centre; squares exactly on the boundary may go either way.
"""
import contextlib
import io
import json
import math
import sys
import unittest
import urllib.parse
from unittest import mock

from tests.test_battlemap import CELLAR, _fresh, _import_app, bm, paths  # noqa: F401

CAMP = "bm-fx"


def _sq(seq):
    """Normalise a list of [x, y] pairs to a set of int tuples."""
    return {(int(p[0]), int(p[1])) for p in seq}


def _disk(cx, cy, r_sq, w, h):
    """Squares whose centre is within r_sq squares of square (cx, cy)'s centre.
    Returns (strictly inside, inside or on the boundary)."""
    strict, closed = set(), set()
    for y in range(h):
        for x in range(w):
            d = math.hypot(x - cx, y - cy)
            if d < r_sq - 1e-9:
                strict.add((x, y))
            if d <= r_sq + 1e-9:
                closed.add((x, y))
    return strict, closed


class _Base(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.app._token_ok = lambda: True
        cls.client = cls.app.app.test_client()
        sys.argv = ["send.py"]
        import send
        cls.send = send
        cls.camp = paths.campaigns_dir() / CAMP
        cls.camp.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.save_map(_fresh())
        self.app._current_stats.pop("map", None)
        self.sent = []      # server broadcasts
        self.posts = []     # (path, payload) as send.py posted them
        self._p = mock.patch.object(self.app, "_broadcast", side_effect=self.sent.append)
        self._p.start()

    def tearDown(self):
        self._p.stop()

    def save_map(self, m):
        bm.save(self.camp, m)

    def map_bytes(self):
        return bm.map_path(self.camp).read_bytes()

    def _fake_post(self, url, data, token):
        path = urllib.parse.urlparse(url).path
        self.posts.append((path, json.loads(data.decode("utf-8"))))
        r = self.client.post(path, data=data, content_type="application/json")
        return r.status_code < 300

    def run_turn(self, turn):
        turn = dict(turn)
        turn.setdefault("campaign", CAMP)
        err = io.StringIO()
        with mock.patch.object(self.send, "_post", side_effect=self._fake_post), \
                contextlib.redirect_stderr(err):
            rc = self.send.send_turn(turn)
        return rc, err.getvalue()

    def chunks(self):
        return [p for path, p in self.posts if path == "/chunk"]

    def stats_posts(self):
        return [p for path, p in self.posts if path == "/stats"]

    def assert_rejected(self, turn, *needles):
        """The whole call is refused before any _post and the map file is untouched."""
        turn = dict(turn)
        turn.setdefault("campaign", CAMP)
        before = self.map_bytes()
        err = io.StringIO()
        with mock.patch.object(self.send, "_post") as p, contextlib.redirect_stderr(err):
            rc = self.send.send_turn(turn)
        self.assertEqual(rc, 2, err.getvalue())
        p.assert_not_called()
        self.assertEqual(self.map_bytes(), before, "battlemap.json changed on a rejected call")
        for n in needles:
            self.assertIn(n.lower(), err.getvalue().lower())
        return err.getvalue()


# ── send.py: placement of map_fx on blocks ────────────────────────────────────

class TestBlockPlacement(_Base):

    def test_move_on_block_two_rides_its_first_chunk_with_path(self):   # AC4, D12
        rc, err = self.run_turn({"blocks": [
            {"text": "The torchlight flickers."},
            {"text": "The rat darts along the wall.",
             "moves": [{"name": "Giant Rat", "x": 8, "y": 1}]}]})
        self.assertEqual(rc, 0, err)
        ch = self.chunks()
        self.assertEqual(len(ch), 2)
        self.assertNotIn("map_fx", ch[0])
        fx = ch[1].get("map_fx")
        self.assertIsInstance(fx, dict, "block 2's chunk has no map_fx")
        mv = fx["moves"][0]
        self.assertEqual((mv["name"], mv["x"], mv["y"]), ("Giant Rat", 8, 1))
        path = [tuple(p) for p in mv["path"]]
        self.assertEqual(path[0], (11, 1))
        self.assertEqual(path[-1], (8, 1))

    def test_final_stats_map_has_end_positions_and_is_posted_once_last(self):   # AC4
        rc, err = self.run_turn({"blocks": [
            {"text": "Havoc edges forward.", "moves": [{"name": "Havoc", "x": 3, "y": 1}]},
            {"text": "The rat darts along the wall.",
             "moves": [{"name": "Giant Rat", "x": 8, "y": 1}]}]})
        self.assertEqual(rc, 0, err)
        stats = self.stats_posts()
        self.assertEqual(len(stats), 1, "stats.map should go in exactly one /stats post")
        self.assertEqual(self.posts[-1][0], "/stats", "the /stats post comes after the chunks")
        toks = {t["name"]: (t["x"], t["y"]) for t in stats[0]["map"]["tokens"]}
        self.assertEqual(toks["Havoc"], (3, 1))
        self.assertEqual(toks["Giant Rat"], (8, 1))
        saved = bm.load(self.camp)["tokens"]
        self.assertEqual((saved["Havoc"]["x"], saved["Giant Rat"]["x"]), (3, 8))
        # both moves went out on their own blocks
        self.assertEqual([c.get("map_fx", {}).get("moves", [{}])[0].get("name") for c in self.chunks()],
                         ["Havoc", "Giant Rat"])

    def test_long_block_carries_map_fx_on_first_chunk_only(self):   # AC4
        para = ("The rat scrabbles over stone. " * 70).strip()
        text = "\n\n".join([para, para, para])
        n = len(self.send._split_paragraphs(text))
        self.assertGreater(n, 1, "fixture text should split")
        rc, err = self.run_turn({"blocks": [
            {"text": text, "moves": [{"name": "Giant Rat", "x": 8, "y": 1}]}]})
        self.assertEqual(rc, 0, err)
        ch = self.chunks()
        self.assertEqual(len(ch), n)
        self.assertIn("map_fx", ch[0])
        for c in ch[1:]:
            self.assertNotIn("map_fx", c)

    def test_turn_level_moves_attach_to_first_block(self):   # D12 (today's form)
        rc, err = self.run_turn({
            "blocks": [{"text": "The rat darts along the wall."}, {"text": "It hisses."}],
            "moves": [{"name": "Giant Rat", "x": 8, "y": 1}]})
        self.assertEqual(rc, 0, err)
        ch = self.chunks()
        self.assertEqual(ch[0].get("map_fx", {}).get("moves", [{}])[0].get("name"), "Giant Rat")
        self.assertNotIn("map_fx", ch[1])

    def test_monster_hp_becomes_hp_state_without_numbers(self):   # AC3 via map_fx
        rc, err = self.run_turn({"blocks": [
            {"text": "The bloodied rat retreats.",
             "moves": [{"name": "Giant Rat", "x": 8, "y": 1, "hp": "3/7"}]}]})
        self.assertEqual(rc, 0, err)
        fx = self.chunks()[0].get("map_fx")
        self.assertIsInstance(fx, dict)
        self.assertEqual(fx["moves"][0].get("hp_state"), "bloodied")
        self.assertNotIn("hp", fx["moves"][0])
        blob = json.dumps(fx)
        self.assertNotIn('"hp"', blob)
        self.assertNotIn("3/7", blob)

    def test_attacks_validated_and_forwarded(self):   # D15 (milestone 1 forwards)
        bm_m = _fresh()
        bm.place(bm_m, "Havoc", 10, 1, kind="pc")
        self.save_map(bm_m)
        rc, err = self.run_turn({"blocks": [
            {"text": "Havoc slashes the rat.",
             "attack": {"from": "Havoc", "to": "Giant Rat", "kind": "melee", "hit": True}},
            {"text": "Anony looses two arrows.",
             "attacks": [{"from": "Anony", "to": "Giant Rat 2", "kind": "ranged", "hit": False},
                         {"from": "Anony", "to": "Giant Rat 2", "kind": "ranged", "hit": True}]}]})
        self.assertEqual(rc, 0, err)
        ch = self.chunks()
        a1 = ch[0].get("map_fx", {}).get("attacks")
        self.assertEqual(a1, [{"from": "Havoc", "to": "Giant Rat", "kind": "melee", "hit": True}])
        a2 = ch[1].get("map_fx", {}).get("attacks")
        self.assertEqual([(a["kind"], a["hit"]) for a in a2 or []],
                         [("ranged", False), ("ranged", True)])

    def test_area_rides_its_block(self):   # D14 data
        rc, err = self.run_turn({"blocks": [
            {"text": "Anony speaks the word."},
            {"text": "Fire blooms in the far room.",
             "area": {"shape": "sphere", "size": 10, "at": [8, 3], "label": "Fireball"}}]})
        self.assertEqual(rc, 0, err)
        ch = self.chunks()
        self.assertNotIn("map_fx", ch[0])
        area = ch[1].get("map_fx", {}).get("area")
        self.assertIsInstance(area, dict, "block 2 has no map_fx.area")
        self.assertEqual(area.get("label"), "Fireball")
        self.assertEqual(area.get("shape"), "sphere")
        sq = _sq(area["squares"])
        self.assertIn((8, 3), sq)
        m = bm.load(self.camp)
        for x, y in sq:
            self.assertTrue(0 <= x < 14 and 0 <= y < 6)
            self.assertNotEqual(m["cells"][y][x], "#", f"wall square {(x, y)} in the area")

    def test_no_map_no_effects_sends_as_before(self):   # no regression
        camp = paths.campaigns_dir() / "bm-fx-nomap"
        camp.mkdir(parents=True, exist_ok=True)
        rc, err = self.run_turn({"campaign": "bm-fx-nomap",
                                 "blocks": [{"text": "Rain on the roof."}, {"npc": "Mira", "text": "Inside!"}],
                                 "turn": "Anony"})
        self.assertEqual(rc, 0, err)
        ch = self.chunks()
        self.assertEqual([c["text"] for c in ch], ["Rain on the roof.", "Inside!"])
        for c in ch:
            self.assertNotIn("map_fx", c)
        stats = self.stats_posts()
        self.assertEqual(len(stats), 1)
        self.assertNotIn("map", stats[0])
        self.assertFalse((camp / "battlemap.json").exists())

    def test_no_campaign_key_no_effects_sends_as_before(self):
        err = io.StringIO()
        with mock.patch.object(self.send, "_post", side_effect=self._fake_post), \
                contextlib.redirect_stderr(err):
            rc = self.send.send_turn({"blocks": [{"text": "Quiet."}]})
        self.assertEqual(rc, 0, err.getvalue())
        self.assertEqual([c["text"] for c in self.chunks()], ["Quiet."])
        self.assertNotIn("map_fx", self.chunks()[0])


# ── send.py: hidden tokens ────────────────────────────────────────────────────

class TestHidden(_Base):

    def setUp(self):
        super().setUp()
        m = _fresh()
        bm.set_hidden(m, "Giant Rat 2", True)
        self.save_map(m)

    def test_hidden_move_absent_from_map_fx(self):   # AC9
        rc, err = self.run_turn({"blocks": [
            {"text": "Something shifts in the dark, and the rat darts.",
             "moves": [{"name": "Giant Rat 2", "x": 12, "y": 3},
                       {"name": "Giant Rat", "x": 8, "y": 1}]}]})
        self.assertEqual(rc, 0, err)
        fx = self.chunks()[0].get("map_fx")
        self.assertIsInstance(fx, dict, "the visible rat's move should still be in map_fx")
        self.assertEqual([mv["name"] for mv in fx["moves"]], ["Giant Rat"])
        self.assertNotIn("Giant Rat 2", json.dumps(self.posts))
        self.assertEqual(bm.load(self.camp)["tokens"]["Giant Rat 2"]["y"], 3)   # still applied

    def test_attack_from_hidden_attacker_loses_from(self):   # AC9
        rc, err = self.run_turn({"blocks": [
            {"text": "Teeth flash out of nowhere.",
             "attack": {"from": "Giant Rat 2", "to": "Anony", "kind": "melee", "hit": True}}]})
        self.assertEqual(rc, 0, err)
        atks = self.chunks()[0].get("map_fx", {}).get("attacks")
        self.assertEqual(atks, [{"to": "Anony", "kind": "melee", "hit": True}])

    def test_attack_on_hidden_target_dropped(self):   # AC9
        rc, err = self.run_turn({"blocks": [
            {"text": "Anony fires into the shadows.",
             "moves": [{"name": "Giant Rat", "x": 8, "y": 1}],
             "attack": {"from": "Anony", "to": "Giant Rat 2", "kind": "ranged", "hit": False}}]})
        self.assertEqual(rc, 0, err)
        fx = self.chunks()[0].get("map_fx")
        self.assertIsInstance(fx, dict, "the rat's move should still be in map_fx")
        self.assertFalse(fx.get("attacks"))
        self.assertNotIn("Giant Rat 2", json.dumps(self.posts))


# ── send.py: rejection (AC5, D13) ─────────────────────────────────────────────

class TestRejection(_Base):

    def _two(self, second):
        return {"blocks": [
            {"text": "The rat darts along the wall.", "moves": [{"name": "Giant Rat", "x": 8, "y": 1}]},
            dict({"text": "Then something else happens."}, **second)]}

    def test_wall_move_in_block_two(self):
        self.assert_rejected(self._two({"moves": [{"name": "Havoc", "x": 5, "y": 1}]}), "Havoc")

    def test_beyond_speed_in_block_two(self):
        err = self.assert_rejected(self._two({"moves": [{"name": "Havoc", "x": 9, "y": 1}]}), "Havoc")
        self.assertIn("ft", err)

    def test_moves_apply_in_block_order(self):
        # 15 + 15 ft in blocks 1-2, then 15 ft more in block 3: 45 ft > 30 ft speed
        self.assert_rejected({"blocks": [
            {"text": "Havoc steps east.", "moves": [{"name": "Havoc", "x": 4, "y": 1}]},
            {"text": "And keeps going.", "moves": [{"name": "Havoc", "x": 4, "y": 4}]},
            {"text": "And on.", "moves": [{"name": "Havoc", "x": 1, "y": 4}]}]}, "Havoc")

    def test_unknown_shape(self):
        self.assert_rejected(self._two({"area": {"shape": "donut", "size": 10, "at": [8, 3]}}))

    def test_area_off_map(self):
        self.assert_rejected(self._two({"area": {"shape": "sphere", "size": 10, "at": [50, 50]}}))

    def test_area_unknown_from_token(self):
        self.assert_rejected(self._two(
            {"area": {"shape": "cone", "size": 15, "from": "Nobody", "to": [8, 3]}}), "Nobody")

    def test_area_size_out_of_range(self):
        for size in (0, 4, 155, "big"):
            with self.subTest(size=size):
                self.assert_rejected(self._two({"area": {"shape": "sphere", "size": size, "at": [8, 3]}}))

    def test_attack_unknown_token(self):
        self.assert_rejected(self._two(
            {"attack": {"from": "Havoc", "to": "Owlbear", "kind": "melee", "hit": True}}), "Owlbear")
        self.assert_rejected(self._two(
            {"attacks": [{"from": "Ghost", "to": "Havoc", "kind": "spell", "hit": True}]}), "Ghost")

    def test_attack_bad_kind(self):
        self.assert_rejected(self._two(
            {"attack": {"from": "Havoc", "to": "Giant Rat", "kind": "psychic", "hit": True}}))

    def test_attack_non_bool_hit(self):
        for hit in ("yes", 1, None):
            with self.subTest(hit=hit):
                self.assert_rejected(self._two(
                    {"attack": {"from": "Havoc", "to": "Giant Rat", "kind": "melee", "hit": hit}}))

    def test_effect_keys_on_award_block(self):
        self.assert_rejected({"blocks": [
            {"text": "The rat falls."},
            {"xp_award": {"names": ["Havoc"], "xp": 25, "reason": "rat"},
             "moves": [{"name": "Giant Rat", "x": 8, "y": 1}]}]})
        self.assert_rejected({"blocks": [
            {"inspiration_award": "Anony", "reason": "great line",
             "area": {"shape": "sphere", "size": 10, "at": [8, 3]}}]})


# ── battlemap.area_squares (D14) ──────────────────────────────────────────────

class TestAreaSquares(unittest.TestCase):

    def _fn(self):
        fn = getattr(bm, "area_squares", None)
        if fn is None:
            self.fail("battlemap.area_squares is missing")
        return fn

    def test_sphere_20ft_open_grid(self):
        m = bm.new_map(12, 12)
        got = _sq(self._fn()(m, {"shape": "sphere", "size": 20, "at": [6, 6]}))
        strict, closed = _disk(6, 6, 4, 12, 12)
        self.assertEqual(len(strict), 45)
        self.assertTrue(strict <= got, f"missing {sorted(strict - got)}")
        self.assertTrue(got <= closed, f"extra {sorted(got - closed)}")

    def test_cylinder_is_sphere(self):
        m = bm.new_map(12, 12)
        fn = self._fn()
        self.assertEqual(_sq(fn(m, {"shape": "cylinder", "size": 20, "at": [6, 6]})),
                         _sq(fn(m, {"shape": "sphere", "size": 20, "at": [6, 6]})))

    def test_sphere_excludes_walls_and_off_map(self):
        cells = ["." * 12 for _ in range(12)]
        cells = [row[:8] + "#" + row[9:] for row in cells]          # wall column x = 8
        m = bm.new_map(12, 12, cells)
        got = _sq(self._fn()(m, {"shape": "sphere", "size": 20, "at": [6, 6]}))
        self.assertFalse({p for p in got if p[0] == 8}, "wall squares included")
        strict, closed = _disk(6, 6, 4, 12, 12)
        strict = {p for p in strict if p[0] != 8}
        self.assertTrue(strict <= got, f"missing {sorted(strict - got)}")
        # corner sphere: nothing off the map
        corner = _sq(self._fn()(m, {"shape": "sphere", "size": 20, "at": [0, 0]}))
        self.assertIn((0, 0), corner)
        self.assertTrue(all(0 <= x < 12 and 0 <= y < 12 for x, y in corner))

    def test_cube(self):
        m = bm.new_map(12, 12)
        got = _sq(self._fn()(m, {"shape": "cube", "size": 15, "at": [2, 3]}))
        self.assertEqual(got, {(x, y) for x in range(2, 5) for y in range(3, 6)})

    def test_cone_from_token_toward_square(self):
        m = bm.new_map(12, 12)
        bm.place(m, "Mage", 2, 6, kind="pc")
        got = _sq(self._fn()(m, {"shape": "cone", "size": 30, "from": "Mage", "to": [11, 6]}))
        for x in range(3, 9):                       # the axis, 6 squares
            self.assertIn((x, 6), got)
        self.assertIn((6, 5), got)                  # 4 out, 1 off axis: inside (width = length)
        self.assertIn((6, 7), got)
        self.assertNotIn((6, 3), got)               # 4 out, 3 off axis: outside
        self.assertNotIn((3, 8), got)               # 1 out, 2 off axis: outside
        self.assertFalse({p for p in got if p[0] < 2}, "squares behind the caster")
        self.assertFalse({p for p in got if p[0] > 9}, "squares past 30 ft")

    def test_cone_from_square(self):
        m = bm.new_map(12, 12)
        got = _sq(self._fn()(m, {"shape": "cone", "size": 15, "from": [2, 6], "to": [11, 6]}))
        self.assertIn((4, 6), got)
        self.assertFalse({p for p in got if p[0] > 6})

    def test_line_default_width_5(self):
        m = bm.new_map(12, 12)
        bm.place(m, "Mage", 2, 6, kind="pc")
        got = _sq(self._fn()(m, {"shape": "line", "size": 30, "from": "Mage", "to": [11, 6]}))
        for x in range(3, 8):
            self.assertIn((x, 6), got)
        self.assertFalse({p for p in got if p[1] != 6}, "a 5-ft line is one square wide")
        self.assertNotIn((9, 6), got)
        self.assertNotIn((10, 6), got)

    def test_line_wider(self):
        m = bm.new_map(12, 12)
        got = _sq(self._fn()(m, {"shape": "line", "size": 30, "width": 15, "from": [2, 6], "to": [11, 6]}))
        for y in (5, 6, 7):
            self.assertIn((4, y), got)
        self.assertFalse({p for p in got if p[1] in (3, 9)})


# ── Server: /chunk forwards a cleaned map_fx, never logs it (AC11 log side) ────

class TestServerChunk(_Base):

    def _junk_fx(self):
        long_name = "N" * 60
        return {
            "moves": ([{"name": "Giant Rat", "x": 8, "y": 1,
                        "path": [[11 - i % 4, 1] for i in range(80)],
                        "hp_state": "bloodied", "hp": [3, 7], "evil": "<script>"},
                       {"name": "Bad", "x": "3", "y": 1},
                       {"name": long_name, "x": 2, "y": 2}]
                      + [{"name": f"T{i}", "x": i % 10, "y": 1} for i in range(25)]),
            "area": {"squares": [[i % 14, i % 6] for i in range(700)] + [["a", 1]],
                     "label": "Fireball" * 10, "shape": "sphere", "junk": 1},
            "attacks": [{"from": "Havoc", "to": "Giant Rat", "kind": "melee", "hit": True, "x": 1}] * 10,
            "script": "alert(1)",
        }

    def _chunk_broadcasts(self):
        return [b for b in self.sent if isinstance(b, dict) and b.get("text") == "Fire and teeth."]

    def test_broadcast_carries_cleaned_map_fx(self):
        r = self.client.post("/chunk", json={"text": "Fire and teeth.", "map_fx": self._junk_fx()})
        self.assertLess(r.status_code, 300)
        b = self._chunk_broadcasts()
        self.assertEqual(len(b), 1)
        fx = b[0].get("map_fx")
        self.assertIsInstance(fx, dict, "the /chunk broadcast has no map_fx")
        self.assertLessEqual(set(fx), {"moves", "area", "attacks"})
        moves = fx["moves"]
        self.assertLessEqual(len(moves), 20)
        for mv in moves:
            self.assertLessEqual(set(mv), {"name", "x", "y", "path", "hp_state"})
            self.assertIsInstance(mv["x"], int)
            self.assertIsInstance(mv["y"], int)
            self.assertLessEqual(len(mv["name"]), 40)
            self.assertLessEqual(len(mv.get("path") or []), 60)
            for p in mv.get("path") or []:
                self.assertTrue(all(isinstance(v, int) for v in p))
        self.assertNotIn("Bad", [mv["name"] for mv in moves], "non-int coords kept")
        self.assertEqual(moves[0]["name"], "Giant Rat")
        self.assertEqual(moves[0].get("hp_state"), "bloodied")
        area = fx["area"]
        self.assertLessEqual(set(area), {"squares", "label", "shape"})
        self.assertLessEqual(len(area["squares"]), 600)
        self.assertTrue(all(isinstance(v, int) for p in area["squares"] for v in p))
        self.assertLessEqual(len(area["label"]), 40)
        self.assertLessEqual(len(fx["attacks"]), 8)
        for a in fx["attacks"]:
            self.assertLessEqual(set(a), {"from", "to", "kind", "hit"})

    def test_map_fx_not_in_text_log(self):   # AC11: a replay never carries effects
        self.client.post("/chunk", json={"text": "Fire and teeth.", "map_fx": {
            "moves": [{"name": "Giant Rat", "x": 8, "y": 1}]}})
        b = self._chunk_broadcasts()
        self.assertTrue(b and "map_fx" in b[0], "the /chunk broadcast has no map_fx")
        with self.app._text_log_lock:
            entry = list(self.app._text_log)[-1]
        self.assertEqual(entry.get("text"), "Fire and teeth.")
        self.assertNotIn("map_fx", entry)
        self.assertEqual(entry.get("seq"), b[0].get("seq"))

    def test_clean_map_fx_function(self):
        fn = getattr(self.app, "_clean_map_fx", None)
        if fn is None:
            self.fail("dnd-display-app._clean_map_fx is missing")
        out = fn(self._junk_fx())
        self.assertNotIn("script", out)
        self.assertLessEqual(len(out["moves"]), 20)
        self.assertIsNone(fn("not a dict") or None)

    def test_chunk_without_map_fx_unchanged(self):
        self.client.post("/chunk", json={"text": "Fire and teeth."})
        b = self._chunk_broadcasts()
        self.assertEqual(len(b), 1)
        self.assertNotIn("map_fx", b[0])

    def test_turn_end_to_end_broadcast(self):   # send.py → /chunk → broadcast
        rc, err = self.run_turn({"blocks": [
            {"text": "A pause."},
            {"text": "The rat darts along the wall.", "moves": [{"name": "Giant Rat", "x": 8, "y": 1}]}]})
        self.assertEqual(rc, 0, err)
        texts = [b for b in self.sent if isinstance(b, dict) and "text" in b]
        self.assertNotIn("map_fx", texts[0])
        self.assertEqual(texts[1].get("map_fx", {}).get("moves", [{}])[0].get("name"), "Giant Rat")
        with self.app._text_log_lock:
            log = list(self.app._text_log)[-2:]
        self.assertFalse(any("map_fx" in e for e in log))


if __name__ == "__main__":
    unittest.main()
