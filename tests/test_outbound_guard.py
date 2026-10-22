# SPDX-License-Identifier: GPL-2.0
"""Tests for evals/tools/outbound_guard.py and evals/tools/egress.sh."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest

from tests.gitfixture import EVAL_TOOLS_DIR, make_tmpdir

import outbound_guard

GUARD = os.path.join(EVAL_TOOLS_DIR, "outbound_guard.py")
BASH = shutil.which("bash")


def guard(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, GUARD, *args], capture_output=True, text=True)


class ClassifyTest(unittest.TestCase):
    def test_classify_b4(self):
        blocked = [["send"], ["send", "-o", "/tmp/x"], ["send", "--dry-run"],
                   ["send", "--reflect"], ["send", "--web-auth-new"], ["-d", "send"],
                   ["-c", "a=b", "send"], ["ty", "-S"], ["ty", "--send-email"],
                   ["pr", "-e"], ["pr", "--explode"], ["pr", "-s", "me"],
                   ["pr", "--send-as-identity=me"], ["ty", "-aS"], ["ty", "--send"],
                   ["ty", "--send-em"], ["--conf", "k=v", "send"], ["-c", "k=v", "ty", "-S"]]
        allowed = [["prep", "-n", "x"], ["prep", "--check"], ["am", "msgid"], ["ty"],
                   ["ty", "-a"], ["pr", "msgid"], ["trailers", "-u"], [], ["--version"],
                   ["prep", "--show-info"], ["shazam", "send"]]
        for argv in blocked:
            self.assertTrue(outbound_guard.classify_b4(argv), argv)
        for argv in allowed:
            self.assertFalse(outbound_guard.classify_b4(argv), argv)


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.join(make_tmpdir(self), "guard")
        self.fake_b4 = os.path.join(os.path.dirname(self.dir), "real-b4.py")
        with open(self.fake_b4, "w", encoding="utf-8") as f:
            f.write("import sys\nprint('REAL B4', ' '.join(sys.argv[1:]))\n")

    def install(self) -> None:
        # The "real b4" is a script run through the interpreter on Windows too.
        res = guard("install", "--dir", self.dir, "--real-b4", sys.executable)
        self.assertEqual(res.returncode, 0, res.stderr)

    def shim(self, name: str, *argv: str) -> subprocess.CompletedProcess:
        return guard("shim", "--dir", self.dir, "--name", name, "--", *argv)

    def attempts(self) -> list[str]:
        with open(os.path.join(self.dir, "attempts.log"), encoding="utf-8") as f:
            return [line for line in f if line.strip()]

    def test_install_writes_files(self):
        self.install()
        for name in outbound_guard.SHIMS:
            path = os.path.join(self.dir, "bin", name)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertTrue(text.startswith("#!/bin/sh\n"), name)
            self.assertIn("SPDX-License-Identifier: GPL-2.0", text)
            self.assertIn(f"--name {name}", text)
        self.assertEqual(self.attempts(), [])
        self.assertEqual(guard("check", "--dir", self.dir).returncode, 0)

    def test_blocked_calls_are_logged(self):
        self.install()
        res = self.shim("b4", "send", "--dry-run")
        self.assertEqual(res.returncode, 1)
        self.assertIn("blocked", res.stderr)
        self.assertEqual(self.shim("git-send-email", "--to", "x@y", "p.patch").returncode, 1)
        self.assertEqual(self.shim("sendmail", "-i", "x@y").returncode, 1)
        lines = self.attempts()
        self.assertEqual(len(lines), 3)
        self.assertIn("\tb4\tsend --dry-run", lines[0])
        res = guard("check", "--dir", self.dir)
        self.assertEqual(res.returncode, 1)
        self.assertIn("3 blocked send attempt(s)", res.stdout)

    def test_allowed_b4_reaches_real_b4(self):
        self.install()
        res = self.shim("b4", self.fake_b4, "prep", "--check")  # real b4 = python
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("REAL B4 prep --check", res.stdout)
        self.assertEqual(self.attempts(), [])

    def test_missing_real_b4(self):
        res = guard("install", "--dir", self.dir, "--real-b4", "")
        self.assertEqual(res.returncode, 0)
        with open(os.path.join(self.dir, "real-b4"), "w") as f:
            f.write("\n")
        self.assertEqual(self.shim("b4", "prep").returncode, 127)

    def test_check_without_install(self):
        self.assertEqual(guard("check", "--dir", self.dir).returncode, 2)


class EnvTest(unittest.TestCase):
    def test_render_env_appends_git_config_pairs(self):
        text = outbound_guard.render_env("/tmp/g")
        self.assertIn("SPDX-License-Identifier: GPL-2.0", text)
        self.assertIn("sendemail.sendmailCmd", text)
        self.assertIn("sendemail.smtpServer", text)
        self.assertIn("${GIT_CONFIG_COUNT:-0}", text)

    @unittest.skipIf(BASH is None, "bash not available")
    def test_env_sh_idempotent_and_keeps_pairs(self):
        tmp = make_tmpdir(self)
        gdir = os.path.join(tmp, "guard")
        self.assertEqual(guard("install", "--dir", gdir, "--real-b4", "").returncode, 0)
        env_sh = outbound_guard.posix(os.path.join(gdir, "env.sh"))
        script = (
            "export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.name GIT_CONFIG_VALUE_0=Keep\n"
            f". '{env_sh}'\n"
            "p1=\"$PATH\"\n"
            f". '{env_sh}'\n"
            "[ \"$p1\" = \"$PATH\" ] && echo SAMEPATH\n"
            "echo COUNT=$GIT_CONFIG_COUNT K0=$GIT_CONFIG_KEY_0 V0=$GIT_CONFIG_VALUE_0\n"
            "echo K1=$GIT_CONFIG_KEY_1 K2=$GIT_CONFIG_KEY_2\n"
            "echo V1=$GIT_CONFIG_VALUE_1\n"
            "case \"$PATH\" in */guard/bin:*) echo FIRST;; esac\n"
        )
        res = subprocess.run([BASH, "-c", script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = res.stdout
        self.assertIn("SAMEPATH", out)
        self.assertIn("COUNT=3 K0=user.name V0=Keep", out)
        self.assertIn("K1=sendemail.sendmailCmd K2=sendemail.smtpServer", out)
        self.assertRegex(out, r"V1=\S+/guard/bin/sendmail")
        self.assertIn("FIRST", out)

    @unittest.skipIf(BASH is None, "bash not available")
    def test_shim_runs_under_sh(self):
        tmp = make_tmpdir(self)
        gdir = os.path.join(tmp, "guard")
        self.assertEqual(guard("install", "--dir", gdir, "--real-b4", "").returncode, 0)
        shim = outbound_guard.posix(os.path.join(gdir, "bin", "b4"))
        res = subprocess.run([BASH, shim, "send"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("blocked", res.stderr)


@unittest.skipIf(BASH is None, "bash not available")
class EgressScriptTest(unittest.TestCase):
    SCRIPT = os.path.join(EVAL_TOOLS_DIR, "egress.sh").replace(os.sep, "/")

    def run_sh(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([BASH, self.SCRIPT, *args], capture_output=True, text=True)

    def test_syntax(self):
        res = subprocess.run([BASH, "-n", self.SCRIPT], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_usage_errors(self):
        self.assertEqual(self.run_sh().returncode, 2)
        self.assertEqual(self.run_sh("bogus").returncode, 2)
        self.assertEqual(self.run_sh("--help").returncode, 0)
        self.assertEqual(self.run_sh("apply", "--user").returncode, 2)


if __name__ == "__main__":
    unittest.main()
