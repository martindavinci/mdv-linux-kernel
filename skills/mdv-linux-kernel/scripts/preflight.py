#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Lint a series as the mails that will go out, before anything is sent.

Usage: preflight.py DIR [--netdev] [--repo KERNEL_DIR] [--maintainers] [--json]

DIR is either the output of `b4 prep -p DIR` (the agent may run it: it sends
nothing) or the output of `b4 send -o DIR` (the human runs it: full mails with
To/Cc/Message-ID/In-Reply-To). The agent never runs `b4 send` in any form.
Recipient and threading checks run only when the mails carry To/Cc headers.

--netdev       add the netdev rules (tree in the subject, list in Cc, Fixes on net)
--maintainers  compare recipients with scripts/get_maintainer.pl (needs --repo)

Exit: 0 no warn/error findings, 1 findings, 2 usage error (DIR missing,
no patch mails, no get_maintainer.pl).
"""
from __future__ import annotations

import argparse
import email
import email.policy
import os
import re
import subprocess
import sys
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

import commit_msg_lint
import mlk_common
from mlk_common import Finding

BRACKET_RE = re.compile(r"^\s*\[([^\]]*)\]\s*(.*)$", re.S)
NUMBER_RE = re.compile(r"^(\d+)/(\d+)$")
VERSION_RE = re.compile(r"^[vV](\d+)$")
ANGLE_RE = re.compile(r"<([^<>\s]+@[^<>\s]+)>")
BARE_RE = re.compile(r"^\s*([^\s<>()@]+@[^\s<>()]+)")
INBODY_HEADER_RE = re.compile(r"^(From|Date|Subject): ")
NETDEV_LIST = "netdev@vger.kernel.org"
STABLE_LIST = "stable@vger.kernel.org"
NETDEV_MAX_PATCHES = 15


class UsageError(Exception):
    pass


class Mail:
    def __init__(self, name: str, path: str, msg: EmailMessage):
        self.file = name
        self.path = path
        self.msg = msg
        self.subject = " ".join(str(msg["Subject"]).split())
        m = BRACKET_RE.match(self.subject)
        tokens = m.group(1).split() if m else []
        self.parsed = bool(m) and any(t.upper() in ("PATCH", "RFC") for t in tokens)
        self.tokens = tokens if self.parsed else []
        self.title = m.group(2).strip() if self.parsed else self.subject
        self.number: int | None = None
        self.total: int | None = None
        self.version = 1
        for tok in self.tokens:
            nm, vm = NUMBER_RE.match(tok), VERSION_RE.match(tok)
            if nm:
                self.number, self.total = int(nm.group(1)), int(nm.group(2))
            elif vm:
                self.version = int(vm.group(1))
        if self.parsed and self.number is None:
            self.number, self.total = 1, 1  # a single "[PATCH] ..." mail
        self.is_cover = self.number == 0

    def header(self, name: str) -> str:
        value = self.msg.get(name)
        return " ".join(str(value).split()) if value is not None else ""

    def recipients(self) -> set[str]:
        values = [str(v) for key in ("To", "Cc") for v in self.msg.get_all(key, [])]
        return {addr.lower() for _, addr in getaddresses(values) if addr}

    def has_recipient_headers(self) -> bool:
        return bool(self.msg.get_all("To") or self.msg.get_all("Cc"))

    def from_addr(self) -> str:
        return parseaddr(self.header("From"))[1].lower()

    def to_dict(self) -> dict:
        return {"file": self.file, "subject": self.subject, "number": self.number,
                "total": self.total, "version": self.version, "is_cover": self.is_cover}


def load_mails(directory: str) -> list[Mail]:
    mails = []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                msg = email.message_from_binary_file(f, policy=email.policy.default)
            if msg["Subject"] is None:
                continue
            mails.append(Mail(name, path, msg))
        except (OSError, ValueError, TypeError, LookupError):
            continue
    mails.sort(key=lambda m: (m.number is None, m.number or 0, m.file))
    return mails


def plain_body(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain",))
    if part is None:
        return ""
    try:
        return part.get_content().replace("\r\n", "\n")
    except (LookupError, ValueError):
        return ""


def html_problem(msg: EmailMessage) -> str | None:
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return "contains a text/html part"
    if msg.is_multipart():
        others = sorted({p.get_content_type() for p in msg.walk()
                         if not p.is_multipart() and p.get_content_type() != "text/plain"})
        if others:
            return "multipart mail with " + ", ".join(others) + " part(s)"
    return None


def split_patch(mail: Mail) -> tuple[str, str | None]:
    """(commit message as git would see it, in-body From address or None)."""
    lines = plain_body(mail.msg).split("\n")
    for i, line in enumerate(lines):
        if line.rstrip() == "---":
            lines = lines[:i]
            break
    inbody_from = None
    head = 0
    while head < len(lines) and INBODY_HEADER_RE.match(lines[head]):
        if lines[head].startswith("From: "):
            inbody_from = parseaddr(lines[head][6:])[1].lower() or None
        head += 1
    if head and head < len(lines) and not lines[head].strip():
        lines = lines[head + 1:]
    elif head:
        lines = lines[head:]
    return mail.title + "\n\n" + "\n".join(lines).strip("\n") + "\n", inbody_from


def numbering_issues(mails: list[Mail]) -> list[str]:
    totals = sorted({m.total for m in mails})
    issues = []
    if len(totals) > 1:
        issues.append("totals disagree: " + ", ".join(str(t) for t in totals))
    numbers = sorted(m.number for m in mails if not m.is_cover)
    expected = list(range(1, max(totals) + 1))
    missing = [n for n in expected if n not in numbers]
    dups = sorted({n for n in numbers if numbers.count(n) > 1})
    beyond = [n for n in numbers if n > max(totals)]
    if missing:
        issues.append("missing " + ", ".join(str(n) for n in missing))
    if dups:
        issues.append("duplicated " + ", ".join(str(n) for n in dups))
    if beyond:
        issues.append("numbered past the total: " + ", ".join(str(n) for n in beyond))
    if sum(1 for m in mails if m.is_cover) > 1:
        issues.append("more than one 0/m cover letter")
    return issues


def series_checks(mails: list[Mail], netdev: bool) -> list[Finding]:
    findings = []
    for m in mails:
        if not m.parsed:
            findings.append(Finding("SUBJECT_UNPARSED", "warn",
                                    "subject has no [PATCH ...] / [RFC PATCH ...] bracket",
                                    detail=f"{m.file}: {m.subject}"))
    parsed = [m for m in mails if m.parsed]
    if not parsed:
        return findings
    issues = numbering_issues(parsed)
    if issues:
        findings.append(Finding("NUMBERING", "warn",
                                "patch numbers are not 1..m without gaps: " + "; ".join(issues)))
    versions = sorted({m.version for m in parsed})
    if len(versions) > 1:
        findings.append(Finding("VERSION_MISMATCH", "warn",
                                "mails carry different versions: "
                                + ", ".join(f"v{v}" for v in versions)))
    total = max(m.total for m in parsed)
    if total > 1 and not any(m.is_cover for m in parsed):
        findings.append(Finding("COVER_MISSING", "warn",
                                f"{total} patches and no 0/{total} cover letter"))
    if netdev:
        for m in parsed:
            if "net" not in m.tokens and "net-next" not in m.tokens:
                findings.append(Finding("NETDEV_TREE_PREFIX", "warn",
                                        "netdev subjects name the tree: [PATCH net ...] for fixes, "
                                        "[PATCH net-next ...] for new work",
                                        detail=f"{m.file}: {m.subject}"))
        count = max(total, sum(1 for m in parsed if not m.is_cover))
        if count > NETDEV_MAX_PATCHES:
            findings.append(Finding("NETDEV_TOO_MANY", "warn",
                                    f"{count} patches; netdev takes at most "
                                    f"{NETDEV_MAX_PATCHES} per series, split it"))
    return findings


def content_checks(mails: list[Mail], netdev: bool) -> list[Finding]:
    findings = []
    for m in mails:
        problem = html_problem(m.msg)
        if problem:
            findings.append(Finding("HTML_PART", "error",
                                    f"patches must be plain text: {problem}", detail=m.file))
        if m.is_cover:
            continue
        message, _ = split_patch(m)
        extra = commit_msg_lint.netdev_fix_findings(message) if netdev else []
        for f in commit_msg_lint.lint_message(message) + extra:
            f.detail = f"{m.file}: {f.detail}" if f.detail else m.file
            findings.append(f)
        keys = {k.lower() for k, _ in mlk_common.trailers(message)}
        if "signed-off-by" not in keys:
            findings.append(Finding("NO_SIGNOFF", "info",
                                    "pending: the human adds it (git rebase --signoff); "
                                    "the agent never does", detail=m.file))
        if netdev and "net" in m.tokens and "fixes" not in keys:
            findings.append(Finding("NETDEV_NET_NO_FIXES", "warn",
                                    "patch for net (fixes tree) without a Fixes: tag; "
                                    "add one or target net-next", detail=m.file))
    return findings


def norm_id(value: str) -> str:
    value = value.strip()
    return value if not value or value.startswith("<") else f"<{value}>"


def header_checks(mails: list[Mail], netdev: bool) -> list[Finding]:
    findings = []
    cover = next((m for m in mails if m.is_cover), None)
    for m in mails:
        if not m.has_recipient_headers():
            findings.append(Finding("NO_RECIPIENTS", "error", "mail has neither To nor Cc",
                                    detail=m.file))
        elif netdev and NETDEV_LIST not in m.recipients():
            findings.append(Finding("NETDEV_LIST_MISSING", "warn",
                                    f"{NETDEV_LIST} is not in To/Cc", detail=m.file))
    patches = [m for m in mails if not m.is_cover]
    head = cover or next((m for m in patches if m.number == 1), None)
    if head and head.version >= 2 and (head.header("In-Reply-To") or head.header("References")):
        findings.append(Finding("NEW_VERSION_IN_OLD_THREAD", "warn",
                                f"v{head.version} replies to an earlier thread; send each "
                                "version as a new thread and link the old one in the cover",
                                detail=head.file))
    cover_id = norm_id(cover.header("Message-ID")) if cover else ""
    if cover_id:
        for m in patches:
            if norm_id(m.header("In-Reply-To")) != cover_id:
                findings.append(Finding("THREADING", "warn",
                                        "patch does not reply to the cover letter "
                                        f"(In-Reply-To should be {cover_id})", detail=m.file))
    for m in patches:
        message, inbody_from = split_patch(m)
        sobs = [v.lower() for k, v in mlk_common.trailers(message) if k.lower() == "signed-off-by"]
        author = inbody_from or m.from_addr()
        if sobs and author and not any(author in s for s in sobs):
            findings.append(Finding("FROM_SOB_MISMATCH", "info",
                                    f"author {author} has no Signed-off-by in this patch",
                                    detail=m.file))
    return findings


def run_get_maintainer(repo: str, patch_path: str) -> str:
    """stdout of scripts/get_maintainer.pl for one patch file (tests replace this)."""
    try:
        res = subprocess.run(
            ["perl", "scripts/get_maintainer.pl", "--no-rolestats", "--no-git",
             "--no-git-fallback", os.path.abspath(patch_path)],
            cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        raise UsageError(f"cannot run perl: {exc}") from exc
    if res.returncode != 0:
        raise UsageError(f"get_maintainer.pl failed on {os.path.basename(patch_path)}: "
                         f"{res.stderr.strip()}")
    return res.stdout


def parse_maintainers(output: str) -> set[str]:
    addrs = set()
    for line in output.splitlines():
        m = ANGLE_RE.search(line) or BARE_RE.match(line)
        if m:
            addrs.add(m.group(1).lower())
    return addrs


def maintainer_checks(mails: list[Mail], repo: str, has_headers: bool,
                      per_mail: dict[str, list[str]]) -> list[Finding]:
    findings = []
    cover = next((m for m in mails if m.is_cover), None)
    cover_rcpt = cover.recipients() if cover else set()
    listed: set[str] = set()
    for m in mails:
        if m.is_cover:
            continue
        wanted = parse_maintainers(run_get_maintainer(repo, m.path))
        per_mail[m.file] = sorted(wanted)
        listed |= wanted
        if has_headers:
            missing = sorted(wanted - m.recipients() - cover_rcpt)
            if missing:
                findings.append(Finding("MAINTAINER_NOT_ADDRESSED", "warn",
                                        "get_maintainer.pl lists address(es) missing from To/Cc: "
                                        + ", ".join(missing), detail=m.file))
    if has_headers:
        rcpt = set().union(*(m.recipients() for m in mails))
        senders = {m.from_addr() for m in mails}
        extra = sorted(rcpt - listed - senders - {STABLE_LIST})
        if extra:
            findings.append(Finding("RECIPIENT_EXTRA", "info",
                                    "in To/Cc but not listed by get_maintainer.pl for any patch: "
                                    + ", ".join(extra)))
    return findings


def human(payload: dict) -> str:
    mails = payload["mails"]
    covers = sum(1 for m in mails if m["is_cover"])
    head = (f"preflight: {len(mails) - covers} patch(es)"
            f"{' + cover' if covers else ''} in {payload['dir']}")
    return head + "\n" + mlk_common.format_findings(payload["findings"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lint a series as the mails that will go out.")
    ap.add_argument("dir", help="output of `b4 prep -p DIR` or of `b4 send -o DIR`")
    ap.add_argument("--netdev", action="store_true", help="apply the netdev rules")
    ap.add_argument("--repo", help="kernel tree (for --maintainers)")
    ap.add_argument("--maintainers", action="store_true",
                    help="compare To/Cc with scripts/get_maintainer.pl")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        if not os.path.isdir(args.dir):
            raise UsageError(f"{args.dir} is not a directory")
        if args.maintainers:
            if not args.repo:
                raise UsageError("--maintainers needs --repo KERNEL_DIR")
            if not os.path.isfile(os.path.join(args.repo, "scripts", "get_maintainer.pl")):
                raise UsageError(f"{args.repo} has no scripts/get_maintainer.pl "
                                 "(--repo must point at a kernel tree)")
        mails = load_mails(args.dir)
        if not mails:
            raise UsageError(f"no patch mails in {args.dir}")
        has_headers = any(m.has_recipient_headers() for m in mails)
        findings = series_checks(mails, args.netdev) + content_checks(mails, args.netdev)
        if has_headers:
            findings += header_checks(mails, args.netdev)
        else:
            findings.append(Finding("NO_HEADERS", "info",
                                    "recipient and threading checks need the directory from "
                                    "`b4 send -o`, which the human runs"))
        per_mail: dict[str, list[str]] = {}
        if args.maintainers:
            findings += maintainer_checks(mails, args.repo, has_headers, per_mail)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return mlk_common.EXIT_USAGE
    entries = []
    for m in mails:
        entry = m.to_dict()
        if m.file in per_mail:
            entry["maintainers"] = per_mail[m.file]
        entries.append(entry)
    payload = {"dir": args.dir, "mails": entries, "findings": [f.to_dict() for f in findings]}
    mlk_common.emit(payload, args.json, human)
    return mlk_common.exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
