"""cli_strict.py — reject options a hand-parsed CLI does not know.

Scripts that scan argv with `"--flag" in args` silently ignore anything they
do not look for, so a misspelt or unsupported option (combat.py --advantage,
before it existed) changes the roll without a word. Call
`reject_unknown(args, known, prog)` before dispatch: any token that looks
like an option (`-x` / `--xyz`, not a negative number) and is not in `known`
exits with status 2 and names it.
"""
import re
import sys

_OPTION = re.compile(r"^--?[A-Za-z][\w-]*(=.*)?$")


def unknown_options(args, known) -> list:
    out = []
    for a in args:
        if _OPTION.match(a):
            name = a.split("=", 1)[0]
            if name not in known:
                out.append(name)
    return out


def reject_unknown(args, known, prog: str) -> None:
    bad = unknown_options(args, known)
    if bad:
        print(f"{prog}: unrecognized option{'s' if len(bad) > 1 else ''}: {' '.join(bad)}"
              f"\n  known: {' '.join(sorted(known))}", file=sys.stderr)
        sys.exit(2)
