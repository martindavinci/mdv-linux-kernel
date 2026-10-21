# SPDX-License-Identifier: GPL-2.0
"""Tests for mlk_common."""
from __future__ import annotations

import os
import unittest

from tests.gitfixture import GitRepo, make_tmpdir

import mlk_common


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


class StateDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tmpdir(self)
        self.repo = GitRepo.create(self.tmp)
        self.repo.commit("init", {"README": "x\n"})

    def test_state_dir_in_git_common_dir(self):
        sd = mlk_common.state_dir(self.repo.path)
        self.assertTrue(os.path.isabs(sd))
        self.assertTrue(os.path.isdir(sd))
        self.assertTrue(same_path(sd, os.path.join(self.repo.path, ".git", "mdv-linux-kernel")))

    def test_state_dir_in_worktree(self):
        wt = os.path.join(self.tmp, "wt")
        self.repo.git("worktree", "add", "-q", "-b", "wtbranch", wt)
        self.assertTrue(os.path.isfile(os.path.join(wt, ".git")))
        sd = mlk_common.state_dir(wt)
        self.assertTrue(same_path(sd, os.path.join(self.repo.path, ".git", "mdv-linux-kernel")))
        self.assertTrue(os.path.isdir(sd))


class GitHelpersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tmpdir(self)
        self.repo = GitRepo.create(self.tmp)

    def test_git_error(self):
        with self.assertRaises(mlk_common.GitError):
            mlk_common.git(["rev-parse", "--verify", "nope"], self.repo.path)

    def test_commits_in_range_oldest_first(self):
        a = self.repo.commit("a")
        b = self.repo.commit("b")
        c = self.repo.commit("c")
        self.assertEqual(mlk_common.commits_in_range(self.repo.path, f"{a}..HEAD"), [b, c])

    def test_commit_message_crlf_normalised(self):
        sha = self.repo.commit("net: x\r\n\r\nbody\r\n")
        self.assertEqual(mlk_common.commit_message(self.repo.path, sha), "net: x\n\nbody")


class TrailersTest(unittest.TestCase):
    def test_trailers_crlf_and_trailing_space(self):
        msg = ("net: foo: fix bar\r\n\r\nBody text: not a trailer here.\r\n\r\n"
               "Fixes: abc  \r\nSigned-off-by: A U Thor <a@example.org> \r\n")
        self.assertEqual(mlk_common.trailers(msg), [
            ("Fixes", "abc"),
            ("Signed-off-by", "A U Thor <a@example.org>"),
        ])

    def test_subject_is_not_a_trailer(self):
        self.assertEqual(mlk_common.trailers("net: fix foo"), [])
        self.assertEqual(mlk_common.trailers("net: fix foo\n\nSome body.\n"), [])

    def test_body_paragraph_not_trailers(self):
        msg = "net: x\n\nNote: this is prose\nand continues here.\n\nSigned-off-by: A <a@b>\n"
        self.assertEqual(mlk_common.trailers(msg), [("Signed-off-by", "A <a@b>")])

    def test_finding_to_dict(self):
        f = mlk_common.Finding("X", "warn", "msg", commit="abc")
        self.assertEqual(f.to_dict(), {"code": "X", "severity": "warn", "message": "msg",
                                       "commit": "abc", "detail": None})


if __name__ == "__main__":
    unittest.main()
