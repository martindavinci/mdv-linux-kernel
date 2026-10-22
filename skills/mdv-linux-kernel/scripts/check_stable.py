#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Check stable tags and backport markers (stable-kernel-rules.rst).

Usage: check_stable.py <rev-range> [--backport] [--repo DIR] [--json]

  STABLE_FORMAT (warn)    a Cc: line for stable that is not one of the
                          documented forms (address, optional "# ..." note)
  STABLE_AS_LIST (warn)   stable@vger.kernel.org given as a list/recipient
                          trailer other than Cc:
  STABLE_NO_FIXES (warn)  Cc: stable without a Fixes: tag (say why in the
                          changelog if no commit introduced the bug)
With --backport (a patch for a stable branch, option 3):
  BACKPORT_NO_UPSTREAM (error)  no "commit <sha1> upstream." or
                                "[ Upstream commit <sha1> ]" line
  BACKPORT_UPSTREAM_UNKNOWN (warn)  that commit is not in the repository
                                    (fetch mainline to verify it)
  BACKPORT_SUBJECT (warn)  the subject differs from the upstream commit's
Exit: 0 no findings, 1 findings, 2 usage or git error.
"""
from __future__ import annotations

import argparse
import re
import sys

import mlk_common
from mlk_common import Finding

ADDR = r"stable@(?:vger\.)?kernel\.org"
NOAUTOSEL = r"stable\+noautosel@kernel\.org"
# Brackets come in pairs; noautosel must give its reason ("# reason goes here").
STABLE_RE = re.compile(rf"^(?:<{ADDR}>|{ADDR})(?:\s+#\s*\S.*)?$")
NOAUTOSEL_RE = re.compile(rf"^(?:<{NOAUTOSEL}>|{NOAUTOSEL})\s+#\s*\S.*$")
UPSTREAM_RES = [re.compile(r"^commit ([0-9a-f]{40}) upstream\.?\s*$", re.M),
                re.compile(r"^\[\s*Upstream commit ([0-9a-f]{40})\s*\]\s*$", re.M)]


def check(repo: str, rev_range: str, backport: bool) -> list[Finding]:
    findings: list[Finding] = []
    for sha in mlk_common.commits_in_range(repo, rev_range):
        message = mlk_common.commit_message(repo, sha)
        tags = mlk_common.trailers(message)
        stable = [(k, v) for k, v in tags if re.search(r"stable(?:\+noautosel)?@", v)]
        for key, value in stable:
            if key.lower() != "cc":
                findings.append(Finding(
                    "STABLE_AS_LIST", "warn",
                    f"'{key}:' with the stable address: use a 'Cc: stable@vger.kernel.org' "
                    "trailer in the sign-off area", commit=sha, detail=f"{key}: {value}"))
            elif not (STABLE_RE.match(value) or NOAUTOSEL_RE.match(value)):
                findings.append(Finding(
                    "STABLE_FORMAT", "warn",
                    "Cc: stable line not in a documented form, e.g. "
                    "'Cc: stable@vger.kernel.org', 'Cc: <stable@vger.kernel.org> # 6.6.x', "
                    "'Cc: <stable+noautosel@kernel.org> # <reason>'",
                    commit=sha, detail=f"{key}: {value}"))
        has_cc = any(k.lower() == "cc" for k, _ in stable)
        noautosel = any("noautosel" in v for _, v in stable)
        if has_cc and not noautosel and not any(k.lower() == "fixes" for k, _ in tags):
            findings.append(Finding(
                "STABLE_NO_FIXES", "warn",
                "Cc: stable without Fixes: add the commit that introduced the bug, or say in "
                "the changelog why there is none", commit=sha))
        if not backport:
            continue
        upstream = next((m.group(1) for r in UPSTREAM_RES for m in [r.search(message)] if m), None)
        if upstream is None:
            findings.append(Finding(
                "BACKPORT_NO_UPSTREAM", "error",
                "backport without the upstream reference: add 'commit <full sha1> upstream.' "
                "or '[ Upstream commit <full sha1> ]' on its own line", commit=sha))
            continue
        res = mlk_common.run_git(["log", "-1", "--format=%s", f"{upstream}^{{commit}}", "--"], repo)
        if res.returncode != 0:
            findings.append(Finding(
                "BACKPORT_UPSTREAM_UNKNOWN", "warn",
                "upstream commit not found here: fetch mainline and check it", commit=sha,
                detail=upstream))
        elif res.stdout.strip() != mlk_common.split_message(message)[0]:
            findings.append(Finding(
                "BACKPORT_SUBJECT", "warn",
                "subject differs from the upstream commit: keep the upstream subject",
                commit=sha, detail=f"upstream: {res.stdout.strip()}"))
    return findings


def human(payload: dict) -> str:
    return f"check_stable: {payload['range']}\n" + mlk_common.format_findings(payload["findings"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check stable tags and backport markers.")
    ap.add_argument("rev_range")
    ap.add_argument("--backport", action="store_true")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        findings = check(mlk_common.repo_root(args.repo), args.rev_range, args.backport)
    except (mlk_common.GitError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return mlk_common.EXIT_USAGE
    payload = {"range": args.rev_range, "findings": [f.to_dict() for f in findings]}
    mlk_common.emit(payload, args.json, human)
    return mlk_common.exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
