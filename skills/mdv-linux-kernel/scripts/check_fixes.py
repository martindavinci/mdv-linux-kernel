#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Check the Fixes: tags of every commit in a revision range.

Usage: check_fixes.py <rev-range> [--repo DIR] [--json]
Exit: 0 no findings (info only), 1 findings, 2 usage or git error.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass

import mlk_common
from mlk_common import Finding

FIXES_RE = re.compile(r'^([0-9a-fA-F]{4,40})\s+\("(.*)"\)$')
BARE_SHA_RE = re.compile(r"^([0-9a-fA-F]{4,40})$")
MIN_SHA_LEN = 12


@dataclass
class FixesTag:
    sha: str
    title: str | None


def parse_fixes(value: str) -> FixesTag | None:
    """Parse '<sha> ("<title>")'. A bare sha gives title None; anything else None."""
    value = value.strip()
    m = FIXES_RE.match(value)
    if m:
        return FixesTag(m.group(1), m.group(2))
    m = BARE_SHA_RE.match(value)
    if m:
        return FixesTag(m.group(1), None)
    return None


def resolve(repo: str, sha: str) -> tuple[str | None, str | None]:
    """Return (full sha, None) or (None, 'unknown'|'ambiguous')."""
    res = mlk_common.run_git(["rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"], repo)
    if res.returncode == 0:
        return res.stdout.strip(), None
    # --quiet hides the reason; ask again to see whether the prefix is ambiguous.
    res = mlk_common.run_git(["rev-parse", "--verify", f"{sha}^{{commit}}"], repo)
    return None, "ambiguous" if "ambiguous" in res.stderr else "unknown"


def changed_files(repo: str, sha: str) -> set[str]:
    out = mlk_common.git(["diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha], repo)
    return set(out.split("\n")) - {""}


def check_commit(repo: str, sha: str) -> list[Finding]:
    findings: list[Finding] = []
    message = mlk_common.commit_message(repo, sha)
    for key, value in mlk_common.trailers(message):
        if key.lower() != "fixes":
            continue

        def add(code: str, severity: str, text: str) -> None:
            findings.append(Finding(code, severity, text, commit=sha, detail=f"Fixes: {value}"))

        tag = parse_fixes(value)
        if tag is None or tag.title is None:
            add("FIXES_FORMAT", "warn",
                'Fixes: must be \'<12+ hex sha> ("<subject of the fixed commit>")\'')
            if tag is None:
                continue
        if len(tag.sha) < MIN_SHA_LEN:
            add("FIXES_SHORT_SHA", "warn",
                f"sha has {len(tag.sha)} hex chars; use at least {MIN_SHA_LEN}")
        full, problem = resolve(repo, tag.sha)
        if problem == "ambiguous":
            add("FIXES_AMBIGUOUS_SHA", "error", f"sha {tag.sha} is ambiguous in this repository")
            continue
        if full is None:
            add("FIXES_UNKNOWN_SHA", "error",
                f"sha {tag.sha} is not a commit in this repository (fetch the target tree?)")
            continue
        fixed_subject = mlk_common.subject(repo, full)
        if tag.title is not None and tag.title.strip() != fixed_subject.strip():
            add("FIXES_TITLE_MISMATCH", "warn",
                f'title differs from the fixed commit subject: "{fixed_subject}"')
        if mlk_common.run_git(["merge-base", "--is-ancestor", full, sha], repo).returncode != 0:
            add("FIXES_NOT_ANCESTOR", "error",
                f"{full[:12]} is not an ancestor of the fixing commit")
        fixed_files = changed_files(repo, full)
        if fixed_files and not (fixed_files & changed_files(repo, sha)):
            add("FIXES_NO_FILE_OVERLAP", "info",
                "the fixing commit touches none of the files changed by the fixed commit; "
                "double-check the culprit")
    return findings


def human(payload: dict) -> str:
    head = f"check_fixes: {payload['commits']} commit(s) in {payload['range']}"
    return head + "\n" + mlk_common.format_findings(payload["findings"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check Fixes: tags in a revision range.")
    ap.add_argument("rev_range")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        repo = mlk_common.repo_root(args.repo)
        findings = []
        commits = mlk_common.commits_in_range(repo, args.rev_range)
        for sha in commits:
            findings.extend(check_commit(repo, sha))
    except mlk_common.GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return mlk_common.EXIT_USAGE
    payload = {"range": args.rev_range, "commits": len(commits),
               "findings": [f.to_dict() for f in findings]}
    mlk_common.emit(payload, args.json, human)
    return mlk_common.exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
