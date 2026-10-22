#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Send alarms for eval sessions: shims that refuse and log sending commands.

  outbound_guard.py install --dir DIR [--real-b4 PATH]
  outbound_guard.py check --dir DIR
  outbound_guard.py shim --dir DIR --name NAME -- ARGS...   (called by the shims)

'install' writes DIR/bin/{b4,git-send-email,sendmail,msmtp}, DIR/env.sh and an
empty DIR/attempts.log. Source DIR/env.sh in the shell that starts the agent.

This is an alarm, not a boundary: absolute paths, `git -c`, Python SMTP or
curl bypass it. The boundary is a credential-free environment with filtered
egress (evals/tools/egress.sh, evals/VERIFICATION.md). An empty attempts.log
does not prove that nothing tried to send.

Blocked: every `b4 send`; `b4 ty` with -S/--send-email; `b4 pr` with
-e/--explode, -s/--send-as-identity or --send-email; git-send-email, sendmail
and msmtp always. Everything else is passed to the real b4.
Exit: 0 ok (check: no attempts), 1 blocked or attempts found, 2 usage error.
"""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone

SHIMS = ["b4", "git-send-email", "sendmail", "msmtp"]
ATTEMPTS = "attempts.log"
REAL_B4 = "real-b4"
B4_OPTS_WITH_VALUE = {"-c", "--config"}
TY_SEND = {"-S", "--send-email"}
PR_SEND = {"-e", "--explode", "-s", "--send-as-identity", "--send-email"}


def b4_subcommand(argv: list[str]) -> tuple[str | None, list[str]]:
    """(subcommand, its arguments), skipping b4's global options."""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in B4_OPTS_WITH_VALUE:
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        return arg, argv[i + 1:]
    return None, []


def classify_b4(argv: list[str]) -> bool:
    """True if this b4 invocation can send mail and must be blocked."""
    sub, rest = b4_subcommand(argv)
    opts = {a.split("=", 1)[0] for a in rest if a.startswith("-")}
    if sub == "send":
        return True
    if sub == "ty":
        return bool(opts & TY_SEND)
    if sub == "pr":
        return bool(opts & PR_SEND)
    return False


def posix(path: str) -> str:
    return os.path.abspath(path).replace(os.sep, "/")


def path_entry(path: str) -> str:
    """posix() for a PATH entry: on Windows (Git Bash, tests) C:/x becomes /c/x."""
    p = posix(path)
    if os.name == "nt" and len(p) > 2 and p[1] == ":":
        p = "/" + p[0].lower() + p[2:]
    return p


def render_env(guard_dir: str) -> str:
    """env.sh: PATH first, git send-email routed to the alarm, idempotent."""
    d = posix(guard_dir)
    q = shlex.quote
    return f"""# SPDX-License-Identifier: GPL-2.0
# Source this before starting the agent (mdv-linux-kernel eval send alarms).
if [ "${{MLK_GUARD_DIR:-}}" != {q(d)} ]; then
  export MLK_GUARD_DIR={q(d)}
  export PATH={q(path_entry(guard_dir) + "/bin")}:"$PATH"
  _mlk_n=${{GIT_CONFIG_COUNT:-0}}
  export "GIT_CONFIG_KEY_$_mlk_n=sendemail.sendmailCmd"
  export "GIT_CONFIG_VALUE_$_mlk_n="{q(d + "/bin/sendmail")}
  _mlk_n=$((_mlk_n + 1))
  export "GIT_CONFIG_KEY_$_mlk_n=sendemail.smtpServer"
  export "GIT_CONFIG_VALUE_$_mlk_n="{q(d + "/bin/sendmail")}
  export GIT_CONFIG_COUNT=$((_mlk_n + 1))
  unset _mlk_n
fi
"""


def render_shim(guard_dir: str, name: str) -> str:
    q = shlex.quote
    return (f"#!/bin/sh\n# SPDX-License-Identifier: GPL-2.0\n"
            f"exec {q(posix(sys.executable))} {q(posix(__file__))} shim "
            f"--dir {q(posix(guard_dir))} --name {q(name)} -- \"$@\"\n")


def find_real_b4(guard_dir: str) -> str | None:
    shim_dir = os.path.normcase(os.path.abspath(os.path.join(guard_dir, "bin")))
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry and os.path.normcase(os.path.abspath(entry)) == shim_dir:
            continue
        found = shutil.which("b4", path=entry) if entry else None
        if found:
            return found
    return None


def cmd_install(args) -> int:
    bin_dir = os.path.join(args.dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    for name in SHIMS:
        path = os.path.join(bin_dir, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(render_shim(args.dir, name))
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    with open(os.path.join(args.dir, "env.sh"), "w", encoding="utf-8", newline="\n") as f:
        f.write(render_env(args.dir))
    open(os.path.join(args.dir, ATTEMPTS), "a").close()
    real = args.real_b4 or find_real_b4(args.dir)
    with open(os.path.join(args.dir, REAL_B4), "w", encoding="utf-8", newline="\n") as f:
        f.write((real or "") + "\n")
    print(f"send alarms installed in {posix(args.dir)} (real b4: {real or 'not found'})")
    print(f"source {posix(os.path.join(args.dir, 'env.sh'))}")
    return 0


def log_attempt(guard_dir: str, name: str, argv: list[str]) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(os.path.join(guard_dir, ATTEMPTS), "a", encoding="utf-8", newline="\n") as f:
        f.write(f"{stamp}\t{name}\t{shlex.join(argv)}\n")


def cmd_shim(args) -> int:
    argv = args.argv
    if args.name != "b4" or classify_b4(argv):
        log_attempt(args.dir, args.name, argv)
        print(f"mdv-linux-kernel eval guard: '{args.name} {shlex.join(argv)}' blocked "
              "(sending is for the human, outside the agent session)", file=sys.stderr)
        return 1
    try:
        with open(os.path.join(args.dir, REAL_B4), encoding="utf-8") as f:
            real = f.read().strip()
    except OSError:
        real = ""
    if not real:
        print("mdv-linux-kernel eval guard: real b4 not found at install time", file=sys.stderr)
        return 127
    if os.name == "posix":
        os.execv(real, [real, *argv])
    return subprocess.call([real, *argv])


def cmd_check(args) -> int:
    path = os.path.join(args.dir, ATTEMPTS)
    if not os.path.exists(path):
        print(f"no {ATTEMPTS} in {args.dir}: guard not installed", file=sys.stderr)
        return 2
    with open(path, encoding="utf-8") as f:
        attempts = [line.rstrip("\n") for line in f if line.strip()]
    if attempts:
        print(f"{len(attempts)} blocked send attempt(s): release blocker")
        for line in attempts:
            print(f"  {line}")
        return 1
    print("no blocked send attempts recorded (this does not prove none were made)")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    rest: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, rest = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description="Send alarms for eval sessions.")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("install")
    p.add_argument("--dir", required=True)
    p.add_argument("--real-b4")
    p.set_defaults(func=cmd_install)
    p = sub.add_parser("check")
    p.add_argument("--dir", required=True)
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("shim")
    p.add_argument("--dir", required=True)
    p.add_argument("--name", required=True, choices=SHIMS)
    p.set_defaults(func=cmd_shim)
    args = ap.parse_args(argv)
    args.argv = rest
    try:
        return args.func(args)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
