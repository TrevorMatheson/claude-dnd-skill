"""Auto-narrate must read every DM block, not just the last one of a burst.

WHY
===
The DM sends a turn as several blocks in quick succession: narration, an NPC
line, a tutor hint. Each new block's auto-play called `_ttsPlayBlock` directly,
and `_ttsPlayBlock` starts by stopping whatever is already playing. So in a
burst every block cut off the one before it and the table only ever heard the
last block. The auto-narrate queue makes each block wait until the previous
one has finished (or failed, or been stopped by hand).

Driven through node, like test_tts_fallback: the queue is timing behaviour, so
"the function exists" proves nothing.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = ROOT / "skills" / "dnd" / "display" / "templates" / "index.html"
NODE = shutil.which("node")

_SLICE = re.compile(r"(// Auto-narrate queue\..*?)\nfunction _ttsExtractText", re.S)

# A fake _ttsPlayBlock: marks the button as loading, then playing, then clears
# it after the block's "duration". `fail` blocks drop straight to idle, the way
# a failed request leaves the button showing an error label.
_HARNESS = """
const log = [];
let playing = 0, maxPlaying = 0;
function makeBtn(name, ms, fail) {
  const cls = new Set();
  return { name, ms, fail,
    classList: { contains: c => cls.has(c), add: c => cls.add(c), remove: (...cs) => cs.forEach(c => cls.delete(c)) } };
}
async function _ttsPlayBlock(btn, blockEl) {
  log.push('start:' + btn.name);
  if (btn.fail) { log.push('fail:' + btn.name); return; }
  btn.classList.add('tts-loading');
  await new Promise(r => setTimeout(r, 20));
  btn.classList.remove('tts-loading');
  btn.classList.add('tts-playing');
  playing++; maxPlaying = Math.max(maxPlaying, playing);
  setTimeout(() => { btn.classList.remove('tts-playing'); playing--; log.push('end:' + btn.name); }, btn.ms);
}
SLICE
const blocks = SPEC.map(([n, ms, fail]) => makeBtn(n, ms, fail));
for (const b of blocks) _ttsAutoEnqueue(b, {});
const check = () => {
  if (_ttsAutoBusy || _ttsAutoQueue.length) { setTimeout(check, 50); return; }
  console.log(JSON.stringify({ log, maxPlaying }));
};
setTimeout(check, 50);
"""


@unittest.skipIf(NODE is None, "node not installed")
class AutoNarrateQueue(unittest.TestCase):
    def run_queue(self, spec):
        m = _SLICE.search(HTML.read_text(encoding="utf-8"))
        self.assertIsNotNone(m, "auto-narrate queue block not found in index.html")
        js = _HARNESS.replace("SLICE", m.group(1)).replace("SPEC", json.dumps(spec))
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "q.js"
            p.write_text(js, encoding="utf-8")
            out = subprocess.run([NODE, str(p)], capture_output=True, text=True,
                                 encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_burst_plays_every_block_in_order_without_overlap(self):
        r = self.run_queue([["narration", 300, False], ["npc", 200, False], ["tutor", 100, False]])
        self.assertEqual(r["log"], [
            "start:narration", "end:narration",
            "start:npc", "end:npc",
            "start:tutor", "end:tutor",
        ])
        self.assertEqual(r["maxPlaying"], 1, "two blocks played at once")

    def test_a_failed_block_does_not_stall_the_queue(self):
        r = self.run_queue([["a", 100, False], ["broken", 0, True], ["c", 100, False]])
        self.assertEqual(r["log"], ["start:a", "end:a", "start:broken", "fail:broken",
                                    "start:c", "end:c"])

    def test_auto_narrate_is_on_unless_explicitly_turned_off(self):
        src = HTML.read_text(encoding="utf-8")
        self.assertIn("localStorage.getItem('dnd_tts_auto') !== '0'", src)
        # auto-play goes through the queue, never straight to _ttsPlayBlock
        self.assertIn("setTimeout(() => _ttsAutoEnqueue(playBtn, blockEl), 0);", src)
        self.assertNotIn("setTimeout(() => _ttsPlayBlock(playBtn, blockEl), 0);", src)


if __name__ == "__main__":
    unittest.main()
