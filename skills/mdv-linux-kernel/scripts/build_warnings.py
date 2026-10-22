#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Compare build diagnostics of a baseline build and a patched build.

Usage: build_warnings.py diff --baseline LOG --patched LOG [--json]

Compiler diagnostics are keyed by (path, level, message, flag) and counted,
so a warning that only moved to another line is not new, while one more
occurrence of an existing warning is. modpost, objtool, linker and Kconfig
warnings are keyed by tool name and normalised message. Documentation
builds work too: Sphinx "WARNING:"/"ERROR:" lines and kernel-doc warnings
are keyed by file, with absolute paths cut back to the tree's top-level
directory so two checkouts compare equal. As a safety net
(like the netdev CI), lines mentioning warn/error are also counted in both
logs (baseline_count, patched_count).
Exit: 0 nothing new, 1 new diagnostics or more warn/error lines than the
baseline, 2 usage or read error.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter

import mlk_common

DIAG_RE = re.compile(
    r"^(?P<path>[^:\s]+):\d+(?::\d+)?: (?P<level>warning|error): (?P<msg>.*?)"
    r"(?: \[(?P<flag>-W[^\]]+)\])?$")
# Tool diagnostics without a source line: (tool, regex with level/msg groups).
TOOL_RES = [
    ("modpost", re.compile(r"^(?P<level>WARNING|ERROR): modpost: (?P<msg>.*)$")),
    ("objtool", re.compile(r"^(?P<obj>\S+): (?P<level>warning|error): objtool: (?P<msg>.*)$")),
    ("ld", re.compile(r"^(?:\S*/)?(?:[\w.-]+-)?ld(?:\.lld|\.bfd|\.gold)?: "
                      r"(?P<level>warning|error): (?P<msg>.*)$")),
    ("kconfig", re.compile(r"^(?P<level>WARNING): (?P<msg>unmet direct dependencies .*)$")),
]
# Sphinx (make htmldocs): "<path>[:<where>][:<line>]: WARNING: <msg>".
SPHINX_RE = re.compile(
    r"^(?P<path>[^:\s]+)(?::[^:]*?)?(?::\d+)?: (?P<level>WARNING|ERROR|CRITICAL): (?P<msg>.*)$")
# Top-level directories of the kernel tree: absolute paths are cut back to them,
# so two checkouts in different places produce the same keys.
TREE_TOP_RE = re.compile(r"/((?:Documentation|include|drivers|net|kernel|arch|fs|mm|lib|tools|"
                         r"scripts|block|crypto|security|sound|init|ipc|virt|rust|samples|"
                         r"io_uring|usr|certs)/.*)$")
# Raw NIPA-style count: any line that mentions a warning or an error.
WARN_LINE_RE = re.compile(r"(?i)warn|error")


def _normalise(msg: str) -> str:
    """Drop details that move with unrelated edits: line numbers and code offsets."""
    msg = re.sub(r"(\.[chS]|\.rs|\.rst):\d+(?::\d+)?", r"\1", msg)
    return re.sub(r"\+0x[0-9a-fA-F]+(?:/0x[0-9a-fA-F]+)?", "+0x?", msg)


def _tree_path(path: str) -> str:
    while path.startswith("./"):
        path = path[2:]
    if path.startswith("/"):
        m = TREE_TOP_RE.search(path)
        if m:
            return m.group(1)
    return path


def parse_diagnostics(text: str) -> Counter:
    """Count diagnostics keyed by (path, level, message, flag).

    For compiler diagnostics path is the source file; for modpost, objtool,
    the linker and Kconfig it is the tool name and the message is normalised.
    """
    counter: Counter = Counter()
    for line in text.splitlines():
        line = line.rstrip()
        m = DIAG_RE.match(line)
        if m:
            path = _tree_path(m.group("path"))
            counter[(path, m.group("level"), m.group("msg"), m.group("flag"))] += 1
            continue
        m = SPHINX_RE.match(line)
        if m:
            counter[(_tree_path(m.group("path")), m.group("level").lower(),
                     _normalise(m.group("msg")), None)] += 1
            continue
        for tool, regex in TOOL_RES:
            m = regex.match(line)
            if m:
                msg = m.group("msg")
                if "obj" in regex.groupindex:
                    msg = f"{m.group('obj')}: {msg}"
                counter[(tool, m.group("level").lower(), _normalise(msg), None)] += 1
                break
    return counter


def count_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if WARN_LINE_RE.search(line))


def new_diagnostics(base: Counter, patched: Counter) -> list[dict]:
    extra = patched - base
    return [{"path": path, "level": level, "message": msg, "flag": flag, "count": count}
            for (path, level, msg, flag), count in sorted(extra.items(), key=lambda kv: str(kv[0]))]


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def human(payload: dict) -> str:
    counts = (f"warn/error lines: {payload['baseline_count']} in baseline, "
              f"{payload['patched_count']} in patched build")
    out = []
    if payload["new"]:
        out.append(f"{len(payload['new'])} new diagnostic(s):")
        for d in payload["new"]:
            flag = f" [{d['flag']}]" if d["flag"] else ""
            times = f" (+{d['count']})" if d["count"] > 1 else ""
            out.append(f"  {d['path']}: {d['level']}: {d['message']}{flag}{times}")
    else:
        out.append(f"No new diagnostics ({payload['baseline_total']} in baseline, "
                   f"{payload['patched_total']} in patched build).")
    out.append(counts)
    if payload["patched_count"] > payload["baseline_count"]:
        out.append("More warn/error lines than the baseline: look for diagnostics this "
                   "script does not parse.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Diff build diagnostics between two build logs.")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("diff", help="report diagnostics present only in the patched build")
    p.add_argument("--baseline", required=True)
    p.add_argument("--patched", required=True)
    p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        base_text, patched_text = read(args.baseline), read(args.patched)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return mlk_common.EXIT_USAGE
    base, patched = parse_diagnostics(base_text), parse_diagnostics(patched_text)
    new = new_diagnostics(base, patched)
    payload = {"baseline": args.baseline, "patched": args.patched,
               "baseline_total": sum(base.values()), "patched_total": sum(patched.values()),
               "baseline_count": count_lines(base_text),
               "patched_count": count_lines(patched_text), "new": new}
    mlk_common.emit(payload, args.json, human)
    worse = new or payload["patched_count"] > payload["baseline_count"]
    return mlk_common.EXIT_FINDINGS if worse else mlk_common.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
