#!/usr/bin/env python3
"""
start_display.py — start / stop / check the display companion on any platform.

start-display.sh needs bash, `python3`, `ipconfig getifaddr` and `pkill`; on
Windows none of those can be relied on. This does the same with the running
interpreter (sys.executable), so `python`, `py` or `python3` all work:

    python start_display.py            # localhost only
    python start_display.py --lan      # TV and phones on the home network
    python start_display.py stop
    python start_display.py status

LAN address: the adapter that routes to the internet (a UDP "connect" sends no
packet), skipping VMware / VirtualBox / Hyper-V / WSL and link-local ones —
the test machine's first adapter was a VMware network the phones couldn't see.
TLS mode stays in start-display.sh (it needs openssl).
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "dnd-display-app.py")
PID_FILE = os.path.join(HERE, "app.pid")
LOG_FILE = os.path.join(HERE, "app.log")
PORT = 5001

# Virtual-adapter address ranges that phones on the home Wi-Fi can't reach.
_VIRTUAL_HINTS = ("vmware", "virtualbox", "vbox", "hyper-v", "vethernet", "wsl", "docker",
                  "loopback", "bluetooth", "tailscale", "zerotier")


def _usable(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and a.is_private and not a.is_loopback and not a.is_link_local


def _route_ip() -> str | None:
    """The address the OS would use to reach the internet. No packet is sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _windows_adapters() -> list:
    """[(adapter name, ipv4)] from `ipconfig`, in order."""
    try:
        out = subprocess.run(["ipconfig"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    res, name = [], ""
    for line in out.splitlines():
        if line and not line.startswith(" ") and line.rstrip().endswith(":"):
            name = line.strip().rstrip(":")
        elif "IPv4" in line and ":" in line:
            ip = line.split(":")[-1].strip().split("(")[0].strip()
            res.append((name, ip))
    return res


def lan_ip() -> str | None:
    """Best LAN address for phones and the TV to reach this machine."""
    candidates = []
    if os.name == "nt":
        for name, ip in _windows_adapters():
            if _usable(ip) and not any(h in name.lower() for h in _VIRTUAL_HINTS):
                candidates.append(ip)
    routed = _route_ip()
    if routed and _usable(routed):
        if not candidates or routed in candidates:
            return routed
    if candidates:
        return candidates[0]
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if _usable(ip):
                return ip
    except OSError:
        pass
    return routed if routed and _usable(routed) else None


def _ping(base: str, timeout: float = 1.0) -> bool:
    try:
        urllib.request.urlopen(f"{base}/ping", timeout=timeout)
        return True
    except Exception:
        return False


def _stop() -> bool:
    try:
        pid = int(open(PID_FILE, encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return False
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    try:
        os.remove(PID_FILE)
    except OSError:
        pass
    return True


def _deps_ok() -> bool:
    try:
        import flask  # noqa: F401
        import flask_cors  # noqa: F401
        return True
    except ImportError:
        return False


def start(lan: bool) -> int:
    if not _deps_ok():
        req = os.path.join(HERE, "requirements.txt")
        print("The display needs its Python packages (one-time install):")
        print(f'    "{sys.executable}" -m pip install -r "{req}"')
        print("Core gameplay works without the display.")
        return 1
    _stop()
    with open(os.path.join(HERE, ".scheme"), "w", encoding="utf-8") as f:
        f.write("http")
    env = dict(os.environ, PYTHONUTF8="1")
    args = [sys.executable, APP] + (["--lan"] if lan else [])
    log = open(LOG_FILE, "w", encoding="utf-8")
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
                               | subprocess.CREATE_NO_WINDOW)
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            env=env, cwd=HERE, **kw)
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(proc.pid))
    local = f"http://localhost:{PORT}"
    for _ in range(30):
        time.sleep(0.5)
        if _ping(local):
            break
        if proc.poll() is not None:
            print(f"The display stopped right away. Last lines of {LOG_FILE}:")
            try:
                print("".join(open(LOG_FILE, encoding="utf-8", errors="replace").readlines()[-15:]))
            except OSError:
                pass
            return 1
    else:
        print(f"The display did not answer within 15 s - see {LOG_FILE}")
        return 1
    print(f"Display running - TV / this computer: {local}")
    if lan:
        ip = lan_ip()
        if ip:
            print(f"  On the TV (same network):   http://{ip}:{PORT}")
            print(f"  On each phone:              http://{ip}:{PORT}/?char=<character name>")
            if not _ping(f"http://{ip}:{PORT}", timeout=2):
                print("  ! That address doesn't answer from this machine - check the firewall "
                      "(`python doctor.py` in scripts/ explains the rule).")
        else:
            print("  ! No LAN address found - is this computer on Wi-Fi or Ethernet?")
    return 0


def status() -> int:
    up = _ping(f"http://localhost:{PORT}")
    print(f"Display: {'running' if up else 'not running'} (localhost:{PORT})")
    if up:
        ip = lan_ip()
        if ip:
            print(f"  LAN: http://{ip}:{PORT} - {'answers' if _ping(f'http://{ip}:{PORT}', 2) else 'no answer'}")
    return 0 if up else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Start, stop or check the display companion.")
    p.add_argument("action", nargs="?", default="start", choices=["start", "stop", "status"])
    p.add_argument("--lan", action="store_true", help="let the TV and phones on this network connect")
    a = p.parse_args()
    if a.action == "stop":
        print("Display stopped." if _stop() else "Display was not running (no app.pid).")
        return 0
    if a.action == "status":
        return status()
    return start(a.lan)


if __name__ == "__main__":
    sys.exit(main())
