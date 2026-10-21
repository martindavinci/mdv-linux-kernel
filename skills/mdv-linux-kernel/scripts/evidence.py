#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Evidence store: every validation claim must point to a recorded command run.

Store: <git-common-dir>/mdv-linux-kernel/evidence.json, logs in .../logs/.

  evidence.py init --base <rev> [--repo DIR] [--force]
  evidence.py run --name NAME --category CAT [--repo DIR] -- CMD...
  evidence.py skip --name NAME --category CAT --reason TEXT [--repo DIR]
  evidence.py note --key KEY --value TEXT [--repo DIR]
  evidence.py status [--json] [--repo DIR]
  evidence.py report [--repo DIR]

An entry is only valid for the exact commit and tree it ran on: once HEAD,
the tree or the working copy changes, it is reported as "stale".
Exit: 0 ok, 1 failure/stale/refused (run: the command's own exit code),
2 usage or git error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Callable

import mlk_common

CATEGORIES = ["build", "static", "test", "style", "review", "repro"]
TOOLS = ["gcc", "clang", "make", "sparse", "b4"]
STORE_NAME = "evidence.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def current_state(repo: str) -> dict:
    status = mlk_common.git(["status", "--porcelain", "--untracked-files=no"], repo)
    return {
        "head": mlk_common.git(["rev-parse", "HEAD"], repo),
        "tree": mlk_common.git(["rev-parse", "HEAD^{tree}"], repo),
        "dirty": bool(status.strip()),
    }


def effective_status(entry: dict, state: dict) -> str:
    if entry["status"] == "skip":
        return "skip"
    recorded = entry.get("state") or {}
    # Stricter than tree-only: an amended commit message also invalidates
    # evidence (checkpatch and commit-message checks read it).
    if (recorded.get("tree") == state["tree"] and recorded.get("head") == state["head"]
            and not state["dirty"] and not recorded.get("dirty")):
        return entry["status"]
    return "stale"


def toolchain_versions(which: Callable[[str], str | None] = shutil.which) -> dict:
    versions: dict[str, str | None] = {}
    for tool in TOOLS:
        versions[tool] = None
        path = which(tool)
        if not path:
            continue
        try:
            res = subprocess.run([path, "--version"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        lines = (res.stdout or res.stderr).strip().splitlines()
        versions[tool] = lines[0].strip() if lines else None
    return versions


class Store:
    def __init__(self, repo: str):
        self.repo = repo
        self.dir = mlk_common.state_dir(repo)
        self.path = os.path.join(self.dir, STORE_NAME)

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def load(self) -> dict:
        if not self.exists():
            raise UsageError("no evidence store yet: run 'evidence.py init --base <rev>' first")
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def save(self, data: dict) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, self.path)

    def new_log_path(self, name: str) -> str:
        logs = os.path.join(self.dir, "logs")
        os.makedirs(logs, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name) or "run"
        path = os.path.join(logs, f"{stamp}-{safe}.log")
        n = 1
        while os.path.exists(path):
            n += 1
            path = os.path.join(logs, f"{stamp}-{safe}-{n}.log")
        return path


class UsageError(Exception):
    pass


def latest_entries(entries: list[dict]) -> list[dict]:
    """Keep the most recent entry per name (a re-run supersedes older results)."""
    latest: dict[str, dict] = {}
    for entry in entries:
        latest.pop(entry["name"], None)
        latest[entry["name"]] = entry
    return list(latest.values())


def cmd_init(store: Store, args) -> int:
    if store.exists() and not args.force:
        print(f"refusing to overwrite {store.path} (use --force to start over)", file=sys.stderr)
        return mlk_common.EXIT_FINDINGS
    base = mlk_common.git(["rev-parse", "--verify", f"{args.base}^{{commit}}"], store.repo)
    store.save({"created": now_iso(), "base": base, "toolchain": toolchain_versions(),
                "entries": [], "notes": {}})
    print(f"evidence store initialised at {store.path} (base {base[:12]})")
    return mlk_common.EXIT_OK


def cmd_run(store: Store, args) -> int:
    if not args.cmd:
        raise UsageError("missing command: evidence.py run --name N --category C -- CMD...")
    data = store.load()
    state = current_state(store.repo)
    log_path = store.new_log_path(args.name)
    started, t0 = now_iso(), time.monotonic()
    with open(log_path, "wb") as log:
        log.write(f"$ {shlex.join(args.cmd)}\n".encode())
        try:
            proc = subprocess.Popen(args.cmd, cwd=store.repo, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
        except OSError as exc:
            msg = f"cannot run command: {exc}\n"
            log.write(msg.encode())
            sys.stderr.write(msg)
            exit_code = 127
        else:
            out = getattr(sys.stdout, "buffer", None)
            for chunk in iter(lambda: proc.stdout.read1(65536), b""):
                log.write(chunk)
                if out is not None:
                    out.write(chunk)
                    out.flush()
            exit_code = proc.wait()
    data["entries"].append({
        "name": args.name, "category": args.category, "cmd": shlex.join(args.cmd),
        "exit_code": exit_code, "status": "pass" if exit_code == 0 else "fail",
        "started": started, "duration_s": round(time.monotonic() - t0, 3),
        "log": log_path, "state": state,
    })
    store.save(data)
    print(f"[evidence] {args.name}: {'pass' if exit_code == 0 else 'FAIL'} "
          f"(exit {exit_code}), log {log_path}", file=sys.stderr)
    return exit_code


def cmd_skip(store: Store, args) -> int:
    data = store.load()
    data["entries"].append({
        "name": args.name, "category": args.category, "status": "skip", "reason": args.reason,
        "started": now_iso(), "state": current_state(store.repo),
    })
    store.save(data)
    print(f"[evidence] {args.name}: skipped ({args.reason})")
    return mlk_common.EXIT_OK


def cmd_note(store: Store, args) -> int:
    data = store.load()
    data.setdefault("notes", {}).setdefault(args.key, []).append(
        {"value": args.value, "time": now_iso()})
    store.save(data)
    print(f"[evidence] note '{args.key}' recorded")
    return mlk_common.EXIT_OK


def evaluated(store: Store) -> tuple[dict, dict, list[dict]]:
    data = store.load()
    state = current_state(store.repo)
    entries = []
    for entry in latest_entries(data["entries"]):
        entries.append(dict(entry, effective_status=effective_status(entry, state)))
    return data, state, entries


def cmd_status(store: Store, args) -> int:
    data, state, entries = evaluated(store)
    bad = [e for e in entries if e["effective_status"] in ("fail", "stale")]
    payload = {"base": data["base"], "state": state, "entries": entries,
               "notes": data.get("notes", {}), "ok": not bad}

    def human(p: dict) -> str:
        lines = [f"HEAD {state['head'][:12]}{' (dirty)' if state['dirty'] else ''}, "
                 f"base {p['base'][:12]}"]
        if not entries:
            lines.append("no checks recorded")
        for e in entries:
            lines.append(f"  {e['effective_status'].upper():5} {e['name']} [{e['category']}]")
        if bad:
            lines.append(f"{len(bad)} check(s) failed or stale: fix and re-run them.")
        return "\n".join(lines)

    mlk_common.emit(payload, args.json, human)
    return mlk_common.EXIT_FINDINGS if bad else mlk_common.EXIT_OK


def md_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def status_label(entry: dict) -> str:
    st = entry["effective_status"]
    if st == "pass":
        return "pass"
    if st == "fail":
        return f"FAIL (exit {entry.get('exit_code')})"
    if st == "skip":
        return f"SKIPPED: {entry.get('reason', '')}"
    return "STALE (re-run)"


def cmd_report(store: Store, args) -> int:
    data, state, entries = evaluated(store)
    out = ["# Evidence report", "", "## State", "",
           f"- Base: `{data['base']}`",
           f"- HEAD: `{state['head']}`",
           f"- Tree: `{state['tree']}`",
           f"- Working copy: {'modified (results below are stale)' if state['dirty'] else 'clean'}",
           f"- Generated: {now_iso()}", "", "## Toolchain", ""]
    for tool, version in data.get("toolchain", {}).items():
        out.append(f"- {tool}: {version or 'not found'}")
    out += ["", "## Checks", ""]
    if entries:
        out += ["| Name | Category | Status | Command | Log |", "|---|---|---|---|---|"]
        for e in entries:
            out.append("| " + " | ".join(md_cell(x) for x in (
                e["name"], e["category"], status_label(e),
                f"`{e['cmd']}`" if e.get("cmd") else "-", e.get("log") or "-")) + " |")
    else:
        out.append("No checks recorded.")
    out += ["", "## Notes", ""]
    notes = data.get("notes", {})
    if not notes:
        out.append("No notes recorded.")
    for key, values in notes.items():
        for note in values:
            out.append(f"- **{key}** ({note['time']}): {note['value']}")
    print("\n".join(out))
    return mlk_common.EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Record validation evidence tied to the tree state.")
    sub = ap.add_subparsers(dest="command", required=True)

    def add(name: str, func, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--repo", default=".")
        p.set_defaults(func=func)
        return p

    p = add("init", cmd_init, "start a new evidence store")
    p.add_argument("--base", required=True)
    p.add_argument("--force", action="store_true")
    for name, func, help_text in (("run", cmd_run, "run and record a command"),
                                  ("skip", cmd_skip, "record a check that was not run")):
        p = add(name, func, help_text)
        p.add_argument("--name", required=True)
        p.add_argument("--category", required=True, choices=CATEGORIES)
        if name == "skip":
            p.add_argument("--reason", required=True)
    p = add("note", cmd_note, "append a note")
    p.add_argument("--key", required=True)
    p.add_argument("--value", required=True)
    p = add("status", cmd_status, "show every check with its effective status")
    p.add_argument("--json", action="store_true")
    add("report", cmd_report, "print a Markdown report")
    return ap


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    args = build_parser().parse_args(argv)
    args.cmd = cmd
    if cmd and args.command != "run":
        print("error: '-- CMD' is only valid with 'run'", file=sys.stderr)
        return mlk_common.EXIT_USAGE
    try:
        store = Store(mlk_common.repo_root(args.repo))
        return args.func(store, args)
    except (UsageError, mlk_common.GitError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return mlk_common.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
