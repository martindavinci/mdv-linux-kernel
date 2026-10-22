# SPDX-License-Identifier: GPL-2.0
"""Tests for check_stable.py."""
from __future__ import annotations

import json
import unittest

from tests.gitfixture import GitRepo, make_tmpdir, run_script

SOB = "Signed-off-by: Test Human <human@example.org>"


class CheckStableTest(unittest.TestCase):
    def setUp(self):
        self.repo = GitRepo.create(make_tmpdir(self))
        self.base = self.repo.commit("init", {"net/foo.c": "int foo;\n"})
        self.upstream = self.repo.commit("net: foo: fix bar", {"net/foo.c": "int foo = 1;\n"})
        self.repo.git("switch", "-q", "-c", "stable", self.base)

    def run_check(self, *extra: str) -> tuple[int, list[str]]:
        res = run_script("check_stable.py", f"{self.base}..HEAD", "--repo", self.repo.path,
                         "--json", *extra)
        self.assertIn(res.returncode, (0, 1), res.stderr)
        return res.returncode, sorted(f["code"] for f in json.loads(res.stdout)["findings"])

    def commit(self, trailers: str, body: str = "Body.", subject: str = "net: foo: fix bar"):
        self.repo.commit(f"{subject}\n\n{body}\n\n{trailers}\n", {"net/foo.c": "int foo = 1;\n"})

    def test_documented_forms_pass(self):
        fixes = f'Fixes: {self.base[:12]} ("init")'
        for cc in ("Cc: stable@vger.kernel.org",
                   "Cc: <stable@vger.kernel.org> # 3.3.x",
                   "Cc: <stable@vger.kernel.org> # 3.3.x: a1f84a3: sched: Check for idle",
                   "Cc: <stable@vger.kernel.org> # after -rc3",
                   "Cc: stable@kernel.org"):
            self.repo.git("reset", "-q", "--hard", self.base)
            self.commit(f"{fixes}\n{cc}\n{SOB}")
            self.assertEqual(self.run_check(), (0, []), cc)

    def test_noautosel_needs_no_fixes(self):
        self.commit(f"Cc: <stable+noautosel@kernel.org> # needs manual backport\n{SOB}")
        self.assertEqual(self.run_check(), (0, []))

    def test_bad_forms(self):
        self.commit(f"Cc: stable@vger.kernel.org 6.6\n{SOB}")
        self.assertEqual(self.run_check(), (1, ["STABLE_FORMAT", "STABLE_NO_FIXES"]))
        self.repo.git("reset", "-q", "--hard", self.base)
        self.commit(f'Fixes: {self.base[:12]} ("init")\nTo: stable@vger.kernel.org\n{SOB}')
        self.assertEqual(self.run_check(), (1, ["STABLE_AS_LIST"]))

    def test_unpaired_brackets_and_bare_noautosel(self):
        fixes = f'Fixes: {self.base[:12]} ("init")'
        for cc in ("Cc: <stable@vger.kernel.org # 6.6.x", "Cc: stable@vger.kernel.org>",
                   "Cc: stable+noautosel@kernel.org"):
            self.repo.git("reset", "-q", "--hard", self.base)
            self.commit(f"{fixes}\n{cc}\n{SOB}")
            self.assertEqual(self.run_check(), (1, ["STABLE_FORMAT"]), cc)

    def test_backport_markers(self):
        self.commit(f"commit {self.upstream} upstream.\n\nBody.", body="Body.")
        self.assertEqual(self.run_check("--backport"), (0, []))
        self.repo.git("reset", "-q", "--hard", self.base)
        self.commit(SOB, body=f"[ Upstream commit {self.upstream} ]\n\nBody.")
        self.assertEqual(self.run_check("--backport"), (0, []))
        self.repo.git("reset", "-q", "--hard", self.base)
        self.commit(SOB)
        self.assertEqual(self.run_check("--backport"), (1, ["BACKPORT_NO_UPSTREAM"]))
        self.repo.git("reset", "-q", "--hard", self.base)
        self.commit(SOB, body="commit " + "b" * 40 + " upstream.")
        self.assertEqual(self.run_check("--backport"), (1, ["BACKPORT_UPSTREAM_UNKNOWN"]))
        self.repo.git("reset", "-q", "--hard", self.base)
        self.commit(SOB, body=f"commit {self.upstream} upstream.", subject="net: foo: other")
        self.assertEqual(self.run_check("--backport"), (1, ["BACKPORT_SUBJECT"]))

    def test_usage_error(self):
        res = run_script("check_stable.py", "nope..HEAD", "--repo", self.repo.path)
        self.assertEqual(res.returncode, 2)


if __name__ == "__main__":
    unittest.main()
