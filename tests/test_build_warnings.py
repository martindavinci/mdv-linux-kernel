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


MODPOST = "WARNING: modpost: missing MODULE_DESCRIPTION() in drivers/net/foo.o"
OBJTOOL = ("drivers/net/foo.o: warning: objtool: foo_xmit+0x1a2: "
           "unreachable instruction")
LINKER = "ld: warning: arch/x86/foo.o: missing .note.GNU-stack section implies executable stack"
KCONFIG = ("WARNING: unmet direct dependencies detected for NET_FOO\n"
           "  Depends on [n]: NET [=n]")


class ToolDiagnosticsTest(unittest.TestCase):
    def test_modpost_missing_description_is_new(self):
        new = diff("  MODPOST Module.symvers\n", "  MODPOST Module.symvers\n" + MODPOST)
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["path"], "modpost")
        self.assertIn("MODULE_DESCRIPTION", new[0]["message"])

    def test_objtool_line(self):
        counter = build_warnings.parse_diagnostics(OBJTOOL)
        self.assertEqual(len(counter), 1)
        (key,) = counter
        self.assertEqual(key[0], "objtool")
        # Offsets change with any code change: the same warning is not new.
        moved = OBJTOOL.replace("0x1a2", "0x1b0")
        self.assertEqual(diff(OBJTOOL, moved), [])
        self.assertEqual(len(diff("", OBJTOOL)), 1)

    def test_linker_and_kconfig(self):
        counter = build_warnings.parse_diagnostics(LINKER + "\n" + KCONFIG)
        self.assertEqual(sorted(k[0] for k in counter), ["kconfig", "ld"])
        cross = LINKER.replace("ld:", "aarch64-linux-gnu-ld:", 1)
        self.assertEqual([k[0] for k in build_warnings.parse_diagnostics(cross)], ["ld"])

    def test_line_numbers_stripped_from_tool_messages(self):
        a = "ld: warning: net/foo.c:12: relocation in read-only section"
        b = "ld: warning: net/foo.c:40: relocation in read-only section"
        self.assertEqual(diff(a, b), [])


class LineCountTest(unittest.TestCase):
    def test_line_counts(self):
        counts = build_warnings.count_lines("make: *** [x] Error 2\nWarning: foo\n  CC a.o\n")
        self.assertEqual(counts, 2)


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

    def test_count_increase_with_unparsed_line(self):
        self.tmp = make_tmpdir(self)
        base = self.write("base.log", W_UNUSED.format(line=10) + "\n")
        patched = self.write("patched.log", W_UNUSED.format(line=10) + "\n"
                             "sparse: some odd warning without a location\n")
        res = run_script("build_warnings.py", "diff", "--baseline", base, "--patched", patched,
                         "--json")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        self.assertEqual(payload["new"], [])
        self.assertEqual((payload["baseline_count"], payload["patched_count"]), (1, 2))


if __name__ == "__main__":
    unittest.main()
