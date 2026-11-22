"""The phone page shell at /phone (phone sheet spec, plan step S1).

AC1 — /phone serves the new page and the old phone page (/ and /?char=Mira)
      is unchanged. The page carries the LAN token meta like tv.html does.
AC2 — (frame) the shell has the Play / Sheet / Map tabs, their panes and the
      layout switch; the Map tab is in the markup but hidden in milestone 1.
D17 — no new install step, dependency or port: every phone file is served by
      Flask, nothing is pulled from the network, requirements.txt unchanged.

Browser half of AC1/AC2 (375x812 tabs, 1440x900 side by side, switch
survives reload) is a verify-mode check, not here.
"""
import importlib.util
import pathlib
import re
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
PHONE_HTML = DISPLAY / "templates" / "phone.html"
PHONE_STATIC = DISPLAY / "static" / "phone"
REQUIREMENTS = DISPLAY / "requirements.txt"
sys.path.insert(0, str(DISPLAY))

URL_RE = re.compile(r"https?://", re.IGNORECASE)

SCRIPTS = (
    "/static/tv/stream.js",
    "/static/tv/panels.js",
    "/static/phone/layout.js",
    "/static/phone/phone.js",
)
STYLES = (
    "/static/tv/tokens.css",
    "/static/phone/phone.css",
)
PHONE_FILES = (
    ("/static/phone/phone.css", "text/css"),
    ("/static/phone/layout.js", "javascript"),
    ("/static/phone/phone.js", "javascript"),
)


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_phone_page",
                                                  str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tag_with(body, attr_re):
    """The first opening tag whose attributes match attr_re, or None."""
    m = re.search(r"<[a-zA-Z][^>]*" + attr_re + r"[^>]*>", body)
    return m.group(0) if m else None


class TestPhoneRoute(unittest.TestCase):
    """AC1 and the shell half of AC2, through the Flask test client."""

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.client = cls.app.app.test_client()

    def _phone(self):
        r = self.client.get("/phone")
        self.assertEqual(r.status_code, 200, "GET /phone")
        return r.get_data(as_text=True)

    # AC1
    def test_phone_route_serves_shell(self):
        self.assertIn('id="phone-app"', self._phone())

    # AC1 — the token travels to the page the same way tv.html carries it.
    def test_phone_carries_token_meta(self):
        body = self._phone()
        self.assertRegex(body, r'<meta\s+name="dnd-token"\s+content="[^"]*"')

    # AC1 — when the LAN token is set, it is the token the page carries.
    def test_phone_token_meta_holds_lan_token(self):
        old = self.app._lan_token
        self.app._lan_token = "tok-phone-s1"
        try:
            body = self._phone()
        finally:
            self.app._lan_token = old
        self.assertIn('<meta name="dnd-token" content="tok-phone-s1">', body)

    # AC1 / D17 — theme and shared foundation scripts plus the phone files.
    def test_phone_loads_theme_and_scripts(self):
        body = self._phone()
        for src in SCRIPTS:
            with self.subTest(script=src):
                self.assertRegex(body, r'<script[^>]+src="' + re.escape(src) + r'(\?[^"]*)?"')
        for href in STYLES:
            with self.subTest(style=href):
                self.assertRegex(body, r'<link[^>]+href="' + re.escape(href) + r'(\?[^"]*)?"')

    # AC2 / D3 — Play, Sheet and Map tab buttons.
    def test_tab_buttons_present(self):
        body = self._phone()
        for tab in ("play", "sheet", "map"):
            with self.subTest(tab=tab):
                self.assertIsNotNone(_tag_with(body, r'data-tab="%s"' % tab),
                                     "no element with data-tab=%r" % tab)

    # AC2 / plan note 12 — the Map tab is in the markup but hidden in milestone 1.
    def test_map_tab_hidden(self):
        tag = _tag_with(self._phone(), r'data-tab="map"')
        self.assertIsNotNone(tag)
        self.assertRegex(tag, r"\shidden(\s|=|>|/)", "Map tab is not hidden: " + str(tag))

    # AC2 — Play and Sheet tabs are not hidden.
    def test_play_and_sheet_tabs_not_hidden(self):
        body = self._phone()
        for tab in ("play", "sheet"):
            tag = _tag_with(body, r'data-tab="%s"' % tab)
            with self.subTest(tab=tab):
                self.assertIsNotNone(tag)
                self.assertNotRegex(tag, r"\shidden(\s|=|>|/)")

    # AC2 / D3 / D4 — one pane per tab.
    def test_panes_present(self):
        body = self._phone()
        for pane in ("pane-play", "pane-sheet", "pane-map"):
            with self.subTest(pane=pane):
                self.assertIn('id="%s"' % pane, body)

    # AC2 / D2 — the Desktop version / Phone version switch.
    def test_layout_switch_present(self):
        self.assertIn('id="layout-switch"', self._phone())

    # Plan: talk.js mounts in #talk-slot, inside the Play pane.
    def test_talk_slot_inside_play_pane(self):
        body = self._phone()
        play = body.find('id="pane-play"')
        self.assertNotEqual(play, -1, "no #pane-play")
        talk = body.find('id="talk-slot"')
        self.assertNotEqual(talk, -1, "no #talk-slot")
        self.assertGreater(talk, play, "#talk-slot comes before #pane-play")
        # It must come before the next pane opens.
        next_panes = [i for i in (body.find('id="pane-sheet"'), body.find('id="pane-map"'))
                      if i > play]
        if next_panes:
            self.assertLess(talk, min(next_panes), "#talk-slot is not inside #pane-play")

    # D17 — the phone's own static files are served by Flask.
    def test_phone_static_served(self):
        for path, kind in PHONE_FILES:
            with self.subTest(path=path):
                r = self.client.get(path)
                try:
                    self.assertEqual(r.status_code, 200, path)
                    self.assertIn(kind, r.content_type, path)
                    self.assertGreater(len(r.get_data()), 0, path)
                finally:
                    r.close()

    # AC1 — the old phone page is unchanged.
    def test_root_still_old_page(self):
        for url in ("/", "/?char=Mira"):
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 200)
                body = r.get_data(as_text=True)
                self.assertIn('id="sidebar-right"', body)
                self.assertNotIn('id="phone-app"', body)


class TestPhoneNoNetwork(unittest.TestCase):
    """D17: nothing is pulled from the network; no new dependency."""

    def test_phone_files_have_no_remote_urls(self):
        self.assertTrue(PHONE_HTML.is_file(), "missing %s" % PHONE_HTML)
        files = [PHONE_HTML]
        if PHONE_STATIC.is_dir():
            files += sorted(PHONE_STATIC.rglob("*.css")) + sorted(PHONE_STATIC.rglob("*.js"))
        self.assertGreater(len(files), 1, "no .css/.js files under %s" % PHONE_STATIC)
        for f in files:
            with self.subTest(file=str(f.relative_to(REPO))):
                hits = URL_RE.findall(f.read_text(encoding="utf-8"))
                self.assertEqual(hits, [], "%s references a remote URL" % f.name)

    def test_requirements_unchanged(self):
        names = set()
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            name = re.split(r"[\s\[<>=!~;]", line, maxsplit=1)[0].lower().replace("_", "-")
            names.add(name)
        self.assertEqual(names, {"flask", "flask-cors", "pygame", "numpy", "pyyaml"})


if __name__ == "__main__":
    unittest.main()
