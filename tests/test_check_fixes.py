# SPDX-License-Identifier: GPL-2.0
"""Tests for check_fixes.py."""
from __future__ import annotations

import hashlib
import json
import os
import unittest

from tests.gitfixture import GitRepo, make_tmpdir, run_script

import check_fixes


def codes(findings) -> list[str]:
    return sorted(f.code for f in findings)


class ParseFixesTest(unittest.TestCase):
    def test_parse_fixes_valid(self):
        tag = check_fixes.parse_fixes('0123456789ab ("net: fix foo")')
        self.assertEqual(tag.sha, "0123456789ab")
        self.assertEqual(tag.title, "net: fix foo")

    def test_parse_fixes_title_with_quotes(self):
        tag = check_fixes.parse_fixes('0123456789ab ("net: fix "foo" (bar)")')
        self.assertEqual(tag.title, 'net: fix "foo" (bar)')

    def test_parse_fixes_bad_format(self):
        self.assertIsNone(check_fixes.parse_fixes("0123456789ab net: fix foo"))


class CheckCommitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tmpdir(self)
        self.repo = GitRepo.create(self.tmp)
        self.repo.commit("init", {"README": "readme\n"})
        self.a = self.repo.commit("net: add foo\n\nAdd foo.\n", {"net/foo.c": "int foo;\n"})

    def fixing(self, fixes_value: str, files=None) -> str:
        msg = (f"net: foo: fix bar\n\nFix the bar.\n\nFixes: {fixes_value}\n"
               "Signed-off-by: Test Human <human@example.org>\n")
        return self.repo.commit(msg, files if files is not None else {"net/foo.c": "int foo = 1;\n"})

    def test_valid_fixes_no_findings(self):
        b = self.fixing(f'{self.a[:12]} ("net: add foo")')
        self.assertEqual(check_fixes.check_commit(self.repo.path, b), [])

    def test_bad_format(self):
        b = self.fixing(f"{self.a[:12]} net: add foo")
        self.assertEqual(codes(check_fixes.check_commit(self.repo.path, b)), ["FIXES_FORMAT"])

    def test_short_sha(self):
        b = self.fixing(f'{self.a[:8]} ("net: add foo")')
        self.assertEqual(codes(check_fixes.check_commit(self.repo.path, b)), ["FIXES_SHORT_SHA"])

    def test_unknown_sha(self):
        b = self.fixing('deadbeefdead ("net: add foo")')
        findings = check_fixes.check_commit(self.repo.path, b)
        self.assertEqual(codes(findings), ["FIXES_UNKNOWN_SHA"])
        self.assertEqual(findings[0].severity, "error")

    def test_title_mismatch(self):
        b = self.fixing(f'{self.a[:12]} ("net: add bar")')
        self.assertEqual(codes(check_fixes.check_commit(self.repo.path, b)), ["FIXES_TITLE_MISMATCH"])

    def test_not_ancestor(self):
        self.repo.git("checkout", "-q", "-b", "other")
        other = self.repo.commit("net: add baz", {"net/foo.c": "int baz;\n"})
        self.repo.git("checkout", "-q", "main")
        b = self.fixing(f'{other[:12]} ("net: add baz")')
        findings = check_fixes.check_commit(self.repo.path, b)
        self.assertEqual(codes(findings), ["FIXES_NOT_ANCESTOR"])
        self.assertEqual(findings[0].severity, "error")

    def test_no_file_overlap_info(self):
        b = self.fixing(f'{self.a[:12]} ("net: add foo")', {"net/other.c": "x\n"})
        findings = check_fixes.check_commit(self.repo.path, b)
        self.assertEqual(codes(findings), ["FIXES_NO_FILE_OVERLAP"])
        self.assertEqual(findings[0].severity, "info")

    def test_ambiguous_sha(self):
        # Find two blob contents whose object ids share a 4-hex prefix, then store both.
        seen: dict[str, bytes] = {}
        i = 0
        while True:
            data = f"blob {i}\n".encode()
            oid = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
            if oid[:4] in seen:
                break
            seen[oid[:4]] = data
            i += 1
        dup = oid[:4]
        for n, content in enumerate((seen[dup], data)):
            path = os.path.join(self.tmp, f"blob{n}")
            with open(path, "wb") as f:
                f.write(content)
            self.repo.git("hash-object", "-w", path)
        b = self.fixing(f'{dup} ("net: add foo")')
        found = codes(check_fixes.check_commit(self.repo.path, b))
        self.assertIn("FIXES_AMBIGUOUS_SHA", found)
        self.assertNotIn("FIXES_UNKNOWN_SHA", found)

    def test_cli_exit_codes(self):
        good = self.fixing(f'{self.a[:12]} ("net: add foo")')
        res = run_script("check_fixes.py", f"{self.a}..{good}", "--repo", self.repo.path)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.fixing('deadbeefdead ("net: add foo")')
        res = run_script("check_fixes.py", f"{self.a}..HEAD", "--repo", self.repo.path, "--json")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        self.assertEqual([f["code"] for f in payload["findings"]], ["FIXES_UNKNOWN_SHA"])
        res = run_script("check_fixes.py", "nosuchref..HEAD", "--repo", self.repo.path)
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
