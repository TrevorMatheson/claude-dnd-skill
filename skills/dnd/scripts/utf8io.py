"""utf8io.py — lossless text reads for the UTF-8 sweep.

The pre-sweep plugin wrote campaign files in the Windows system codepage
(GBK). Reading such a file with errors="replace" turns every multi-byte
character into U+FFFD, and writing that text back makes the corruption
permanent. read_text() below never produces that outcome:

  * valid UTF-8 (the whole tree after the sweep) decodes as-is;
  * legacy GBK decodes losslessly — proven by a GBK round-trip — so a
    read-modify-write becomes a one-time migration to UTF-8;
  * anything else raises TextDecodeError, so the caller refuses loudly
    instead of silently flattening the file.
"""
import pathlib


class TextDecodeError(ValueError):
    """Raised when a file is neither valid UTF-8 nor losslessly GBK-decodable."""


def read_text(path: pathlib.Path) -> str:
    """Read `path` as text without ever producing U+FFFD replacement chars.

    Tries strict UTF-8 first (the canonical form after the sweep). If the
    bytes are not valid UTF-8, tries GBK/cp936 — the legacy Windows codepage
    the old plugin wrote; the round-trip check proves the bytes were
    genuinely GBK before the text is returned, so a caller's write-back is a
    verified migration rather than a guess. Raises TextDecodeError (a
    ValueError) when the file is neither.
    """
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        text = raw.decode("gbk")
    except UnicodeDecodeError:
        raise TextDecodeError(
            f"{path}: not valid UTF-8 or GBK — refusing to read"
        ) from None
    if text.encode("gbk") != raw:
        raise TextDecodeError(
            f"{path}: bytes decode as GBK but do not round-trip — refusing to read"
        ) from None
    return text


def detect_newline(raw: bytes) -> str:
    """The line ending a file already uses: "\r\n" if its first line break is
    CRLF, otherwise "\n" (also for a file with no line break)."""
    i = raw.find(b"\n")
    return "\r\n" if i > 0 and raw[i - 1:i] == b"\r" else "\n"


def write_text(path, text: str, newline: "str | None" = None) -> None:
    """Write `text` as UTF-8 keeping the file's existing line ending.

    pathlib's write_text translates "\n" to os.linesep, so on Windows every
    read-modify-write turned an LF campaign file into CRLF, and a raw read
    paired with a text-mode write added one more "\r" per line per pass (the
    narrator-voice bug in state.md). Here the text is normalized to "\n" and
    written back with the line ending the file had — "\n" for a new file —
    unless `newline` names one. Bytes are written, so nothing translates.
    """
    path = pathlib.Path(path)
    if newline is None:
        try:
            newline = detect_newline(path.read_bytes())
        except OSError:
            newline = "\n"
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    if newline != "\n":
        body = body.replace("\n", newline)
    path.write_bytes(body.encode("utf-8"))
