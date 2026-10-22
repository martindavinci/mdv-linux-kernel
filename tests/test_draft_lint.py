# SPDX-License-Identifier: GPL-2.0
"""Tests for draft_lint.py."""
from __future__ import annotations

import json
import os
import unittest

from tests.gitfixture import make_tmpdir, run_script

import draft_lint

GOOD_REVIEW = """\
On Mon, 20 Oct 2026 10:00:00 +0000, A Author wrote:
> +	if (!skb)
> +		return -ENOMEM;

This path runs in softirq context, so the allocation below with
GFP_KERNEL can sleep there. Should it be GFP_ATOMIC?

> +	buf = kmalloc(len, GFP_KERNEL);

Same question here.
"""


def codes(text: str, kind: str = "reply") -> list[str]:
    return sorted(f.code for f in draft_lint.lint(text, kind))


class DraftLintTest(unittest.TestCase):
    def test_clean_review(self):
        self.assertEqual(codes(GOOD_REVIEW, "review"), [])

    def test_attestation_lines(self):
        self.assertEqual(codes("Looks good.\n\nReviewed-by: Me <me@example.org>\n"),
                         ["ATTESTATION_IN_DRAFT"])
        self.assertEqual(codes("Tested-by: Me <me@example.org>\n", "test"), ["ATTESTATION_IN_DRAFT"])
        # Quoted from the patch under review: someone else's text.
        self.assertEqual(codes("> Reviewed-by: Other <o@example.org>\n\nThanks.\n"), [])

    def test_secrets(self):
        for text in ("-----BEGIN OPENSSH PRIVATE KEY-----", "token ghp_" + "a" * 36,
                     "password = hunter2", "AKIAABCDEFGHIJKLMNOP", "> api_key: abcd1234"):
            self.assertIn("SECRET", codes(text), text)
        found = [f for f in draft_lint.lint("ok\npassword = hunter2\n", "reply") if f.code == "SECRET"]
        self.assertEqual(found[0].detail, "line 2")  # the secret is never echoed
        self.assertNotIn("hunter2", found[0].message)

    def test_folded_attestation(self):
        self.assertEqual(codes("Thanks.\n\nReviewed-by:\n  Me <me@example.org>\n"),
                         ["ATTESTATION_IN_DRAFT"])

    def test_ipv6(self):
        self.assertEqual(codes("peer 2a00:1450:4001:82a::200e answered\n"), ["REDACT_IP"])
        self.assertEqual(codes("doc example 2001:db8::1 and fe80::1 and ::1\n"), [])
        self.assertEqual(codes("at 12:34:56 the link went down\n"), [])

    def test_redaction_hints(self):
        text = ("eth0: link up, addr 3c:52:82:aa:bb:cc\n"
                "connected to 8.8.8.8 and 192.168.1.10 and 192.0.2.1\n"
                "loaded from /home/marty/src/foo.ko\n"
                "Serial Number: ABC123XYZ\n"
                "broadcast ff:ff:ff:ff:ff:ff\n")
        self.assertEqual(codes(text), ["REDACT_HOME", "REDACT_IP", "REDACT_MAC", "REDACT_SERIAL"])
        findings = draft_lint.lint(text, "reply")
        ip = [f for f in findings if f.code == "REDACT_IP"]
        self.assertEqual(len(ip), 1)
        self.assertIn("8.8.8.8", ip[0].message)

    def test_markdown(self):
        self.assertEqual(codes("## Summary\n"), ["MARKDOWN"])
        self.assertEqual(codes("this is **important**\n"), ["MARKDOWN"])
        self.assertEqual(codes("```\ncode\n```\n"), ["MARKDOWN", "MARKDOWN"])
        self.assertEqual(codes("# cat /proc/version\nLinux version 7.3\n"), [])

    def test_top_posting(self):
        top = "Looks fine to me, thanks!\n\nOn Mon, X wrote:\n> patch text\n> more\n"
        self.assertEqual(codes(top, "review"), ["TOP_POSTING"])
        self.assertNotIn("TOP_POSTING", codes(top, "report"))
        inline = "On Mon, X wrote:\n> patch text\n\nAnswer.\n"
        self.assertEqual(codes(inline, "review"), [])

    def test_report_summary(self):
        good = ("Since v7.3-rc2 my e1000e NIC drops all traffic after resume.\n"
                "v7.2 works. Vanilla kernel, not tainted.\n\n"
                "[  12.345] e1000e 0000:00:1f.6 eth0: NIC Link is Down\n")
        self.assertEqual(codes(good, "report"), [])
        log_first = "[  12.345] e1000e: NIC Link is Down\n\nIt broke.\n"
        self.assertEqual(codes(log_first, "report"), ["NO_SUMMARY"])
        long_first = "\n".join(f"line {i}" for i in range(8)) + "\n"
        self.assertEqual(codes(long_first, "report"), ["NO_SUMMARY"])

    def test_cli(self):
        tmp = make_tmpdir(self)
        path = os.path.join(tmp, "draft.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(GOOD_REVIEW)
        res = run_script("draft_lint.py", path, "--kind", "review", "--json")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(json.loads(res.stdout)["findings"], [])
        with open(path, "a", encoding="utf-8") as f:
            f.write("\nReviewed-by: Me <me@example.org>\n")
        self.assertEqual(run_script("draft_lint.py", path).returncode, 1)
        self.assertEqual(run_script("draft_lint.py", os.path.join(tmp, "missing")).returncode, 2)


if __name__ == "__main__":
    unittest.main()
