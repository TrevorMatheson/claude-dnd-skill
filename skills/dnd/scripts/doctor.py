#!/usr/bin/env python3
"""
doctor.py — the pre-game table check (`/dm:dnd doctor`).

Everything that went wrong in the first live test, checked in one go before
the players sit down:

  Python version, Flask / flask-cors / PyYAML, the SRD datasets (2014 bundled,
  2024 built — with spell and feature counts), the voice key and whether the
  running display can use it, the display on localhost and on the LAN address
  phones will use, the Windows firewall rule for port 5001, and the `claude`
  CLI that DM Help calls.

    python doctor.py              # all checks
    python doctor.py --deep       # also ask the claude CLI for a one-word reply
                                  # (proves it is signed in; uses a few tokens)
    python doctor.py --json

Nothing here changes a setting: a missing firewall rule is reported with the
command to add it, for the user to run in an administrator terminal.
Exit status 0 = ready, 1 = something to fix.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "display"))

OK, WARN, FAIL = "ok", "warn", "fail"
_MARK = {OK: "✓", WARN: "!", FAIL: "✗"}


def _check(results, name, status, detail, fix=""):
    results.append({"check": name, "status": status, "detail": detail, "fix": fix})


def _get_json(url: str, timeout: float = 2.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except Exception:
        return None


def check_python(results):
    v = sys.version_info
    st = OK if v >= (3, 9) else FAIL
    _check(results, "Python", st, f"{v.major}.{v.minor}.{v.micro} at {sys.executable}",
           "" if st == OK else "Install Python 3.9 or newer")


def check_packages(results):
    req = HERE.parent / "display" / "requirements.txt"
    for mod, pip_name, needed_for, level in (("flask", "flask", "the TV display", FAIL),
                                             ("flask_cors", "flask-cors", "the TV display", FAIL),
                                             ("yaml", "pyyaml", "building the 2024 dataset", WARN)):
        try:
            __import__(mod)
        except ImportError:
            _check(results, f"package {pip_name}", level, f"missing — needed for {needed_for}",
                   f'"{sys.executable}" -m pip install -r "{req}"')
            continue
        try:
            from importlib.metadata import version
            ver = version(pip_name)
        except Exception:
            ver = "installed"
        _check(results, f"package {pip_name}", OK, ver)


def check_datasets(results):
    import paths
    for rs in ("2014", "2024"):
        p = paths.srd_path(rs)
        if not p.exists():
            _check(results, f"SRD {rs}", WARN if rs == "2024" else FAIL, f"not found ({p})",
                   f'"{sys.executable}" "{HERE / "build_srd.py"}" --ruleset {rs}')
            continue
        try:
            counts = json.loads(p.read_text(encoding="utf-8")).get("_meta", {}).get("record_counts", {})
        except (OSError, ValueError) as exc:
            _check(results, f"SRD {rs}", FAIL, f"unreadable: {exc}")
            continue
        spells, feats = counts.get("spells", 0), counts.get("features", 0)
        where = "built copy" if paths.built_data_dir() in p.parents else "bundled"
        st = OK if spells and feats else FAIL
        _check(results, f"SRD {rs}", st, f"{spells} spells, {feats} features, "
               f"{counts.get('monsters', 0)} monsters ({where}: {p})",
               "" if st == OK else f'rebuild: "{sys.executable}" "{HERE / "build_srd.py"}" --ruleset {rs}')


def check_tts(results, health):
    try:
        import tts
        src = tts.key_source()
    except Exception as exc:
        _check(results, "voice key", WARN, f"tts module failed to load: {exc}")
        return
    if src == "unset":
        _check(results, "voice key", WARN,
               "no Gemini key — narration uses the browser's own voice",
               f"put the key in {tts.KEY_FILE} (or set GEMINI_API_KEY) for the better voices")
    else:
        _check(results, "voice key", OK, f"found ({src.split(':')[0]})")
    if health is not None and "tts_key" in health:
        if src != "unset" and not health["tts_key"]:
            _check(results, "display voice", WARN,
                   "the running display can't see the key (started with a different user or env)",
                   "restart the display from the same terminal")
        else:
            _check(results, "display voice", OK,
                   "the running display can use the key" if health["tts_key"] else "browser voice")


def check_display(results):
    health = _get_json("http://localhost:5001/health")
    if health is None:
        _check(results, "display (localhost)", WARN, "not running",
               f'"{sys.executable}" "{HERE.parent / "display" / "start_display.py"}" --lan')
        return None
    _check(results, "display (localhost)", OK,
           f"running — campaign {health.get('campaign') or '(none)'}, {health.get('clients', 0)} screen(s) connected")
    try:
        import start_display
        ip = start_display.lan_ip()
    except Exception:
        ip = None
    if not ip:
        _check(results, "display (LAN)", WARN, "no LAN address found", "connect to Wi-Fi or Ethernet")
    elif _get_json(f"http://{ip}:5001/health") is None:
        _check(results, "display (LAN)", WARN,
               f"http://{ip}:5001 doesn't answer — started without --lan, or blocked",
               "restart with start_display.py --lan; see the firewall check")
    else:
        _check(results, "display (LAN)", OK, f"phones use http://{ip}:5001/?char=<name>")
    return health


def check_firewall(results):
    if os.name != "nt":
        return
    try:
        out = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", "name=all", "dir=in",
                              "verbose"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        _check(results, "firewall 5001", WARN, "couldn't read the firewall rules")
        return
    blocks = [b for b in out.replace("\r\n", "\n").split("\n\n") if b.strip()]
    allow = [b for b in blocks if any(w in b for w in ("Allow", "Zulassen", "Autoriser", "Permitir"))]
    exe = os.path.normcase(os.path.realpath(sys.executable))

    def _program(block: str) -> str:
        for line in block.splitlines():
            key, sep, val = line.partition(":")
            if sep and key.strip().lower() in ("program", "programm", "programme", "programa"):
                return os.path.normcase(os.path.realpath(val.strip())) if val.strip() else ""
        return ""

    if any("5001" in b for b in allow):
        _check(results, "firewall 5001", OK, "an inbound rule allows port 5001")
    elif any(_program(b) == exe for b in allow):
        _check(results, "firewall 5001", OK, f"an inbound rule allows {sys.executable} on all ports")
    else:
        _check(results, "firewall 5001", WARN,
               "no inbound rule for port 5001 found — phones and the TV may be blocked",
               'in an administrator terminal: netsh advfirewall firewall add rule name="DnD display" '
               "dir=in action=allow protocol=TCP localport=5001 profile=private")


def check_claude(results, deep: bool):
    exe = shutil.which("claude")
    if not exe:
        _check(results, "claude CLI", WARN, "not on PATH — DM Help won't work",
               "install Claude Code and run `claude` once to sign in")
        return
    try:
        v = subprocess.run([exe, "--version"], capture_output=True, text=True, encoding="utf-8",
                           timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        v = "?"
    if not deep:
        _check(results, "claude CLI", OK, f"{v} (run with --deep to confirm it is signed in)")
        return
    try:
        r = subprocess.run([exe, "-p", "Reply with the single word OK."], capture_output=True,
                           text=True, encoding="utf-8", timeout=90)
        good = r.returncode == 0 and "ok" in r.stdout.lower()
    except (OSError, subprocess.SubprocessError):
        good, r = False, None
    _check(results, "claude CLI", OK if good else FAIL,
           f"{v} — {'signed in, answered' if good else 'did not answer'}",
           "" if good else "run `claude` in a terminal, then /login")


def run(deep: bool = False) -> list:
    results: list = []
    check_python(results)
    check_packages(results)
    check_datasets(results)
    health = check_display(results)
    check_tts(results, health)
    check_firewall(results)
    check_claude(results, deep)
    return results


def main() -> int:
    p = argparse.ArgumentParser(description="Check the table setup before a session.")
    p.add_argument("--deep", action="store_true", help="also confirm the claude CLI is signed in")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    results = run(a.deep)
    if a.json:
        print(json.dumps(results, indent=1))
    else:
        for r in results:
            print(f" {_MARK[r['status']]} {r['check']:<20} {r['detail']}")
            if r["fix"]:
                print(f"   {'':<20} → {r['fix']}")
        n_fail = sum(r["status"] == FAIL for r in results)
        n_warn = sum(r["status"] == WARN for r in results)
        print()
        print("Ready to play." if not n_fail and not n_warn else
              f"{n_fail} to fix, {n_warn} to look at.")
    return 1 if any(r["status"] == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
