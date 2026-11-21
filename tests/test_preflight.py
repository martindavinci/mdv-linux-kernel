# SPDX-License-Identifier: GPL-2.0
"""Tests for preflight.py."""
from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from unittest import mock

from tests.gitfixture import make_tmpdir

import preflight

HUMAN = "Test Human <human@example.org>"
RCPT = ("To: netdev@vger.kernel.org\n"
        "Cc: Maint Ainer <maint@example.org>, linux-kernel@vger.kernel.org\n")
COVER_ID = "<20261121-foo-v1-0-abc@example.org>"
BODY = """\
bar() dereferences priv->dev before checking that the device was
registered, so probing a port without a PHY oopses. Check the pointer
first and return -ENODEV.

Found by code inspection, not triggered. Not tested beyond a W=1 build."""
DIFF = """\
---
 drivers/net/foo.c | 2 ++
 1 file changed, 2 insertions(+)

diff --git a/drivers/net/foo.c b/drivers/net/foo.c
--- a/drivers/net/foo.c
+++ b/drivers/net/foo.c
@@ -1,1 +1,3 @@
+	if (!priv->dev)
+		return -ENODEV;
"""
TRAILERS = ('Fixes: 0123456789ab ("net: foo: add foo")\n'
            f"Signed-off-by: {HUMAN}")


def mail(subject: str, body: str, headers: str = "", extra: str = "") -> str:
    return (f"From: {HUMAN}\nDate: Sat, 21 Nov 2026 10:00:00 +0000\n"
            f"Subject: {subject}\n{headers}{extra}"
            "MIME-Version: 1.0\nContent-Type: text/plain; charset=utf-8\n"
            "Content-Transfer-Encoding: 8bit\n\n" + body)


def patch(subject: str, body: str = BODY, trailers: str = TRAILERS, headers: str = "",
          extra: str = "") -> str:
    return mail(subject, f"{body}\n\n{trailers}\n{DIFF}", headers, extra)


def cover(subject: str, headers: str = "", extra: str = "") -> str:
    return mail(subject, "This series fixes two NULL derefs in foo.\n\n---\nTest Human (2):\n",
                headers, extra)


def threaded(n: int, version: int = 1) -> str:
    return (f"Message-ID: <20261121-foo-v{version}-{n}-abc@example.org>\n"
            f"In-Reply-To: {COVER_ID}\nReferences: {COVER_ID}\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = make_tmpdir(self)

    def write(self, files: dict[str, str]) -> None:
        for name, text in files.items():
            with open(os.path.join(self.dir, name), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)

    def run_main(self, *args: str) -> tuple[int, dict | None, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = preflight.main([self.dir, "--json", *args])
        return code, (json.loads(out.getvalue()) if out.getvalue() else None), err.getvalue()

    def codes(self, *args: str) -> tuple[int, list[str]]:
        code, payload, _ = self.run_main(*args)
        return code, sorted(f["code"] for f in payload["findings"])

    def netdev_series(self, version: int = 1, tree: str = "net", cover_extra: str = "",
                      patch_headers: tuple[str, str] | None = None) -> None:
        tag = f"{tree} v{version} " if version > 1 else f"{tree} "
        ph = patch_headers or (threaded(1, version), threaded(2, version))
        self.write({
            "0000-cover.eml": cover(f"[PATCH {tag}0/2] net: foo: fix NULL derefs", RCPT,
                                    f"Message-ID: {COVER_ID}\n" + cover_extra),
            "0001-a.eml": patch(f"[PATCH {tag}1/2] net: foo: check priv->dev in bar()",
                                headers=RCPT, extra=ph[0]),
            "0002-b.eml": patch(f"[PATCH {tag}2/2] net: foo: check priv->dev in baz()",
                                headers=RCPT, extra=ph[1]),
        })


class PreflightTest(Base):
    def test_clean_netdev_series(self):
        self.netdev_series()
        code, payload, _ = self.run_main("--netdev")
        self.assertEqual(payload["findings"], [])
        self.assertEqual(code, 0)
        self.assertEqual([(m["number"], m["total"], m["is_cover"]) for m in payload["mails"]],
                         [(0, 2, True), (1, 2, False), (2, 2, False)])
        self.assertEqual({m["version"] for m in payload["mails"]}, {1})

    def test_human_output(self):
        self.netdev_series()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            preflight.main([self.dir])
        self.assertIn("2 patch(es) + cover", out.getvalue())
        self.assertIn("No findings.", out.getvalue())

    def test_ignores_non_mail_files(self):
        self.netdev_series()
        self.write({"notes.txt": "just some notes\n"})
        os.mkdir(os.path.join(self.dir, "sub"))
        code, payload, _ = self.run_main("--netdev")
        self.assertEqual((code, len(payload["mails"])), (0, 3))

    def test_b4_prep_files_without_headers(self):
        self.write({
            "0000-cover-letter.patch": "From abc Mon Sep 17 00:00:00 2001\n"
                                       + cover("[PATCH net-next 0/2] net: foo: fixes"),
            "0001-a.patch": "From abc Mon Sep 17 00:00:00 2001\n"
                            + patch("[PATCH net-next 1/2] net: foo: check priv->dev in bar()"),
            "0002-b.patch": "From abc Mon Sep 17 00:00:00 2001\n"
                            + patch("[PATCH net-next 2/2] net: foo: check priv->dev in baz()"),
        })
        code, codes = self.codes("--netdev")
        self.assertEqual((code, codes), (0, ["NO_HEADERS"]))

    def test_subject_unparsed(self):
        self.write({"a.eml": patch("net: foo: check priv->dev in bar()", headers=RCPT)})
        self.assertEqual(self.codes(), (1, ["SUBJECT_UNPARSED"]))

    def test_single_patch_without_number(self):
        self.write({"a.eml": patch("[PATCH net] net: foo: check priv->dev in bar()",
                                   headers=RCPT)})
        code, payload, _ = self.run_main("--netdev")
        self.assertEqual((code, payload["findings"]), (0, []))
        self.assertEqual(payload["mails"][0]["number"], 1)

    def test_numbering_gap(self):
        self.write({
            "0.eml": cover("[PATCH 0/3] net: foo: fixes", RCPT),
            "1.eml": patch("[PATCH 1/3] net: foo: check priv->dev in bar()", headers=RCPT),
            "3.eml": patch("[PATCH 3/3] net: foo: check priv->dev in qux()", headers=RCPT),
        })
        code, payload, _ = self.run_main()
        found = {f["code"]: f for f in payload["findings"]}
        self.assertEqual((code, sorted(found)), (1, ["NUMBERING"]))
        self.assertIn("missing 2", found["NUMBERING"]["message"])

    def test_numbering_totals_disagree(self):
        self.write({
            "1.eml": patch("[PATCH 1/2] net: foo: check priv->dev in bar()", headers=RCPT),
            "2.eml": patch("[PATCH 2/3] net: foo: check priv->dev in baz()", headers=RCPT),
        })
        code, codes = self.codes()
        self.assertIn("NUMBERING", codes)
        self.assertIn("COVER_MISSING", codes)

    def test_version_mismatch(self):
        self.write({
            "0.eml": cover("[PATCH v2 0/2] net: foo: fixes", RCPT),
            "1.eml": patch("[PATCH v2 1/2] net: foo: check priv->dev in bar()", headers=RCPT),
            "2.eml": patch("[PATCH 2/2] net: foo: check priv->dev in baz()", headers=RCPT),
        })
        self.assertEqual(self.codes(), (1, ["VERSION_MISMATCH"]))

    def test_cover_missing(self):
        self.write({
            "1.eml": patch("[PATCH 1/2] net: foo: check priv->dev in bar()", headers=RCPT),
            "2.eml": patch("[PATCH 2/2] net: foo: check priv->dev in baz()", headers=RCPT),
        })
        self.assertEqual(self.codes(), (1, ["COVER_MISSING"]))

    def test_netdev_tree_prefix(self):
        self.write({"a.eml": patch("[PATCH v2] net: foo: check priv->dev in bar()",
                                   headers=RCPT)})
        self.assertEqual(self.codes("--netdev"), (1, ["NETDEV_TREE_PREFIX"]))
        self.assertEqual(self.codes(), (0, []))

    def test_netdev_too_many(self):
        files = {"00.eml": cover("[PATCH net-next 00/16] net: foo: rework", RCPT)}
        for n in range(1, 17):
            files[f"{n:02}.eml"] = patch(
                f"[PATCH net-next {n:02}/16] net: foo: check priv->dev in f{n}()", headers=RCPT)
        self.write(files)
        self.assertEqual(self.codes("--netdev"), (1, ["NETDEV_TOO_MANY"]))

    def test_net_without_fixes(self):
        self.write({"a.eml": patch("[PATCH net] net: foo: check priv->dev in bar()",
                                   trailers=f"Signed-off-by: {HUMAN}", headers=RCPT)})
        self.assertEqual(self.codes("--netdev"), (1, ["NETDEV_NET_NO_FIXES"]))

    def test_net_next_without_fixes_is_fine(self):
        self.write({"a.eml": patch("[PATCH net-next] net: foo: check priv->dev in bar()",
                                   trailers=f"Signed-off-by: {HUMAN}", headers=RCPT)})
        self.assertEqual(self.codes("--netdev"), (0, []))

    def test_html_part(self):
        text = (f"From: {HUMAN}\nSubject: [PATCH] net: foo: check priv->dev in bar()\n{RCPT}"
                "MIME-Version: 1.0\nContent-Type: multipart/alternative; boundary=XX\n\n"
                "--XX\nContent-Type: text/plain\n\n"
                f"{BODY}\n\n{TRAILERS}\n{DIFF}\n"
                "--XX\nContent-Type: text/html\n\n<p>hi</p>\n--XX--\n")
        self.write({"a.eml": text})
        code, payload, _ = self.run_main()
        found = [f for f in payload["findings"] if f["code"] == "HTML_PART"]
        self.assertEqual((code, [f["severity"] for f in found]), (1, ["error"]))
        self.assertEqual(sorted(f["code"] for f in payload["findings"]), ["HTML_PART"])

    def test_attachment_part(self):
        text = (f"From: {HUMAN}\nSubject: [PATCH] net: foo: check priv->dev in bar()\n{RCPT}"
                "MIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=XX\n\n"
                "--XX\nContent-Type: text/plain\n\n"
                f"{BODY}\n\n{TRAILERS}\n\n"
                "--XX\nContent-Type: application/octet-stream\n\nxx\n--XX--\n")
        self.write({"a.eml": text})
        self.assertEqual(self.codes(), (1, ["HTML_PART"]))

    def test_no_headers_info(self):
        self.write({"a.eml": patch("[PATCH] net: foo: check priv->dev in bar()")})
        code, payload, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual([(f["code"], f["severity"]) for f in payload["findings"]],
                         [("NO_HEADERS", "info")])

    def test_no_recipients(self):
        self.write({
            "0.eml": cover("[PATCH 0/1] net: foo: fixes", RCPT),
            "1.eml": patch("[PATCH 1/1] net: foo: check priv->dev in bar()"),
        })
        self.assertEqual(self.codes(), (1, ["NO_RECIPIENTS"]))

    def test_v2_cover_in_old_thread(self):
        self.netdev_series(version=2, cover_extra="In-Reply-To: <old-v1@example.org>\n")
        self.assertEqual(self.codes("--netdev"), (1, ["NEW_VERSION_IN_OLD_THREAD"]))

    def test_v1_reply_is_not_flagged(self):
        self.netdev_series(version=1, cover_extra="In-Reply-To: <old@example.org>\n")
        self.assertEqual(self.codes("--netdev"), (0, []))

    def test_patch_not_threaded(self):
        self.netdev_series(patch_headers=(threaded(1), "Message-ID: <lone@example.org>\n"))
        code, payload, _ = self.run_main("--netdev")
        self.assertEqual((code, [(f["code"], f["detail"]) for f in payload["findings"]]),
                         (1, [("THREADING", "0002-b.eml")]))

    def test_netdev_list_missing(self):
        rcpt = "To: Maint Ainer <maint@example.org>\n"
        self.write({"a.eml": patch("[PATCH net] net: foo: check priv->dev in bar()",
                                   headers=rcpt)})
        self.assertEqual(self.codes("--netdev"), (1, ["NETDEV_LIST_MISSING"]))
        self.assertEqual(self.codes(), (0, []))

    def test_netdev_fix_without_discovery_statement(self):
        body = BODY.split("\n\nFound by")[0]
        self.write({"a.eml": patch("[PATCH net] net: foo: check priv->dev in bar()",
                                   body=body, headers=RCPT)})
        self.assertEqual(self.codes("--netdev"),
                         (1, ["NETDEV_DISCOVERY_MISSING", "NETDEV_TESTING_MISSING"]))
        self.assertEqual(self.codes(), (0, []))

    def test_no_signoff_is_info_only(self):
        self.write({"a.eml": patch("[PATCH net] net: foo: check priv->dev in bar()",
                                   trailers='Fixes: 0123456789ab ("net: foo: add foo")',
                                   headers=RCPT)})
        code, payload, _ = self.run_main("--netdev")
        self.assertEqual(code, 0)
        self.assertEqual([(f["code"], f["severity"]) for f in payload["findings"]],
                         [("NO_SIGNOFF", "info")])

    def test_from_sob_mismatch(self):
        self.write({"a.eml": patch("[PATCH] net: foo: check priv->dev in bar()",
                                   trailers="Signed-off-by: Other <other@example.org>",
                                   headers=RCPT)})
        code, payload, _ = self.run_main()
        self.assertEqual((code, [(f["code"], f["severity"]) for f in payload["findings"]]),
                         (0, [("FROM_SOB_MISMATCH", "info")]))

    def test_inbody_from_is_the_author(self):
        body = "From: Other <other@example.org>\n\n" + BODY
        self.write({"a.eml": patch("[PATCH] net: foo: check priv->dev in bar()", body=body,
                                   trailers="Signed-off-by: Other <other@example.org>\n"
                                            f"Signed-off-by: {HUMAN}",
                                   headers=RCPT)})
        self.assertEqual(self.codes(), (0, []))

    def test_commit_message_finding_reemitted(self):
        body = BODY + "\n\n**Root cause**: priv->dev is NULL."
        self.write({"a.eml": patch("[PATCH] net: foo: check priv->dev in bar()", body=body,
                                   headers=RCPT)})
        code, payload, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual([(f["code"], f["detail"]) for f in payload["findings"]],
                         [("MARKDOWN", "a.eml")])

    def test_subject_lint_uses_title_without_bracket(self):
        self.write({"a.eml": patch("[PATCH] Fixed the foo driver", headers=RCPT)})
        code, codes = self.codes()
        self.assertEqual((code, codes), (1, ["SUBJECT_NOT_IMPERATIVE", "SUBJECT_NO_PREFIX"]))

    def test_no_mails_exit_2(self):
        self.write({"notes.txt": "nothing here\n"})
        code, payload, err = self.run_main()
        self.assertEqual((code, payload), (2, None))
        self.assertIn("no patch mails", err)

    def test_missing_dir_exit_2(self):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = preflight.main([os.path.join(self.dir, "nope")])
        self.assertEqual(code, 2)
        self.assertIn("not a directory", err.getvalue())

    def test_maintainers_needs_kernel_tree(self):
        self.netdev_series()
        code, _, err = self.run_main("--maintainers")
        self.assertEqual(code, 2)
        self.assertIn("--repo", err)
        code, _, err = self.run_main("--maintainers", "--repo", self.dir)
        self.assertEqual(code, 2)
        self.assertIn("get_maintainer.pl", err)


class MaintainersTest(Base):
    OUTPUT = {
        "0001-a.eml": "Maint Ainer <maint@example.org>\nnetdev@vger.kernel.org\n"
                      "linux-kernel@vger.kernel.org\n",
        "0002-b.eml": "Other Maint <Other@Example.org>\nnetdev@vger.kernel.org\n",
    }

    def setUp(self):
        super().setUp()
        self.kernel = make_tmpdir(self)
        os.makedirs(os.path.join(self.kernel, "scripts"))
        with open(os.path.join(self.kernel, "scripts", "get_maintainer.pl"), "w") as f:
            f.write("# stub\n")
        self.calls = []

        def fake(repo, path):
            self.calls.append((repo, os.path.basename(path)))
            return self.OUTPUT[os.path.basename(path)]

        patcher = mock.patch.object(preflight, "run_get_maintainer", fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_maintainer_not_addressed(self):
        self.netdev_series()
        code, payload, _ = self.run_main("--netdev", "--maintainers", "--repo", self.kernel)
        found = [(f["code"], f["detail"]) for f in payload["findings"]]
        self.assertEqual((code, found), (1, [("MAINTAINER_NOT_ADDRESSED", "0002-b.eml")]))
        self.assertIn("other@example.org", payload["findings"][0]["message"])
        self.assertEqual([c[1] for c in self.calls], ["0001-a.eml", "0002-b.eml"])
        self.assertEqual(payload["mails"][2]["maintainers"],
                         ["netdev@vger.kernel.org", "other@example.org"])

    def test_cover_recipients_count(self):
        rcpt = RCPT.rstrip("\n") + ", Other Maint <other@example.org>\n"
        self.write({
            "0000-cover.eml": cover("[PATCH net 0/2] net: foo: fixes", rcpt,
                                    f"Message-ID: {COVER_ID}\n"),
            "0001-a.eml": patch("[PATCH net 1/2] net: foo: check priv->dev in bar()",
                                headers=RCPT, extra=threaded(1)),
            "0002-b.eml": patch("[PATCH net 2/2] net: foo: check priv->dev in baz()",
                                headers=RCPT, extra=threaded(2)),
        })
        code, codes = self.codes("--netdev", "--maintainers", "--repo", self.kernel)
        self.assertEqual((code, codes), (0, []))

    def test_recipient_extra(self):
        rcpt = (RCPT.rstrip("\n") + ", Other Maint <other@example.org>, "
                "stable@vger.kernel.org, Random <random@example.org>\n")
        self.write({
            "0000-cover.eml": cover("[PATCH net 0/2] net: foo: fixes", rcpt,
                                    f"Message-ID: {COVER_ID}\n"),
            "0001-a.eml": patch("[PATCH net 1/2] net: foo: check priv->dev in bar()",
                                headers=rcpt, extra=threaded(1)),
            "0002-b.eml": patch("[PATCH net 2/2] net: foo: check priv->dev in baz()",
                                headers=rcpt, extra=threaded(2)),
        })
        code, payload, _ = self.run_main("--netdev", "--maintainers", "--repo", self.kernel)
        found = [(f["code"], f["severity"]) for f in payload["findings"]]
        self.assertEqual((code, found), (0, [("RECIPIENT_EXTRA", "info")]))
        self.assertIn("random@example.org", payload["findings"][0]["message"])
        self.assertNotIn("stable@", payload["findings"][0]["message"])

    def test_without_headers_lists_maintainers_only(self):
        self.write({
            "0000-cover.patch": cover("[PATCH net 0/2] net: foo: fixes"),
            "0001-a.eml": patch("[PATCH net 1/2] net: foo: check priv->dev in bar()"),
            "0002-b.eml": patch("[PATCH net 2/2] net: foo: check priv->dev in baz()"),
        })
        code, payload, _ = self.run_main("--netdev", "--maintainers", "--repo", self.kernel)
        self.assertEqual((code, [f["code"] for f in payload["findings"]]), (0, ["NO_HEADERS"]))
        self.assertEqual(payload["mails"][1]["maintainers"],
                         ["linux-kernel@vger.kernel.org", "maint@example.org",
                          "netdev@vger.kernel.org"])


class ParseMaintainersTest(unittest.TestCase):
    def test_parse(self):
        out = ("Jane Dev <Jane@Example.org>\n"
               "\"Doe, John\" <john@example.org>\n"
               "netdev@vger.kernel.org\n"
               "linux-kernel@vger.kernel.org (open list)\n"
               "\n")
        self.assertEqual(preflight.parse_maintainers(out),
                         {"jane@example.org", "john@example.org", "netdev@vger.kernel.org",
                          "linux-kernel@vger.kernel.org"})


if __name__ == "__main__":
    unittest.main()
