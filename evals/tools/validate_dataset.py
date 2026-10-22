#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Validate evals/historical/dataset.json.

  validate_dataset.py [--file F]                 schema checks (offline)
  validate_dataset.py [--file F] --repo DIR [--write]

With --repo, accepted fixes are checked in a kernel checkout: the commit
exists, its subject matches, and the Fixes: target resolves. --write then
records today's date in provenance.verified_tree. That proves the commit is
real; it does not prove why a patch was rejected or accepted: for that the
case cites its source (rationale.url).
Exit: 0 valid, 1 problems found, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FILE = os.path.join(os.path.dirname(HERE), "historical", "dataset.json")

KINDS = {"accepted-fix", "rejected"}
SPLITS = {"dev", "heldout"}
TREES = {"net", "net-next"}
# Gates of the skill a case can expect to fire (besides script codes like FIXES_*).
GATES = {"standalone-cleanup", "missing-fixes", "tree-choice", "untested-hardware",
         "unverified-claim", "llm-style"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA_RE = re.compile(r"^[0-9a-f]{12,40}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")
FIXES_RE = re.compile(r'^Fixes: ([0-9a-f]{12,40}) \("(.+)"\)$')
MSGID_RE = re.compile(r"^[^<>\s]+@[^<>\s]+$")


def _url(value, prefix: str = "https://") -> bool:
    return isinstance(value, str) and value.startswith(prefix)


def validate(data) -> list[str]:
    if not isinstance(data, list):
        return ["dataset must be a JSON list"]
    errors: list[str] = []
    seen: set[str] = set()
    for i, case in enumerate(data):
        where = f"case {i} ({case.get('id', '?') if isinstance(case, dict) else '?'})"

        def err(msg: str) -> None:
            errors.append(f"{where}: {msg}")

        if not isinstance(case, dict):
            err("not an object")
            continue
        cid = case.get("id")
        if not isinstance(cid, str) or not ID_RE.match(cid):
            err("id must match [a-z0-9-]+")
        elif cid in seen:
            err("duplicate id")
        seen.add(cid)
        kind = case.get("kind")
        if kind not in KINDS:
            err(f"kind must be one of {sorted(KINDS)}")
        if case.get("split") not in SPLITS:
            err(f"split must be one of {sorted(SPLITS)}")
        if case.get("tree") not in TREES:
            err(f"tree must be one of {sorted(TREES)}")
        if not isinstance(case.get("subject"), str) or not case["subject"]:
            err("subject is required")
        if case.get("base") is not None and not SHA_RE.match(str(case["base"])):
            err("base must be a hex SHA or null")
        if not isinstance(case.get("patch"), str) or not MSGID_RE.match(case["patch"]):
            err("patch must be the message-id of the exact version (no <>)")
        if not _url(case.get("lore"), "https://lore.kernel.org/"):
            err("lore must be a https://lore.kernel.org/ URL")
        fixes = case.get("fixes")
        if fixes is not None and not FIXES_RE.match(str(fixes)):
            err('fixes must look like: Fixes: <12+ hex> ("<subject>")')
        expected = case.get("expected")
        if not isinstance(expected, dict):
            err("expected is required")
            expected = {}
        fire = expected.get("must_fire", [])
        if not isinstance(fire, list) or not all(
                isinstance(g, str) and (g in GATES or CODE_RE.match(g)) for g in fire):
            err(f"expected.must_fire: gates from {sorted(GATES)} or script codes")
        if not isinstance(expected.get("must_not_error"), bool):
            err("expected.must_not_error must be true or false")
        rationale = case.get("rationale") or {}
        if not isinstance(rationale, dict):
            err("rationale must be an object")
            rationale = {}
        if kind == "accepted-fix":
            if not SHA40_RE.match(str(case.get("commit"))):
                err("accepted-fix needs the full 40-hex commit")
            if expected.get("must_not_error") is not True:
                err("accepted-fix: expected.must_not_error must be true")
        if kind == "rejected":
            if case.get("commit") is not None:
                err("rejected: commit must be null")
            if not fire:
                err("rejected: expected.must_fire must name the gate(s) that should fire")
            if not rationale.get("quote") or not _url(rationale.get("url")):
                err("rejected: rationale needs the reviewer's quote and its URL")
        if rationale.get("url") is not None and not _url(rationale.get("url")):
            err("rationale.url must be https")
        prov = case.get("provenance")
        if not isinstance(prov, dict):
            err("provenance is required")
            continue
        sources = prov.get("sources")
        if not isinstance(sources, list) or not sources or not all(_url(s) for s in sources):
            err("provenance.sources must list the https pages the facts were read from")
        for key in ("retrieved", "verified_web", "verified_tree"):
            value = prov.get(key)
            if key == "retrieved" and value is None:
                err("provenance.retrieved is required")
            elif value is not None and not DATE_RE.match(str(value)):
                err(f"provenance.{key} must be YYYY-MM-DD or null")
    return errors


def git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def check_tree(data: list, repo: str) -> tuple[list[str], list[str]]:
    """(errors, ids verified) for accepted fixes in a kernel checkout."""
    errors, verified = [], []
    for case in data:
        if case.get("kind") != "accepted-fix":
            continue
        cid, sha = case["id"], case["commit"]
        if git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
            errors.append(f"{cid}: commit {sha[:12]} not in {repo} (git fetch the {case['tree']} tree?)")
            continue
        subject = git(repo, "log", "-1", "--format=%s", sha).stdout.strip()
        if subject != case["subject"]:
            errors.append(f"{cid}: subject in tree is '{subject}'")
            continue
        m = FIXES_RE.match(case.get("fixes") or "")
        if m and git(repo, "cat-file", "-e", f"{m.group(1)}^{{commit}}").returncode != 0:
            errors.append(f"{cid}: Fixes: target {m.group(1)} not found")
            continue
        verified.append(cid)
    return errors, verified


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate the historical eval dataset.")
    ap.add_argument("--file", default=DEFAULT_FILE)
    ap.add_argument("--repo", help="kernel checkout with the net/net-next history")
    ap.add_argument("--write", action="store_true", help="with --repo: record verified_tree")
    args = ap.parse_args(argv)
    if args.write and not args.repo:
        print("error: --write needs --repo", file=sys.stderr)
        return 2
    try:
        with open(args.file, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    errors = validate(data)
    verified: list[str] = []
    if not errors and args.repo:
        tree_errors, verified = check_tree(data, args.repo)
        errors += tree_errors
        if args.write and verified:
            today = date.today().isoformat()
            for case in data:
                if case["id"] in verified:
                    case["provenance"]["verified_tree"] = today
            with open(args.file, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
    for e in errors:
        print(e)
    kinds = {k: sum(c.get("kind") == k for c in data) for k in sorted(KINDS)} \
        if isinstance(data, list) else {}
    print(f"{len(data) if isinstance(data, list) else 0} case(s) {kinds}; "
          f"{len(errors)} problem(s)" + (f"; verified in tree: {len(verified)}" if args.repo else ""))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
