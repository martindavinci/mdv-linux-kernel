#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Heuristic lint of kernel commit messages (style and "AI slop" signals).

Usage: commit_msg_lint.py <rev-range> [--repo DIR] [--json]
All findings are warnings: they are prompts to look again, not rules.
Exit: 0 no findings, 1 findings, 2 usage or git error.
"""
from __future__ import annotations

import argparse
import re
import sys

import mlk_common
from mlk_common import Finding

PREFIX_RE = re.compile(r"^[\w./-]+(: [\w./-]+)*: ")
MAX_LINE = 75
NON_IMPERATIVE = {"fixed", "fixes", "added", "adds", "updated", "updates", "changed",
                  "changes", "removed", "removes", "adding", "fixing"}
THIS_PATCH_RE = re.compile(r"(?:^|[.!?]\s+)This (?:patch|commit)\b")
LLM_PHRASES = ["comprehensive", "robust", "seamless", "leverage", "delve", "in summary",
               "overall,", "this change improves", "enhances", "ensures that", "streamline"]
MAX_BULLETS = 5
# maintainer-netdev.rst "Bug fixes": say how the issue was discovered and
# validated, and how the fix was tested. Keyword heuristics, not proof.
DISCOVERY_RE = re.compile(
    r"\b(?:found|discovered|noticed|spotted|observed|reported|triggered|triggers|"
    r"reproduc\w*|caught|detected|inspection|reading|static analysis|fuzz\w*|"
    r"syzkaller|syzbot|KASAN|KCSAN|UBSAN|KMSAN|lockdep|smatch|sparse|coccinelle)\b", re.I)
TESTING_RE = re.compile(
    r"\b(?:tested|untested|testing|verified|selftests?|kselftests?|kunit|reproducer|"
    r"not run)\b", re.I)


def lint_message(message: str) -> list[Finding]:
    subject, body, _trailers = mlk_common.split_message(message)
    findings: list[Finding] = []

    def add(code: str, text: str, detail: str | None = None) -> None:
        findings.append(Finding(code, "warn", text, detail=detail))

    prefix = PREFIX_RE.match(subject)
    if not prefix:
        add("SUBJECT_NO_PREFIX", "subject has no 'subsystem: ' prefix", subject)
    if len(subject) > MAX_LINE:
        add("SUBJECT_TOO_LONG", f"subject is {len(subject)} chars (keep it <= {MAX_LINE})", subject)
    if subject.endswith("."):
        add("SUBJECT_TRAILING_PERIOD", "subject ends with a period", subject)
    words = subject[prefix.end() if prefix else 0:].split()
    if words and words[0].lower() in NON_IMPERATIVE:
        add("SUBJECT_NOT_IMPERATIVE",
            f"use the imperative mood ('{words[0]}' -> e.g. 'fix', 'add')", subject)

    text_lines = [line for line in body if line.strip()]
    if not text_lines:
        add("BODY_MISSING", "no body: explain what is wrong, why it matters and how this fixes it")
    long_lines = [line for line in text_lines
                  if len(line) > MAX_LINE and "://" not in line and not line.startswith(("  ", "\t"))
                  and not mlk_common.TRAILER_RE.match(line)]
    if long_lines:
        add("BODY_LINE_TOO_LONG", f"{len(long_lines)} body line(s) longer than {MAX_LINE} chars",
            long_lines[0])
    prose = " ".join(line.strip() for line in text_lines)
    if THIS_PATCH_RE.search(prose):
        add("BODY_THIS_PATCH", "describe the change directly instead of 'This patch/commit ...'")
    lowered = (subject + "\n" + "\n".join(body)).lower()
    hits = [p for p in LLM_PHRASES if p in lowered]
    if hits:
        add("LLM_PHRASES", "wording often found in generated text; say it plainly",
            ", ".join(hits))
    if any(line.startswith("#") or "**" in line or line.strip().startswith("```")
           for line in body):
        add("MARKDOWN", "Markdown in a plain-text commit message")
    bullets = [line for line in body if line.lstrip().startswith(("- ", "* "))]
    if len(bullets) > MAX_BULLETS:
        add("BULLET_HEAVY", f"{len(bullets)} bullet lines; prefer explanatory prose")
    return findings


def netdev_fix_findings(message: str) -> list[Finding]:
    """maintainer-netdev.rst "Bug fixes" statements, for commits with a Fixes: tag."""
    tags = mlk_common.trailers(message)
    keys = {(k.lower(), v.lower().startswith("syzbot")) for k, v in tags}
    if not any(k == "fixes" for k, _ in keys):
        return []
    _subject, body, _trailers = mlk_common.split_message(message)
    prose = " ".join(line.strip() for line in body)
    findings: list[Finding] = []
    if not DISCOVERY_RE.search(prose) and ("reported-by", True) not in keys:
        findings.append(Finding(
            "NETDEV_DISCOVERY_MISSING", "warn",
            "netdev bug fix without a statement of how the issue was discovered and "
            "validated (maintainer-netdev.rst, Bug fixes); if unknown, ask the human",
            detail="by default: the issue was not triggered"))
    if not TESTING_RE.search(prose) and ("tested-by", True) not in keys:
        findings.append(Finding(
            "NETDEV_TESTING_MISSING", "warn",
            "netdev bug fix without a statement of how the fix was tested "
            "(maintainer-netdev.rst, Bug fixes)",
            detail="by default: not tested; only a pass, non-stale evidence entry backs 'tested'"))
    return findings


def human(payload: dict) -> str:
    head = f"commit_msg_lint: {payload['commits']} commit(s) in {payload['range']}"
    return head + "\n" + mlk_common.format_findings(payload["findings"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lint kernel commit messages.")
    ap.add_argument("rev_range")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--netdev", action="store_true",
                    help="also check the netdev bug-fix discovery and testing statements")
    args = ap.parse_args(argv)
    findings: list[Finding] = []
    try:
        repo = mlk_common.repo_root(args.repo)
        commits = mlk_common.commits_in_range(repo, args.rev_range)
        for sha in commits:
            message = mlk_common.commit_message(repo, sha)
            extra = netdev_fix_findings(message) if args.netdev else []
            for f in lint_message(message) + extra:
                f.commit = sha
                findings.append(f)
    except mlk_common.GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return mlk_common.EXIT_USAGE
    payload = {"range": args.rev_range, "commits": len(commits),
               "findings": [f.to_dict() for f in findings]}
    mlk_common.emit(payload, args.json, human)
    return mlk_common.exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
