#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Summarise checkpatch.pl output.

Usage:
  checkpatch_summary.py [--json] --run <rev-range> [--repo DIR] [--extra-args "..."]
  checkpatch_summary.py [--json] --input FILE
  checkpatch_summary.py [--json] -            (read checkpatch output from stdin)

A missing Signed-off-by is expected until the human signs off: it is not
counted as an error and is reported as "pending_signoff" instead.
Exit: 0 nothing to fix, 1 errors/warnings/checks, 2 usage or run error.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys

import mlk_common

ITEM_RE = re.compile(r"^(ERROR|WARNING|CHECK):(?:([A-Z][A-Z0-9_]*):)?\s*(.*)$")
LOCATION_RE = re.compile(r"^#\d+: FILE: (.+?):?\s*$")
COUNT_KEYS = {"ERROR": "errors", "WARNING": "warnings", "CHECK": "checks"}


def _is_signoff(item_type: str | None, message: str) -> bool:
    return item_type == "MISSING_SIGN_OFF" or "Missing Signed-off-by" in message


def parse_checkpatch(text: str) -> dict:
    result = {"errors": 0, "warnings": 0, "checks": 0, "pending_signoff": False, "items": []}
    current = None
    for line in text.replace("\r\n", "\n").split("\n"):
        m = ITEM_RE.match(line)
        if m:
            level, item_type, message = m.group(1), m.group(2), m.group(3).strip()
            if _is_signoff(item_type, message):
                result["pending_signoff"] = True
                current = None
                continue
            current = {"level": level, "type": item_type, "message": message, "location": None}
            result["items"].append(current)
            result[COUNT_KEYS[level]] += 1
            continue
        if current is None:
            continue
        if not line.strip():
            current = None
            continue
        loc = LOCATION_RE.match(line)
        if loc and current["location"] is None:
            current["location"] = loc.group(1)
    return result


def run_checkpatch(repo: str, rev_range: str, extra_args: str) -> str:
    script = os.path.join(repo, "scripts", "checkpatch.pl")
    if not os.path.isfile(script):
        raise RuntimeError(f"{script} not found (is --repo a kernel checkout?)")
    cmd = ["perl", "scripts/checkpatch.pl", "--strict", "--show-types", "-g", rev_range]
    cmd += shlex.split(extra_args or "")
    try:
        res = subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RuntimeError(f"cannot run perl: {exc}") from exc
    output = res.stdout + res.stderr
    if res.returncode != 0 and not ITEM_RE.search(output) and "total:" not in output:
        raise RuntimeError(f"checkpatch.pl failed (exit {res.returncode}):\n{output.strip()}")
    return output


def human(payload: dict) -> str:
    out = [f"checkpatch: {payload['errors']} errors, {payload['warnings']} warnings, "
           f"{payload['checks']} checks"]
    for item in payload["items"]:
        kind = f":{item['type']}" if item["type"] else ""
        where = f" ({item['location']})" if item["location"] else ""
        out.append(f"  {item['level']}{kind}: {item['message']}{where}")
    if payload["pending_signoff"]:
        out.append("Signed-off-by missing: expected until the human signs off "
                   "(git rebase --signoff, run by the human). Not counted as an error.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Summarise checkpatch.pl output.")
    ap.add_argument("stdin", nargs="?", choices=["-"], help="read checkpatch output from stdin")
    ap.add_argument("--run", metavar="REV_RANGE", help="run checkpatch.pl -g on this range")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--extra-args", default="", help="extra checkpatch.pl arguments")
    ap.add_argument("--input", metavar="FILE", help="parse a saved checkpatch output")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if sum(x is not None for x in (args.stdin, args.run, args.input)) != 1:
        ap.error("give exactly one of --run REV_RANGE, --input FILE or -")
    try:
        if args.run:
            text = run_checkpatch(mlk_common.repo_root(args.repo), args.run, args.extra_args)
        elif args.input and args.input != "-":
            with open(args.input, encoding="utf-8", errors="replace") as f:
                text = f.read()
        else:
            text = sys.stdin.read()
    except (OSError, RuntimeError, mlk_common.GitError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return mlk_common.EXIT_USAGE
    payload = parse_checkpatch(text)
    mlk_common.emit(payload, args.json, human)
    total = payload["errors"] + payload["warnings"] + payload["checks"]
    return mlk_common.EXIT_FINDINGS if total else mlk_common.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
