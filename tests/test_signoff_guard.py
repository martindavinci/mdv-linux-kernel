# SPDX-License-Identifier: GPL-2.0
"""Tests for signoff_guard.py."""
from __future__ import annotations

import json
import os
import unittest

from tests.gitfixture import GitRepo, make_tmpdir, run_script

import mlk_common
import signoff_guard

HUMAN_SOB = "Signed-off-by: Test Human <human@example.org>"
B4_COVER = """\
net: foo: fix bar handling

This series fixes the bar handling in foo.

Signed-off-by: Claude <noreply@anthropic.com>

--- b4-submit-tracking ---
# This section is used internally by b4 prep for tracking purposes.
{
  "series": {
    "revision": 1,
    "change-id": "20261021-foo-bar-0123456789ab",
    "prefixes": [],
    "base-branch": "main"
  }
}
"""


class SobValuesTest(unittest.TestCase):
    def test_sob_values_normalised(self):
        msg = "net: x\r\n\r\nBody.\r\n\r\nSigned-off-by:   Test   Human <h@e.org>  \r\nAcked-by: X <x@y>\r\n"
        self.assertEqual(signoff_guard.sob_values(msg), ["Test Human <h@e.org>"])

    def test_b4_cover_letter(self):
        self.assertEqual(signoff_guard.sob_values(B4_COVER), ["Claude <noreply@anthropic.com>"])

    def test_looks_like_ai_identity(self):
        for value in ("Claude <noreply@anthropic.com>", "Codex <x@openai.com>",
                      "GitHub Copilot <c@github.com>", "ChatGPT <a@b>", "gpt-5 <a@b>",
                      "Some LLM <a@b>", "My AI Assistant <a@b>", "Gemini <a@google.com>"):
            self.assertTrue(signoff_guard.looks_like_ai_identity(value), value)
        for value in ("Test Human <human@example.org>", "Eric Allman <eric@example.org>"):
            self.assertFalse(signoff_guard.looks_like_ai_identity(value), value)


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.repo = GitRepo.create(make_tmpdir(self))
        self.base = self.repo.commit("init", {"net/foo.c": "int foo;\n"})

    def guard(self, *args: str):
        return run_script("signoff_guard.py", *args, "--repo", self.repo.path)

    def check(self) -> tuple[int, list[str]]:
        res = self.guard("check", f"{self.base}..HEAD", "--json")
        self.assertIn(res.returncode, (0, 1), res.stderr)
        return res.returncode, sorted(f["code"] for f in json.loads(res.stdout)["findings"])

    def snapshot(self):
        res = self.guard("snapshot", f"{self.base}..HEAD")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_snapshot_file(self):
        self.repo.commit(f"net: foo: fix\n\nBody.\n\n{HUMAN_SOB}\n")
        self.snapshot()
        path = os.path.join(mlk_common.series_dir(self.repo.path), "signoff-snapshot.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["sob"], ["Test Human <human@example.org>"])
        self.assertEqual(data["range"], f"{self.base}..HEAD")
        self.assertIn("created", data)

    def test_snapshot_is_per_series(self):
        self.repo.commit(f"net: foo: fix\n\nBody.\n\n{HUMAN_SOB}\n")
        self.snapshot()
        self.assertEqual(self.check(), (0, []))
        self.repo.git("switch", "-q", "-c", "other")
        self.assertEqual(self.check(), (1, ["NO_SNAPSHOT"]))
        res = self.guard("check", f"{self.base}..HEAD", "--series", "main", "--json")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_preserved_human_sob_not_flagged(self):
        self.repo.commit(f"net: foo: fix\n\nBody.\n\n{HUMAN_SOB}\n", {"net/foo.c": "int foo = 1;\n"})
        self.snapshot()
        # Agent amends the code, then rebases onto a new base: the SoB is preserved.
        with open(os.path.join(self.repo.path, "net", "foo.c"), "w", newline="") as f:
            f.write("int foo = 2;\n")
        self.repo.git("commit", "-q", "-a", "--amend", "--no-edit")
        self.repo.git("checkout", "-q", "-b", "newbase", self.base)
        self.repo.commit("other: unrelated", {"other.c": "x\n"})
        self.repo.git("checkout", "-q", "main")
        self.repo.git("rebase", "-q", "newbase")
        self.base = self.repo.git("rev-parse", "newbase")
        self.assertEqual(self.check(), (0, []))

    def test_new_sob_flagged(self):
        self.repo.commit("net: foo: fix\n\nBody.\n")
        self.snapshot()
        self.repo.commit(f"net: foo: more\n\nBody.\n\n{HUMAN_SOB}\n")
        code, found = self.check()
        self.assertEqual((code, found), (1, ["SOB_ADDED"]))

    def test_duplicate_sob_flagged(self):
        self.repo.commit(f"net: foo: fix\n\nBody.\n\n{HUMAN_SOB}\n")
        self.snapshot()
        self.repo.commit(f"net: foo: more\n\nBody.\n\n{HUMAN_SOB}\n")
        self.assertEqual(self.check(), (1, ["SOB_ADDED"]))

    def test_ai_identity_flagged(self):
        self.repo.commit("net: foo: fix\n\nBody.\n\n"
                         "Co-developed-by: Codex <x@openai.com>\n"
                         "Signed-off-by: Claude <noreply@anthropic.com>\n"
                         f"{HUMAN_SOB}\n")
        self.snapshot()
        res = self.guard("check", f"{self.base}..HEAD", "--json")
        self.assertEqual(res.returncode, 1)
        findings = json.loads(res.stdout)["findings"]
        self.assertEqual([f["code"] for f in findings], ["AI_IDENTITY", "AI_IDENTITY"])
        self.assertEqual({f["severity"] for f in findings}, {"error"})

    def test_assisted_by_not_flagged(self):
        self.repo.commit(f"net: foo: fix\n\nBody.\n\nAssisted-by: LLM sparse\n{HUMAN_SOB}\n")
        self.snapshot()
        self.assertEqual(self.check(), (0, []))

    def test_no_snapshot_warns(self):
        self.repo.commit(f"net: foo: fix\n\nBody.\n\n{HUMAN_SOB}\n")
        self.assertEqual(self.check(), (1, ["NO_SNAPSHOT"]))

    def test_crlf_message(self):
        self.repo.commit("net: foo: fix\r\n\r\nBody.\r\n\r\n" + HUMAN_SOB + "  \r\n")
        self.snapshot()
        self.assertEqual(self.check(), (0, []))
        self.repo.commit("net: foo: more\r\n\r\nBody.\r\n\r\n"
                         "Signed-off-by: Claude <noreply@anthropic.com> \r\n")
        self.assertEqual(self.check(), (1, ["AI_IDENTITY", "SOB_ADDED"]))

    def test_b4_cover_commit_ai_identity(self):
        self.repo.commit(B4_COVER)
        self.snapshot()
        res = self.guard("check", f"{self.base}..HEAD", "--json")
        self.assertEqual(res.returncode, 1)
        self.assertEqual([f["code"] for f in json.loads(res.stdout)["findings"]], ["AI_IDENTITY"])

    def test_snapshot_per_commit_records(self):
        self.repo.commit(f"net: foo: fix\n\nBody.\n\n{HUMAN_SOB}\n")
        self.repo.commit("net: foo: more\n\nBody.\n")
        self.snapshot()
        path = os.path.join(mlk_common.series_dir(self.repo.path), "signoff-snapshot.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["commits"], [
            {"subject": "net: foo: fix", "sob": ["Test Human <human@example.org>"]},
            {"subject": "net: foo: more", "sob": []},
        ])

    def test_sob_moved_to_new_patch_flagged(self):
        self.repo.commit(f"net: foo: fix\n\nBody.\n\n{HUMAN_SOB}\n")
        self.snapshot()
        # The agent rewrites: patch A loses the SoB, a new patch B carries it.
        self.repo.git("commit", "-q", "--amend", "--allow-empty", "-m", "net: foo: fix\n\nBody.\n")
        self.repo.commit(f"net: foo: new\n\nBody.\n\n{HUMAN_SOB}\n")
        res = self.guard("check", f"{self.base}..HEAD", "--json")
        findings = json.loads(res.stdout)["findings"]
        self.assertEqual([(f["code"], f["severity"]) for f in findings],
                         [("SOB_UNVERIFIED", "warn")])
        self.assertEqual(res.returncode, 1)

    def test_sob_moved_to_existing_patch_flagged(self):
        self.repo.commit(f"net: foo: fix\n\nBody.\n\n{HUMAN_SOB}\n")
        self.repo.commit("net: foo: more\n\nBody.\n")
        self.snapshot()
        self.repo.git("reset", "-q", "--hard", self.base)
        self.repo.commit("net: foo: fix\n\nBody.\n")
        self.repo.commit(f"net: foo: more\n\nBody.\n\n{HUMAN_SOB}\n")
        self.assertEqual(self.check(), (1, ["SOB_ADDED"]))

    def test_old_snapshot_without_commits(self):
        self.repo.commit(f"net: foo: fix\n\nBody.\n\n{HUMAN_SOB}\n")
        path = os.path.join(mlk_common.series_dir(self.repo.path), "signoff-snapshot.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"created": "2026-10-01T00:00:00Z", "range": "x",
                       "sob": ["Test Human <human@example.org>"]}, f)
        self.repo.commit(f"net: foo: new\n\nBody.\n\n{HUMAN_SOB}\n")
        self.assertEqual(self.check(), (1, ["SOB_ADDED"]))

    def test_bad_range(self):
        self.assertEqual(self.guard("check", "nosuch..HEAD").returncode, 2)


if __name__ == "__main__":
    unittest.main()
