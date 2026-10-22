#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Check a draft the human is about to send: bug report, review, test report, reply.

Usage: draft_lint.py FILE [--kind report|review|test|reply] [--json]

Lines quoted from someone else ("> ...") are not checked, except for secrets.
  ATTESTATION_IN_DRAFT (error)  a Reviewed-by/Tested-by/Acked-by/Signed-off-by
                                line: only the person named gives it; the
                                human types it themselves, never the agent
  SECRET (error)                token, private key or password-like text
  REDACT_MAC / REDACT_IP / REDACT_HOME / REDACT_SERIAL (warn)
                                data to mask in logs (keep the structure, e.g.
                                xx:xx:xx:12:34:56, so the log stays useful)
  MARKDOWN (warn)               headings, bold or code fences: mail is plain text
  TOP_POSTING (warn)            reply text above the quote with nothing below
                                (review, test, reply)
  NO_SUMMARY (warn)             report does not open with a short summary
Exit: 0 no findings, 1 findings, 2 usage or read error.
"""
from __future__ import annotations

import argparse
import ipaddress
import re
import sys

import mlk_common
from mlk_common import Finding

KINDS = ["report", "review", "test", "reply"]
REPLY_KINDS = {"review", "test", "reply"}
# Also a folded trailer, whose value is on the next line.
ATTEST_RE = re.compile(r"^\s*(Reviewed-by|Tested-by|Acked-by|Signed-off-by)\s*:", re.I)
SECRET_RES = [re.compile(p) for p in (
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\bghp_[A-Za-z0-9]{30,}",
    r"\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\bglpat-[A-Za-z0-9_-]{20,}",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    r"\bsk-[A-Za-z0-9_-]{20,}",
    r"(?i)\b(?:password|passwd|api[_-]?key|secret|access[_-]?token)\s*[:=]\s*\S+",
)]
MAC_RE = re.compile(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
IPV6_RE = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])")
HOME_RE = re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\Users\\)([^/\\\s]+)")
SERIAL_RE = re.compile(r"(?i)\bserial(?:[ _-]?(?:number|no))?\s*[:=]\s*\S+")
MD_RES = [re.compile(p) for p in (r"^#{2,6}\s", r"\*\*[^*\n]+\*\*", r"^\s*```")]
ATTRIBUTION_RE = re.compile(r"^On .+ wrote:\s*$")
LOG_LINE_RE = re.compile(r"^\s*(?:\[\s*\d+\.\d+\]|BUG:|WARNING:|Oops|Call Trace:)")
NEUTRAL_MACS = {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}
MAX_SUMMARY_LINES = 5


def public_ipv4(text: str) -> bool:
    try:
        ip = ipaddress.IPv4Address(text)
    except ValueError:
        return False
    documentation = any(ip in ipaddress.IPv4Network(n) for n in
                        ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified
                or ip.is_multicast or ip.is_reserved or documentation)


def public_ipv6(text: str) -> bool:
    try:
        return ipaddress.IPv6Address(text).is_global  # documentation 2001:db8:: is not
    except ValueError:
        return False


def lint(text: str, kind: str) -> list[Finding]:
    findings: list[Finding] = []
    lines = text.replace("\r\n", "\n").split("\n")

    def add(code: str, severity: str, message: str, n: int, line: str) -> None:
        findings.append(Finding(code, severity, message, detail=f"line {n}: {line.strip()[:120]}"))

    for n, line in enumerate(lines, 1):
        if any(r.search(line) for r in SECRET_RES):
            # Never repeat the secret itself: output ends up in logs and transcripts.
            findings.append(Finding("SECRET", "error",
                                    "looks like a secret: remove it before sending",
                                    detail=f"line {n}"))
        if line.lstrip().startswith(">"):
            continue
        if ATTEST_RE.match(line):
            add("ATTESTATION_IN_DRAFT", "error",
                "only the person named can give this tag: the human types it themselves "
                "after deciding, the agent never writes it", n, line)
        for mac in MAC_RE.findall(line):
            if mac.lower() not in NEUTRAL_MACS:
                add("REDACT_MAC", "warn", f"MAC address {mac}: mask part of it if it is yours",
                    n, line)
        for ip in IPV4_RE.findall(line):
            if public_ipv4(ip):
                add("REDACT_IP", "warn", f"public IP address {ip}: mask it if it is yours", n, line)
        for ip in IPV6_RE.findall(line):
            if public_ipv6(ip):
                add("REDACT_IP", "warn", f"public IPv6 address {ip}: mask it if it is yours",
                    n, line)
        m = HOME_RE.search(line)
        if m:
            add("REDACT_HOME", "warn", f"home directory path with user name '{m.group(1)}'",
                n, line)
        if SERIAL_RE.search(line):
            add("REDACT_SERIAL", "warn", "serial number: mask it", n, line)
        if any(r.search(line) for r in MD_RES):
            add("MARKDOWN", "warn", "Markdown markup: kernel mail is plain text", n, line)

    if kind in REPLY_KINDS:
        quoted = [i for i, line in enumerate(lines) if line.lstrip().startswith(">")]
        if quoted:
            first = quoted[0]
            own_before = [line for line in lines[:first]
                          if line.strip() and not ATTRIBUTION_RE.match(line.strip())]
            own_after = [line for line in lines[first:]
                         if line.strip() and not line.lstrip().startswith(">")]
            if own_before and not own_after:
                findings.append(Finding(
                    "TOP_POSTING", "warn",
                    "reply above the quoted text: answer inline, below each quoted part, "
                    "and trim what you do not answer"))
    if kind == "report":
        para: list[str] = []
        for line in lines:
            if line.strip():
                para.append(line)
            elif para:
                break
        if not para or len(para) > MAX_SUMMARY_LINES or LOG_LINE_RE.match(para[0]):
            findings.append(Finding(
                "NO_SUMMARY", "warn",
                f"start with a short summary (at most {MAX_SUMMARY_LINES} lines: what goes "
                "wrong, which kernel, since when), then the details and logs"))
    return findings


def human(payload: dict) -> str:
    return (f"draft_lint: {payload['file']} ({payload['kind']})\n"
            + mlk_common.format_findings(payload["findings"]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check a draft before the human sends it.")
    ap.add_argument("file")
    ap.add_argument("--kind", choices=KINDS, default="reply")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        with open(args.file, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return mlk_common.EXIT_USAGE
    findings = lint(text, args.kind)
    payload = {"file": args.file, "kind": args.kind, "findings": [f.to_dict() for f in findings]}
    mlk_common.emit(payload, args.json, human)
    return mlk_common.exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
