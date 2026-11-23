"""Check narration contrast on a /tv screenshot (scene backgrounds AC6).

A QA tool, not a test: its name doesn't match test*.py, so discover skips it.
Take a screenshot of /tv with the text layer hidden (so only the background
is measured), then:

  python tests/scene_contrast_check.py shot.png --rect x,y,w,h --ink "#f1ebe1"

It prints the 95th-percentile background luminance inside the rectangle (the
narration column, in screenshot pixels) and the WCAG contrast ratio of the
ink colour against it. Exit 0 when the ratio is at least --min (default 4.5),
1 when it is below, 2 for bad input. Standard library only (zlib).
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib


def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb) -> float:
    r, g, b = rgb[:3]
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(l1: float, l2: float) -> float:
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def parse_color(text: str):
    s = str(text).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"not a colour: {text!r}")
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise ValueError(f"not a colour: {text!r}") from None


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_png(path):
    """(width, height, rows) with rows[y][x] == (r, g, b). 8-bit RGB / RGBA
    (alpha dropped), grey and grey+alpha, non-interlaced."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, ihdr = 8, [], None
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"IDAT":
            idat.append(chunk)
        elif kind == b"IEND":
            break
    if not ihdr:
        raise ValueError("PNG has no header")
    w, h, depth, ctype, _comp, _filt, interlace = ihdr
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(ctype)
    if depth != 8 or channels is None or interlace:
        raise ValueError("only 8-bit, non-interlaced grey/RGB(A) PNGs are supported")
    raw = zlib.decompress(b"".join(idat))
    stride = w * channels
    rows, prev = [], bytearray(stride)
    p = 0
    for _ in range(h):
        ft = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if ft == 1:
                line[i] = (line[i] + a) & 0xFF
            elif ft == 2:
                line[i] = (line[i] + b) & 0xFF
            elif ft == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif ft == 4:
                line[i] = (line[i] + _paeth(a, b, c)) & 0xFF
            elif ft != 0:
                raise ValueError(f"bad PNG row filter {ft}")
        if channels >= 3:
            px = [tuple(line[x * channels:x * channels + 3]) for x in range(w)]
        else:
            px = [(line[x * channels],) * 3 for x in range(w)]
        rows.append(px)
        prev = line
    return w, h, rows


def _percentile(vals, p):
    """Nearest-rank percentile (p on 0-100)."""
    s = sorted(vals)
    rank = -(-p * len(s) // 100)          # ceil(p% of n)
    return s[max(0, min(len(s) - 1, int(rank) - 1))]


def p95_luminance(path, rect) -> float:
    w, h, rows = read_png(path)
    x, y, rw, rh = (int(v) for v in rect)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(w, x + rw), min(h, y + rh)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("the rectangle is outside the image")
    lums = [relative_luminance(rows[yy][xx]) for yy in range(y0, y1) for xx in range(x0, x1)]
    return _percentile(lums, 95)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scene_contrast_check.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("png")
    ap.add_argument("--rect", required=True, help="x,y,w,h in screenshot pixels")
    ap.add_argument("--ink", default="#f1ebe1", help="the text colour (default --ink #f1ebe1)")
    ap.add_argument("--min", type=float, default=4.5, help="the ratio to meet (default 4.5)")
    args = ap.parse_args(argv)
    try:
        parts = [int(float(v)) for v in args.rect.split(",")]
        if len(parts) != 4:
            raise ValueError("--rect needs x,y,w,h")
        ink = relative_luminance(parse_color(args.ink))
        p95 = p95_luminance(args.png, parts)
    except (OSError, ValueError, zlib.error, struct.error) as e:
        print(f"scene_contrast_check: {e}", file=sys.stderr)
        return 2
    ratio = contrast_ratio(ink, p95)
    print(f"p95 luminance: {p95:.4f}")
    print(f"ratio: {ratio:.2f} (ink {args.ink}, need {args.min:g})")
    return 0 if ratio >= args.min else 1


if __name__ == "__main__":
    sys.exit(main())
