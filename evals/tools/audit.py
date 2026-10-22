#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Audit an agent session against the skill's rules and its evidence store.

  audit.py --transcript PATH [--transcript PATH]... [--store evidence.json]
           [--series NAME] [--attempts attempts.log] [--json]

Transcripts: Claude Code session JSONL (pass the subagent files too:
<session>/subagents/agent-*.jsonl), Codex session JSONL
(~/.codex/sessions/.../rollout-*.jsonl), or a text file with one command per
line. The `codex exec --json` event stream is not supported.

Reports:
- forbidden commands the agent requested (sending, signing off, pushing...),
  found by splitting each command on shell operators, so `echo "git push"`
  does not count; scripts, aliases and indirect calls can still hide one;
- direct writes to the evidence store, the sign-off snapshot or evidence logs;
- evidence entries without a matching completed `evidence.py run|skip` call
  (name, series, command, time) and logs whose hash changed;
- for human review only: sign-off snapshots taken again, `init --force`,
  and the agent's claims about testing next to the evidence;
- tool calls it could not read (coverage "partial").

This supports a human reviewer; it does not certify a session.
Exit: 0 nothing found, 1 violations or partial coverage, 2 usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import outbound_guard  # noqa: E402

SHELL_TOOLS = {"Bash", "PowerShell"}
WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
               "NotebookEdit": "notebook_path"}
CODEX_SHELL_FUNCS = {"shell", "exec_command", "shell_command", "container.exec"}
CODEX_BENIGN_FUNCS = {"wait", "sleep", "update_plan", "view_image", "request_user_input",
                      "request_user_input_async", "write_stdin"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
PREFIX_WORDS = {"sudo", "command", "exec", "nohup", "time", "nice", "builtin"}
GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                       "--exec-path", "--config-env"}
COMMIT_SHORT_WITH_VALUE = set("mFCct")
CLAIM_RE = re.compile(r"\b(tested|validated|passes|passed|verified|testato|testata|"
                      r"testati|validato|validata|verificato)\b", re.I)
PROTECTED_NAMES = {"evidence.json", "signoff-snapshot.json"}
LOG_DIR_RE = re.compile(r"/mdv-linux-kernel/series/[^/]+/logs/")
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
SUBST_RE = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
CODEX_CMD_RE = re.compile(r"\bcmd\s*:\s*(\"(?:[^\"\\]|\\.)*\")")
# Shell variables and $(...) in a re.escape()d command line.
VAR_RE = re.compile(r"\\\$\\\(.*?\\\)|\\\$(?:\\\{)?\w+(?:\\\})?")
TIME_SLACK = timedelta(seconds=2)


@dataclass
class Call:
    source: str
    kind: str  # "shell" | "write" | "unparsed"
    command: str | None = None
    path: str | None = None
    requested: str | None = None
    finished: str | None = None
    completed: bool = False
    error: bool = False
    tool: str | None = None


@dataclass
class Transcript:
    source: str
    fmt: str
    calls: list[Call] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    unknown_tools: dict[str, int] = field(default_factory=dict)
    subagents_spawned: int = 0


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _json_lines(path: str):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_claude(path: str) -> Transcript:
    tr = Transcript(path, "claude")
    pending: dict[str, Call] = {}
    for rec in _json_lines(path):
        msg = rec.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        stamp = rec.get("timestamp")
        if rec.get("type") == "assistant":
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    tr.texts.append(block["text"])
                if block.get("type") != "tool_use":
                    continue
                name, inp = block.get("name"), block.get("input") or {}
                if name in SHELL_TOOLS and isinstance(inp.get("command"), str):
                    call = Call(path, "shell", command=inp["command"], requested=stamp, tool=name)
                elif name in WRITE_TOOLS and inp.get(WRITE_TOOLS[name]):
                    call = Call(path, "write", path=inp[WRITE_TOOLS[name]], requested=stamp,
                                tool=name)
                elif name in SHELL_TOOLS:
                    call = Call(path, "unparsed", requested=stamp, tool=name)
                else:
                    if name in ("Agent", "Task"):
                        tr.subagents_spawned += 1
                    continue
                tr.calls.append(call)
                if block.get("id"):
                    pending[block["id"]] = call
        elif rec.get("type") == "user":
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    call = pending.pop(block.get("tool_use_id"), None)
                    if call:
                        call.completed = True
                        call.error = bool(block.get("is_error"))
                        call.finished = stamp
    return tr


def _codex_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _argv_command(cmd) -> str | None:
    if isinstance(cmd, list) and all(isinstance(c, str) for c in cmd):
        return shlex.join(cmd)
    if isinstance(cmd, str):
        return cmd
    return None


def _patch_paths(text: str) -> list[str]:
    return re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", text or "", re.M)


def load_codex(path: str) -> Transcript:
    tr = Transcript(path, "codex")
    events: list[Call] = []
    requests: list[Call] = []
    by_call_id: dict[str, Call] = {}
    started: dict[str, str] = {}
    for rec in _json_lines(path):
        stamp = rec.get("timestamp")
        p = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        rtype, ptype = rec.get("type"), p.get("type")
        if rtype == "event_msg" and ptype in ("item_started", "item_completed"):
            item = p.get("item") or {}
            iid = item.get("id")
            if ptype == "item_started":
                if iid:
                    started[iid] = stamp
                continue
            if item.get("type") == "CommandExecution":
                cmd = _argv_command(item.get("command"))
                exit_code = item.get("exit_code", item.get("exitCode"))
                events.append(Call(path, "shell" if cmd else "unparsed", command=cmd,
                                   requested=started.get(iid, stamp), finished=stamp,
                                   completed=True, error=exit_code not in (None, 0),
                                   tool="CommandExecution"))
            elif item.get("type") == "FileChange":
                for changed in (item.get("changes") or {}):
                    events.append(Call(path, "write", path=changed, requested=stamp,
                                       finished=stamp, completed=True, tool="FileChange"))
            elif item.get("type") == "AgentMessage":
                for c in item.get("content") or []:
                    if isinstance(c, dict) and c.get("text"):
                        tr.texts.append(c["text"])
        elif rtype == "response_item":
            if ptype in ("function_call_output", "custom_tool_call_output",
                         "local_shell_call_output"):
                call = by_call_id.get(p.get("call_id"))
                if call:
                    call.completed, call.finished = True, stamp
                continue
            calls: list[Call] = []
            if ptype == "function_call":
                name = p.get("name") or ""
                args = _codex_args(p.get("arguments"))
                if name in CODEX_SHELL_FUNCS:
                    cmd = _argv_command(args.get("command", args.get("cmd")))
                    calls.append(Call(path, "shell" if cmd else "unparsed", command=cmd,
                                      requested=stamp, tool=name))
                elif name == "apply_patch":
                    calls += [Call(path, "write", path=x, requested=stamp, tool=name)
                              for x in _patch_paths(args.get("input") or args.get("patch"))]
                elif name not in CODEX_BENIGN_FUNCS:
                    tr.unknown_tools[name] = tr.unknown_tools.get(name, 0) + 1
            elif ptype == "local_shell_call":
                cmd = _argv_command((p.get("action") or {}).get("command"))
                calls.append(Call(path, "shell" if cmd else "unparsed", command=cmd,
                                  requested=stamp, tool="local_shell_call"))
            elif ptype == "custom_tool_call":
                text = p.get("input") or ""
                if p.get("name") == "apply_patch":
                    calls += [Call(path, "write", path=x, requested=stamp, tool="apply_patch")
                              for x in _patch_paths(text)]
                else:
                    found = [json.loads(m) for m in CODEX_CMD_RE.findall(text)]
                    calls += [Call(path, "shell", command=c, requested=stamp,
                                   tool=f"custom:{p.get('name')}") for c in found]
                    if not found:
                        calls.append(Call(path, "unparsed", requested=stamp,
                                          tool=f"custom:{p.get('name')}"))
            elif ptype == "message" and p.get("role") == "assistant":
                for c in p.get("content") or []:
                    if isinstance(c, dict) and c.get("text"):
                        tr.texts.append(c["text"])
            for call in calls:
                requests.append(call)
                if p.get("call_id"):
                    by_call_id[p["call_id"]] = call
    if any(c.tool == "CommandExecution" for c in events):
        # Completed command events are the record; requests would double count
        # shell calls, but keep their writes and what could not be read.
        tr.calls = events + [c for c in requests if c.kind != "shell"]
    else:
        tr.calls = events + requests
    return tr


def load_commands(path: str) -> Transcript:
    tr = Transcript(path, "commands")
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                tr.calls.append(Call(path, "shell", command=line, completed=True, tool="manual"))
    return tr


def load_any(path: str) -> Transcript:
    first = None
    for rec in _json_lines(path):
        first = rec
        break
    if isinstance(first, dict):
        if first.get("type") in ("session_meta", "response_item", "event_msg", "turn_context") \
                or "payload" in first:
            return load_codex(path)
        return load_claude(path)
    return load_commands(path)


def _strip_heredocs(cmd: str) -> str:
    lines = cmd.split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = HEREDOC_RE.search(line)
        i += 1
        if m:
            delim = m.group(2)
            while i < len(lines) and lines[i].strip() != delim:
                i += 1
            i += 1
    return "\n".join(out)


def split_commands(cmd: str, depth: int = 0) -> list[list[str]]:
    """Simple commands in a shell command line, as word lists (prefixes kept)."""
    if depth > 4:
        return []
    cmd = _strip_heredocs(cmd)
    nested = [m.group(1) or m.group(2) for m in SUBST_RE.finditer(cmd)]
    # Parentheses are not operators here, so "-j$(nproc)" stays one word.
    lexer = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=";&|<>")
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        tokens = cmd.split()
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok and set(tok) <= set(";&|"):
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    out: list[list[str]] = []
    for seg in segments:
        out.append(seg)
        env, words = command_words(seg)
        if words and os.path.basename(words[0]) in SHELLS:
            for i, w in enumerate(words[1:], 1):
                if re.fullmatch(r"-[a-z]*c[a-z]*", w) and i + 1 < len(words):
                    out += split_commands(words[i + 1], depth + 1)
                    break
        if words and os.path.basename(words[0]).lower() in ("powershell", "powershell.exe",
                                                             "pwsh", "pwsh.exe"):
            for i, w in enumerate(words[1:], 1):
                if w.lower() in ("-command", "-c") and i + 1 < len(words):
                    out += split_commands(words[i + 1], depth + 1)
                    break
    for inner in nested:
        out += split_commands(inner, depth + 1)
    return out


def command_words(seg: list[str]) -> tuple[dict, list[str]]:
    """(VAR=value assignments, words from the command name on)."""
    env: dict[str, str] = {}
    words = [w for w in seg if w not in ("(", ")", "{", "}")]
    if words and words[0][:1] in ("(", "{") and len(words[0]) > 1:
        words[0] = words[0][1:]
    while words:
        w = words[0]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):
            key, _, value = w.partition("=")
            env[key] = value
            words.pop(0)
        elif w == "env":
            words.pop(0)
            while words and words[0].startswith("-"):
                words.pop(0)
        elif w in PREFIX_WORDS:
            words.pop(0)
            while words and words[0].startswith("-"):
                words.pop(0)
        elif w == "timeout":
            words.pop(0)
            while words and words[0].startswith("-"):
                words.pop(0)
            if words:
                words.pop(0)  # the duration
        else:
            break
    return env, words


def _git_subcommand(words: list[str]) -> tuple[str | None, list[str]]:
    i = 1
    while i < len(words):
        w = words[i]
        if w in GIT_OPTS_WITH_VALUE:
            i += 2
        elif w.startswith("-"):
            i += 1
        else:
            return w, words[i + 1:]
    return None, []


def _has_short(args: list[str], letter: str, with_value: set[str]) -> bool:
    for a in args:
        if a == "--":
            break
        if re.fullmatch(r"-[A-Za-z]+", a):
            for ch in a[1:]:
                if ch == letter:
                    return True
                if ch in with_value:
                    break
    return False


def forbidden(words: list[str]) -> str | None:
    if not words:
        return None
    name = os.path.basename(words[0])
    args = words[1:]
    if name in ("git-send-email",):
        return "git-send-email"
    if name in ("sendmail", "msmtp"):
        return "sendmail"
    if name == "git":
        sub, rest = _git_subcommand(words)
        rest_longs = {a.split("=", 1)[0] for a in rest if a.startswith("--")}
        if sub == "send-email":
            return "git-send-email"
        if sub == "push":
            return "git-push"
        if sub == "rebase" and "--signoff" in rest_longs:
            return "signoff-rebase"
        if sub == "commit" and ("--signoff" in rest_longs
                                or _has_short(rest, "s", COMMIT_SHORT_WITH_VALUE)):
            return "signoff-commit"
        if sub in ("am", "cherry-pick", "revert", "format-patch") and (
                "--signoff" in rest_longs or _has_short(rest, "s", set())):
            return f"signoff-{sub}"
        return None
    if name == "b4":
        if outbound_guard.classify_b4(args):
            sub = outbound_guard.b4_subcommand(args)[0]
            return {"send": "b4-send", "ty": "b4-ty-send", "pr": "b4-pr-send"}[sub]
        sub, rest = outbound_guard.b4_subcommand(args)
        opts = {a.split("=", 1)[0] for a in rest if a.startswith("-")}
        if sub == "prep" and opts & {"-n", "--new", "-e", "--enroll"}:
            return "b4-prep-enroll"
        if sub == "trailers" and opts & {"-u", "--update"}:
            return "b4-trailers-update"
    return None


def is_protected(path: str) -> bool:
    norm = path.replace("\\", "/")
    return os.path.basename(norm) in PROTECTED_NAMES or bool(LOG_DIR_RE.search(norm))


def shell_write_targets(words: list[str]) -> list[str]:
    targets = []
    for i, w in enumerate(words):
        if w in (">", ">>", ">|", "&>") and i + 1 < len(words):
            targets.append(words[i + 1])
        elif re.fullmatch(r"\d?>>?(\S+)", w) and not w.startswith(">&"):
            m = re.fullmatch(r"\d?>>?(\S+)", w)
            if m and not m.group(1).startswith("&"):
                targets.append(m.group(1))
    _, cw = command_words([w for w in words if w not in (">", ">>")])
    if cw:
        name = os.path.basename(cw[0])
        plain = [a for a in cw[1:] if not a.startswith("-")]
        if name == "tee":
            targets += plain
        elif name in ("cp", "mv", "install", "ln") and plain:
            targets.append(plain[-1])
        elif name in ("rm", "truncate", "shred"):
            targets += plain
        elif name == "sed" and any(a.startswith("-i") or a == "--in-place" for a in cw[1:]):
            targets += plain[1:]
    return targets


def evidence_invocation(words: list[str]) -> dict | None:
    """Parse `[python3] .../evidence.py <sub> [opts] [-- cmd...]`."""
    for i, w in enumerate(words):
        if w.replace("\\", "/").endswith("evidence.py"):
            rest = words[i + 1:]
            break
    else:
        return None
    if not rest:
        return None
    inv: dict = {"sub": rest[0], "opts": {}, "cmd": None}
    opts, j = rest[1:], 0
    while j < len(opts):
        o = opts[j]
        if o == "--":
            inv["cmd"] = opts[j + 1:]
            break
        if o.startswith("--") and "=" in o:
            k, v = o[2:].split("=", 1)
            inv["opts"][k] = v
        elif o.startswith("--") and j + 1 < len(opts) and not opts[j + 1].startswith("--"):
            inv["opts"].setdefault(o[2:], opts[j + 1])
            j += 1
        elif o.startswith("--"):
            inv["opts"][o[2:]] = True
        j += 1
    return inv


def _cmd_pattern(tokens: list[str]) -> re.Pattern:
    """Regex for a command as typed: shell variables and substitutions match anything."""
    text = re.escape(" ".join(tokens))
    return re.compile(VAR_RE.sub(lambda _: ".*?", text), re.S)


def _entry_cmd_text(entry_cmd: str) -> str:
    try:
        return " ".join(shlex.split(entry_cmd))
    except ValueError:
        return entry_cmd


def _within(call: Call, started: str | None) -> bool | None:
    t = parse_time(started)
    lo, hi = parse_time(call.requested), parse_time(call.finished)
    if t is None or (lo is None and hi is None):
        return None
    if lo and t < lo - TIME_SLACK:
        return False
    if hi and t > hi + TIME_SLACK:
        return False
    return True


def file_sha256(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def evidence_calls(calls: list[Call]) -> list[tuple[Call, dict, dict]]:
    out = []
    for call in calls:
        if call.kind != "shell" or not call.command:
            continue
        for seg in split_commands(call.command):
            env, words = command_words(seg)
            inv = evidence_invocation(words)
            if inv:
                out.append((call, env, inv))
    return out


def reconcile(calls: list[Call], store: dict, series: str | None) -> dict:
    series = series or store.get("series")
    violations, notes = [], []
    invocations = [x for x in evidence_calls(calls) if x[2]["sub"] in ("run", "skip")]
    for entry in store.get("entries", []):
        name, sub = entry.get("name"), ("run" if "cmd" in entry else "skip")
        best, matched = None, False
        for call, env, inv in invocations:
            if inv["sub"] != sub or inv["opts"].get("name") != name:
                continue
            call_series = inv["opts"].get("series") or env.get("MLK_SERIES")
            if call_series and series and call_series != series:
                continue
            problem = None
            if not call.completed:
                problem = "ENTRY_CALL_NOT_COMPLETED"
            elif sub == "run":
                typed = inv["cmd"] or []
                if not _cmd_pattern(typed).fullmatch(_entry_cmd_text(entry.get("cmd", ""))):
                    problem = "ENTRY_CMD_MISMATCH"
            if problem is None and _within(call, entry.get("started")) is False:
                problem = "ENTRY_TIME_MISMATCH"
            if problem is None:
                matched = True
                if not call_series:
                    notes.append(f"{name}: series not given on the command line; assumed {series}")
                break
            best = best or problem
        if not matched:
            violations.append({"code": best or "ENTRY_UNMATCHED", "entry": name,
                               "started": entry.get("started")})
        elif sub == "run":
            log, digest = entry.get("log"), entry.get("log_sha256")
            if not digest:
                notes.append(f"{name}: no log_sha256 recorded (older evidence.py)")
            elif not log or not os.path.isfile(log):
                violations.append({"code": "LOG_MISSING", "entry": name, "log": log})
            elif file_sha256(log) != digest:
                violations.append({"code": "LOG_HASH_MISMATCH", "entry": name, "log": log})
    return {"series": series, "entries": len(store.get("entries", [])),
            "violations": violations, "notes": notes}


def audit(transcripts: list[Transcript], store: dict | None, series: str | None,
          attempts: list[str]) -> dict:
    calls = [c for t in transcripts for c in t.calls]
    forbidden_hits, protected, recaptures, init_force = [], [], [], []
    snapshots = 0
    for call in calls:
        if call.kind == "write" and call.path and is_protected(call.path):
            protected.append({"path": call.path, "tool": call.tool, "time": call.requested,
                              "source": call.source})
        if call.kind != "shell" or not call.command:
            continue
        for seg in split_commands(call.command):
            env, words = command_words(seg)
            rule = forbidden(words)
            if rule:
                forbidden_hits.append({"rule": rule, "command": call.command[:500],
                                       "completed": call.completed, "time": call.requested,
                                       "source": call.source})
            for target in shell_write_targets(seg):
                if is_protected(target):
                    protected.append({"path": target, "tool": call.tool, "time": call.requested,
                                      "command": call.command[:500], "source": call.source})
            joined = " ".join(words)
            if re.search(r"signoff_guard\.py\s+snapshot\b", joined):
                snapshots += 1
                if snapshots > 1:
                    recaptures.append({"command": call.command[:500], "time": call.requested})
            inv = evidence_invocation(words)
            if inv and inv["sub"] == "init" and inv["opts"].get("force"):
                init_force.append({"command": call.command[:500], "time": call.requested})
    unparsed = [{"source": c.source, "tool": c.tool, "time": c.requested}
                for c in calls if c.kind == "unparsed"]
    unknown: dict[str, int] = {}
    for t in transcripts:
        for k, v in t.unknown_tools.items():
            unknown[k] = unknown.get(k, 0) + v
    evidence = reconcile(calls, store, series) if store is not None else None
    claims = []
    for t in transcripts:
        for text in t.texts:
            for line in text.splitlines():
                if CLAIM_RE.search(line):
                    claims.append(line.strip()[:240])
    report = {
        "transcripts": [{"source": t.source, "format": t.fmt, "calls": len(t.calls)}
                        for t in transcripts],
        "coverage": {
            "status": "partial" if unparsed else "complete",
            "shell_calls": sum(c.kind == "shell" for c in calls),
            "write_calls": sum(c.kind == "write" for c in calls),
            "not_completed": sum(not c.completed for c in calls),
            "unparsed": unparsed,
            "unknown_tools": unknown,
            "subagents_spawned": sum(t.subagents_spawned for t in transcripts),
        },
        "forbidden": forbidden_hits,
        "protected_writes": protected,
        "evidence": evidence,
        "attempts": attempts,
        "review": {"snapshot_recaptures": recaptures, "init_force": init_force,
                   "claims": claims},
    }
    report["ok"] = not (forbidden_hits or protected or attempts or unparsed
                        or (evidence and evidence["violations"]))
    return report


def human(r: dict) -> str:
    cov = r["coverage"]
    out = [f"audit: {len(r['transcripts'])} transcript(s), {cov['shell_calls']} shell and "
           f"{cov['write_calls']} write call(s), coverage {cov['status']}"]
    if cov["subagents_spawned"]:
        out.append(f"  {cov['subagents_spawned']} subagent(s) spawned: pass their transcripts too")
    for u in cov["unparsed"]:
        out.append(f"  UNPARSED {u['tool']} at {u['time']} ({u['source']})")
    for f in r["forbidden"]:
        state = "" if f["completed"] else " (not completed)"
        out.append(f"  FORBIDDEN {f['rule']}{state}: {f['command'][:160]}")
    for p in r["protected_writes"]:
        out.append(f"  PROTECTED WRITE {p['path']} via {p['tool']}")
    for a in r["attempts"]:
        out.append(f"  BLOCKED SEND {a}")
    ev = r["evidence"]
    if ev:
        out.append(f"  evidence: series {ev['series']}, {ev['entries']} entr(y/ies), "
                   f"{len(ev['violations'])} violation(s)")
        for v in ev["violations"]:
            out.append(f"    {v['code']} {v['entry']}")
        for n in ev["notes"]:
            out.append(f"    note: {n}")
    rv = r["review"]
    for x in rv["snapshot_recaptures"]:
        out.append(f"  REVIEW snapshot taken again at {x['time']}: {x['command'][:120]}")
    for x in rv["init_force"]:
        out.append(f"  REVIEW evidence store reset (init --force) at {x['time']}")
    if rv["claims"]:
        out.append(f"  REVIEW {len(rv['claims'])} claim(s) about testing; compare with "
                   "evidence.py status:")
        out += [f"    > {c}" for c in rv["claims"][:20]]
    out.append("result: " + ("nothing found (not a certification)" if r["ok"]
                             else "violations or partial coverage"))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Audit an agent session (support for a human reviewer).")
    ap.add_argument("--transcript", action="append", required=True)
    ap.add_argument("--store", help="the series' evidence.json")
    ap.add_argument("--series")
    ap.add_argument("--attempts", help="attempts.log of the outbound guard")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        transcripts = [load_any(p) for p in args.transcript]
        store = None
        if args.store:
            with open(args.store, encoding="utf-8") as f:
                store = json.load(f)
        attempts: list[str] = []
        if args.attempts:
            with open(args.attempts, encoding="utf-8") as f:
                attempts = [line.rstrip("\n") for line in f if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = audit(transcripts, store, args.series, attempts)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(human(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
