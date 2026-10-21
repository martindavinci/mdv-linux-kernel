# SPDX-License-Identifier: GPL-2.0
"""Tests for install.sh (skipped when bash is unavailable)."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests.gitfixture import REPO_ROOT

BASH = shutil.which("bash")
INSTALL = os.path.join(REPO_ROOT, "install.sh").replace(os.sep, "/")
MARKER = ".mdv-linux-kernel-install"


def target(home: str, agent: str) -> str:
    sub = {"claude": ".claude", "codex": ".agents"}[agent]
    return os.path.join(home, sub, "skills", "mdv-linux-kernel")


def installed(path: str) -> bool:
    """A usable install: symlink to the skill, or a marked copy."""
    if os.path.islink(path):
        return os.path.isfile(os.path.join(path, "SKILL.md"))
    return os.path.isfile(os.path.join(path, MARKER)) and \
        os.path.isfile(os.path.join(path, "SKILL.md"))


@unittest.skipIf(BASH is None, "bash not available")
class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_install(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [BASH, INSTALL, "--home", self.home.replace(os.sep, "/"), *args],
            capture_output=True, text=True,
        )

    def test_install_symlink_both(self):
        res = self.run_install()
        self.assertEqual(res.returncode, 0, res.stderr)
        for agent in ("claude", "codex"):
            self.assertTrue(installed(target(self.home, agent)), agent)

    def test_install_copy_marker(self):
        res = self.run_install("--agent", "claude", "--copy")
        self.assertEqual(res.returncode, 0, res.stderr)
        path = target(self.home, "claude")
        self.assertFalse(os.path.islink(path))
        self.assertTrue(os.path.isfile(os.path.join(path, MARKER)))
        self.assertFalse(os.path.exists(target(self.home, "codex")))

    def test_refuse_existing_dir(self):
        path = target(self.home, "claude")
        os.makedirs(path)
        with open(os.path.join(path, "SKILL.md"), "w") as f:
            f.write("someone else's skill\n")
        res = self.run_install("--agent", "claude")
        self.assertEqual(res.returncode, 1)
        with open(os.path.join(path, "SKILL.md")) as f:
            self.assertEqual(f.read(), "someone else's skill\n")

    def test_force_replaces(self):
        path = target(self.home, "claude")
        os.makedirs(path)
        res = self.run_install("--agent", "claude", "--force")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(installed(path))

    def test_reinstall_is_idempotent(self):
        self.assertEqual(self.run_install("--agent", "codex").returncode, 0)
        res = self.run_install("--agent", "codex")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(installed(target(self.home, "codex")))

    def test_copy_upgrade_leaves_no_staging_dirs(self):
        self.assertEqual(self.run_install("--agent", "claude", "--copy").returncode, 0)
        res = self.run_install("--agent", "claude", "--copy")
        self.assertEqual(res.returncode, 0, res.stderr)
        parent = os.path.dirname(target(self.home, "claude"))
        self.assertEqual(sorted(os.listdir(parent)), ["mdv-linux-kernel"])
        self.assertTrue(installed(target(self.home, "claude")))

    def test_failed_copy_keeps_previous_install(self):
        self.assertEqual(self.run_install("--agent", "claude", "--copy").returncode, 0)
        path = target(self.home, "claude")
        res = subprocess.run(
            [BASH, INSTALL, "--home", self.home.replace(os.sep, "/"),
             "--agent", "claude", "--copy"],
            capture_output=True, text=True,
            env={**os.environ, "MDV_INSTALL_FAIL_COPY": "1"},
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(installed(path))
        parent = os.path.dirname(path)
        self.assertEqual(sorted(os.listdir(parent)), ["mdv-linux-kernel"])

    def test_uninstall_only_own(self):
        self.assertEqual(self.run_install("--agent", "claude").returncode, 0)
        foreign = target(self.home, "codex")
        os.makedirs(foreign)
        res = self.run_install("--uninstall")
        self.assertEqual(res.returncode, 1)
        self.assertFalse(os.path.exists(target(self.home, "claude")))
        self.assertTrue(os.path.isdir(foreign))

    def test_bad_agent_exit_2(self):
        res = self.run_install("--agent", "vim")
        self.assertEqual(res.returncode, 2)


if __name__ == "__main__":
    unittest.main()
