#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Guard against Signed-off-by lines the agent must never add.

  signoff_guard.py snapshot <rev-range> [--repo DIR] [--series NAME]
  signoff_guard.py check <rev-range> [--repo DIR] [--series NAME] [--json]

'snapshot' records the Signed-off-by values present before the agent starts
editing, for the series and per patch (by subject). 'check' flags:
  SOB_ADDED (error)      more SoB lines than the snapshot, or a SoB on a
                         snapshot patch that it did not have (only the human
                         may add one);
  SOB_UNVERIFIED (warn)  a patch not in the snapshot carries a SoB: the human
                         must confirm they added it;
  AI_IDENTITY (error)    a SoB / Co-developed-by that looks like an AI;
  NO_SNAPSHOT (warn).
Snapshots from older versions (series list only) get the series check only.
The snapshot is per series (<state dir>/series/<series>/), like the evidence
store: --series, else $MLK_SERIES, else the current branch.
Exit: 0 no findings, 1 findings, 2 usage or git error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

import mlk_common
from mlk_common import Finding

SNAPSHOT_NAME = "signoff-snapshot.json"
AI_MARKERS = ["claude", "anthropic", "openai", "chatgpt", "codex", "gpt-", "gemini",
              "copilot", "llm", "ai assistant", "noreply@anthropic.com"]


def _marker_re(marker: str) -> re.Pattern:
    # Whole-word match where the marker starts/ends with a letter or digit, so
    # human names such as "Allman" do not match "llm".
    left = r"(?<![a-z0-9])" if marker[0].isalnum() else ""
    right = r"(?![a-z0-9])" if marker[-1].isalnum() else ""
    return re.compile(left + re.escape(marker) + right)


AI_RES = [_marker_re(m) for m in AI_MARKERS]


def sob_values(message: str) -> list[str]:
    return [" ".join(value.split()) for key, value in mlk_common.trailers(message)
            if key.lower() == "signed-off-by"]


def looks_like_ai_identity(value: str) -> bool:
    lowered = value.lower()
    return any(r.search(lowered) for r in AI_RES)


def snapshot_path(repo: str, series: str | None = None) -> str:
    return os.path.join(mlk_common.series_dir(repo, series), SNAPSHOT_NAME)


def take_snapshot(repo: str, rev_range: str, series: str | None = None) -> dict:
    sobs: list[str] = []
    commits = []
    for sha in mlk_common.commits_in_range(repo, rev_range):
        message = mlk_common.commit_message(repo, sha)
        values = sob_values(message)
        sobs.extend(values)
        commits.append({"subject": mlk_common.split_message(message)[0], "sob": values})
    data = {"created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "range": rev_range, "sob": sorted(sobs), "commits": commits}
    with open(snapshot_path(repo, series), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return data


def load_snapshot(repo: str, series: str | None = None) -> dict | None:
    path = snapshot_path(repo, series)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _sob_added(sha: str, value: str, why: str) -> Finding:
    return Finding("SOB_ADDED", "error",
                   f"Signed-off-by {why}; only the human may add it "
                   "(git rebase --signoff, run by the human)",
                   commit=sha, detail=f"Signed-off-by: {value}")


def check(repo: str, rev_range: str, series: str | None = None) -> list[Finding]:
    findings: list[Finding] = []
    snap = load_snapshot(repo, series)
    allowed: Counter | None = None
    by_subject: dict[str, list[dict]] | None = None
    if snap is None:
        findings.append(Finding(
            "NO_SNAPSHOT", "warn",
            "no sign-off snapshot: run 'signoff_guard.py snapshot <base>..HEAD' before "
            "editing commits, so added Signed-off-by lines can be detected"))
    else:
        allowed = Counter(snap.get("sob", []))
        if "commits" in snap:  # older snapshots only have the series multiset
            by_subject = {}
            for record in snap["commits"]:
                by_subject.setdefault(record["subject"], []).append(record)
    for sha in mlk_common.commits_in_range(repo, rev_range):
        message = mlk_common.commit_message(repo, sha)
        for key, value in mlk_common.trailers(message):
            if key.lower() in ("signed-off-by", "co-developed-by") and looks_like_ai_identity(value):
                findings.append(Finding(
                    "AI_IDENTITY", "error",
                    f"{key} must name a human; AI tools are disclosed, never signed off",
                    commit=sha, detail=f"{key}: {value}"))
        if allowed is None:
            continue
        values = sob_values(message)
        flagged: Counter = Counter()
        # Series-wide: more SoB lines than the snapshot had.
        for value in values:
            if allowed[value] > 0:
                allowed[value] -= 1
            else:
                flagged[value] += 1
                findings.append(_sob_added(sha, value, "added after the agent started"))
        if by_subject is None:
            continue
        # Per patch: a SoB moved between patches keeps the series count unchanged.
        records = by_subject.get(mlk_common.split_message(message)[0])
        remaining = Counter(values) - flagged
        if records:
            record = records.pop(0)
            for value in (remaining - Counter(record["sob"])).elements():
                findings.append(_sob_added(sha, value, "not present on this patch in the snapshot"))
        elif remaining:
            findings.append(Finding(
                "SOB_UNVERIFIED", "warn",
                "commit not in snapshot carries Signed-off-by; human must confirm they added it",
                commit=sha, detail="; ".join(f"Signed-off-by: {v}" for v in remaining.elements())))
    return findings


def human(payload: dict) -> str:
    return f"signoff_guard: {payload['range']}\n" + mlk_common.format_findings(payload["findings"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Detect Signed-off-by lines not added by the human.")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "check"):
        p = sub.add_parser(name)
        p.add_argument("rev_range")
        p.add_argument("--repo", default=".")
        p.add_argument("--series", help="series name (default: $MLK_SERIES, else the branch)")
        if name == "check":
            p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        repo = mlk_common.repo_root(args.repo)
        if args.command == "snapshot":
            data = take_snapshot(repo, args.rev_range, args.series)
            print(f"sign-off snapshot of {args.rev_range}: {len(data['sob'])} Signed-off-by "
                  f"line(s) recorded in {snapshot_path(repo, args.series)}")
            return mlk_common.EXIT_OK
        findings = check(repo, args.rev_range, args.series)
    except (mlk_common.GitError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return mlk_common.EXIT_USAGE
    payload = {"range": args.rev_range, "findings": [f.to_dict() for f in findings]}
    mlk_common.emit(payload, args.json, human)
    return mlk_common.exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
