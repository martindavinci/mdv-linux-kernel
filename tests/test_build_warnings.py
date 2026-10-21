# SPDX-License-Identifier: GPL-2.0
"""Tests for build_warnings.py."""
from __future__ import annotations

import json
import os
import unittest

from tests.gitfixture import make_tmpdir, run_script

import build_warnings

W_UNUSED = "net/foo.c:{line}:5: warning: unused variable 'x' [-Wunused-variable]"
W_OTHER = "drivers/net/bar.c:7:1: warning: no previous prototype for 'bar' [-Wmissing-prototypes]"


def diff(base: str, patched: str) -> list[dict]:
    return build_warnings.new_diagnostics(build_warnings.parse_diagnostics(base),
                                          build_warnings.parse_diagnostics(patched))


class ParseTest(unittest.TestCase):
    def test_parse_keys(self):
        text = "\n".join([
            "  CC      net/foo.o",
            "./" + W_UNUSED.format(line=10),
            "net/foo.c:3: error: expected ';' before '}' token",
            "sparse: net/foo.c:9:1: not a diagnostic line",
        ])
        counter = build_warnings.parse_diagnostics(text)
        self.assertEqual(counter[("net/foo.c", "warning", "unused variable 'x'", "-Wunused-variable")], 1)
        self.assertEqual(counter[("net/foo.c", "error", "expected ';' before '}' token", None)], 1)
        self.assertEqual(sum(counter.values()), 2)

    def test_line_number_only_changes_are_not_new(self):
        self.assertEqual(diff(W_UNUSED.format(line=10), W_UNUSED.format(line=12)), [])

    def test_crlf_log(self):
        self.assertEqual(diff(W_UNUSED.format(line=10) + "\r\n", W_UNUSED.format(line=12) + "\n"), [])

    def test_new_warning_detected(self):
        new = diff(W_UNUSED.format(line=10), W_UNUSED.format(line=10) + "\n" + W_OTHER)
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["path"], "drivers/net/bar.c")
        self.assertEqual(new[0]["flag"], "-Wmissing-prototypes")
        self.assertEqual(new[0]["count"], 1)

    def test_count_increase_detected(self):
        one = W_UNUSED.format(line=10)
        two = one + "\n" + W_UNUSED.format(line=20)
        new = diff(one, two)
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["count"], 1)

    def test_fixed_warning_is_not_new(self):
        self.assertEqual(diff(W_UNUSED.format(line=10) + "\n" + W_OTHER, W_OTHER), [])


class CliTest(unittest.TestCase):
    def write(self, name: str, text: str) -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "w", newline="") as f:
            f.write(text)
        return path

    def test_cli(self):
        self.tmp = make_tmpdir(self)
        base = self.write("base.log", W_UNUSED.format(line=10) + "\n")
        same = self.write("same.log", W_UNUSED.format(line=11) + "\n")
        worse = self.write("worse.log", W_UNUSED.format(line=11) + "\n" + W_OTHER + "\n")
        res = run_script("build_warnings.py", "diff", "--baseline", base, "--patched", same)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        res = run_script("build_warnings.py", "diff", "--baseline", base, "--patched", worse, "--json")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertEqual(len(json.loads(res.stdout)["new"]), 1)
        res = run_script("build_warnings.py", "diff", "--baseline", base,
                         "--patched", os.path.join(self.tmp, "missing.log"))
        self.assertEqual(res.returncode, 2)


if __name__ == "__main__":
    unittest.main()
