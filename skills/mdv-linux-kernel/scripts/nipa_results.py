#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Read the results of a local netdev CI (NIPA) run.

Usage: nipa_results.py RESULT_DIR [--series-id N] [--json]

`ingest_mdir.py` (https://github.com/linux-netdev/nipa) exits 0 even when
tests fail, so its exit code proves nothing. This script reads what it
wrote under --result-dir: <series-id>/<test>/ (series tests) and
<series-id>/<patch-id>/<test>/ (patch tests), each with a `retcode` file:
0 OKAY, 111 PENDING, 250 WARNING, anything else (or no retcode) FAILED.
`apply/` exists only when the series did not apply; `.tester_done` marks a
finished run. The latest series id is used unless --series-id is given.

Record it as evidence in the same command as the run, e.g.
  evidence.py run --name nipa-ci --category test --results none -- sh -c \
    './docker/build/run.sh --pull ./ingest_mdir.py --mdir M --tree T \
     --result-dir R --noninteractive && python3 <skill-dir>/scripts/nipa_results.py R'

Exit: 0 every test OKAY, 1 any warning, failure, pending or incomplete run,
2 usage error (no results found).
"""
from __future__ import annotations

import argparse
import os
import sys

import mlk_common
from mlk_common import Finding

STATUS_BY_RETCODE = {0: "OKAY", 111: "PENDING", 250: "WARNING"}
SKIP_DIRS = {"tree_selection", "apply"}


def read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def test_result(path: str, name: str, patch: int | None) -> dict:
    raw = read_text(os.path.join(path, "retcode"))
    try:
        retcode = int(raw.strip()) if raw is not None else None
    except ValueError:
        retcode = None
    status = STATUS_BY_RETCODE.get(retcode, "FAILED") if retcode is not None else "FAILED"
    desc = (read_text(os.path.join(path, "desc")) or "").strip()
    return {"test": name, "patch": patch, "retcode": retcode, "status": status,
            "desc": desc, "dir": path}


def latest_series(result_dir: str) -> str | None:
    ids = [int(n) for n in os.listdir(result_dir)
           if n.isdigit() and os.path.isdir(os.path.join(result_dir, n))]
    return str(max(ids)) if ids else None


def collect(series_dir: str) -> tuple[list[dict], dict | None]:
    """(test results, apply result or None). Patch ids are numbered 1..n in id order."""
    results: list[dict] = []
    names = sorted(os.listdir(series_dir))
    patch_ids = sorted((n for n in names
                        if n.isdigit() and os.path.isdir(os.path.join(series_dir, n))), key=int)
    for name in names:
        path = os.path.join(series_dir, name)
        if os.path.isdir(path) and not name.isdigit() and name not in SKIP_DIRS:
            results.append(test_result(path, name, None))
    for number, pid in enumerate(patch_ids, start=1):
        pdir = os.path.join(series_dir, pid)
        for name in sorted(os.listdir(pdir)):
            if os.path.isdir(os.path.join(pdir, name)):
                results.append(test_result(os.path.join(pdir, name), name, number))
    apply_dir = os.path.join(series_dir, "apply")
    applied = test_result(apply_dir, "apply", None) if os.path.isdir(apply_dir) else None
    return results, applied


def findings_for(results: list[dict], applied: dict | None, done: bool) -> list[Finding]:
    findings: list[Finding] = []
    if applied and applied["retcode"] != 0:
        findings.append(Finding("APPLY_FAILED", "error",
                                "the series did not apply to the tree", detail=applied["desc"]))
    if not done:
        findings.append(Finding("INCOMPLETE", "error",
                                "no .tester_done: the run did not finish"))
    if not results and not (applied and applied["retcode"] != 0):
        findings.append(Finding("NO_TESTS", "error", "no test results in the series directory"))
    codes = {"WARNING": ("TEST_WARNING", "warn"), "FAILED": ("TEST_FAILED", "error"),
             "PENDING": ("TEST_PENDING", "warn")}
    for r in results:
        if r["status"] in codes:
            code, severity = codes[r["status"]]
            where = f"patch {r['patch']}" if r["patch"] else "series"
            findings.append(Finding(code, severity, f"{r['test']} ({where}): {r['status']}",
                                    detail=r["desc"] or r["dir"]))
    return findings


def human(payload: dict) -> str:
    lines = [f"nipa_results: series {payload['series_id']} in {payload['result_dir']}"]
    for r in payload["results"]:
        where = f"patch {r['patch']}" if r["patch"] else "series"
        desc = f" - {r['desc']}" if r["desc"] else ""
        lines.append(f"  {r['status']:8} {r['test']} [{where}]{desc}")
    lines.append(mlk_common.format_findings(payload["findings"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read local netdev CI (NIPA) results.")
    ap.add_argument("result_dir")
    ap.add_argument("--series-id")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.result_dir):
        print(f"error: {args.result_dir} is not a directory", file=sys.stderr)
        return mlk_common.EXIT_USAGE
    series_id = args.series_id or latest_series(args.result_dir)
    series_dir = os.path.join(args.result_dir, series_id) if series_id else None
    if not series_dir or not os.path.isdir(series_dir):
        print(f"error: no NIPA series results in {args.result_dir}", file=sys.stderr)
        return mlk_common.EXIT_USAGE
    results, applied = collect(series_dir)
    done = os.path.exists(os.path.join(series_dir, ".tester_done"))
    findings = findings_for(results, applied, done)
    payload = {"result_dir": args.result_dir, "series_id": series_id, "done": done,
               "applied": applied is None or applied["retcode"] == 0,
               "results": results, "findings": [f.to_dict() for f in findings]}
    mlk_common.emit(payload, args.json, human)
    return mlk_common.exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
