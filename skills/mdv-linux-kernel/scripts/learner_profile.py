#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Optional learner profile: how much to explain, and what was explained already.

  learner_profile.py path
  learner_profile.py show [--json]
  learner_profile.py init --level more|normal|less [--language LANG] [--force]
  learner_profile.py set-level more|normal|less
  learner_profile.py set-language LANG
  learner_profile.py add-concept ID        (e.g. merge-window, dco, napi)
  learner_profile.py forget-concept ID
  learner_profile.py export
  learner_profile.py reset
All take [--path FILE]. Default file: $MLK_PROFILE, else
${XDG_CONFIG_HOME:-~/.config}/mdv-linux-kernel/profile.json, outside any
repository.

Created only when the human asks for it. It holds only what the human chose
(explanation level, language) and the ids of concepts already explained:
never inferred skills, names, employers, transcripts or unpublished bugs.
Unknown fields are dropped on load. Whatever the agent reads from it goes
to the model provider like the rest of the conversation. Remembered
concepts never replace the comprehension check of a patch.
Exit: 0 ok, 1 refused (no profile yet, exists already), 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

LEVELS = ["more", "normal", "less"]
FIELDS = {"version", "level", "language", "concepts", "created", "updated"}
CONCEPT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
LANG_RE = re.compile(r"^[A-Za-z]{2,8}(?:[-_][A-Za-z0-9]{1,8})?$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class Refused(Exception):
    pass


def default_path() -> str:
    if os.environ.get("MLK_PROFILE"):
        return os.environ["MLK_PROFILE"]
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "mdv-linux-kernel", "profile.json")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a profile")
    extra = sorted(set(data) - FIELDS)
    if extra:
        print(f"warning: dropping unknown fields {extra} from the profile", file=sys.stderr)
    clean = {k: v for k, v in data.items() if k in FIELDS}
    # Every field has a fixed shape: nothing else can travel inside one.
    clean["version"] = 1
    if clean.get("level") not in LEVELS:
        clean["level"] = "normal"
    language = clean.get("language")
    clean["language"] = language if isinstance(language, str) and LANG_RE.match(language) else None
    for key in ("created", "updated"):
        value = clean.get(key)
        if not (isinstance(value, str) and DATE_RE.match(value)):
            clean.pop(key, None)
    concepts = clean.get("concepts")
    clean["concepts"] = sorted({c for c in concepts if isinstance(c, str) and CONCEPT_RE.match(c)}) \
        if isinstance(concepts, list) else []
    return clean


def save(path: str, data: dict) -> None:
    data = dict(data, updated=now())
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="profile-", suffix=".tmp",
                               dir=os.path.dirname(os.path.abspath(path)))
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def need(path: str) -> dict:
    data = load(path)
    if data is None:
        raise Refused("no profile yet: create one only if the human wants it "
                      "(learner_profile.py init --level normal)")
    return data


def check_concept(cid: str) -> str:
    if not CONCEPT_RE.match(cid):
        raise ValueError(f"concept id '{cid}': use lowercase words joined by '-', e.g. merge-window")
    return cid


def human(data: dict | None, path: str) -> str:
    if data is None:
        return f"no profile at {path} (the skill works without one)"
    lines = [f"profile: {path}", f"  explanation level: {data['level']}",
             f"  language: {data.get('language') or '-'}",
             f"  concepts explained: {', '.join(data['concepts']) or '-'}",
             f"  created {data.get('created', '?')}, updated {data.get('updated', '?')}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Optional learner profile for mdv-linux-kernel.")
    sub = ap.add_subparsers(dest="command", required=True)

    def add(name: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name)
        p.add_argument("--path", default=None)
        return p

    add("path")
    add("show").add_argument("--json", action="store_true")
    p = add("init")
    p.add_argument("--level", choices=LEVELS, required=True)
    p.add_argument("--language")
    p.add_argument("--force", action="store_true")
    add("set-level").add_argument("level", choices=LEVELS)
    add("set-language").add_argument("language")
    add("add-concept").add_argument("concept")
    add("forget-concept").add_argument("concept")
    add("export")
    add("reset")
    args = ap.parse_args(argv)
    path = args.path or default_path()
    try:
        if args.command == "path":
            print(path)
        elif args.command == "show":
            data = load(path)
            if args.json:
                print(json.dumps(data, indent=2, sort_keys=True))
            else:
                print(human(data, path))
        elif args.command == "init":
            if os.path.exists(path) and not args.force:
                raise Refused(f"a profile exists at {path} (use --force to start over)")
            if args.language and not LANG_RE.match(args.language):
                raise ValueError("language: a code like 'it' or 'pt-BR'")
            stamp = now()
            save(path, {"version": 1, "level": args.level, "language": args.language,
                        "concepts": [], "created": stamp})
            print(f"profile created at {path}")
        elif args.command == "set-level":
            save(path, dict(need(path), level=args.level))
            print(f"explanation level: {args.level}")
        elif args.command == "set-language":
            if not LANG_RE.match(args.language):
                raise ValueError("language: a code like 'it' or 'pt-BR'")
            save(path, dict(need(path), language=args.language))
            print(f"language: {args.language}")
        elif args.command == "add-concept":
            data = need(path)
            concepts = sorted(set(data["concepts"]) | {check_concept(args.concept)})
            save(path, dict(data, concepts=concepts))
            print(f"concept recorded: {args.concept}")
        elif args.command == "forget-concept":
            data = need(path)
            save(path, dict(data, concepts=[c for c in data["concepts"] if c != args.concept]))
            print(f"concept forgotten: {args.concept}")
        elif args.command == "export":
            print(json.dumps(need(path), indent=2, sort_keys=True))
        elif args.command == "reset":
            if os.path.exists(path):
                os.remove(path)
                print(f"profile removed: {path}")
            else:
                print(f"no profile at {path}")
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
