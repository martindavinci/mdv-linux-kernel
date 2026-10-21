#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Snapshot of the in-tree process documents the skill follows.

Usage: policy_snapshot.py [--repo DIR] [--upstream REF] [--max-age-days N] [--json]

Reports whether each policy document exists in the checkout, whether it was
modified locally or changed upstream, and how old / behind the checkout is.
Without --upstream the branch's configured upstream (@{upstream}) is used.
Exit: 0 nothing to act on (info only), 1 warnings, 2 usage or git error.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import mlk_common
from mlk_common import Finding

POLICY_DOCS = [
    "Documentation/process/coding-assistants.rst",
    "Documentation/process/generated-content.rst",
    "Documentation/process/submitting-patches.rst",
    "Documentation/process/development-process.rst",
    "Documentation/process/maintainer-netdev.rst",
    "Documentation/process/security-bugs.rst",
    "MAINTAINERS",
]


def _ok(repo: str, args: list[str]) -> bool:
    return mlk_common.run_git(args, repo).returncode == 0


def _resolve_upstream(repo: str, upstream: str | None) -> tuple[str | None, str | None]:
    """Return (ref, None) or (None, reason)."""
    if upstream is None:
        res = mlk_common.run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
                                 repo)
        if res.returncode != 0:
            return None, "no --upstream given and the branch has no upstream configured"
        upstream = res.stdout.strip()
    if not _ok(repo, ["rev-parse", "--verify", "--quiet", f"{upstream}^{{commit}}"]):
        return None, f"upstream ref '{upstream}' not found (git fetch first?)"
    return upstream, None


def snapshot(repo: str, upstream: str | None = None, max_age_days: int = 14,
             now: float | None = None) -> dict:
    now = time.time() if now is None else now
    findings: list[Finding] = []
    head = mlk_common.git(["rev-parse", "HEAD"], repo)
    head_date = mlk_common.git(["log", "-1", "--format=%cI", "HEAD"], repo)
    head_ts = int(mlk_common.git(["log", "-1", "--format=%ct", "HEAD"], repo))
    age_days = int((now - head_ts) // 86400)
    if age_days > max_age_days:
        findings.append(Finding(
            "STALE_CHECKOUT", "warn",
            f"HEAD is {age_days} days old (limit {max_age_days}): fetch and rebase so the "
            "process documents are current", detail=head_date))

    ref, reason = _resolve_upstream(repo, upstream)
    upstream_info = None
    if ref is None:
        findings.append(Finding(
            "UPSTREAM_MISSING", "warn",
            f"{reason}; cannot tell whether the policy documents changed upstream"))
    else:
        behind = int(mlk_common.git(["rev-list", "--count", f"HEAD..{ref}"], repo))
        upstream_info = {"ref": ref, "behind": behind}
        if behind > 0:
            findings.append(Finding(
                "BEHIND_UPSTREAM", "warn", f"HEAD is {behind} commit(s) behind {ref}"))

    docs = []
    for path in POLICY_DOCS:
        exists = os.path.isfile(os.path.join(repo, path))
        last = mlk_common.git(["log", "-1", "--format=%H", "HEAD", "--", path], repo) or None
        modified = not _ok(repo, ["diff", "--quiet", "HEAD", "--", path])
        changed = None
        if ref is not None:
            changed = not _ok(repo, ["diff", "--quiet", "HEAD", ref, "--", path])
        docs.append({"path": path, "exists": exists, "last_commit": last,
                     "locally_modified": modified, "changed_upstream": changed})
        if not exists:
            findings.append(Finding(
                "DOC_MISSING", "info",
                f"{path} is not in this checkout (older tree?); use the fallback guidance "
                "and say so", detail=path))
        if modified:
            findings.append(Finding(
                "DOC_MODIFIED", "warn",
                f"{path} has local modifications; read the committed version "
                f"(git show HEAD:{path})", detail=path))
        if changed:
            findings.append(Finding(
                "DOC_CHANGED_UPSTREAM", "warn",
                f"{path} differs in {ref}; read the upstream version with "
                f"'git show {ref}:{path}'", detail=path))

    return {"head": head, "head_date": head_date, "age_days": age_days,
            "upstream": upstream_info, "docs": docs,
            "findings": [f.to_dict() for f in findings]}


def human(payload: dict) -> str:
    up = payload["upstream"]
    lines = [f"HEAD {payload['head'][:12]} ({payload['head_date']}, {payload['age_days']} days old)",
             f"upstream: {up['ref']} (HEAD is {up['behind']} commit(s) behind)" if up
             else "upstream: none"]
    for d in payload["docs"]:
        flags = [] if d["exists"] else ["missing"]
        flags += ["locally modified"] if d["locally_modified"] else []
        flags += ["changed upstream"] if d["changed_upstream"] else []
        lines.append(f"  {d['path']}: {', '.join(flags) or 'ok'}")
    return "\n".join(lines) + "\n" + mlk_common.format_findings(payload["findings"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Snapshot the in-tree process documents.")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--upstream", metavar="REF")
    ap.add_argument("--max-age-days", type=int, default=14)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        repo = mlk_common.repo_root(args.repo)
        payload = snapshot(repo, args.upstream, args.max_age_days)
    except (mlk_common.GitError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return mlk_common.EXIT_USAGE
    mlk_common.emit(payload, args.json, human)
    failing = [f for f in payload["findings"] if f["severity"] != "info"]
    return mlk_common.EXIT_FINDINGS if failing else mlk_common.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
