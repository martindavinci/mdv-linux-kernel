#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Results log for the behaviour evals (evals/results/results.jsonl).

  results.py record --scenario ID --agent NAME --agent-version V --model M
                    --met 1,3 --unmet 2 [--note N:TEXT]... [--audit PATH]
                    [--audit-ok yes|no] [--transcript PATH] [--duration-s S]
                    [--cost C] [--skill-commit SHA] [--date YYYY-MM-DD]
  results.py summary

Both take [--file F] [--scenarios PATH]. Expectation numbers are 1-based and
must cover every "expect" line of the scenario exactly once. Each row keeps
the sha256 of the scenario as it was; summary reports rows for a scenario
that has since changed as "obsolete".
Exit: 0 ok, 2 usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
EVALS = os.path.dirname(HERE)
DEFAULT_FILE = os.path.join(EVALS, "results", "results.jsonl")
DEFAULT_SCENARIOS = os.path.join(EVALS, "scenarios.json")


class UsageError(Exception):
    pass


def scenario_hash(scenario: dict) -> str:
    text = json.dumps(scenario, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_scenarios(path: str) -> dict[str, dict]:
    with open(path, encoding="utf-8") as f:
        return {s["id"]: s for s in json.load(f)}


def parse_indexes(text: str | None) -> list[int]:
    if not text:
        return []
    try:
        return [int(x) for x in text.split(",") if x.strip()]
    except ValueError as exc:
        raise UsageError(f"bad expectation list '{text}': use numbers like 1,3") from exc


def skill_commit() -> str | None:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=EVALS, capture_output=True,
                             text=True)
    except OSError:
        return None
    return res.stdout.strip() if res.returncode == 0 else None


def build_row(args, scenario: dict) -> dict:
    met, unmet = parse_indexes(args.met), parse_indexes(args.unmet)
    n = len(scenario["expect"])
    given = met + unmet
    if sorted(given) != list(range(1, n + 1)):
        raise UsageError(f"scenario '{scenario['id']}' has {n} expectation(s): --met and "
                         f"--unmet must list 1..{n} exactly once (got {sorted(given)})")
    notes: dict[int, str] = {}
    for item in args.note:
        num, sep, text = item.partition(":")
        if not sep or not num.strip().isdigit() or not 1 <= int(num) <= n:
            raise UsageError(f"bad --note '{item}': use N:text with N in 1..{n}")
        notes[int(num)] = text.strip()
    expect = [{"n": i, "text": scenario["expect"][i - 1], "met": i in met,
               "note": notes.get(i)} for i in range(1, n + 1)]
    return {
        "date": args.date or date.today().isoformat(),
        "skill_commit": args.skill_commit or skill_commit(),
        "agent": args.agent, "agent_version": args.agent_version, "model": args.model,
        "scenario": scenario["id"], "scenario_sha256": scenario_hash(scenario),
        "expect": expect, "pass": not unmet,
        "audit": {"report": args.audit, "ok": {"yes": True, "no": False}.get(args.audit_ok)},
        "transcript": args.transcript, "duration_s": args.duration_s, "cost": args.cost,
    }


def cmd_record(args) -> int:
    scenarios = load_scenarios(args.scenarios)
    if args.scenario not in scenarios:
        raise UsageError(f"unknown scenario '{args.scenario}'")
    row = build_row(args, scenarios[args.scenario])
    os.makedirs(os.path.dirname(os.path.abspath(args.file)), exist_ok=True)
    with open(args.file, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"recorded {row['scenario']} for {row['agent']}: "
          f"{'pass' if row['pass'] else 'FAIL'}")
    return 0


def read_rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def summarise(rows: list[dict], scenarios: dict[str, dict]) -> dict[tuple[str, str], str]:
    """Latest result per (scenario, agent): pass, FAIL or obsolete."""
    out: dict[tuple[str, str], str] = {}
    for row in rows:
        current = scenarios.get(row["scenario"])
        if current is None or scenario_hash(current) != row.get("scenario_sha256"):
            state = "obsolete"
        else:
            state = "pass" if row["pass"] else "FAIL"
            if row.get("audit", {}).get("ok") is False:
                state = "FAIL (audit)"
        out[(row["scenario"], row["agent"])] = state
    return out


def cmd_summary(args) -> int:
    scenarios = load_scenarios(args.scenarios)
    table = summarise(read_rows(args.file), scenarios)
    agents = sorted({a for _, a in table})
    if not agents:
        print("no results recorded yet")
        return 0
    width = max(len(s) for s in scenarios)
    print(f"{'scenario':{width}}  " + "  ".join(f"{a:14}" for a in agents))
    for sid in scenarios:
        cells = [table.get((sid, a), "-") for a in agents]
        print(f"{sid:{width}}  " + "  ".join(f"{c:14}" for c in cells))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Record and summarise behaviour eval results.")
    sub = ap.add_subparsers(dest="command", required=True)
    for name, func in (("record", cmd_record), ("summary", cmd_summary)):
        p = sub.add_parser(name)
        p.add_argument("--file", default=DEFAULT_FILE)
        p.add_argument("--scenarios", default=DEFAULT_SCENARIOS)
        p.set_defaults(func=func)
        if name == "record":
            p.add_argument("--scenario", required=True)
            p.add_argument("--agent", required=True)
            p.add_argument("--agent-version", required=True)
            p.add_argument("--model", required=True)
            p.add_argument("--met")
            p.add_argument("--unmet")
            p.add_argument("--note", action="append", default=[])
            p.add_argument("--audit")
            p.add_argument("--audit-ok", choices=["yes", "no"])
            p.add_argument("--transcript")
            p.add_argument("--duration-s", type=float)
            p.add_argument("--cost")
            p.add_argument("--skill-commit")
            p.add_argument("--date")
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (UsageError, OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
