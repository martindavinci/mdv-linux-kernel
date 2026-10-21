#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Evidence store: every validation claim must point to a recorded command run.

Store: <git-common-dir>/mdv-linux-kernel/evidence.json, logs in .../logs/.

  evidence.py init --base <rev> [--repo DIR] [--force]
  evidence.py run --name NAME --category CAT [--input PATH]... [--repo DIR] -- CMD...
  evidence.py skip --name NAME --category CAT --reason TEXT [--repo DIR]
  evidence.py note --key KEY --value TEXT [--repo DIR]
  evidence.py status [--json] [--repo DIR]
  evidence.py report [--repo DIR]

An entry is only valid for the exact state it ran on: once HEAD, the tree,
the working copy (including untracked files), an --input file or the
toolchain changes, it is reported as "stale".
Writes are serialised with <state dir>/evidence.lock.
Exit: 0 ok, 1 failure/stale/refused (run: 1 if the command failed; its real
exit code is stored in the entry), 2 usage or git error.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Callable

import mlk_common

CATEGORIES = ["build", "static", "test", "style", "review", "repro"]
TOOLS = ["gcc", "clang", "make", "sparse", "b4"]
STORE_NAME = "evidence.json"
LOCK_NAME = "evidence.lock"
LOCK_TIMEOUT_S = 60.0
LOCK_STALE_S = 15 * 60


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def current_state(repo: str) -> dict:
    # Untracked files count: a new source file can change what a build or test sees.
    status = mlk_common.git(["status", "--porcelain"], repo)
    return {
        "head": mlk_common.git(["rev-parse", "HEAD"], repo),
        "tree": mlk_common.git(["rev-parse", "HEAD^{tree}"], repo),
        "dirty": bool(status.strip()),
    }


def file_sha256(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def effective_status(entry: dict, state: dict, toolchain: dict | None = None) -> str:
    """The recorded status if nothing it depends on changed, else "stale".

    Depends on: HEAD and its tree (an amended message also invalidates, since
    checkpatch and commit-message checks read it), a clean working copy then
    and now, every --input file hash, and the toolchain (when given and the
    entry recorded one).
    """
    if entry["status"] == "skip":
        return "skip"
    recorded = entry.get("state") or {}
    if (recorded.get("tree") != state["tree"] or recorded.get("head") != state["head"]
            or state["dirty"] or recorded.get("dirty")):
        return "stale"
    for path, digest in (entry.get("inputs") or {}).items():
        if file_sha256(path) != digest:
            return "stale"
    if toolchain is not None and "toolchain" in entry and entry["toolchain"] != toolchain:
        return "stale"
    return entry["status"]


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


class UsageError(Exception):
    pass


class Store:
    def __init__(self, repo: str):
        self.repo = repo
        self.dir = mlk_common.state_dir(repo)
        self.path = os.path.join(self.dir, STORE_NAME)
        self.lock_path = os.path.join(self.dir, LOCK_NAME)

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def load(self) -> dict:
        if not self.exists():
            raise UsageError("no evidence store yet: run 'evidence.py init --base <rev>' first")
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def save(self, data: dict) -> None:
        fd, tmp = tempfile.mkstemp(prefix="evidence-", suffix=".tmp", dir=self.dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    @contextlib.contextmanager
    def locked(self):
        """Exclusive lock for a read-modify-write of the store."""
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        while True:
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except (FileExistsError, PermissionError):
                try:
                    if time.time() - os.path.getmtime(self.lock_path) > LOCK_STALE_S:
                        os.remove(self.lock_path)  # left behind by a killed process
                        continue
                except OSError:
                    continue  # the lock disappeared meanwhile: retry at once
                if time.monotonic() >= deadline:
                    raise UsageError(f"evidence store is locked ({self.lock_path}); if no other "
                                     "evidence.py is running, remove that file")
                time.sleep(0.1)
        try:
            os.write(fd, f"{os.getpid()}\n".encode())
            os.close(fd)
            yield
        finally:
            try:
                os.remove(self.lock_path)
            except OSError:
                pass

    def update(self, change: Callable[[dict], None]) -> None:
        with self.locked():
            data = self.load()
            change(data)
            self.save(data)

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


def latest_entries(entries: list[dict]) -> list[dict]:
    """Keep the most recent entry per name (a re-run supersedes older results)."""
    latest: dict[str, dict] = {}
    for entry in entries:
        latest.pop(entry["name"], None)
        latest[entry["name"]] = entry
    return list(latest.values())


def cmd_init(store: Store, args) -> int:
    base = mlk_common.git(["rev-parse", "--verify", f"{args.base}^{{commit}}"], store.repo)
    toolchain = toolchain_versions()
    with store.locked():
        if store.exists() and not args.force:
            print(f"refusing to overwrite {store.path} (use --force to start over)",
                  file=sys.stderr)
            return mlk_common.EXIT_FINDINGS
        store.save({"created": now_iso(), "base": base, "toolchain": toolchain,
                    "entries": [], "notes": {}})
    print(f"evidence store initialised at {store.path} (base {base[:12]})")
    return mlk_common.EXIT_OK


def cmd_run(store: Store, args) -> int:
    if not args.cmd:
        raise UsageError("missing command: evidence.py run --name N --category C -- CMD...")
    store.load()  # fail early if the store was never initialised
    state = current_state(store.repo)
    inputs = {}
    for path in args.input:
        full = os.path.abspath(os.path.join(store.repo, path))
        inputs[full] = file_sha256(full)
    toolchain = toolchain_versions()
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
    entry = {
        "name": args.name, "category": args.category, "cmd": shlex.join(args.cmd),
        "exit_code": exit_code, "status": "pass" if exit_code == 0 else "fail",
        "started": started, "duration_s": round(time.monotonic() - t0, 3),
        "log": log_path, "state": state, "inputs": inputs, "toolchain": toolchain,
    }
    # Reload after the command: other evidence.py calls may have written meanwhile.
    store.update(lambda data: data["entries"].append(entry))
    print(f"[evidence] {args.name}: {'pass' if exit_code == 0 else 'FAIL'} "
          f"(exit {exit_code}), log {log_path}", file=sys.stderr)
    return mlk_common.EXIT_OK if exit_code == 0 else mlk_common.EXIT_FINDINGS


def cmd_skip(store: Store, args) -> int:
    entry = {"name": args.name, "category": args.category, "status": "skip",
             "reason": args.reason, "started": now_iso(), "state": current_state(store.repo)}
    store.update(lambda data: data["entries"].append(entry))
    print(f"[evidence] {args.name}: skipped ({args.reason})")
    return mlk_common.EXIT_OK


def cmd_note(store: Store, args) -> int:
    note = {"value": args.value, "time": now_iso()}
    store.update(lambda data: data.setdefault("notes", {}).setdefault(args.key, []).append(note))
    print(f"[evidence] note '{args.key}' recorded")
    return mlk_common.EXIT_OK


def evaluated(store: Store) -> tuple[dict, dict, list[dict]]:
    data = store.load()
    state = current_state(store.repo)
    toolchain = toolchain_versions()
    entries = []
    for entry in latest_entries(data["entries"]):
        entries.append(dict(entry, effective_status=effective_status(entry, state, toolchain)))
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
        else:
            p.add_argument("--input", action="append", default=[], metavar="PATH",
                           help="file the result depends on (e.g. build .config, kernel "
                                "image); repeatable; a changed hash makes the entry stale")
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
