"""The phone's network layer (phone sheet spec, plan step S8).

AC11 — With the server unreachable, a roll, action or edit shows Not sent with
       Retry and is never sent later on its own.
D14  — Nothing is lost when the phone drops; nothing is queued silently and
       sent later out of order.

Contract pinned for skills/dnd/display/static/phone/net.js (module.exports):

  makeNet({fetch, headers, onChange}) -> {send, retry, dismiss, items}
    send(kind, url, body, label) -> Promise<{ok, status, body?, item?, notSent?}>
      One attempt. Network error or 5xx -> one Not sent item (notSent: true).
      4xx -> resolves as the server's answer, no item. 202 -> status 202, no item.
    retry(id) -> sends the item again with the SAME op; success removes it.
    Every request body carries an `op`.

The real net.js is loaded in node with a fake fetch and fake timers. Skipped
when node is not installed.
"""
import json
import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
NET_JS = ROOT / "skills" / "dnd" / "display" / "static" / "phone" / "net.js"
NODE = shutil.which("node")

# A fake fetch whose next answers are scripted with `script.push(...)`:
#   'down' -> rejects like a dropped connection; a number -> that status.
# Fake timers: setTimeout/setInterval only record callbacks; advance() runs
# everything recorded (repeatedly) as if a long time had passed.
_PRELUDE = r"""
const timers = [];
global.setTimeout = (fn, ms) => { timers.push(fn); return timers.length; };
global.setInterval = (fn, ms) => { timers.push(fn); return timers.length; };
global.clearTimeout = () => {}; global.clearInterval = () => {};
function advance(rounds) {
  for (let r = 0; r < (rounds || 50); r++) {
    const now = timers.splice(0);
    now.forEach((fn) => { try { fn(); } catch (_) {} });
  }
}
const realNow = Date.now;
let skew = 0;
Date.now = () => realNow() + skew;

const net = require(%(path)s);
const calls = [];
const script = [];
function fakeFetch(url, opts) {
  calls.push({ url, body: JSON.parse(opts.body), method: opts.method });
  const next = script.length ? script.shift() : 200;
  if (next === 'down') return Promise.reject(new TypeError('Failed to fetch'));
  const body = typeof next === 'object' ? next.body : {};
  const status = typeof next === 'object' ? next.status : next;
  return Promise.resolve({ status, ok: status >= 200 && status < 300,
                           text: () => Promise.resolve(JSON.stringify(body || {})) });
}
const changes = [];
const n = net.makeNet({ fetch: fakeFetch, headers: () => ({ 'Content-Type': 'application/json' }),
                        onChange: (items) => changes.push(items.map((x) => x.id)) });
const out = {};
const flush = () => new Promise((r) => setImmediate(r));
(async () => {
%(body)s
  console.log(JSON.stringify(out));
})().catch((e) => { console.log(JSON.stringify({ __error: String(e && e.stack || e) })); });
"""


@unittest.skipIf(NODE is None, "node not installed")
class PhoneNet(unittest.TestCase):

    def run_js(self, body):
        self.assertTrue(NET_JS.exists(), "static/phone/net.js is missing")
        js = _PRELUDE % {"path": json.dumps(str(NET_JS)), "body": body}
        r = subprocess.run([NODE, "-"], input=js, capture_output=True, text=True,
                           encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [l for l in r.stdout.strip().splitlines() if l.startswith("{")]
        self.assertTrue(lines, "no output: " + r.stdout + r.stderr)
        res = json.loads(lines[-1])
        self.assertNotIn("__error", res, res.get("__error"))
        return res

    # ── one failure, one Not sent item ─────────────────────────────────────
    def test_network_error_gives_one_not_sent_item(self):
        res = self.run_js("""
  script.push('down');
  const r = await n.send('roll', '/player-input/dice', { spec: '1d20' }, 'Stealth roll');
  out.r = { ok: r.ok, notSent: !!r.notSent };
  out.items = n.items().map((x) => ({ label: x.label, kind: x.kind }));
  out.calls = calls.length;
""")
        self.assertFalse(res["r"]["ok"])
        self.assertTrue(res["r"]["notSent"])
        self.assertEqual(res["items"], [{"label": "Stealth roll", "kind": "roll"}])
        self.assertEqual(res["calls"], 1, "one attempt only")

    def test_5xx_gives_one_not_sent_item(self):
        for status in (500, 502, 503):
            with self.subTest(status=status):
                res = self.run_js(f"""
  script.push({status});
  const r = await n.send('action', '/player-input/stage', {{ text: 'hi' }}, 'hi');
  out.notSent = !!r.notSent; out.n = n.items().length; out.calls = calls.length;
""")
                self.assertTrue(res["notSent"])
                self.assertEqual(res["n"], 1)
                self.assertEqual(res["calls"], 1)

    def test_4xx_is_the_servers_answer_not_a_not_sent_item(self):
        for status in (400, 403, 409, 413, 429):
            with self.subTest(status=status):
                res = self.run_js(f"""
  script.push({{ status: {status}, body: {{ reason: 'no' }} }});
  const r = await n.send('edit', '/sheet/Havoc', {{ changes: [] }}, 'HP');
  out.r = {{ ok: r.ok, status: r.status, notSent: !!r.notSent, body: r.body }};
  out.n = n.items().length;
""")
                self.assertFalse(res["r"]["ok"])
                self.assertEqual(res["r"]["status"], status)
                self.assertFalse(res["r"]["notSent"])
                self.assertEqual(res["r"]["body"], {"reason": "no"})
                self.assertEqual(res["n"], 0)

    def test_202_is_approval_pending_not_a_not_sent_item(self):
        res = self.run_js("""
  script.push({ status: 202, body: { status: 'pending' } });
  const r = await n.send('action', '/player-input/stage', { text: 'hi' }, 'hi');
  out.r = { status: r.status, notSent: !!r.notSent }; out.n = n.items().length;
""")
        self.assertEqual(res["r"]["status"], 202)
        self.assertFalse(res["r"]["notSent"])
        self.assertEqual(res["n"], 0)

    def test_success_leaves_no_item(self):
        res = self.run_js("""
  script.push({ status: 200, body: { total: 17 } });
  const r = await n.send('roll', '/player-input/dice', {}, 'r');
  out.r = { ok: r.ok, status: r.status, body: r.body }; out.n = n.items().length;
""")
        self.assertEqual(res["r"], {"ok": True, "status": 200, "body": {"total": 17}})
        self.assertEqual(res["n"], 0)

    # ── nothing is sent later on its own ───────────────────────────────────
    def test_no_fetch_on_its_own_after_time_and_reconnect(self):
        res = self.run_js("""
  script.push('down');
  await n.send('roll', '/player-input/dice', { spec: '1d20' }, 'r');
  const after = calls.length;
  skew = 10 * 60 * 1000;           // ten minutes later
  advance(200);
  await flush(); await flush();
  // The server comes back: every answer from now on would succeed.
  skew = 30 * 60 * 1000;
  advance(200);
  if (typeof process.emit === 'function') { process.emit('online'); }
  await flush(); await flush();
  out.after = after; out.total = calls.length; out.n = n.items().length;
""")
        self.assertEqual(res["after"], 1)
        self.assertEqual(res["total"], 1, "a failed request was sent again without the player's Retry")
        self.assertEqual(res["n"], 1, "the Not sent item must stay until Retry or Dismiss")

    # ── Retry ──────────────────────────────────────────────────────────────
    def test_retry_reuses_the_op_and_success_removes_the_item(self):
        res = self.run_js("""
  script.push('down');
  await n.send('roll', '/player-input/dice', { spec: '1d20', character: 'Havoc' }, 'r');
  const id = n.items()[0].id;
  script.push({ status: 200, body: { total: 12 } });
  const r = await n.retry(id);
  out.ops = calls.map((c) => c.body.op);
  out.bodies = calls.map((c) => ({ spec: c.body.spec, character: c.body.character }));
  out.r = { ok: r.ok, status: r.status }; out.n = n.items().length;
  out.lastChange = changes[changes.length - 1];
""")
        self.assertEqual(len(res["ops"]), 2)
        self.assertTrue(res["ops"][0])
        self.assertEqual(res["ops"][0], res["ops"][1], "Retry must reuse the same op")
        self.assertEqual(res["bodies"][0], res["bodies"][1])
        self.assertEqual(res["r"], {"ok": True, "status": 200})
        self.assertEqual(res["n"], 0)
        self.assertEqual(res["lastChange"], [])

    def test_retry_that_fails_again_stays_one_item(self):
        res = self.run_js("""
  script.push('down');
  await n.send('roll', '/x', {}, 'r');
  const id = n.items()[0].id;
  script.push(503);
  const r = await n.retry(id);
  out.notSent = !!r.notSent; out.ids = n.items().map((x) => x.id); out.id = id;
  out.ops = calls.map((c) => c.body.op);
""")
        self.assertTrue(res["notSent"])
        self.assertEqual(res["ids"], [res["id"]])
        self.assertEqual(res["ops"][0], res["ops"][1])

    def test_dismiss_removes_without_sending(self):
        res = self.run_js("""
  script.push('down');
  await n.send('roll', '/x', {}, 'r');
  n.dismiss(n.items()[0].id);
  out.n = n.items().length; out.calls = calls.length;
""")
        self.assertEqual(res, {"n": 0, "calls": 1})

    # ── order, never merged ────────────────────────────────────────────────
    def test_two_failed_items_keep_order_and_are_not_merged(self):
        res = self.run_js("""
  script.push('down', 'down');
  await n.send('action', '/player-input/stage', { text: 'first' }, 'first');
  await n.send('action', '/player-input/stage', { text: 'second' }, 'second');
  out.labels = n.items().map((x) => x.label);
  out.ops = n.items().map((x) => x.op || null);
  // Retrying either one and failing again must not reorder them.
  script.push('down');
  await n.retry(n.items()[0].id);
  out.labels2 = n.items().map((x) => x.label);
  script.push('down');
  await n.retry(n.items()[1].id);
  out.labels3 = n.items().map((x) => x.label);
  out.sentOps = calls.map((c) => c.body.op);
  out.sentTexts = calls.map((c) => c.body.text);
""")
        self.assertEqual(res["labels"], ["first", "second"])
        self.assertEqual(res["labels2"], ["first", "second"])
        self.assertEqual(res["labels3"], ["first", "second"])
        self.assertNotEqual(res["sentOps"][0], res["sentOps"][1], "two sends share one op")
        self.assertEqual(res["sentTexts"], ["first", "second", "first", "second"])

    # ── every request carries an op ────────────────────────────────────────
    def test_every_request_carries_an_op(self):
        res = self.run_js("""
  script.push(200, 400, 'down', 202, 500);
  await n.send('roll', '/a', { x: 1 }, 'a');
  await n.send('edit', '/b', {}, 'b');
  await n.send('action', '/c', null, 'c');
  await n.send('move', '/d', { op: undefined }, 'd');
  await n.send('roll', '/e', {}, 'e');
  for (const it of n.items()) { script.push(200); await n.retry(it.id); }
  out.ops = calls.map((c) => c.body.op);
  out.methods = calls.map((c) => c.method);
  out.x = calls[0].body.x;
""")
        self.assertEqual(len(res["ops"]), 7)
        for op in res["ops"]:
            self.assertIsInstance(op, str)
            self.assertTrue(op)
        self.assertEqual(len(set(res["ops"][:5])), 5, "each new send needs its own op")
        self.assertEqual(set(res["methods"]), {"POST"})
        self.assertEqual(res["x"], 1, "the caller's body must be kept")


if __name__ == "__main__":
    unittest.main()
