#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Compare compiler diagnostics of a baseline build and a patched build.

Usage: build_warnings.py diff --baseline LOG --patched LOG [--json]

Diagnostics are keyed by (path, level, message, flag) and counted, so a
warning that only moved to another line is not new, while one more
occurrence of an existing warning is.
Exit: 0 no new diagnostics, 1 new diagnostics, 2 usage or read error.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter

import mlk_common

DIAG_RE = re.compile(
    r"^(?P<path>[^:\s]+):\d+(?::\d+)?: (?P<level>warning|error): (?P<msg>.*?)"
    r"(?: \[(?P<flag>-W[^\]]+)\])?$")


def parse_diagnostics(text: str) -> Counter:
    counter: Counter = Counter()
    for line in text.splitlines():
        m = DIAG_RE.match(line.rstrip())
        if not m:
            continue
        path = m.group("path")
        while path.startswith("./"):
            path = path[2:]
        counter[(path, m.group("level"), m.group("msg"), m.group("flag"))] += 1
    return counter


def new_diagnostics(base: Counter, patched: Counter) -> list[dict]:
    extra = patched - base
    return [{"path": path, "level": level, "message": msg, "flag": flag, "count": count}
            for (path, level, msg, flag), count in sorted(extra.items(), key=lambda kv: str(kv[0]))]


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def human(payload: dict) -> str:
    if not payload["new"]:
        return (f"No new diagnostics ({payload['baseline_total']} in baseline, "
                f"{payload['patched_total']} in patched build).")
    out = [f"{len(payload['new'])} new diagnostic(s):"]
    for d in payload["new"]:
        flag = f" [{d['flag']}]" if d["flag"] else ""
        times = f" (+{d['count']})" if d["count"] > 1 else ""
        out.append(f"  {d['path']}: {d['level']}: {d['message']}{flag}{times}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Diff compiler diagnostics between two build logs.")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("diff", help="report diagnostics present only in the patched build")
    p.add_argument("--baseline", required=True)
    p.add_argument("--patched", required=True)
    p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        base = parse_diagnostics(read(args.baseline))
        patched = parse_diagnostics(read(args.patched))
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return mlk_common.EXIT_USAGE
    new = new_diagnostics(base, patched)
    payload = {"baseline": args.baseline, "patched": args.patched,
               "baseline_total": sum(base.values()), "patched_total": sum(patched.values()),
               "new": new}
    mlk_common.emit(payload, args.json, human)
    return mlk_common.EXIT_FINDINGS if new else mlk_common.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
