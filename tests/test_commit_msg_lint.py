# SPDX-License-Identifier: GPL-2.0
"""Tests for commit_msg_lint.py."""
from __future__ import annotations

import json
import unittest

from tests.gitfixture import GitRepo, make_tmpdir, run_script

import commit_msg_lint

SUBJECT = "net: foo: fix NULL deref in bar()"
BODY = """\
bar() dereferences priv->dev before checking that the device was
registered, so probing a port without a PHY oopses. Check the pointer
first and return -ENODEV."""
TRAILERS = """\
Fixes: 0123456789ab ("net: add foo")
Signed-off-by: Test Human <human@example.org>"""


def msg(subject: str = SUBJECT, body: str = BODY, trailers: str = TRAILERS) -> str:
    parts = [subject] + [p for p in (body, trailers) if p]
    return "\n\n".join(parts) + "\n"


def lint_codes(message: str) -> list[str]:
    return sorted(f.code for f in commit_msg_lint.lint_message(message))


class LintTest(unittest.TestCase):
    def test_good_message(self):
        self.assertEqual(commit_msg_lint.lint_message(msg()), [])

    def test_all_codes_are_warn(self):
        findings = commit_msg_lint.lint_message("Fixed it.\n")
        self.assertTrue(findings)
        self.assertEqual({f.severity for f in findings}, {"warn"})

    def test_subject_no_prefix(self):
        self.assertEqual(lint_codes(msg(subject="fix NULL deref in bar()")), ["SUBJECT_NO_PREFIX"])

    def test_subject_too_long(self):
        self.assertEqual(lint_codes(msg(subject="net: foo: fix " + "x" * 70)), ["SUBJECT_TOO_LONG"])

    def test_subject_trailing_period(self):
        self.assertEqual(lint_codes(msg(subject="net: foo: fix bar.")), ["SUBJECT_TRAILING_PERIOD"])

    def test_subject_not_imperative(self):
        self.assertEqual(lint_codes(msg(subject="net: foo: Fixed NULL deref")),
                         ["SUBJECT_NOT_IMPERATIVE"])

    def test_body_missing(self):
        self.assertEqual(lint_codes(msg(body="")), ["BODY_MISSING"])

    def test_body_line_too_long(self):
        self.assertEqual(lint_codes(msg(body=BODY + "\n" + "word " * 17)), ["BODY_LINE_TOO_LONG"])

    def test_url_line_not_too_long(self):
        url = "See https://lore.kernel.org/netdev/" + "a" * 60 + "@example.org/"
        self.assertEqual(lint_codes(msg(body=BODY + "\n" + url)), [])

    def test_indented_line_not_too_long(self):
        oops = "  RIP: 0010:bar+0x12/0x40 [foo] " + "x" * 60
        self.assertEqual(lint_codes(msg(body=BODY + "\n\n" + oops)), [])

    def test_long_trailer_not_too_long(self):
        trailers = 'Fixes: 0123456789ab ("net: foo: add a very long subject line for the foo driver")\n' \
                   "Signed-off-by: Test Human <human@example.org>"
        self.assertEqual(lint_codes(msg(trailers=trailers)), [])

    def test_body_this_patch(self):
        self.assertEqual(lint_codes(msg(body=BODY + " This patch checks it.")), ["BODY_THIS_PATCH"])
        self.assertEqual(lint_codes(msg(body="This commit checks the pointer.")), ["BODY_THIS_PATCH"])

    def test_llm_phrases(self):
        findings = commit_msg_lint.lint_message(
            msg(body=BODY + "\nThis makes the driver more robust and we leverage the helper."))
        self.assertEqual([f.code for f in findings], ["LLM_PHRASES"])
        self.assertIn("robust", findings[0].detail)
        self.assertIn("leverage", findings[0].detail)

    def test_markdown(self):
        self.assertEqual(lint_codes(msg(body=BODY + "\n\n**Root cause**: the check.")), ["MARKDOWN"])
        self.assertEqual(lint_codes(msg(body="## Root cause\n" + BODY)), ["MARKDOWN"])
        self.assertEqual(lint_codes(msg(body=BODY + "\n\n```\nfoo();\n```")), ["MARKDOWN"])

    def test_bullet_heavy(self):
        bullets = "\n".join(f"- item {i}" for i in range(6))
        self.assertEqual(lint_codes(msg(body=BODY + "\n\n" + bullets)), ["BULLET_HEAVY"])
        five = "\n".join(f"- item {i}" for i in range(5))
        self.assertEqual(lint_codes(msg(body=BODY + "\n\n" + five)), [])

    def test_crlf(self):
        self.assertEqual(commit_msg_lint.lint_message(msg().replace("\n", "\r\n")), [])


class CliTest(unittest.TestCase):
    def test_cli(self):
        repo = GitRepo.create(make_tmpdir(self))
        base = repo.commit("init")
        repo.commit(msg(), {"net/foo.c": "x\n"})
        res = run_script("commit_msg_lint.py", f"{base}..HEAD", "--repo", repo.path)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        sha = repo.commit("Fixed stuff.\n")
        res = run_script("commit_msg_lint.py", f"{base}..HEAD", "--repo", repo.path, "--json")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        findings = json.loads(res.stdout)["findings"]
        self.assertTrue(findings)
        self.assertEqual({f["commit"] for f in findings}, {sha})
        res = run_script("commit_msg_lint.py", "bad..HEAD", "--repo", repo.path)
        self.assertEqual(res.returncode, 2)


if __name__ == "__main__":
    unittest.main()
