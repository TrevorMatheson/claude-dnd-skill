"""The new TV page shell at /tv (display foundation, plan step S1).

AC1 — /tv serves the new page; / still serves the old page unchanged.
AC2 — the new page runs from a plain install with no build step and no new
      dependencies: its static files (tokens, fonts) are served by Flask,
      nothing is pulled from the network, and requirements.txt is unchanged.
"""
import importlib.util
import pathlib
import re
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
TV_HTML = DISPLAY / "templates" / "tv.html"
TV_STATIC = DISPLAY / "static" / "tv"
REQUIREMENTS = DISPLAY / "requirements.txt"
sys.path.insert(0, str(DISPLAY))

FONT_PATHS = (
    "/static/tv/fonts/Fraunces.woff2",
    "/static/tv/fonts/Fraunces-Italic.woff2",
    "/static/tv/fonts/Literata.woff2",
    "/static/tv/fonts/Literata-Italic.woff2",
    "/static/tv/fonts/OFL.txt",
)

URL_RE = re.compile(r"https?://", re.IGNORECASE)


def _import_app():
    spec = importlib.util.spec_from_file_location("dnd_display_app_tv_shell", str(DISPLAY / "dnd-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestTvRoutes(unittest.TestCase):
    """AC1 and the served-files half of AC2, through the Flask test client."""

    @classmethod
    def setUpClass(cls):
        cls.app = _import_app()
        cls.client = cls.app.app.test_client()

    # AC1
    def test_tv_route_serves_new_shell(self):
        r = self.client.get("/tv")
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="tv-stage"', r.get_data(as_text=True))

    # AC1
    def test_root_still_serves_old_page(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn('id="sidebar-right"', body)
        self.assertNotIn('id="tv-stage"', body)

    # AC2
    def test_tokens_css_served(self):
        r = self.client.get("/static/tv/tokens.css")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content_type.startswith("text/css"), r.content_type)
        css = r.get_data(as_text=True)
        r.close()
        for needle in ("#e06a55", "Fraunces", "Literata"):
            self.assertIn(needle, css)

    # AC2
    def test_font_files_served(self):
        for path in FONT_PATHS:
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200, path)
                self.assertGreater(len(r.get_data()), 0, path)
                r.close()


class TestTvNoNetwork(unittest.TestCase):
    """AC2: the table needs no network and no new dependency."""

    def test_tv_html_and_css_have_no_remote_urls(self):
        self.assertTrue(TV_HTML.is_file(), f"missing {TV_HTML}")
        css_files = sorted(TV_STATIC.rglob("*.css")) if TV_STATIC.is_dir() else []
        self.assertTrue(css_files, f"no .css files under {TV_STATIC}")
        for f in [TV_HTML, *css_files]:
            with self.subTest(file=str(f.relative_to(REPO))):
                text = f.read_text(encoding="utf-8")
                hits = URL_RE.findall(text)
                self.assertEqual(hits, [], f"{f.name} references a remote URL")

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
