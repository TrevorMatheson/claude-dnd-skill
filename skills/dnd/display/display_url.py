"""display_url.py — where the display companion is, and whether it's up, fast.

Two Windows delays measured after the table test, both on every request:

  * The server listens on 127.0.0.1 (IPv4) only. A client that connects to
    "localhost" tries ::1 first, and Windows takes ~2 s to give up on it, so
    every send.py / push_stats.py call paid ~2 s — a 5-block turn took 12.6 s.
    Clients connect to 127.0.0.1 directly (DND_DISPLAY_URL still overrides).
  * With the display not running, a refused connection takes ~2 s per address
    on Windows (it retries the SYN). `display_up()` connects with a short
    timeout first: a local server accepts in about a millisecond, so anything
    slower than 0.25 s means nobody is listening.
"""
import os
import socket
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 5001


def scheme() -> str:
    try:
        with open(os.path.join(_HERE, ".scheme"), encoding="utf-8") as f:
            return f.read().strip() or "http"
    except OSError:
        return "http"


def base_url() -> str:
    """The display's base URL, without a trailing slash."""
    env = os.environ.get("DND_DISPLAY_URL", "").strip().rstrip("/")
    return env or f"{scheme()}://127.0.0.1:{PORT}"


_UP_CACHE: dict = {}


def display_up(url: str = "", timeout: float = 0.25, cached: bool = True) -> bool:
    """True if something accepts connections at the display's host and port.
    Cached for the life of the process (each script call is one process);
    pass cached=False when waiting for a server to come up."""
    u = urllib.parse.urlsplit(url or base_url())
    host = u.hostname or "127.0.0.1"
    port = u.port or (443 if u.scheme == "https" else 80)
    key = (host, port)
    if not cached or key not in _UP_CACHE:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                _UP_CACHE[key] = True
        except OSError:
            _UP_CACHE[key] = False
    return _UP_CACHE[key]
