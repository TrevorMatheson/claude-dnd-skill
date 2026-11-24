"""The Sheet tab, read-only and live (phone sheet spec, plan step S3).

AC4 (browser half) — the sheet shows every section of a real imported
      character; an unreadable section shows as plain text.
D3  — the Sheet tab lives in the phone page.
D5  — the sheet is read from the character's .md file (GET /sheet/<name>).

Contract pinned for skills/dnd/display/static/phone/sheet.js (attaches to
window.PH.sheet and sets module.exports; loading it must not need a DOM):

  sheetView(json) -> [{kind, title, data, editable}, ...]
      json is the GET /sheet JSON {sheet, editable, path_ok}. One block per
      parsed section (identity, combat, abilities, saves, skills, attacks,
      slots, spells, features, inventory, notes) plus one 'plain' block per
      sheet.plain entry. editable is true exactly for kinds in json.editable.
  slotDots({level, total, used, label}) -> {total, used, left, dots}
      dots.length === total; count of true dots === total - used, with used
      clamped to 0..total.
  wantsReload(payload, me) -> bool
      {sheet: {name}} for this character (case-insensitive) -> true; another
      character -> false; {rewind: ...} -> true; anything else -> false.
  html(blocks) -> string
      a <section ... data-kind="<kind>"> per block; names escaped; a
      class="pencil" button only when hooks.pencil is set and the block is
      editable.

Fixture inputs are built by running the real parser (sheet.parse) in Python.
Node tests are skipped when node is not installed.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "skills" / "dnd" / "display"
SHEET_JS = DISPLAY / "static" / "phone" / "sheet.js"
FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "sheets"
NODE = shutil.which("node")
if str(DISPLAY) not in sys.path:
    sys.path.insert(0, str(DISPLAY))

# `## ` title -> the block kind it should produce (None: a plain block).
SECTION_KIND = {
    "Identity": "identity",
    "Character Pillar": None,
    "Campaign History": None,
    "Ability Scores": "abilities",
    "Combat Stats": "combat",
    "Saving Throws": "saves",
    "Skills": "skills",
    "Attacks": "attacks",
    "Spell Slots (if applicable)": "slots",
    "Known Spells / Cantrips": "spells",
    "Features & Traits": "features",
    "Equipment & Inventory": "inventory",
    "Backstory & Notes": "notes",
    "Session Log": None,
}
PARSED_KINDS = ("identity", "combat", "abilities", "saves", "skills", "attacks",
                "slots", "spells", "features", "inventory", "notes")


def _route_json(fixture: str, editable=None) -> dict:
    """What GET /sheet/<name> returns for a fixture, built with the real parser."""
    sheet = importlib.import_module("sheet")
    text = (FIX / fixture).read_bytes().decode("utf-8")
    parsed = sheet.parse(text)
    return {"sheet": parsed,
            "editable": sheet.editable(parsed) if editable is None else editable,
            "path_ok": True}


_PRELUDE = """
globalThis.window = { PH: {} };
const mod = require(%(js)s);
function need(name) {
  if (!mod || typeof mod[name] !== 'function') throw new Error('sheet.js does not export ' + name + '()');
  return mod[name];
}
const sheetView = (...a) => need('sheetView')(...a);
const slotDots = (...a) => need('slotDots')(...a);
const wantsReload = (...a) => need('wantsReload')(...a);
const html = (...a) => need('html')(...a);
const out = (v) => console.log(JSON.stringify(v));
const DATA = %(data)s;
"""


@unittest.skipUnless(NODE, "node is not installed")
class _NodeCase(unittest.TestCase):

    def run_js(self, body: str, data=None):
        if not SHEET_JS.exists():
            self.fail("not built yet: static/phone/sheet.js")
        script = _PRELUDE % {"js": json.dumps(str(SHEET_JS)),
                             "data": json.dumps(data, ensure_ascii=False)} + body
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True,
                               encoding="utf-8", timeout=30)
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        if r.returncode != 0:
            self.fail("node failed:\n" + (r.stderr or r.stdout)[-1500:])
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        self.assertTrue(lines, "scenario printed nothing")
        return json.loads(lines[-1])


class TestExports(_NodeCase):

    # Plan Shape: each phone file attaches to window.PH as well as module.exports.
    def test_attaches_to_window_ph(self):
        res = self.run_js("""
const S = window.PH && window.PH.sheet;
out(!!S && ['sheetView', 'slotDots', 'wantsReload', 'html'].every(n => typeof S[n] === 'function'));
""")
        self.assertTrue(res, "window.PH.sheet is missing or incomplete")


class TestSheetView(_NodeCase):

    def _blocks(self, fixture, editable=None):
        return self.run_js("out(sheetView(DATA));", _route_json(fixture, editable))

    def _assert_every_section(self, fixture):
        data = _route_json(fixture)
        blocks = self.run_js("out(sheetView(DATA));", data)
        kinds = [b["kind"] for b in blocks]
        plain_titles = [b["title"] for b in blocks if b["kind"] == "plain"]
        for title in data["sheet"]["sections"]:
            kind = SECTION_KIND.get(title)
            with self.subTest(section=title):
                if kind is None or kind not in kinds:
                    self.assertIn(title, plain_titles,
                                  f"section {title!r} has no block ({kind!r} or plain)")
                else:
                    self.assertEqual(kinds.count(kind), 1, f"{kind} block appears {kinds.count(kind)} times")
        return data, blocks

    # AC4 — every section of a real imported character has a block.
    def test_imported_every_section(self):
        self._assert_every_section("imported.md")

    # AC4 — same for a sheet filled in from the template.
    def test_template_filled_every_section(self):
        self._assert_every_section("template_filled.md")

    # AC4 — one plain block per sheet.plain entry, in that order, holding its text.
    def test_one_plain_block_per_plain_entry(self):
        for fx in ("imported.md", "template_filled.md", "broken.md"):
            with self.subTest(fixture=fx):
                data = _route_json(fx)
                blocks = self.run_js("out(sheetView(DATA));", data)
                plain = [(b["title"], b["data"]["text"]) for b in blocks if b["kind"] == "plain"]
                want = [(p["title"], p["text"]) for p in data["sheet"]["plain"]]
                self.assertEqual(plain, want)

    # AC4 — one block per parsed section: every non-empty parsed key has a block.
    def test_one_block_per_parsed_key(self):
        for fx in ("imported.md", "template_filled.md"):
            with self.subTest(fixture=fx):
                data = _route_json(fx)
                kinds = [b["kind"] for b in self.run_js("out(sheetView(DATA));", data)]
                for k in PARSED_KINDS:
                    v = data["sheet"].get(k)
                    if v is None or v == [] or v == {}:
                        continue
                    self.assertEqual(kinds.count(k), 1, f"{fx}: {k}")

    # AC4 — broken.md's unreadable Skills is a plain block with the raw text.
    def test_broken_skills_is_plain_raw_text(self):
        data = _route_json("broken.md")
        raw = {p["title"]: p["text"] for p in data["sheet"]["plain"]}
        self.assertIn("Skills", raw, "fixture precondition: parser puts broken Skills in plain")
        blocks = self.run_js("out(sheetView(DATA));", data)
        self.assertNotIn("skills", [b["kind"] for b in blocks])
        hit = [b for b in blocks if b["kind"] == "plain" and b["title"] == "Skills"]
        self.assertEqual(len(hit), 1, "no plain block titled Skills")
        self.assertEqual(hit[0]["data"]["text"], raw["Skills"])

    # Blocks are ordered: identity first, plain blocks after the parsed ones.
    def test_order(self):
        blocks = self._blocks("imported.md")
        kinds = [b["kind"] for b in blocks]
        self.assertEqual(kinds[0], "identity")
        first_plain = kinds.index("plain")
        self.assertTrue(all(k == "plain" for k in kinds[first_plain:]), kinds)
        parsed = [k for k in kinds if k != "plain"]
        self.assertEqual(parsed, [k for k in PARSED_KINDS if k in parsed], "parsed blocks out of order")

    # D7 — editable is true exactly for the kinds the server lists.
    def test_editable_matches_server_list(self):
        data = _route_json("imported.md")
        blocks = self.run_js("out(sheetView(DATA));", data)
        allowed = set(data["editable"])
        self.assertTrue(allowed, "fixture precondition: server lists editable sections")
        for b in blocks:
            with self.subTest(kind=b["kind"], title=b["title"]):
                self.assertIs(b["editable"], b["kind"] in allowed)

    # Roster-only sheets come back with editable [] -> nothing is editable.
    def test_nothing_editable_when_list_empty(self):
        blocks = self._blocks("imported.md", editable=[])
        self.assertEqual([b["kind"] for b in blocks if b["editable"]], [])

    def test_editable_subset(self):
        blocks = self._blocks("imported.md", editable=["notes"])
        self.assertEqual([b["kind"] for b in blocks if b["editable"]], ["notes"])

    # Blocks carry the documented fields.
    def test_block_shape(self):
        for b in self._blocks("template_filled.md"):
            with self.subTest(kind=b["kind"]):
                self.assertTrue({"kind", "title", "data", "editable"} <= set(b), sorted(b))
                self.assertIsInstance(b["title"], str)
                self.assertIsInstance(b["editable"], bool)


class TestSlotDots(_NodeCase):

    def _check(self, res, total, left):
        self.assertEqual(res["total"], total)
        self.assertEqual(len(res["dots"]), total)
        self.assertEqual(sum(1 for d in res["dots"] if d is True), left)
        self.assertEqual(res["left"], left)
        self.assertEqual(res["used"], total - left)

    def test_normal(self):
        res = self.run_js("out([slotDots({level:1,total:4,used:1,label:'1st'}), "
                          "slotDots({level:2,total:3,used:0,label:'2nd'}), "
                          "slotDots({level:3,total:2,used:2,label:'3rd'})]);")
        self._check(res[0], 4, 3)
        self._check(res[1], 3, 3)
        self._check(res[2], 2, 0)

    def test_used_clamped(self):
        res = self.run_js("out([slotDots({level:1,total:2,used:5,label:''}), "
                          "slotDots({level:1,total:3,used:-2,label:''})]);")
        self._check(res[0], 2, 0)
        self._check(res[1], 3, 3)

    def test_zero_total(self):
        self._check(self.run_js("out(slotDots({level:1,total:0,used:0,label:''}));"), 0, 0)

    # Slots from a real sheet go through slotDots.
    def test_slots_block_uses_dots(self):
        blocks = self.run_js("out(sheetView(DATA));", _route_json("imported.md"))
        slots = [b for b in blocks if b["kind"] == "slots"]
        self.assertEqual(len(slots), 1)
        for s in slots[0]["data"]:
            self.assertEqual(len(s["dots"]), s["total"])
            self.assertEqual(sum(1 for d in s["dots"] if d), s["total"] - s["used"])


class TestWantsReload(_NodeCase):

    # Plan S3 — a `sheet` event for another character doesn't refetch.
    def test_reload_rules(self):
        res = self.run_js("""
out([
  wantsReload({sheet: {name: 'Havoc'}}, 'havoc'),
  wantsReload({sheet: {name: 'havoc'}}, 'Havoc'),
  wantsReload({sheet: {name: 'HAVOC'}}, 'havoc'),
  wantsReload({sheet: {name: 'Mira'}}, 'havoc'),
  wantsReload({rewind: {seq: 4}}, 'havoc'),
  wantsReload({rewind: true}, 'havoc'),
  wantsReload({stats: {}}, 'havoc'),
  wantsReload({text: 'hi'}, 'havoc'),
  wantsReload({}, 'havoc'),
  wantsReload(null, 'havoc'),
  wantsReload({sheet: {name: 'Havoc'}}, ''),
]);
""")
        self.assertEqual(res, [True, True, True, False, True, True,
                               False, False, False, False, False])


class TestHtml(_NodeCase):

    def test_section_per_block(self):
        res = self.run_js("const b = sheetView(DATA); out({kinds: b.map(x => x.kind), html: html(b)});",
                          _route_json("imported.md"))
        found = re.findall(r'<section\b[^>]*\bdata-kind="([^"]+)"', res["html"])
        self.assertEqual(found, res["kinds"])

    def test_names_escaped(self):
        data = _route_json("imported.md")
        data["sheet"]["name"] = "<b>x</b>"
        data["sheet"]["plain"].append({"title": "<b>x</b>", "text": "<b>x</b>"})
        for g in data["sheet"]["inventory"]["groups"]:
            for it in g["items"]:
                it["name"] = "<b>x</b>"
        for a in data["sheet"]["attacks"]:
            a["name"] = "<b>x</b>"
        out = self.run_js("out(html(sheetView(DATA)));", data)
        self.assertNotIn("<b>x</b>", out)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", out)

    # S3: hooks unset -> no pencil anywhere, even on editable blocks.
    def test_no_pencil_without_hook(self):
        out = self.run_js("out(html(sheetView(DATA)));", _route_json("imported.md"))
        self.assertNotIn('class="pencil"', out)

    # With the hook set, pencils only on editable blocks.
    def test_pencil_only_on_editable_with_hook(self):
        res = self.run_js("""
mod.hooks.pencil = () => {};
const b = sheetView(DATA);
out(b.map(x => ({kind: x.kind, editable: x.editable, html: html([x])})));
""", _route_json("imported.md"))
        self.assertTrue(any(r["editable"] for r in res), "fixture precondition: some block editable")
        for r in res:
            with self.subTest(kind=r["kind"]):
                self.assertEqual('class="pencil"' in r["html"], r["editable"])


# ── Flask / static ────────────────────────────────────────────────────────

def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_phone_sheet_js",
                                                  str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPhoneWiring(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.client = cls.app.app.test_client()

    # D3 / D5 — phone.html loads sheet.js before phone.js.
    def test_phone_html_loads_sheet_before_phone(self):
        r = self.client.get("/phone")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        r.close()
        srcs = re.findall(r'<script[^>]+src="([^"?]+)', body)
        self.assertIn("/static/phone/sheet.js", srcs, "phone.html does not load sheet.js")
        self.assertIn("/static/phone/phone.js", srcs)
        self.assertLess(srcs.index("/static/phone/sheet.js"), srcs.index("/static/phone/phone.js"))

    def test_sheet_js_served(self):
        r = self.client.get("/static/phone/sheet.js")
        try:
            self.assertEqual(r.status_code, 200)
            self.assertIn("javascript", r.content_type)
            self.assertGreater(len(r.get_data()), 0)
        finally:
            r.close()


if __name__ == "__main__":
    unittest.main()
