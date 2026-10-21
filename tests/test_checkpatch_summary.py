# SPDX-License-Identifier: GPL-2.0
"""Tests for checkpatch_summary.py."""
from __future__ import annotations

import json
import os
import unittest

from tests.gitfixture import make_tmpdir, run_script

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
