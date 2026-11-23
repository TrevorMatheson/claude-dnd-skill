"""Scene backgrounds S7: the screenshot contrast tool (AC6).

tests/scene_contrast_check.py is a QA tool, not a test (its name does not
match test*.py, so discover never runs it). It reads a screenshot PNG with
the standard library only (zlib), takes a rectangle (the narration column),
and prints the 95th-percentile background luminance there and the WCAG
contrast ratio of the ink colour against it.

Interface these tests pin:
  relative_luminance((r, g, b)) -> float     WCAG 2.x, 8-bit sRGB in, 0..1 out
  contrast_ratio(l1, l2) -> float            (lighter + 0.05) / (darker + 0.05)
  parse_color("#rrggbb" | "#rgb") -> (r, g, b)
  read_png(path) -> (width, height, rows)    rows[y][x] == (r, g, b), alpha dropped
  p95_luminance(path, (x, y, w, h)) -> float the 95th percentile of the
                                             rectangle's pixel luminances,
                                             clipped to the image
  main(argv) -> int
     main([png, "--rect", "x,y,w,h", "--ink", "#rrggbb", ("--min", "4.5")])
     prints the p95 luminance and the ratio; exit 0 when ratio >= --min
     (default 4.5), 1 when below, 2 for bad input (unreadable PNG, a
     rectangle wholly outside the image, a bad colour).

Synthetic PNGs are built here with every row filter type (None, Sub, Up,
Average, Paeth), several IDAT chunks, RGB and RGBA, as real screenshots use.
"""
import contextlib
import importlib
import io
import pathlib
import re
import shutil
import struct
import tempfile
import unittest
import zlib

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "tests" / "scene_contrast_check.py"


# --- a small PNG writer with real filters ----------------------------------

def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _filter_row(ftype, row, prev, bpp):
    out = bytearray()
    for i, x in enumerate(row):
        a = row[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        if ftype == 0:
            v = x
        elif ftype == 1:
            v = x - a
        elif ftype == 2:
            v = x - b
        elif ftype == 3:
            v = x - ((a + b) // 2)
        else:
            v = x - _paeth(a, b, c)
        out.append(v & 0xFF)
    return bytes([ftype]) + bytes(out)


def _chunk(kind, data):
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def make_png(pixels, alpha=False, idat_parts=3):
    """pixels: list of rows of (r, g, b). Row y uses filter y % 5."""
    h, w = len(pixels), len(pixels[0])
    bpp = 4 if alpha else 3
    raw = bytearray()
    prev = bytes(w * bpp)
    for y, row in enumerate(pixels):
        flat = bytearray()
        for (r, g, b) in row:
            flat += bytes((r, g, b, 200) if alpha else (r, g, b))
        raw += _filter_row(y % 5, bytes(flat), prev, bpp)
        prev = bytes(flat)
    comp = zlib.compress(bytes(raw), 9)
    step = max(1, len(comp) // idat_parts + 1)
    idats = b"".join(_chunk(b"IDAT", comp[i:i + step]) for i in range(0, len(comp), step))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6 if alpha else 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"tEXt", b"Software\x00test") + idats + _chunk(b"IEND", b""))


WHITE, BLACK = (255, 255, 255), (0, 0, 0)


def solid(w, h, color):
    return [[color] * w for _ in range(h)]


def halves(w, h, left, right):
    return [[left if x < w // 2 else right for x in range(w)] for _ in range(h)]


def gradient(w, h):
    """Every pixel different, so a filter mistake shows up."""
    return [[((x * 37 + y * 11) % 256, (x * 5 + y * 53) % 256, (x * y + 7) % 256)
             for x in range(w)] for y in range(h)]


class ContrastToolBase(unittest.TestCase):

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="scene-contrast-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tool = importlib.import_module("tests.scene_contrast_check")

    def png(self, pixels, name="shot.png", **kw):
        path = self.tmp / name
        path.write_bytes(make_png(pixels, **kw))
        return str(path)

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.tool.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def numbers(self, text):
        return [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]

    def assertPrints(self, out, value, tol):
        """Some number in the output is within tol of value."""
        nums = self.numbers(out)
        self.assertTrue(any(abs(n - value) <= tol for n in nums),
                        f"expected about {value} in output: {out!r}")


class TestToolFile(unittest.TestCase):

    def test_tool_exists_outside_discover(self):
        self.assertTrue(TOOL.is_file(), f"missing {TOOL}")
        self.assertFalse(TOOL.name.startswith("test"), "discover must not run the tool")

    def test_stdlib_only(self):
        src = TOOL.read_text(encoding="utf-8")
        self.assertIn("zlib", src)
        for lib in ("PIL", "numpy", "cv2", "imageio", "png"):
            self.assertNotRegex(src, rf"^\s*(import|from)\s+{lib}\b", lib)

    def test_import_has_no_side_effects(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            importlib.reload(importlib.import_module("tests.scene_contrast_check"))
        self.assertEqual(out.getvalue(), "")


class TestColourMaths(ContrastToolBase):

    def test_luminance_endpoints(self):
        self.assertAlmostEqual(self.tool.relative_luminance(WHITE), 1.0, places=4)
        self.assertAlmostEqual(self.tool.relative_luminance(BLACK), 0.0, places=4)

    def test_luminance_mid_grey_and_primaries(self):
        self.assertAlmostEqual(self.tool.relative_luminance((128, 128, 128)), 0.2159, places=3)
        self.assertAlmostEqual(self.tool.relative_luminance((255, 0, 0)), 0.2126, places=3)
        self.assertAlmostEqual(self.tool.relative_luminance((0, 255, 0)), 0.7152, places=3)
        self.assertAlmostEqual(self.tool.relative_luminance((0, 0, 255)), 0.0722, places=3)

    def test_contrast_ratio(self):
        self.assertAlmostEqual(self.tool.contrast_ratio(1.0, 0.0), 21.0, places=3)
        self.assertAlmostEqual(self.tool.contrast_ratio(0.0, 1.0), 21.0, places=3)
        self.assertAlmostEqual(self.tool.contrast_ratio(0.5, 0.5), 1.0, places=6)
        grey = self.tool.relative_luminance((0x77, 0x77, 0x77))
        self.assertAlmostEqual(self.tool.contrast_ratio(1.0, grey), 4.48, places=2)

    def test_parse_color(self):
        self.assertEqual(self.tool.parse_color("#ffffff"), WHITE)
        self.assertEqual(self.tool.parse_color("#000"), BLACK)
        self.assertEqual(self.tool.parse_color("#E8dcc0"), (0xE8, 0xDC, 0xC0))


class TestReadPng(ContrastToolBase):

    def test_reads_rgb_with_every_filter(self):
        pix = gradient(23, 17)
        w, h, rows = self.tool.read_png(self.png(pix))
        self.assertEqual((w, h), (23, 17))
        self.assertEqual([list(map(tuple, r)) for r in rows], pix)

    def test_reads_rgba_and_drops_alpha(self):
        pix = gradient(9, 11)
        w, h, rows = self.tool.read_png(self.png(pix, alpha=True))
        self.assertEqual((w, h), (9, 11))
        self.assertEqual([list(map(tuple, r)) for r in rows], pix)

    def test_not_a_png_raises(self):
        bad = self.tmp / "bad.png"
        bad.write_bytes(b"not a png at all")
        with self.assertRaises(Exception):
            self.tool.read_png(str(bad))


class TestP95(ContrastToolBase):

    def test_white_and_black(self):
        self.assertAlmostEqual(self.tool.p95_luminance(self.png(solid(20, 10, WHITE)), (0, 0, 20, 10)),
                               1.0, places=4)
        self.assertAlmostEqual(self.tool.p95_luminance(self.png(solid(20, 10, BLACK)), (0, 0, 20, 10)),
                               0.0, places=4)

    def test_rect_selects_the_region(self):
        path = self.png(halves(40, 10, BLACK, WHITE))
        self.assertAlmostEqual(self.tool.p95_luminance(path, (0, 0, 20, 10)), 0.0, places=4)
        self.assertAlmostEqual(self.tool.p95_luminance(path, (20, 0, 20, 10)), 1.0, places=4)

    def test_p95_is_the_bright_tail_not_the_mean(self):
        # 10 % white pixels in black: the mean is 0.1, the p95 is white.
        pix = [[WHITE if x < 10 else BLACK for x in range(100)] for _ in range(4)]
        self.assertAlmostEqual(self.tool.p95_luminance(self.png(pix), (0, 0, 100, 4)), 1.0, places=4)
        # 2 % white pixels: below the 95th percentile, so it reads black.
        pix = [[WHITE if x < 2 else BLACK for x in range(100)] for _ in range(4)]
        self.assertAlmostEqual(self.tool.p95_luminance(self.png(pix), (0, 0, 100, 4)), 0.0, places=4)

    def test_rect_partly_outside_is_clipped(self):
        path = self.png(halves(40, 10, BLACK, WHITE))
        self.assertAlmostEqual(self.tool.p95_luminance(path, (0, 0, 20, 500)), 0.0, places=4)


class TestMain(ContrastToolBase):

    def test_white_ink_on_black_is_21(self):
        code, out, err = self.run_main([self.png(solid(30, 20, BLACK)), "--rect", "0,0,30,20",
                                        "--ink", "#ffffff"])
        self.assertEqual(code, 0, out + err)
        self.assertPrints(out, 21.0, 0.05)

    def test_white_ink_on_white_is_1_and_fails(self):
        code, out, err = self.run_main([self.png(solid(30, 20, WHITE)), "--rect", "0,0,30,20",
                                        "--ink", "#ffffff"])
        self.assertEqual(code, 1, out + err)
        self.assertRegex(out, r"\b1\.0")

    def test_black_ink_on_white_is_21(self):
        code, out, err = self.run_main([self.png(solid(30, 20, WHITE)), "--rect", "0,0,30,20",
                                        "--ink", "#000000"])
        self.assertEqual(code, 0, out + err)
        self.assertPrints(out, 21.0, 0.05)

    def test_rect_picks_the_half(self):
        path = self.png(halves(40, 10, BLACK, WHITE))
        code, out, _ = self.run_main([path, "--rect", "0,0,20,10", "--ink", "#ffffff"])
        self.assertEqual(code, 0)
        self.assertPrints(out, 21.0, 0.05)
        code, out, _ = self.run_main([path, "--rect", "20,0,20,10", "--ink", "#ffffff"])
        self.assertEqual(code, 1)

    def test_prints_p95_and_ratio(self):
        grey = (0x77, 0x77, 0x77)
        code, out, err = self.run_main([self.png(solid(10, 10, grey)), "--rect", "0,0,10,10",
                                        "--ink", "#ffffff"])
        self.assertEqual(code, 1, out + err)  # 4.48 < 4.5
        self.assertIn("p95", out.lower())
        self.assertIn("ratio", out.lower())
        self.assertPrints(out, 4.48, 0.01)
        self.assertPrints(out, 0.1845, 0.002)  # luminance of #777 is 0.1845

    def test_min_threshold_flag(self):
        grey = (0x77, 0x77, 0x77)
        code, out, err = self.run_main([self.png(solid(10, 10, grey)), "--rect", "0,0,10,10",
                                        "--ink", "#ffffff", "--min", "4.4"])
        self.assertEqual(code, 0, out + err)

    def test_bad_inputs_exit_2(self):
        good = self.png(solid(10, 10, BLACK))
        bad = self.tmp / "bad.png"
        bad.write_bytes(b"nope")
        for argv in ([str(bad), "--rect", "0,0,5,5", "--ink", "#fff"],
                     [str(self.tmp / "missing.png"), "--rect", "0,0,5,5", "--ink", "#fff"],
                     [good, "--rect", "50,50,5,5", "--ink", "#fff"],
                     [good, "--rect", "0,0,5", "--ink", "#fff"],
                     [good, "--rect", "0,0,5,5", "--ink", "white-ish"]):
            with contextlib.redirect_stderr(io.StringIO()):
                try:
                    code, out, err = self.run_main(argv)
                except SystemExit as e:  # argparse errors exit 2 too
                    code = e.code
            self.assertEqual(code, 2, argv)


if __name__ == "__main__":
    unittest.main()
