"""The TV feed, reconnect catch-up and phone-approval cards (display
foundation, plan step S6).

AC11 (feed parts) — /tv keeps D13 parity: blocks in order, a pending roll
       request shown as a roll card after the newest block, the rewind and
       clear re-renders, and (D24) the phone-approval cards.
AC12 — after a dropped connection the page catches up with no duplicated
       blocks: same epoch skips seqs it has seen, a new epoch rebuilds from
       the replay as history (not spoken).

The logic lives in DOM-free models under skills/dnd/display/static/tv/, each
file ending with `if (typeof module !== 'undefined') module.exports = ...`:

  stream.js   makeSeqFilter()   -> {hello(epoch), keep(entry)}
  feed.js     makeFeedModel()   -> {apply(payload), view()}
  devices.js  makeDeviceModel() -> {apply(payload), cards(), respond(id, approve)}

They are driven through node with the payloads the server really sends.
Where each shape comes from (skills/dnd/display/dnd-display-app.py):

  hello            {"hello": {"epoch": E}}                         :3579
  replay_batch     {"replay_batch": [log entries]} only if non-empty :3592-3593
  log entries      {"text", "seq", "npc"|"player"|"action"|"dice"|"tutor",
                    "voice"?, "_camp"?}                  :1716-1731, :2861-2870
  dice_pending     list of {"request_id","pending":[names],"label"}
                   (_dice_pending_snapshot :1313-1318); only requests with
                   someone still to roll. Sent on request (:2992), on each
                   roll (:2913), on cancel together with
                   {"dice_request_cancelled": rid} (:3064), and on connect
                   ONLY when non-empty (:3622-3624). A rewind sends
                   {"queue_status": [], "dice_pending": {}} -- an empty
                   DICT, not a list (:3500).
  dice_request     {"request_id","characters","character",
                    "onscreen_targets","spec","modifier","advantage",
                    "label","dc"} (:2996-3008); re-sent on connect for every
                   still-pending request (:3629-3644). The server sends
                   dice_pending BEFORE dice_request for a new request.
  rewind           {"rewind": {"inputs": [...], "log": [last 200 entries],
                    "stats": {...}}} (:3501)
  clear            {"clear": true} (:2702)
  device_request   {"device_request": {"id","ip"}} live (:341) and
                   replayed on connect for every pending device (:3656-3659)
  device_approved  {"device_approved": id} (:3129)
  device_denied    {"device_denied": id} (:3143)

The old page's handling these mirror: SSE dispatch index.html:9152-9300,
renderReplayBatch :5994 (replay rendered as isReplay, dimmed, not spoken),
_applyRewind :5636 (clear, then render r.log as replay), dice badge
_updateDicePendingBadge :9989 (non-array or empty -> hidden), device cards
_showDeviceRequest :8937 (no duplicate per id), _respondDevice :8983.

Skipped when node is not installed.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "skills" / "dnd" / "display"
TV_STATIC = DISPLAY / "static" / "tv"
STREAM_JS = TV_STATIC / "stream.js"
FEED_JS = TV_STATIC / "feed.js"
DEVICES_JS = TV_STATIC / "devices.js"
NODE = shutil.which("node")

# Each scenario runs in a fresh node process. `window` is provided because the
# files also attach to window.TV in the browser; feed.js may find the seq
# filter either through require('./stream.js') or window.TV.
_PRELUDE = """
globalThis.window = globalThis.window || { TV: {} };
const FILES = { stream: %(stream)s, feed: %(feed)s, devices: %(devices)s };
function need(key, name) {
  const mod = require(FILES[key]);
  if (!mod || typeof mod[name] !== 'function') {
    throw new Error(key + '.js does not export ' + name + '()');
  }
  return mod[name];
}
const makeSeqFilter = (...a) => need('stream', 'makeSeqFilter')(...a);
const makeFeedModel = (...a) => need('feed', 'makeFeedModel')(...a);
const makeDeviceModel = (...a) => need('devices', 'makeDeviceModel')(...a);
const out = (v) => console.log(JSON.stringify(v));
// Text payloads as /chunk and the dice roller send them, with their seq.
const T = (seq, text, extra) => Object.assign({ text: text || ('block ' + seq), seq: seq }, extra || {});
const seqs = (m) => m.view().blocks.map(b => b.seq);
"""


def _js_path(p: pathlib.Path) -> str:
    return json.dumps(str(p))


@unittest.skipUnless(NODE, "node is not installed")
class _NodeCase(unittest.TestCase):
    NEEDS: tuple = (STREAM_JS, FEED_JS)

    def run_js(self, body: str):
        missing = [p.name for p in self.NEEDS if not p.exists()]
        if missing:
            self.fail("not built yet: static/tv/" + ", static/tv/".join(missing))
        script = _PRELUDE % {"stream": _js_path(STREAM_JS), "feed": _js_path(FEED_JS),
                             "devices": _js_path(DEVICES_JS)} + body
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=30)
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        if r.returncode != 0:
            self.fail("node failed:\n" + (r.stderr or r.stdout)[-1500:])
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        self.assertTrue(lines, "scenario printed nothing")
        return json.loads(lines[-1])


# ── stream.js: the seq / epoch filter (Shape, AC12) ──────────────────────

class TestSeqFilter(_NodeCase):
    NEEDS = (STREAM_JS,)

    def test_hello_reports_first_same_new(self):
        res = self.run_js("""
const f = makeSeqFilter();
out([f.hello('E1'), f.hello('E1'), f.hello('E2'), f.hello('E2')]);
""")
        self.assertEqual(res, ["first", "same", "new", "same"])

    def test_keep_drops_seen_seq_within_epoch(self):
        res = self.run_js("""
const f = makeSeqFilter();
f.hello('E1');
const a = [1, 2, 3].map(s => f.keep({seq: s}));
f.hello('E1');
const b = [2, 3, 4].map(s => f.keep({seq: s}));
out({a, b, again: f.keep({seq: 4})});
""")
        self.assertEqual(res["a"], [True, True, True])
        self.assertEqual(res["b"], [False, False, True])
        self.assertFalse(res["again"])

    def test_new_epoch_forgets_seen_seqs(self):
        res = self.run_js("""
const f = makeSeqFilter();
f.hello('E1');
[1, 2, 3].forEach(s => f.keep({seq: s}));
f.hello('E2');
out([1, 2, 3].map(s => f.keep({seq: s})));
""")
        self.assertEqual(res, [True, True, True])

    def test_entries_without_numeric_seq_always_kept(self):
        res = self.run_js("""
const f = makeSeqFilter();
f.hello('E1');
out([f.keep({text: 'a'}), f.keep({text: 'a'}), f.keep({seq: '5'}), f.keep({seq: '5'}), f.keep({seq: null})]);
""")
        self.assertEqual(res, [True, True, True, True, True])


# ── feed.js: blocks, catch-up, history (AC12, AC11) ──────────────────────

class TestFeedCatchUp(_NodeCase):

    # AC12 — same-epoch reconnect with an overlapping replay renders each seq once.
    def test_same_epoch_overlapping_replay_renders_each_seq_once(self):
        res = self.run_js("""
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply({replay_batch: [T(1), T(2), T(3)]});
m.apply(T(4, 'live four', {npc: 'Odessa', voice: 'af_bella'}));
// dropped; while away the DM sent 5 and 6
m.apply({hello: {epoch: 'E1'}});
m.apply({replay_batch: [T(1), T(2), T(3), T(4, 'live four', {npc: 'Odessa', voice: 'af_bella'}), T(5), T(6)]});
m.apply(T(7));
out(seqs(m));
""")
        self.assertEqual(res, [1, 2, 3, 4, 5, 6, 7])

    # AC12 — a new epoch rebuilds from the replay, with no duplicates, as history.
    def test_new_epoch_rebuilds_from_replay_as_history(self):
        res = self.run_js("""
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply({replay_batch: [T(1), T(2)]});
m.apply(T(3));
m.apply(T(4));
// server restarted: seq continues from the saved log, epoch is new
m.apply({hello: {epoch: 'E2'}});
m.apply({replay_batch: [T(1), T(2), T(3), T(4)]});
const v = m.view();
out({seqs: v.blocks.map(b => b.seq), history: v.blocks.map(b => b.history === true)});
""")
        self.assertEqual(res["seqs"], [1, 2, 3, 4])
        self.assertEqual(res["history"], [True, True, True, True])

    # AC12 — new epoch whose log is empty: the server sends no replay_batch at
    # all (:3592), so the old feed must go on the hello itself.
    def test_new_epoch_with_no_replay_clears_feed(self):
        res = self.run_js("""
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply({replay_batch: [T(1), T(2)]});
m.apply({hello: {epoch: 'E2'}});
m.apply({scene: {name: 'tavern'}});
out(seqs(m));
""")
        self.assertEqual(res, [])

    # AC12 / old renderReplayBatch: a page opened mid-session shows the
    # replay as history, not as 200 blocks to speak.
    def test_first_connect_replay_is_history_and_live_is_not(self):
        res = self.run_js("""
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply({replay_batch: [T(1), T(2)]});
m.apply(T(3, 'The door creaks.'));
const v = m.view();
out(v.blocks.map(b => [b.seq, b.history === true, b.text]));
""")
        self.assertEqual(res, [[1, True, "block 1"], [2, True, "block 2"], [3, False, "The door creaks."]])

    # AC11 — live blocks after a replay append in order and keep their fields.
    def test_live_blocks_append_in_order_with_fields(self):
        res = self.run_js("""
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply({replay_batch: [T(1)]});
m.apply(T(2, 'Hold!', {npc: 'Guard', voice: 'am_adam'}));
m.apply(T(3, 'I sneak past', {player: 'Mira'}));
m.apply(T(4, 'Mira rolls 1d20+5: 14 + 5 = 19', {dice: true}));
m.apply(T(5, 'Tip: hide first', {tutor: true}));
m.apply(T(6, 'Mira lunges', {action: 'Mira'}));
m.apply(T(7, 'Dusk falls.', {scene: {name: 'forest'}}));
const b = m.view().blocks;
out(b.map(x => [x.seq, x.npc || null, x.player || null, !!x.dice, !!x.tutor, x.action || null, x.text]));
""")
        self.assertEqual(res, [
            [1, None, None, False, False, None, "block 1"],
            [2, "Guard", None, False, False, None, "Hold!"],
            [3, None, "Mira", False, False, None, "I sneak past"],
            [4, None, None, True, False, None, "Mira rolls 1d20+5: 14 + 5 = 19"],
            [5, None, None, False, True, None, "Tip: hide first"],
            [6, None, None, False, False, "Mira", "Mira lunges"],
            [7, None, None, False, False, None, "Dusk falls."],
        ])

    # AC11 — award and effect blocks are feed blocks (index.html:9224-9259);
    # milestone_spend and non-feed payloads add nothing.
    def test_award_and_effect_blocks_and_non_feed_payloads(self):
        res = self.run_js("""
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply({inspiration_award: 'Mira', text: 'Mira', reason: 'clever', seq: 1});
m.apply({milestone_award: 'Mira', label: 'Milestone', text: 'Mira', seq: 2});
m.apply({xp_award: {summary: '+50 XP'}, text: '+50 XP', seq: 3});
m.apply({milestone_spend: 'Mira', label: 'Milestone', text: 'Mira', seq: 4});
m.apply({effect_expired: {owner: 'Mira', name: 'Bless', was_concentration: true}});
m.apply({stats: {players: []}});
m.apply({scene: {name: 'cave'}});
m.apply({queue_status: []});
const b = m.view().blocks;
out(b.map(x => Object.keys(x).filter(k => ['inspiration_award','milestone_award','xp_award','milestone_spend','effect_expired'].includes(k))));
""")
        self.assertEqual(res, [["inspiration_award"], ["milestone_award"], ["xp_award"], ["effect_expired"]])

    # Plan S6 — one bad payload cannot stop the feed.
    def test_bad_payloads_do_not_break_later_blocks(self):
        res = self.run_js("""
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
for (const bad of [null, 'x', {replay_batch: null}, {replay_batch: [null, 7]}, {dice_pending: 'oops'},
                   {rewind: null}, {dice_request: null}]) {
  try { m.apply(bad); } catch (e) {}
}
m.apply(T(1, 'still here'));
out(m.view().blocks.map(b => b.text));
""")
        self.assertEqual(res, ["still here"])


# ── feed.js: rewind and clear (AC11, D13 "the rewind") ──────────────────

class TestFeedRewindClear(_NodeCase):

    # _applyRewind (index.html:5636): wipe, then render rewind.log as replay.
    def test_rewind_rebuilds_from_log_as_history(self):
        res = self.run_js("""
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply({replay_batch: [T(1), T(2)]});
m.apply(T(3, 'I open the chest', {player: 'Mira'}));
m.apply(T(4, 'A trap!'));
m.apply({queue_status: [], dice_pending: {}});
m.apply({rewind: {inputs: [{character: 'Mira', text: 'I open the chest'}], log: [T(1), T(2)], stats: {}}});
const v = m.view();
out({seqs: v.blocks.map(b => b.seq), history: v.blocks.map(b => b.history === true)});
""")
        self.assertEqual(res["seqs"], [1, 2])
        self.assertEqual(res["history"], [True, True])

    # After a rewind the seq counter keeps going up (:1055); later live blocks
    # append, and a same-epoch reconnect replaying the rewound log adds nothing.
    def test_after_rewind_live_appends_and_reconnect_does_not_duplicate(self):
        res = self.run_js("""
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply({replay_batch: [T(1), T(2)]});
m.apply(T(3, 'I open the chest', {player: 'Mira'}));
m.apply(T(4));
m.apply({rewind: {inputs: [], log: [T(1), T(2)], stats: {}}});
m.apply(T(5, 'I check for traps', {player: 'Mira'}));
m.apply({hello: {epoch: 'E1'}});
m.apply({replay_batch: [T(1), T(2), T(5, 'I check for traps', {player: 'Mira'})]});
out(seqs(m));
""")
        self.assertEqual(res, [1, 2, 5])

    def test_clear_empties_feed_and_later_blocks_append(self):
        res = self.run_js("""
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply({replay_batch: [T(1), T(2)]});
m.apply(T(3));
m.apply({clear: true});
const afterClear = seqs(m);
m.apply(T(4, 'A new campaign begins.'));
out({afterClear, after: seqs(m)});
""")
        self.assertEqual(res["afterClear"], [])
        self.assertEqual(res["after"], [4])


# ── feed.js: the roll card (D13 "roll requests pinned to the bottom") ───

_REQ = """
const REQ = (rid, chars, label) => ({dice_request: {
  request_id: rid, characters: chars, character: chars.length === 1 ? chars[0] : 'any',
  onscreen_targets: [], spec: '1d20', modifier: 5, advantage: 'normal', label: label || 'Stealth check', dc: 13}});
const PEND = (...rs) => ({dice_pending: rs.map(([rid, who, label]) => ({request_id: rid, pending: who, label: label || 'Stealth check'}))});
const card = (m) => { const c = m.view().rollCard; return c ? c.request_id : null; };
"""


class TestRollCard(_NodeCase):

    # Server order for a new named request: dice_pending, then dice_request (:2992-3008).
    def test_card_stays_reported_after_later_blocks(self):
        res = self.run_js(_REQ + """
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply(T(1, 'Roll for stealth.'));
m.apply(PEND(['r1', ['Mira']]));
m.apply(REQ('r1', ['Mira']));
m.apply(T(2, 'The guard turns...'));
m.apply(T(3, 'Mira, waiting on you.', {npc: 'Guard'}));
out({card: card(m), label: m.view().rollCard.label, seqs: seqs(m)});
""")
        self.assertEqual(res["card"], "r1")
        self.assertEqual(res["label"], "Stealth check")
        self.assertEqual(res["seqs"], [1, 2, 3])

    def test_card_goes_on_empty_dice_pending(self):
        res = self.run_js(_REQ + """
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply(PEND(['r1', ['Mira']]));
m.apply(REQ('r1', ['Mira']));
m.apply({dice_pending: []});
out(card(m));
""")
        self.assertIsNone(res)

    # A group roll stays up until the last roller is in (:2896, snapshot :1316).
    def test_card_stays_while_someone_still_to_roll_then_goes(self):
        res = self.run_js(_REQ + """
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply(PEND(['r1', ['Mira', 'Piper']]));
m.apply(REQ('r1', ['Mira', 'Piper']));
m.apply(T(1, 'Mira rolls 1d20+5: 9 + 5 = 14', {dice: true}));
m.apply(PEND(['r1', ['Piper']]));
const mid = card(m);
m.apply(T(2, 'Piper rolls 1d20+5: 3 + 5 = 8', {dice: true}));
m.apply({dice_pending: []});
out({mid, end: card(m), seqs: seqs(m)});
""")
        self.assertEqual(res["mid"], "r1")
        self.assertIsNone(res["end"])
        self.assertEqual(res["seqs"], [1, 2])

    # DELETE /dice-request/<id> broadcasts both keys in one payload (:3064).
    def test_card_goes_on_cancel(self):
        res = self.run_js(_REQ + """
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply(PEND(['r1', ['Mira']]));
m.apply(REQ('r1', ['Mira']));
m.apply({dice_pending: [], dice_request_cancelled: 'r1'});
out(card(m));
""")
        self.assertIsNone(res)

    # Resolving one of two requests leaves the other on the card.
    def test_resolving_one_request_keeps_the_other(self):
        res = self.run_js(_REQ + """
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply(PEND(['r1', ['Mira']]));
m.apply(REQ('r1', ['Mira'], 'Stealth check'));
m.apply(PEND(['r1', ['Mira']], ['r2', ['Piper'], 'Perception']));
m.apply(REQ('r2', ['Piper'], 'Perception'));
m.apply(PEND(['r1', ['Mira']]));
out(card(m));
""")
        self.assertEqual(res, "r1")

    # A rewind sends dice_pending as an empty dict, not a list (:3500).
    def test_card_goes_on_rewind_empty_dict(self):
        res = self.run_js(_REQ + """
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply(PEND(['r1', ['Mira']]));
m.apply(REQ('r1', ['Mira']));
m.apply({queue_status: [], dice_pending: {}});
m.apply({rewind: {inputs: [], log: [], stats: {}}});
out(card(m));
""")
        self.assertIsNone(res)

    # Reconnect: the server re-sends dice_pending then dice_request for each
    # still-pending request (:3622-3644). The card is back, once.
    def test_reconnect_restores_card_from_replayed_request(self):
        res = self.run_js(_REQ + """
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply(PEND(['r1', ['Mira']]));
m.apply(REQ('r1', ['Mira']));
m.apply({hello: {epoch: 'E1'}});
m.apply({replay_batch: [T(1)]});
m.apply(PEND(['r1', ['Mira']]));
m.apply(REQ('r1', ['Mira']));
out(card(m));
""")
        self.assertEqual(res, "r1")

    # The roll was made while the TV was away. On connect the server sends
    # dice_pending only when non-empty (:3622-3624), so nothing says the
    # request is done: the hello itself must drop the old card.
    def test_reconnect_drops_card_resolved_while_away(self):
        res = self.run_js(_REQ + """
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply(PEND(['r1', ['Mira']]));
m.apply(REQ('r1', ['Mira']));
m.apply({hello: {epoch: 'E1'}});
m.apply({replay_batch: [T(1), T(2, 'Mira rolls 1d20+5: 12 + 5 = 17', {dice: true})]});
out(card(m));
""")
        self.assertIsNone(res)

    # Server restart: pending requests live only in memory, so nothing is
    # replayed and the old card must go.
    def test_new_epoch_drops_card(self):
        res = self.run_js(_REQ + """
const m = makeFeedModel();
m.apply({hello: {epoch: 'E1'}});
m.apply(PEND(['r1', ['Mira']]));
m.apply(REQ('r1', ['Mira']));
m.apply({hello: {epoch: 'E2'}});
m.apply({replay_batch: [T(1)]});
out(card(m));
""")
        self.assertIsNone(res)


# ── devices.js: phone-approval cards (D24) ──────────────────────────────

class TestDeviceCards(_NodeCase):
    NEEDS = (DEVICES_JS,)

    def test_request_shows_one_card_per_id(self):
        res = self.run_js("""
const d = makeDeviceModel();
d.apply({device_request: {id: 'dev-a', ip: '192.168.1.20'}});
d.apply({device_request: {id: 'dev-a', ip: '192.168.1.20'}});
d.apply({device_request: {id: 'dev-b', ip: '192.168.1.21'}});
out(d.cards());
""")
        self.assertEqual(res, [{"id": "dev-a", "ip": "192.168.1.20"}, {"id": "dev-b", "ip": "192.168.1.21"}])

    # Reconnect replays every pending device (:3656-3659): still one card.
    def test_replayed_request_after_reconnect_is_not_duplicated(self):
        res = self.run_js("""
const d = makeDeviceModel();
d.apply({device_request: {id: 'dev-a', ip: '192.168.1.20'}});
d.apply({hello: {epoch: 'E1'}});
d.apply({device_request: {id: 'dev-a', ip: '192.168.1.20'}});
out(d.cards().map(c => c.id));
""")
        self.assertEqual(res, ["dev-a"])

    def test_approve_and_deny_build_the_old_requests_and_remove_card(self):
        res = self.run_js("""
const d = makeDeviceModel();
d.apply({device_request: {id: 'dev-a', ip: '192.168.1.20'}});
d.apply({device_request: {id: 'dev-b', ip: '192.168.1.21'}});
const a = d.respond('dev-a', true);
const left = d.cards().map(c => c.id);
const b = d.respond('dev-b', false);
out({a, b, left, end: d.cards()});
""")
        self.assertEqual(res["a"], {"url": "/device/approve", "body": {"id": "dev-a"}})
        self.assertEqual(res["b"], {"url": "/device/deny", "body": {"id": "dev-b"}})
        self.assertEqual(res["left"], ["dev-b"])
        self.assertEqual(res["end"], [])

    # Another screen (or the old page) answered: the broadcast removes the card.
    def test_approved_and_denied_broadcasts_remove_card(self):
        res = self.run_js("""
const d = makeDeviceModel();
d.apply({device_request: {id: 'dev-a', ip: '192.168.1.20'}});
d.apply({device_request: {id: 'dev-b', ip: '192.168.1.21'}});
d.apply({device_request: {id: 'dev-c', ip: '192.168.1.22'}});
d.apply({device_approved: 'dev-a'});
d.apply({device_denied: 'dev-c'});
d.apply({device_approved: 'dev-unknown'});
out(d.cards().map(c => c.id));
""")
        self.assertEqual(res, ["dev-b"])

    # A device approved elsewhere while this TV was disconnected is not
    # replayed on reconnect (:3656 replays only pending ones). The stale card
    # must not survive the reconnect.
    def test_reconnect_drops_card_answered_while_away(self):
        res = self.run_js("""
const d = makeDeviceModel();
d.apply({device_request: {id: 'dev-a', ip: '192.168.1.20'}});
d.apply({device_request: {id: 'dev-b', ip: '192.168.1.21'}});
d.apply({hello: {epoch: 'E1'}});
d.apply({device_request: {id: 'dev-b', ip: '192.168.1.21'}});
out(d.cards().map(c => c.id));
""")
        self.assertEqual(res, ["dev-b"])


# ── /tv shell carries the S6 scripts and elements ───────────────────────

def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_tv_feed", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestTvShellS6(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(DISPLAY))
        cls.app = _import_app()
        cls.client = cls.app.app.test_client()

    def setUp(self):
        r = self.client.get("/tv")
        self.assertEqual(r.status_code, 200)
        self.body = r.get_data(as_text=True)

    def test_tv_loads_s6_scripts(self):
        for name in ("stream.js", "feed.js", "devices.js", "bg.js"):
            with self.subTest(script=name):
                self.assertTrue(f'src="/static/tv/{name}"' in self.body, f"/tv does not load {name}")

    def test_s6_scripts_are_served(self):
        for name in ("stream.js", "feed.js", "devices.js", "bg.js"):
            with self.subTest(script=name):
                r = self.client.get(f"/static/tv/{name}")
                self.assertEqual(r.status_code, 200, name)
                self.assertIn("javascript", r.content_type, name)
                r.close()

    # AC12 — the reconnect badge.
    def test_reconnect_badge_element(self):
        self.assertTrue('id="tv-reconnect"' in self.body, '/tv has no id="tv-reconnect"')

    # D24 — where the approval cards go.
    def test_device_cards_element(self):
        self.assertTrue('id="tv-devices"' in self.body, '/tv has no id="tv-devices"')


if __name__ == "__main__":
    unittest.main()
