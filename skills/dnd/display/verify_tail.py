#!/usr/bin/env python3
"""verify_tail.py <campaign-name> — is the campaign's session_tail.json safe to replay?

Exit 0 = healthy, 1 = missing / too small / unparseable / wrong shape, 2 = usage.

Replaces verify_tail.sh for /dm:dnd save. The shell version ran its check
through `python3`, which on Windows is often the Microsoft Store stub, so it
failed every time and every save "repaired" a healthy tail; it also looked in
$DND_CAMPAIGN_ROOT/<name> instead of $DND_CAMPAIGN_ROOT/campaigns/<name>.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "scripts"))
from paths import find_campaign  # noqa: E402


def check(campaign: str) -> tuple:
    path = find_campaign(campaign) / "session_tail.json"
    if not path.is_file():
        return 1, f"{path} — MISSING"
    size = path.stat().st_size
    if size < 50:
        return 1, f"{path} — TOO SMALL ({size} bytes; likely [] or near-empty)"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return 1, f"{path} — UNPARSEABLE: {exc}"
    if not isinstance(data, list):
        return 1, f"{path} — NOT A LIST"
    if not data:
        return 1, f"{path} — EMPTY LIST"
    if not all(isinstance(e, dict) and any(k in e for k in ("text", "player", "npc", "dice")) for e in data):
        return 1, f"{path} — entries not in expected shape"
    return 0, f"{path} — HEALTHY ({len(data)} entries)"


def main() -> int:
    if len(sys.argv) != 2:
        print("verify_tail.py: usage: verify_tail.py <campaign-name>", file=sys.stderr)
        return 2
    rc, msg = check(sys.argv[1])
    print(f"verify_tail: {msg}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
