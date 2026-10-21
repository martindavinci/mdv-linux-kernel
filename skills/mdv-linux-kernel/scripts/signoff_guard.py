#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Guard against Signed-off-by lines the agent must never add.

  signoff_guard.py snapshot <rev-range> [--repo DIR]
  signoff_guard.py check <rev-range> [--repo DIR] [--json]

'snapshot' records the Signed-off-by values present before the agent starts
editing; 'check' flags any SoB beyond that set (only the human may add one)
and any SoB / Co-developed-by that looks like an AI identity.
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


def snapshot_path(repo: str) -> str:
    return os.path.join(mlk_common.state_dir(repo), SNAPSHOT_NAME)


def take_snapshot(repo: str, rev_range: str) -> dict:
    sobs: list[str] = []
    for sha in mlk_common.commits_in_range(repo, rev_range):
        sobs.extend(sob_values(mlk_common.commit_message(repo, sha)))
    data = {"created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "range": rev_range, "sob": sorted(sobs)}
    with open(snapshot_path(repo), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return data


def check(repo: str, rev_range: str) -> list[Finding]:
    findings: list[Finding] = []
    path = snapshot_path(repo)
    allowed: Counter | None = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            allowed = Counter(json.load(f).get("sob", []))
    else:
        findings.append(Finding(
            "NO_SNAPSHOT", "warn",
            "no sign-off snapshot: run 'signoff_guard.py snapshot <base>..HEAD' before "
            "editing commits, so added Signed-off-by lines can be detected"))
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
        for value in sob_values(message):
            if allowed[value] > 0:
                allowed[value] -= 1
            else:
                findings.append(Finding(
                    "SOB_ADDED", "error",
                    "Signed-off-by added after the agent started; only the human may add it "
                    "(git rebase --signoff, run by the human)",
                    commit=sha, detail=f"Signed-off-by: {value}"))
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
        if name == "check":
            p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        repo = mlk_common.repo_root(args.repo)
        if args.command == "snapshot":
            data = take_snapshot(repo, args.rev_range)
            print(f"sign-off snapshot of {args.rev_range}: {len(data['sob'])} Signed-off-by "
                  f"line(s) recorded in {snapshot_path(repo)}")
            return mlk_common.EXIT_OK
        findings = check(repo, args.rev_range)
    except (mlk_common.GitError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return mlk_common.EXIT_USAGE
    payload = {"range": args.rev_range, "findings": [f.to_dict() for f in findings]}
    mlk_common.emit(payload, args.json, human)
    return mlk_common.exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
