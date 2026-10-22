#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Evidence store: every validation claim must point to a recorded command run.

One store per series: <git-common-dir>/mdv-linux-kernel/series/<series>/
evidence.json, logs in .../logs/. The series is --series, else $MLK_SERIES,
else the current branch.

  evidence.py init --base <rev> [--force]
  evidence.py require (--preset generic|netdev | --name NAME --category CAT --why TEXT)
  evidence.py run --name NAME --category CAT [--input PATH]... [--scope code|commits]
                  [--results tap|none] [--splats fail|ignore] [--timeout S] -- CMD...
  evidence.py skip --name NAME --category CAT --reason TEXT
  evidence.py note --key KEY --value TEXT
  evidence.py status [--json]
  evidence.py report
  evidence.py where
Every subcommand also takes [--repo DIR] [--series NAME].

An entry is only valid for the exact state it ran on. "code" entries (default
for build, test and repro) depend on the tree of every commit in base..HEAD:
rewording a commit message or adding the human's sign-off keeps them valid.
"commits" entries (default for static, style and review) depend on HEAD
itself. Both go stale with uncommitted or untracked changes, a changed
--input file or a different toolchain.

For "test" entries the log is read as well: a failed TAP/KTAP or KUnit
result, no result at all, or a kernel splat (Call Trace, KASAN, lockdep...)
makes the entry fail even if the command exited 0; all results skipped
makes it "skip".

A required check with no entry is "missing". status exits 1 if anything is
fail, stale or missing, or if nothing was recorded at all.
Writes are serialised with <series dir>/evidence.lock.
Exit: 0 ok, 1 failure/stale/missing/refused (run: 1 if the check failed; the
command's real exit code is stored in the entry), 2 usage or git error.
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
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Callable

import mlk_common

CATEGORIES = ["build", "static", "test", "style", "review", "repro"]
CODE_CATEGORIES = {"build", "test", "repro"}
TOOLS = ["gcc", "clang", "ld", "make", "sparse", "b4", "vng"]
# Build-affecting environment, recorded with each run (not used for staleness).
ENV_KEYS = ["ARCH", "CROSS_COMPILE", "LLVM", "CC", "LD", "HOSTCC", "KCFLAGS",
            "KCPPFLAGS", "KBUILD_OUTPUT", "INSTALL_MOD_PATH"]
STORE_NAME = "evidence.json"
LOCK_NAME = "evidence.lock"
LOCK_TIMEOUT_S = 60.0
LOCK_STALE_S = 15 * 60
TIMEOUT_EXIT = 124

PRESETS = {
    "generic": [
        ("checkpatch", "style", "checkpatch.pl --strict on every patch"),
        ("commit-msg-lint", "style", "changelog checks"),
        ("build-objs", "build", "every patch builds on its own (W=1 C=1)"),
    ],
}
PRESETS["netdev"] = PRESETS["generic"] + [
    ("allmodconfig-W1", "build", "maintainer-netdev.rst: allmodconfig W=1 build"),
    ("allyesconfig-W1", "build", "maintainer-netdev.rst: allyesconfig W=1 build"),
    ("new-warnings-allmodconfig", "build", "no new warnings vs the baseline build"),
    ("new-warnings-allyesconfig", "build", "no new warnings vs the baseline build"),
]
FIXES_CHECK = ("fixes-tags", "static", "the series has Fixes: tags")

TAP_RE = re.compile(r"^\s*(?:#\s*)*(not ok|ok)\s+\d+\b(.*)$")
TAP_DIRECTIVE_RE = re.compile(r"#\s*(SKIP|TODO|XFAIL)\b", re.I)
BAIL_RE = re.compile(r"^\s*(?:#\s*)*Bail out!")
KUNIT_SUMMARY_RE = re.compile(r"Testing complete\.\s+Ran\s+(\d+)\s+tests?(?::\s*(.*))?")
SPLAT_RES = [re.compile(p) for p in (
    r"Call Trace:",
    r"\bBUG: ",
    r"kernel BUG at ",
    r"\b(?:KASAN|UBSAN|KCSAN|KMSAN): ",
    r"WARNING: CPU: \d+ PID: \d+",
    r"WARNING: possible ",
    r"WARNING: suspicious RCU usage",
    r"WARNING: inconsistent lock state",
    r"WARNING: bad unlock balance",
    r"general protection fault",
    r"Kernel panic - not syncing",
    r"\bOops: ",
    r"INFO: task .* blocked for more than",
    r"rcu: INFO: .* detected stalls",
    r"unreferenced object 0x",
)]
MAX_SPLAT_LINES = 10


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def commit_trees(repo: str, base: str) -> list[str]:
    """Tree of every commit in base..HEAD, oldest first."""
    out = mlk_common.git(["log", "--reverse", "--format=%T", f"{base}..HEAD", "--"], repo)
    return out.split()


def current_state(repo: str, base: str | None = None) -> dict:
    # Untracked files count: a new source file can change what a build or test sees.
    status = mlk_common.git(["status", "--porcelain"], repo)
    state = {
        "head": mlk_common.git(["rev-parse", "HEAD"], repo),
        "tree": mlk_common.git(["rev-parse", "HEAD^{tree}"], repo),
        "dirty": bool(status.strip()),
    }
    if base:
        state["trees"] = commit_trees(repo, base)
    return state


def file_sha256(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def default_scope(category: str) -> str:
    return "code" if category in CODE_CATEGORIES else "commits"


def same_code(recorded: dict, state: dict, scope: str) -> bool:
    if scope == "code" and "trees" in recorded and "trees" in state:
        return recorded["trees"] == state["trees"] and recorded.get("tree") == state["tree"]
    return recorded.get("tree") == state["tree"] and recorded.get("head") == state["head"]


def effective_status(entry: dict, state: dict, toolchain: dict | None = None) -> str:
    """The recorded status if nothing it depends on changed, else "stale".

    Depends on: the code ("code" scope: the tree of every commit in the
    series; "commits" scope: HEAD itself, since checkpatch and message
    checks read the messages), a clean working copy then and now, every
    --input file hash, and the toolchain (when given and the entry recorded
    one). A manual skip stays a skip.
    """
    if entry["status"] == "skip" and "cmd" not in entry:
        return "skip"
    recorded = entry.get("state") or {}
    scope = entry.get("scope", "commits")
    if not same_code(recorded, state, scope) or state["dirty"] or recorded.get("dirty"):
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


def parse_results(text: str) -> dict:
    """Count test results in a log: KUnit summary if present, else TAP/KTAP lines."""
    counts = {"ok": 0, "fail": 0, "skip": 0}
    summary = None
    for line in text.splitlines():
        m = KUNIT_SUMMARY_RE.search(line)
        if m:
            summary = m
    if summary:
        fields = dict(re.findall(r"(\w+):\s*(\d+)", summary.group(2) or ""))
        counts["fail"] = sum(int(fields.get(k, 0)) for k in ("failed", "crashed", "errors"))
        counts["skip"] = int(fields.get("skipped", 0))
        counts["ok"] = int(fields.get("passed", 0))
        if not fields:  # "Ran N tests" with nothing else listed
            counts["ok"] = int(summary.group(1))
        counts["source"] = "kunit"
        return counts
    for line in text.splitlines():
        if BAIL_RE.match(line):
            counts["fail"] += 1
            continue
        m = TAP_RE.match(line)
        if not m:
            continue
        directive = TAP_DIRECTIVE_RE.search(m.group(2))
        if directive and directive.group(1).upper() == "SKIP":
            counts["skip"] += 1
        elif m.group(1) == "ok" or directive:  # TODO/XFAIL: expected failure
            counts["ok"] += 1
        else:
            counts["fail"] += 1
    counts["source"] = "tap"
    return counts


def find_splats(text: str) -> list[str]:
    hits = []
    for line in text.splitlines():
        if any(r.search(line) for r in SPLAT_RES):
            hits.append(line.strip()[:300])
            if len(hits) >= MAX_SPLAT_LINES:
                break
    return hits


def interpret(text: str, results: str, splats: str) -> tuple[str | None, str, dict]:
    """Verdict from a log: ("fail"|"skip"|None, reason, details)."""
    details: dict = {}
    if splats == "fail":
        hits = find_splats(text)
        if hits:
            details["splats"] = hits
            return "fail", f"kernel splat in log: {hits[0]}", details
    if results == "tap":
        counts = parse_results(text)
        details["results"] = counts
        total = counts["ok"] + counts["fail"] + counts["skip"]
        if counts["fail"]:
            return "fail", f"{counts['fail']} test result(s) failed", details
        if total == 0:
            return "fail", "no test results in the output: did the test run?", details
        if counts["skip"] == total:
            return "skip", f"all {total} test result(s) skipped", details
    return None, "", details


class UsageError(Exception):
    pass


class Store:
    def __init__(self, repo: str, series: str | None = None):
        self.repo = repo
        self.series = mlk_common.series_name(repo, series)
        self.dir = mlk_common.series_dir(repo, series)
        self.path = os.path.join(self.dir, STORE_NAME)
        self.lock_path = os.path.join(self.dir, LOCK_NAME)

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def load(self) -> dict:
        if not self.exists():
            raise UsageError(f"no evidence store for series '{self.series}' yet: run "
                             "'evidence.py init --base <rev>' first")
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
        store.save({"created": now_iso(), "series": store.series, "base": base,
                    "toolchain": toolchain, "required": {}, "entries": [], "notes": {}})
    print(f"evidence store for series '{store.series}' initialised at {store.path} "
          f"(base {base[:12]})")
    return mlk_common.EXIT_OK


def has_fixes_tag(repo: str, base: str) -> bool:
    for sha in mlk_common.commits_in_range(repo, f"{base}..HEAD"):
        if any(k.lower() == "fixes" for k, _ in
               mlk_common.trailers(mlk_common.commit_message(repo, sha))):
            return True
    return False


def cmd_require(store: Store, args) -> int:
    if args.preset and (args.name or args.category or args.why):
        raise UsageError("use either --preset or --name/--category/--why")
    if args.preset:
        base = store.load()["base"]
        checks = list(PRESETS[args.preset])
        if has_fixes_tag(store.repo, base):
            checks.append(FIXES_CHECK)
    elif args.name and args.category and args.why:
        checks = [(args.name, args.category, args.why)]
    else:
        raise UsageError("require needs --preset, or all of --name, --category and --why")

    def change(data: dict) -> None:
        required = data.setdefault("required", {})
        for name, category, why in checks:
            required[name] = {"category": category, "why": why}

    store.update(change)
    print(f"[evidence] required: {', '.join(name for name, _, _ in checks)}")
    return mlk_common.EXIT_OK


def run_logged(cmd: list[str], cwd: str, log, timeout: float | None) -> tuple[int, bool]:
    """Run cmd, tee its output to log and stdout; return (exit code, timed out)."""
    posix = os.name == "posix"
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=bool(timeout) and posix)
    except OSError as exc:
        msg = f"cannot run command: {exc}\n"
        log.write(msg.encode())
        sys.stderr.write(msg)
        return 127, False
    timed_out = threading.Event()

    def kill() -> None:
        timed_out.set()
        try:
            if posix:
                os.killpg(proc.pid, signal.SIGKILL)  # the whole group: make, vng, qemu...
            else:
                proc.kill()
        except OSError:
            pass

    timer = threading.Timer(timeout, kill) if timeout else None
    if timer:
        timer.start()
    try:
        out = getattr(sys.stdout, "buffer", None)
        for chunk in iter(lambda: proc.stdout.read1(65536), b""):
            log.write(chunk)
            if out is not None:
                out.write(chunk)
                out.flush()
        exit_code = proc.wait()
    finally:
        if timer:
            timer.cancel()
    if timed_out.is_set():
        msg = f"\n[evidence] timed out after {timeout:g}s, killed\n"
        log.write(msg.encode())
        sys.stderr.write(msg)
        return TIMEOUT_EXIT, True
    return exit_code, False


def cmd_run(store: Store, args) -> int:
    if not args.cmd:
        raise UsageError("missing command: evidence.py run --name N --category C -- CMD...")
    if args.timeout is not None and args.timeout <= 0:
        raise UsageError("--timeout must be a positive number of seconds")
    data = store.load()  # fail early if the store was never initialised
    state = current_state(store.repo, data["base"])
    inputs = {}
    for path in args.input:
        full = os.path.abspath(os.path.join(store.repo, path))
        inputs[full] = file_sha256(full)
    toolchain = toolchain_versions()
    is_test = args.category == "test"
    results = args.results or ("tap" if is_test else "none")
    splats = args.splats or ("fail" if is_test else "ignore")
    log_path = store.new_log_path(args.name)
    started, t0 = now_iso(), time.monotonic()
    with open(log_path, "wb") as log:
        log.write(f"$ {shlex.join(args.cmd)}\n".encode())
        exit_code, timed_out = run_logged(args.cmd, store.repo, log, args.timeout)
    entry = {
        "name": args.name, "category": args.category, "cmd": shlex.join(args.cmd),
        "exit_code": exit_code, "status": "pass" if exit_code == 0 else "fail",
        "scope": args.scope or default_scope(args.category),
        "started": started, "duration_s": round(time.monotonic() - t0, 3),
        "log": log_path, "state": state, "inputs": inputs, "toolchain": toolchain,
        "env": {k: os.environ[k] for k in ENV_KEYS if k in os.environ},
        "interpret": {"results": results, "splats": splats},
    }
    if timed_out:
        entry.update(status="fail", timed_out=True, reason=f"timed out after {args.timeout:g}s")
    elif results != "none" or splats != "ignore":
        with open(log_path, encoding="utf-8", errors="replace") as f:
            f.readline()  # the "$ command" header: a grep pattern there is not a splat
            verdict, reason, details = interpret(f.read(), results, splats)
        entry.update(details)
        if verdict == "fail":
            entry.update(status="fail", reason=reason)
        elif verdict == "skip" and entry["status"] == "pass":
            entry.update(status="skip", reason=reason)
    # Reload after the command: other evidence.py calls may have written meanwhile.
    store.update(lambda d: d["entries"].append(entry))
    shown = entry["status"].upper() if entry["status"] != "pass" else "pass"
    why = f": {entry['reason']}" if entry.get("reason") else ""
    print(f"[evidence] {args.name}: {shown} (exit {exit_code}){why}, log {log_path}",
          file=sys.stderr)
    return mlk_common.EXIT_FINDINGS if entry["status"] == "fail" else mlk_common.EXIT_OK


def cmd_skip(store: Store, args) -> int:
    data = store.load()
    entry = {"name": args.name, "category": args.category, "status": "skip",
             "reason": args.reason, "started": now_iso(),
             "state": current_state(store.repo, data["base"])}
    store.update(lambda d: d["entries"].append(entry))
    print(f"[evidence] {args.name}: skipped ({args.reason})")
    return mlk_common.EXIT_OK


def cmd_note(store: Store, args) -> int:
    note = {"value": args.value, "time": now_iso()}
    store.update(lambda data: data.setdefault("notes", {}).setdefault(args.key, []).append(note))
    print(f"[evidence] note '{args.key}' recorded")
    return mlk_common.EXIT_OK


def cmd_where(store: Store, args) -> int:
    print(store.dir)
    return mlk_common.EXIT_OK


def evaluated(store: Store) -> tuple[dict, dict, list[dict]]:
    """Latest entry per name with its effective status, plus missing required checks."""
    data = store.load()
    state = current_state(store.repo, data["base"])
    toolchain = toolchain_versions()
    entries = []
    for entry in latest_entries(data["entries"]):
        shown = dict(entry, effective_status=effective_status(entry, state, toolchain))
        if shown["effective_status"] == "stale" and shown.get("scope", "commits") == "commits":
            # Only messages changed (e.g. the human's sign-off): say so, so the human
            # can decide whether an expensive re-run (external review) is needed.
            code_only = dict(entry, scope="code")
            if "trees" in (entry.get("state") or {}) and \
                    effective_status(code_only, state, toolchain) != "stale":
                shown["code_unchanged"] = True
        entries.append(shown)
    seen = {e["name"] for e in entries}
    for name, req in data.get("required", {}).items():
        if name not in seen:
            entries.append({"name": name, "category": req["category"], "status": "missing",
                            "effective_status": "missing", "why": req["why"]})
    return data, state, entries


def cmd_status(store: Store, args) -> int:
    data, state, entries = evaluated(store)
    bad = [e for e in entries if e["effective_status"] in ("fail", "stale", "missing")]
    ok = bool(entries) and not bad
    payload = {"series": store.series, "base": data["base"], "state": state,
               "entries": entries, "required": data.get("required", {}),
               "notes": data.get("notes", {}), "ok": ok}

    def human(p: dict) -> str:
        lines = [f"series {store.series}: HEAD {state['head'][:12]}"
                 f"{' (dirty)' if state['dirty'] else ''}, base {p['base'][:12]}"]
        if not entries:
            lines.append("no checks recorded: nothing has been validated yet.")
        if not p["required"]:
            lines.append("no required checks set (evidence.py require --preset ...).")
        for e in entries:
            reason = f" - {e['reason']}" if e.get("reason") else ""
            if e["effective_status"] == "missing":
                reason = f" - required: {e['why']}"
            elif e.get("code_unchanged"):
                reason = " - only commit messages changed since it ran"
            lines.append(f"  {e['effective_status'].upper():7} {e['name']} "
                         f"[{e['category']}]{reason}")
        if bad:
            lines.append(f"{len(bad)} check(s) failed, stale or missing: fix and (re-)run them.")
        return "\n".join(lines)

    mlk_common.emit(payload, args.json, human)
    return mlk_common.EXIT_OK if ok else mlk_common.EXIT_FINDINGS


def md_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def results_suffix(entry: dict) -> str:
    counts = entry.get("results")
    if not counts:
        return ""
    parts = [f"{counts['ok']} ok"]
    if counts["skip"]:
        parts.append(f"{counts['skip']} skipped")
    if counts["fail"]:
        parts.append(f"{counts['fail']} failed")
    return f" ({', '.join(parts)})"


def status_label(entry: dict) -> str:
    st = entry["effective_status"]
    if st == "pass":
        return "pass" + results_suffix(entry)
    if st == "fail":
        why = f": {entry['reason']}" if entry.get("reason") else ""
        return f"FAIL (exit {entry.get('exit_code')}){why}"
    if st == "skip":
        return f"SKIPPED: {entry.get('reason', '')}"
    if st == "missing":
        return f"MISSING (required: {entry.get('why', '')})"
    if entry.get("code_unchanged"):
        return "STALE (only commit messages changed; re-run or confirm)"
    return "STALE (re-run)"


def cmd_report(store: Store, args) -> int:
    data, state, entries = evaluated(store)
    out = ["# Evidence report", "", "## State", "",
           f"- Series: `{store.series}`",
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
        out.append("No checks recorded: nothing has been validated.")
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
        p.add_argument("--series", help="series name (default: $MLK_SERIES, else the branch)")
        p.set_defaults(func=func)
        return p

    p = add("init", cmd_init, "start a new evidence store for the series")
    p.add_argument("--base", required=True)
    p.add_argument("--force", action="store_true")
    p = add("require", cmd_require, "declare checks that must be recorded")
    p.add_argument("--preset", choices=sorted(PRESETS))
    p.add_argument("--name")
    p.add_argument("--category", choices=CATEGORIES)
    p.add_argument("--why")
    for name, func, help_text in (("run", cmd_run, "run and record a command"),
                                  ("skip", cmd_skip, "record a check that was not run")):
        p = add(name, func, help_text)
        p.add_argument("--name", required=True)
        p.add_argument("--category", required=True, choices=CATEGORIES)
        if name == "skip":
            p.add_argument("--reason", required=True)
            continue
        p.add_argument("--input", action="append", default=[], metavar="PATH",
                       help="file the result depends on (e.g. build .config, kernel "
                            "image, baseline log); repeatable; a changed hash makes the "
                            "entry stale")
        p.add_argument("--scope", choices=["code", "commits"],
                       help="what the result depends on (default: code for build/test/"
                            "repro, commits otherwise)")
        p.add_argument("--results", choices=["tap", "none"],
                       help="parse TAP/KTAP/KUnit results (default: tap for test)")
        p.add_argument("--splats", choices=["fail", "ignore"],
                       help="fail on kernel splats in the log (default: fail for test)")
        p.add_argument("--timeout", type=float, metavar="SECONDS",
                       help="kill the command after this long and record a failure")
    p = add("note", cmd_note, "append a note")
    p.add_argument("--key", required=True)
    p.add_argument("--value", required=True)
    p = add("status", cmd_status, "show every check with its effective status")
    p.add_argument("--json", action="store_true")
    add("report", cmd_report, "print a Markdown report")
    add("where", cmd_where, "print the series state directory")
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
        store = Store(mlk_common.repo_root(args.repo), args.series)
        return args.func(store, args)
    except (UsageError, mlk_common.GitError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return mlk_common.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
