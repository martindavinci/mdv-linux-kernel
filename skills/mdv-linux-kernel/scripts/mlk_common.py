#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Shared helpers for the mdv-linux-kernel scripts (stdlib only)."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from typing import Callable

TRAILER_RE = re.compile(r"^([A-Za-z-]+):\s*(.+?)\s*$")

# Exit codes shared by every script.
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2


class GitError(Exception):
    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


def run_git(args: list[str], repo: str) -> subprocess.CompletedProcess:
    """Run git without raising; caller inspects returncode/stdout/stderr."""
    try:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
    except (OSError, ValueError) as exc:
        raise GitError(f"cannot run git in {repo}: {exc}") from exc


def git(args: list[str], repo: str) -> str:
    res = run_git(args, repo)
    if res.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {res.stderr.strip()}", res.stderr)
    return res.stdout.rstrip("\n")


def repo_root(cwd: str) -> str:
    return os.path.abspath(git(["rev-parse", "--show-toplevel"], cwd))


def state_dir(repo: str) -> str:
    """<git-common-dir>/mdv-linux-kernel, absolute, created if missing.

    Uses the common dir so worktrees share state with the main checkout and
    nothing is ever written to the tracked tree.
    """
    common = git(["rev-parse", "--git-common-dir"], repo)
    path = os.path.abspath(os.path.join(repo, common, "mdv-linux-kernel"))
    os.makedirs(path, exist_ok=True)
    return path


def commits_in_range(repo: str, rev_range: str) -> list[str]:
    out = git(["rev-list", "--reverse", rev_range, "--"], repo)
    return out.split()


def commit_message(repo: str, sha: str) -> str:
    return git(["log", "-1", "--format=%B", sha, "--"], repo).replace("\r\n", "\n").rstrip("\n")


def subject(repo: str, sha: str) -> str:
    return git(["log", "-1", "--format=%s", sha, "--"], repo)


def split_message(message: str) -> tuple[str, list[str], list[str]]:
    """Return (subject, body lines, trailer lines) of a commit message.

    Anything after a line that is exactly '---' is ignored. Trailer lines are
    the trailing paragraph(s) made only of 'Key: value' lines (the subject
    paragraph never counts).
    """
    lines = [line.rstrip() for line in message.replace("\r\n", "\n").split("\n")]
    if "---" in lines:
        lines = lines[:lines.index("---")]
    while lines and not lines[-1]:
        lines.pop()
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
        elif current:
            paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)
    if not paragraphs:
        return "", [], []
    subj = paragraphs[0][0]
    rest = paragraphs[1:]
    trailer_lines: list[str] = []
    while rest and _is_trailer_block(rest[-1]):
        trailer_lines = rest.pop() + trailer_lines
    body = list(paragraphs[0][1:])
    for para in rest:
        if body:
            body.append("")
        body.extend(para)
    return subj, body, trailer_lines


def _is_trailer_block(para: list[str]) -> bool:
    """All 'Key: value' lines, allowing '[maintainer notes]' and indented continuations."""
    has_trailer = False
    for line in para:
        if TRAILER_RE.match(line):
            has_trailer = True
        elif not (line[:1].isspace() or (line.startswith("[") and line.endswith("]"))):
            return False
    return has_trailer


def trailers(message: str) -> list[tuple[str, str]]:
    out = []
    for line in split_message(message)[2]:
        m = TRAILER_RE.match(line)
        if m:
            out.append((m.group(1), m.group(2).strip()))
    return out


@dataclass
class Finding:
    code: str
    severity: str  # "error" | "warn" | "info"
    message: str
    commit: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def exit_code(findings: list[Finding]) -> int:
    """1 if any warn/error finding (info findings do not fail), else 0."""
    return EXIT_FINDINGS if any(f.severity != "info" for f in findings) else EXIT_OK


def format_findings(findings: list[dict]) -> str:
    if not findings:
        return "No findings."
    out = []
    for f in findings:
        where = f" [{f['commit'][:12]}]" if f.get("commit") else ""
        out.append(f"{f['severity'].upper():5} {f['code']}{where}: {f['message']}")
        if f.get("detail"):
            out.append(f"      {f['detail']}")
    return "\n".join(out)


def emit(payload: dict, as_json: bool, human: Callable[[dict], str]) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=False))
    else:
        print(human(payload))
