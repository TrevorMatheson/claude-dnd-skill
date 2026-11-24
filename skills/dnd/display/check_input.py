#!/usr/bin/env python3
"""
check_input.py — Non-blocking check for queued player input.

Prints any pending player input from the display panel to stdout, then clears
the queue. If the queue is empty, prints nothing and exits 0.

Called by the DM at the start of each turn when the display companion is running:
  python3 ${CLAUDE_SKILL_DIR}/display/check_input.py

Output format (when non-empty):
  [CharName]: action text
  [CharName2]: action text

One line per character. Same format as autorun_wait.py output.
"""
import os
import sys

# Windows UTF-8 fix: default piped stdout is cp936/GBK, which mangles Chinese
# action text before the harness can read it (same class of bug as the /voice
# patch). Force UTF-8 so queue output survives. No-op on non-Windows/consoles.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from runtime_paths import rt          # writable runtime dir (update-safe)
QUEUE_FILE = rt(".input_queue")
NARRATION_TARGET = rt("narration_target")  # set by the display's Narration slider
ROLL_PREFS = rt("roll_prefs.json")         # per-character roll overrides (Settings → Rolls)


def _narration_directive():
    """A bracketed length directive the DM honors this turn, or '' if unset."""
    try:
        if os.path.exists(NARRATION_TARGET):
            n = open(NARRATION_TARGET, encoding="utf-8").read().strip()
            if n.isdigit() and int(n) > 0:
                return (f"[[Narration length for this turn: aim for ~{n} words. "
                        f"The table set this — keep it concise; do not pad.]]")
    except Exception:
        pass
    return ""


def _roll_directives():
    """One [[<Char> roll mode: …]] line per per-character override, or '' if none."""
    try:
        if os.path.exists(ROLL_PREFS):
            import json
            with open(ROLL_PREFS, encoding="utf-8") as f:
                prefs = json.load(f)
            lines = [f"[[{c} roll mode: {m}]]" for c, m in prefs.items()
                     if m in ("auto", "players")]
            return "\n".join(lines)
    except Exception:
        pass
    return ""


def _mark_consumed():
    """Tell the display the DM has the queued moves and rolls, so every screen
    stops showing them as waiting (autorun_wait.py does the same)."""
    try:
        import ssl
        import urllib.request
        here = os.path.dirname(os.path.abspath(__file__))
        scheme_file = os.path.join(here, ".scheme")
        scheme = open(scheme_file, encoding="utf-8").read().strip() if os.path.exists(scheme_file) else "http"
        tok_file = rt(".token")
        token = open(tok_file, encoding="utf-8").read().strip() if os.path.exists(tok_file) else ""
        ctx = None
        if scheme == "https":
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        base = os.environ.get("DND_DISPLAY_URL", "").strip().rstrip("/") or f"{scheme}://127.0.0.1:5001"
        req = urllib.request.Request(base + "/queue/consumed", data=b"", method="POST",
                                     headers={"X-DND-Token": token})
        urllib.request.urlopen(req, timeout=1, context=ctx)
    except Exception:
        pass


try:
    if os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE, encoding="utf-8") as f:
            content = f.read().strip()
        os.remove(QUEUE_FILE)
        if content:
            try:
                import rewind
                note = rewind.take_note(os.path.dirname(QUEUE_FILE))
                if note:
                    print(note)
            except Exception:
                pass
            try:
                import sheet_notes      # players' hand edits (phone sheet D9)
                for ln in sheet_notes.take_lines(os.path.dirname(QUEUE_FILE)):
                    print(ln)
            except Exception:
                pass
            for d in (_roll_directives(), _narration_directive()):
                if d:
                    print(d)
            print(content)
            _mark_consumed()
except Exception:
    pass
