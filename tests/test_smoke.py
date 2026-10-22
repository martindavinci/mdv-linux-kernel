# SPDX-License-Identifier: GPL-2.0
"""Argument handling of evals/smoke.sh (a real run needs a kernel tree)."""
from __future__ import annotations

import os
import shutil
import subprocess
import unittest

from tests.gitfixture import REPO_ROOT, GitRepo, make_tmpdir

BASH = shutil.which("bash")
SMOKE = os.path.join(REPO_ROOT, "evals", "smoke.sh").replace(os.sep, "/")


@unittest.skipIf(BASH is None, "bash not available")
class SmokeArgsTest(unittest.TestCase):
    def run_smoke(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([BASH, SMOKE, *args], capture_output=True, text=True)

    def test_syntax(self):
        res = subprocess.run([BASH, "-n", SMOKE], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_help(self):
        res = self.run_smoke("--help")
        self.assertEqual(res.returncode, 0)
        self.assertIn("--kernel DIR", res.stdout)

    def test_requires_kernel(self):
        res = self.run_smoke()
        self.assertEqual(res.returncode, 2)
        self.assertIn("--kernel", res.stderr)
        self.assertEqual(self.run_smoke("--bogus").returncode, 2)

    def test_rejects_non_kernel_dir(self):
        repo = GitRepo.create(make_tmpdir(self))
        repo.commit("init", {"README": "x\n"})
        res = self.run_smoke("--kernel", repo.path.replace(os.sep, "/"))
        self.assertEqual(res.returncode, 2)
        self.assertIn("not a kernel git checkout", res.stderr)


if __name__ == "__main__":
    unittest.main()
