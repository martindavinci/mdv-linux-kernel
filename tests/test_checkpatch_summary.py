# SPDX-License-Identifier: GPL-2.0
"""Tests for checkpatch_summary.py."""
from __future__ import annotations

import contextlib
import functools
import io
import json
import os
import subprocess
import unittest
from unittest import mock

from tests.gitfixture import GitRepo, make_tmpdir, run_script

import checkpatch_summary

CLEAN = "total: 0 errors, 0 warnings, 0 checks, 12 lines checked\n"

FINDINGS = """\
ERROR:MISSING_SIGN_OFF: Missing Signed-off-by: line(s)

WARNING:LONG_LINE: line length of 101 exceeds 100 columns
#12: FILE: net/foo.c:12:
+\tthis_is_a_long_line(aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa);

CHECK:PARENTHESIS_ALIGNMENT: Alignment should match open parenthesis
#20: FILE: net/foo.c:20:
+\tfoo(a,
+\t\tb);

total: 1 errors, 1 warnings, 1 checks, 30 lines checked
"""

NO_TYPES = """\
WARNING: Prefer 'unsigned int' to bare use of 'unsigned'
#8: FILE: drivers/net/bar.c:8:
+unsigned x;

ERROR: Missing Signed-off-by: line(s)

total: 1 errors, 1 warnings, 0 checks, 9 lines checked
"""


class ParseTest(unittest.TestCase):
    def test_clean(self):
        res = checkpatch_summary.parse_checkpatch(CLEAN)
        self.assertEqual((res["errors"], res["warnings"], res["checks"]), (0, 0, 0))
        self.assertFalse(res["pending_signoff"])
        self.assertEqual(res["items"], [])

    def test_findings(self):
        res = checkpatch_summary.parse_checkpatch(FINDINGS)
        self.assertEqual(res["errors"], 0)
        self.assertTrue(res["pending_signoff"])
        self.assertEqual(res["warnings"], 1)
        self.assertEqual(res["checks"], 1)
        warn = [i for i in res["items"] if i["level"] == "WARNING"][0]
        self.assertEqual(warn["type"], "LONG_LINE")
        self.assertEqual(warn["location"], "net/foo.c:12")
        self.assertEqual(warn["message"], "line length of 101 exceeds 100 columns")
        self.assertNotIn("MISSING_SIGN_OFF", [i["type"] for i in res["items"]])

    def test_without_show_types(self):
        res = checkpatch_summary.parse_checkpatch(NO_TYPES)
        self.assertEqual(res["errors"], 0)
        self.assertTrue(res["pending_signoff"])
        self.assertEqual(res["warnings"], 1)
        self.assertIsNone(res["items"][0]["type"])
        self.assertEqual(res["items"][0]["location"], "drivers/net/bar.c:8")

    def test_crlf_input(self):
        res = checkpatch_summary.parse_checkpatch(FINDINGS.replace("\n", "\r\n"))
        self.assertEqual(res["warnings"], 1)
        self.assertEqual(res["items"][0]["location"], "net/foo.c:12")


class RunCompletenessTest(unittest.TestCase):
    def setUp(self):
        self.repo = GitRepo.create(make_tmpdir(self))
        self.base = self.repo.commit("init", {"scripts/checkpatch.pl": "# fake\n"})
        self.repo.commit("net: one", {"net/a.c": "a\n"})
        self.repo.commit("net: two", {"net/b.c": "b\n"})
        self.range = f"{self.base}..HEAD"

    def fake(self, returncode: int, stdout: str):
        def runner(cmd, **kwargs):
            self.cmd = cmd
            return subprocess.CompletedProcess(cmd, returncode, stdout, "")
        return runner

    def run_cp(self, returncode: int, stdout: str) -> str:
        return checkpatch_summary.run_checkpatch(self.repo.path, self.range, "",
                                                 runner=self.fake(returncode, stdout))

    def test_complete_run(self):
        out = self.run_cp(1, FINDINGS + CLEAN)
        self.assertEqual(checkpatch_summary.parse_checkpatch(out)["warnings"], 1)
        self.assertEqual(self.cmd[:5], ["perl", "scripts/checkpatch.pl", "--strict",
                                        "--show-types", "-g"])

    def test_bad_return_code(self):
        for code in (2, -9):
            with self.assertRaisesRegex(RuntimeError, "did not complete"):
                self.run_cp(code, FINDINGS + CLEAN)

    def test_missing_totals(self):
        with self.assertRaisesRegex(RuntimeError, "did not complete"):
            self.run_cp(1, FINDINGS)

    def test_cli_exit_2(self):
        orig = checkpatch_summary.run_checkpatch
        wrapped = functools.partial(orig, runner=self.fake(0, CLEAN))
        err = io.StringIO()
        with mock.patch.object(checkpatch_summary, "run_checkpatch", wrapped), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = checkpatch_summary.main(["--run", self.range, "--repo", self.repo.path])
        self.assertEqual(code, 2)
        self.assertIn("did not complete", err.getvalue())


class CliTest(unittest.TestCase):
    def test_input_file_and_stdin(self):
        tmp = make_tmpdir(self)
        path = os.path.join(tmp, "cp.txt")
        with open(path, "w", newline="") as f:
            f.write(FINDINGS)
        res = run_script("checkpatch_summary.py", "--json", "--input", path)
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertEqual(json.loads(res.stdout)["warnings"], 1)
        res = run_script("checkpatch_summary.py", "-", stdin=CLEAN)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_pending_signoff_alone_is_clean(self):
        res = run_script("checkpatch_summary.py", "-",
                         stdin="ERROR:MISSING_SIGN_OFF: Missing Signed-off-by: line(s)\n")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_usage_error(self):
        self.assertEqual(run_script("checkpatch_summary.py").returncode, 2)


if __name__ == "__main__":
    unittest.main()
