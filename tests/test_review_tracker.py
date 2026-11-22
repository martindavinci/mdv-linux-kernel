# SPDX-License-Identifier: GPL-2.0
"""Tests for review_tracker.py."""
from __future__ import annotations

import contextlib
import email.message
import io
import json
import mailbox
import os
import unittest

from tests.gitfixture import GitRepo, make_tmpdir, run_script

import mlk_common
import review_tracker

ME = "Test Human <human@example.org>"
JANE = "Jane Reviewer <jane@example.com>"
BOB = "Bob Maintainer <bob@example.net>"
ALICE = "Alice Acker <alice@example.com>"
SASHIKO = "Sashiko <review@sashiko.dev>"
CARL = "Carl Lost <carl@example.org>"
D0 = "Mon, 16 Nov 2026 10:00:00 +0000"
HUNK = "@@ -1,3 +1,4 @@ static int foo_set(void)"

PATCH2_BODY = f"""net: foo: b

Signed-off-by: Test Human <human@example.org>
---
diff --git a/net/foo.c b/net/foo.c
--- a/net/foo.c
+++ b/net/foo.c
{HUNK}
 int a;
+int b;
+int c;
+int d;
"""

REVIEW_BODY = f"""On Mon, 16 Nov 2026 10:00:00 +0000, Test Human wrote:
> Signed-off-by: Test Human <human@example.org>
> ---
> diff --git a/net/foo.c b/net/foo.c
> +++ b/net/foo.c
> {HUNK}
> +int b;

Is rtnl held here?

> +int c;
> +int d;

This leaks on error.
Please free it.

--
Jane
"""

TOP_POST_BODY = """Please split this patch.

On Mon, 16 Nov 2026 10:00:00 +0000, Test Human wrote:
> net: foo: a
>
> Signed-off-by: Test Human <human@example.org>
"""

TAG_BODY = """On Mon, 16 Nov 2026 10:00:00 +0000, Test Human wrote:
> net: foo: a

Reviewed-by: Alice Acker <alice@example.com>
"""


def mail(msgid: str, subject: str, frm: str, date: str, body: str,
         parent: str | None = None, refs: tuple[str, ...] = ()) -> email.message.EmailMessage:
    msg = email.message.EmailMessage()
    msg["Message-ID"] = msgid
    msg["Subject"] = subject
    msg["From"] = frm
    msg["Date"] = date
    if parent:
        msg["In-Reply-To"] = parent
    if refs:
        msg["References"] = " ".join(refs)
    msg.set_content(body)
    return msg


def thread_v1() -> list[email.message.EmailMessage]:
    cover = "<cover@x>"
    return [
        mail(cover, "[PATCH net-next 0/2] net: foo: fix things", ME, D0, "cover text\n"),
        mail("<p1@x>", "[PATCH net-next 1/2] net: foo: a", ME, D0,
             "net: foo: a\n\nSigned-off-by: Test Human <human@example.org>\n", cover, (cover,)),
        mail("<p2@x>", "[PATCH net-next 2/2] net: foo: b", ME, D0, PATCH2_BODY, cover, (cover,)),
        mail("<r1@x>", "Re: [PATCH net-next 2/2] net: foo: b", JANE,
             "Mon, 16 Nov 2026 11:00:00 +0000", REVIEW_BODY, "<p2@x>", (cover, "<p2@x>")),
        mail("<r2@x>", "Re: [PATCH net-next 1/2] net: foo: a", BOB,
             "Mon, 16 Nov 2026 12:00:00 +0000", TOP_POST_BODY, "<p1@x>", (cover, "<p1@x>")),
        mail("<r3@x>", "Re: [PATCH net-next 1/2] net: foo: a", ALICE,
             "Mon, 16 Nov 2026 13:00:00 +0000", TAG_BODY, "<p1@x>", (cover, "<p1@x>")),
        mail("<r4@x>", "Re: [PATCH net-next 0/2] net: foo: fix things", SASHIKO,
             "Mon, 16 Nov 2026 14:00:00 +0000",
             "> cover text\n\nPossible use-after-free in foo_free().\n", cover, (cover,)),
        mail("<r5@x>", "Re: [PATCH net-next 2/2] net: foo: b", ME,
             "Mon, 16 Nov 2026 15:00:00 +0000", "> Is rtnl held here?\n\nYes.\n", "<r1@x>",
             (cover, "<p2@x>", "<r1@x>")),
        mail("<r6@x>", "Re: something else", CARL, "Mon, 16 Nov 2026 16:00:00 +0000",
             "> whatever\n\nNot mapped to anything.\n", "<elsewhere@x>", ("<elsewhere@x>",)),
    ]


def write_mbox(path: str, messages) -> str:
    box = mailbox.mbox(path)
    try:
        for msg in messages:
            box.add(msg)
        box.flush()
    finally:
        box.close()
    return path


class ReviewBase(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tmpdir(self)
        self.repo = GitRepo.create(self.tmp)
        self.sha = self.repo.commit("net: foo: take rtnl in foo_store()",
                                    {"net/foo.c": "int a;\n"})
        self.mbox = write_mbox(os.path.join(self.tmp, "v1.mbox"), thread_v1())

    def rt(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = review_tracker.main([args[0], "--repo", self.repo.path, *args[1:]])
        return code, out.getvalue(), err.getvalue()

    def ok(self, *args: str) -> str:
        code, out, err = self.rt(*args)
        self.assertEqual(code, 0, out + err)
        return out

    def import_v1(self, *extra: str) -> None:
        self.ok("import", self.mbox, "--me", "human@example.org", *extra)

    def state(self) -> dict:
        path = os.path.join(mlk_common.series_dir(self.repo.path), "review.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def comments(self) -> dict[str, dict]:
        return {c["id"]: c for c in self.state()["comments"]}

    def status(self, *extra: str) -> tuple[int, list[str]]:
        code, out, _ = self.rt("status", "--json", *extra)
        return code, [f["code"] for f in json.loads(out)["findings"]]


class ImportTest(ReviewBase):
    def test_postings_and_inline_comments(self):
        self.import_v1()
        data = self.state()
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["postings"], [{
            "version": 1, "msgid": "<cover@x>", "date": "2026-11-16T10:00:00Z",
            "subject": "[PATCH net-next 0/2] net: foo: fix things"}])
        self.assertEqual(len(data["imports"]), 1)
        self.assertEqual(len(data["imports"][0]["sha256"]), 64)
        c = self.comments()
        first, second = c["r1@x#1"], c["r1@x#2"]
        for comment in (first, second):
            self.assertEqual((comment["patch"], comment["version"]), (2, 1))
            self.assertEqual(comment["file"], "net/foo.c")
            self.assertEqual(comment["hunk"], HUNK)
            self.assertEqual((comment["status"], comment["kind"]), ("open", "comment"))
            self.assertEqual((comment["source"], comment["author"]), ("human", "Jane Reviewer"))
            self.assertEqual(comment["from_addr"], "jane@example.com")
            self.assertEqual(comment["msgid"], "<r1@x>")
        self.assertEqual(first["text"], "Is rtnl held here?")
        self.assertEqual(first["date"], "2026-11-16T11:00:00Z")
        self.assertEqual(len(first["quote"]), 6)
        self.assertEqual(first["quote"][-1], "> +int b;")
        self.assertEqual(second["text"], "This leaks on error.\nPlease free it.")  # no signature
        self.assertEqual(second["quote"], ["> +int c;", "> +int d;"])

    def test_top_post_tag_bot_unanchored(self):
        self.import_v1()
        c = self.comments()
        top = c["r2@x#1"]
        self.assertEqual(top["text"], "Please split this patch.")  # attribution dropped
        self.assertEqual((top["patch"], top["file"], top["hunk"]), (1, None, None))
        self.assertNotIn("r2@x#2", c)
        tag = c["r3@x#1"]
        self.assertEqual((tag["kind"], tag["status"], tag["patch"]), ("tag", "tag", 1))
        self.assertEqual(tag["text"], "Reviewed-by: Alice Acker <alice@example.com>")
        self.assertNotIn("r3@x#2", c)
        bot = c["r4@x#1"]
        self.assertEqual((bot["source"], bot["patch"], bot["file"]), ("bot", 0, None))
        lost = c["r6@x#1"]
        self.assertEqual((lost["status"], lost["patch"]), ("unanchored", None))
        self.assertEqual(lost["version"], 1)

    def test_own_reply_skipped(self):
        self.import_v1()
        self.assertFalse([cid for cid in self.comments() if cid.startswith("r5@x")])

    def test_own_reply_skipped_without_me(self):
        self.ok("import", self.mbox)  # default: the From of the patch mails
        ids = set(self.comments())
        self.assertFalse([cid for cid in ids if cid.startswith("r5@x")])
        self.assertIn("r1@x#1", ids)

    def test_reimport_keeps_state_and_adds_new(self):
        self.import_v1()
        self.ok("propose", "r1@x#1", "--status", "replied", "--why", "the caller holds rtnl")
        self.ok("confirm", "r1@x#1", "--words", "yes, I replied on the list")
        self.import_v1()
        self.assertEqual(self.comments()["r1@x#1"]["status"], "replied")
        newer = thread_v1() + [
            mail("<r7@x>", "Re: [PATCH net-next 2/2] net: foo: b", JANE,
                 "Tue, 17 Nov 2026 09:00:00 +0000", "> Yes.\n\nOK, thanks.\n", "<r5@x>",
                 ("<cover@x>", "<p2@x>", "<r1@x>", "<r5@x>"))]
        self.mbox = write_mbox(os.path.join(self.tmp, "v1-newer.mbox"), newer)
        self.import_v1()
        c = self.comments()
        self.assertEqual(c["r1@x#1"]["status"], "replied")
        self.assertEqual(c["r1@x#1"]["confirmation"]["words"], "yes, I replied on the list")
        self.assertEqual(c["r7@x#1"]["patch"], 2)  # via my reply, up to patch 2
        self.assertEqual(len(self.state()["imports"]), 3)
        self.assertEqual(len(self.state()["postings"]), 1)

    def test_control_characters_not_echoed(self):
        evil = thread_v1()[:3] + [
            mail("<r8@x>", "Re: [PATCH net-next 2/2] net: foo: b", JANE, D0,
                 "> +int b;\n\nok \x1b[31mred\x1b[0m\n", "<p2@x>")]
        self.mbox = write_mbox(os.path.join(self.tmp, "evil.mbox"), evil)
        self.import_v1()
        out = self.ok("list")
        self.assertNotIn("\x1b", out)
        self.assertIn("ok ?[31mred?[0m", out)


class FlowTest(ReviewBase):
    def setUp(self):
        super().setUp()
        self.import_v1()

    def test_propose_confirm_reopen_history(self):
        self.ok("propose", "r1@x#2", "--status", "addressed", "--why", "free on error",
                "--commit", "HEAD")
        c = self.comments()["r1@x#2"]
        self.assertEqual(c["status"], "proposed")
        self.assertEqual(c["proposal"]["commit"], self.sha)
        self.ok("confirm", "r1@x#2", "--words", "looks right, fixed")
        c = self.comments()["r1@x#2"]
        self.assertEqual(c["status"], "addressed")
        self.assertEqual(c["confirmation"]["words"], "looks right, fixed")
        self.assertEqual(c["confirmation"]["commit"], self.sha)
        code, _, err = self.rt("propose", "r1@x#2", "--status", "wontfix", "--why", "x")
        self.assertEqual(code, 1, err)  # confirmed: the agent cannot override it
        self.ok("reopen", "r1@x#2", "--why", "the fix was incomplete")
        c = self.comments()["r1@x#2"]
        self.assertEqual((c["status"], c["proposal"], c["confirmation"]), ("open", None, None))
        self.assertEqual([(h["from"], h["to"]) for h in c["history"]],
                         [(None, "open"), ("open", "proposed"), ("proposed", "addressed"),
                          ("addressed", "open")])
        self.assertEqual(c["history"][-1]["why"], "the fix was incomplete")
        self.assertEqual(c["history"][-1]["confirmation"]["words"], "looks right, fixed")
        code, _, _ = self.rt("reopen", "r1@x#2", "--why", "again")
        self.assertEqual(code, 1)

    def test_confirm_without_proposal(self):
        code, _, err = self.rt("confirm", "r1@x#1", "--words", "fine")
        self.assertEqual(code, 1)
        self.assertIn("no proposal", err)
        self.assertEqual(self.comments()["r1@x#1"]["status"], "open")
        self.ok("confirm", "r1@x#1", "--status", "replied", "--words", "I answered Jane")
        c = self.comments()["r1@x#1"]
        self.assertEqual((c["status"], c["proposal"]), ("replied", None))

    def test_addressed_needs_commit(self):
        code, _, err = self.rt("propose", "r1@x#1", "--status", "addressed", "--why", "done")
        self.assertEqual(code, 2, err)
        code, _, err = self.rt("confirm", "r1@x#1", "--status", "addressed", "--words", "ok")
        self.assertEqual(code, 2, err)
        code, _, err = self.rt("propose", "r1@x#1", "--status", "addressed", "--why", "done",
                               "--commit", "deadbeef")
        self.assertEqual(code, 2, err)
        self.assertEqual(self.comments()["r1@x#1"]["status"], "open")

    def test_tags_cannot_be_resolved(self):
        code, _, err = self.rt("propose", "r3@x#1", "--status", "replied", "--why", "x")
        self.assertEqual(code, 1)
        self.assertIn("b4 trailers -u", err)

    def test_unknown_id(self):
        code, _, _ = self.rt("propose", "nope", "--status", "replied", "--why", "x")
        self.assertEqual(code, 2)

    def test_list(self):
        out = self.ok("list")
        self.assertIn("r1@x#1  open  v1 p2 net/foo.c  Jane Reviewer: Is rtnl held here?", out)
        self.assertIn("r6@x#1  unanchored  v1 p- -  Carl Lost: Not mapped to anything.", out)
        comments_part, tags_part = out.split("Tags (")
        self.assertNotIn("r3@x#1", comments_part)
        self.assertIn("r3@x#1  tag  v1 p1 -  Alice Acker: Reviewed-by:", tags_part)
        payload = json.loads(self.ok("list", "--status", "unanchored", "--json"))
        self.assertEqual([c["id"] for c in payload["comments"]], ["r6@x#1"])
        self.assertEqual(payload["tags"], [])

    def test_add_manual(self):
        self.ok("add", "--patch", "2", "--text", "AI review: missing NULL check",
                "--author", "sashiko.dev", "--source", "bot", "--file", "net/foo.c")
        self.ok("add", "--patch", "1", "--text", "second")
        c = self.comments()
        manual = c["manual-1"]
        self.assertEqual((manual["patch"], manual["version"], manual["source"]), (2, 1, "bot"))
        self.assertEqual((manual["file"], manual["status"], manual["msgid"]),
                         ("net/foo.c", "open", None))
        self.assertEqual(c["manual-2"]["author"], "unknown")
        code, findings = self.status()
        self.assertIn("OPEN", findings)


class StatusTest(ReviewBase):
    def test_nothing_imported(self):
        code, _, err = self.rt("status")
        self.assertEqual(code, 2)
        self.assertIn("nothing imported yet", err)

    def test_blocking_findings(self):
        self.import_v1()
        code, findings = self.status()
        self.assertEqual(code, 1)
        self.assertEqual(sorted(set(findings)), ["OPEN", "UNANCHORED"])
        self.assertEqual(findings.count("OPEN"), 4)  # r1 x2, r2, r4; the tag never blocks
        self.ok("propose", "r1@x#1", "--status", "replied", "--why", "rtnl is held")
        self.assertIn("PROPOSED", self.status()[1])
        for cid in ("r1@x#1", "r1@x#2", "r2@x#1", "r4@x#1", "r6@x#1"):
            self.ok("confirm", cid, "--status", "wontfix", "--words", "agreed")
        self.assertEqual(self.status(), (0, []))
        code, out, _ = self.rt("status")
        self.assertEqual(code, 0)
        self.assertIn("No findings.", out)

    def test_netdev_24h(self):
        self.import_v1()
        for cid in ("r1@x#1", "r1@x#2", "r2@x#1", "r4@x#1", "r6@x#1"):
            self.ok("confirm", cid, "--status", "replied", "--words", "answered")
        code, findings = self.status("--netdev", "--now", "2026-11-17T09:59:00Z")
        self.assertEqual(code, 1)
        self.assertEqual(findings, ["POSTED_LESS_THAN_24H", "DISCUSSION_ACTIVE"])
        _, out, _ = self.rt("status", "--netdev", "--now", "2026-11-17T09:59:00Z")
        self.assertIn("blocked until the time above", out)
        self.assertNotIn("every comment needs", out)
        code, findings = self.status("--netdev", "--now", "2026-11-17T12:00:00Z")
        self.assertEqual((code, findings), (0, ["DISCUSSION_ACTIVE"]))  # info only
        self.assertEqual(self.status("--netdev", "--now", "2026-11-18T12:00:00Z"), (0, []))
        self.assertEqual(self.status("--now", "2026-11-16T10:30:00Z"), (0, []))
        code, _, _ = self.rt("status", "--now", "yesterday")
        self.assertEqual(code, 2)


PATCHWORK = "patchwork-bot+netdevbpf@kernel.org"
APPLIED_BODY = """Hello:

This series was applied to netdev/net-next.git (main)
by Jakub Kicinski <kuba@kernel.org>:

On Mon, 16 Nov 2026 10:00:00 +0000 you wrote:
> net: foo: a
"""


class PatchworkTest(ReviewBase):
    def applied_mbox(self) -> str:
        msgs = thread_v1() + [
            mail("<pw.git-patchwork-notify@kernel.org>",
                 "Re: [PATCH net-next 0/2] net: foo: fix things", PATCHWORK,
                 "Tue, 17 Nov 2026 10:00:00 +0000", APPLIED_BODY, "<cover@x>", ("<cover@x>",))]
        return write_mbox(os.path.join(self.tmp, "applied.mbox"), msgs)

    def test_notice_never_blocks_but_applied_does(self):
        self.mbox = self.applied_mbox()
        self.import_v1()
        notice = self.comments()["pw.git-patchwork-notify@kernel.org#1"]
        self.assertEqual((notice["kind"], notice["status"]), ("notice", "notice"))
        self.assertNotIn("pw.git-patchwork-notify@kernel.org#2", self.comments())
        for cid in ("r1@x#1", "r1@x#2", "r2@x#1", "r4@x#1", "r6@x#1"):
            self.ok("confirm", cid, "--status", "replied", "--words", "answered")
        self.assertEqual(self.status(), (1, ["APPLIED"]))
        code, out, _ = self.rt("status")
        self.assertIn("the series was applied: no next version", out)
        code, _, err = self.rt("propose", "pw.git-patchwork-notify@kernel.org#1",
                               "--status", "replied", "--why", "x")
        self.assertEqual(code, 1)
        self.assertIn("patchwork notice", err)
        out = self.ok("list")
        comments_part, notices_part = out.split("Patchwork notices (")
        self.assertNotIn("git-patchwork-notify", comments_part)
        self.assertIn("pw.git-patchwork-notify@kernel.org#1  notice", notices_part)

    def test_old_store_comment_becomes_notice(self):
        self.mbox = self.applied_mbox()
        self.import_v1()
        path = os.path.join(mlk_common.series_dir(self.repo.path), "review.json")
        data = self.state()
        for c in data["comments"]:  # as written before notices existed
            if c["kind"] == "notice":
                c["kind"], c["status"] = "comment", "open"
        data["comments"].append(dict(data["comments"][-1],
                                     id="pw.git-patchwork-notify@kernel.org#2"))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.import_v1()
        kinds = {cid: c["kind"] for cid, c in self.comments().items()
                 if "patchwork" in cid}
        self.assertEqual(set(kinds.values()), {"notice"})
        self.assertNotIn("OPEN", [f for f in self.status()[1] if "patchwork" in f])


class OutputTest(ReviewBase):
    def setUp(self):
        super().setUp()
        self.import_v1()

    def test_changelog(self):
        self.ok("propose", "r1@x#1", "--status", "addressed", "--why",
                "take rtnl in foo_store()", "--commit", self.sha)
        self.ok("confirm", "r1@x#1", "--words", "yes")
        self.ok("confirm", "r2@x#1", "--status", "addressed", "--commit", "HEAD",
                "--words", "split into two patches")
        self.ok("propose", "r4@x#1", "--status", "wontfix", "--why", "false positive")
        self.ok("confirm", "r4@x#1", "--words", "agreed")
        self.ok("propose", "r1@x#2", "--status", "addressed", "--why", "not confirmed",
                "--commit", self.sha)
        out = self.ok("changelog")
        self.assertEqual(out, "v1: https://lore.kernel.org/r/cover@x/\n"
                              "- take rtnl in foo_store() (Jane Reviewer)\n"
                              "- split into two patches (Bob Maintainer)\n")
        payload = json.loads(self.ok("changelog", "--json"))
        self.assertEqual([i["id"] for i in payload["blocks"][0]["items"]], ["r1@x#1", "r2@x#1"])

    def test_changelog_newest_first(self):
        v2 = [mail("<cover2@x>", "[PATCH net-next v2 0/2] net: foo: fix things", ME,
                   "Wed, 18 Nov 2026 10:00:00 +0000", "cover v2\n"),
              mail("<q1@x>", "Re: [PATCH net-next v2 0/2] net: foo: fix things", JANE,
                   "Wed, 18 Nov 2026 11:00:00 +0000", "> cover v2\n\nNit: typo.\n",
                   "<cover2@x>")]
        self.mbox = write_mbox(os.path.join(self.tmp, "v2.mbox"), v2)
        self.import_v1()
        self.ok("confirm", "q1@x#1", "--status", "addressed", "--commit", "HEAD",
                "--words", "fixed the typo")
        out = self.ok("changelog")
        self.assertEqual(out, "v2: https://lore.kernel.org/r/cover2@x/\n"
                              "- fixed the typo (Jane Reviewer)\n\n"
                              "v1: https://lore.kernel.org/r/cover@x/\n")
        self.assertEqual(self.comments()["q1@x#1"]["version"], 2)

    def test_skeleton(self):
        self.ok("propose", "r1@x#1", "--status", "addressed", "--why", "take rtnl",
                "--commit", self.sha)
        path = self.ok("skeleton", "r1@x#1").strip()
        series = mlk_common.series_dir(self.repo.path)
        self.assertEqual(os.path.dirname(path), os.path.join(series, "replies"))
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        self.assertNotIn("\r", text)
        self.assertTrue(text.startswith("On 2026-11-16T11:00:00Z, Jane Reviewer wrote:\n"
                                        "> > ---\n> > diff --git"))
        self.assertIn(f"> > {HUNK}\n> > +int b;\n> Is rtnl held here?\n\n", text)
        self.assertIn(f"[your reply: proposed addressed; {self.sha[:12]} "
                      "net: foo: take rtnl in foo_store()]", text)
        for word in ("Signed-off-by", "Reviewed-by", "Tested-by", "Acked-by"):
            self.assertNotIn(word, text)
        code, _, _ = self.rt("skeleton", "r1@x#1")
        self.assertEqual(code, 1)  # never overwrites a draft the human may have edited
        self.ok("skeleton", "r1@x#1", "--force")
        self.assertEqual(self.rt("skeleton", "r3@x#1")[0], 1)  # a tag has nothing to reply to

    def test_cli(self):
        res = run_script("review_tracker.py", "status", "--repo", self.repo.path)
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("OPEN", res.stdout)
        res = run_script("review_tracker.py", "list", "--repo", self.repo.path, "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("r1@x#1", [c["id"] for c in json.loads(res.stdout)["comments"]])


class ParseTest(unittest.TestCase):
    def test_subjects(self):
        parse = review_tracker.parse_patch_subject
        self.assertEqual(parse("[PATCH net-next v3 2/5] net: x"), (3, 2, 5))
        self.assertEqual(parse("[PATCHv2] foo"), (2, 1, 1))
        self.assertEqual(parse("[RFC PATCH 0/3] foo"), (1, 0, 3))
        self.assertIsNone(parse("Re: [PATCH 1/2] foo"))
        self.assertIsNone(parse("[GIT PULL] foo"))

    def test_bot(self):
        self.assertTrue(review_tracker.is_bot("kernel test robot", "lkp@intel.com"))
        self.assertTrue(review_tracker.is_bot("syzbot", "syzbot+abc@syzkaller.appspotmail.com"))
        self.assertTrue(review_tracker.is_bot("", "patchwork-bot+netdevbpf@kernel.org"))
        self.assertFalse(review_tracker.is_bot("Ann Abbott", "ann@example.org"))

    def test_bare_dashes_before_quotes_are_not_a_signature(self):
        # sashiko: summary, a bare "--", then its inline review of the diff.
        body = ("Found 2 potential issue(s).\n\n--\n\nnet: foo: b\n\n"
                "> diff --git a/net/foo.c b/net/foo.c\n> +++ b/net/foo.c\n\n[ ... ]\n\n"
                f"> {HUNK}\n> +int b;\n\n[Severity: Low]\nIs rtnl held?\n\n"
                "> +int c;\n\n[Severity: Medium]\nLeak on error.\n")
        blocks, _, _ = review_tracker.extract(body)
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[0]["file"], None)  # the top-posted summary
        self.assertEqual([b["file"] for b in blocks[1:]], ["net/foo.c", "net/foo.c"])
        self.assertEqual(blocks[2]["text"], "[Severity: Medium]\nLeak on error.")
        # A bare "--" with only unquoted text after it is still a signature.
        blocks, _, _ = review_tracker.extract("> +x\n\nNit.\n\n--\nJane\n")
        self.assertEqual([b["text"] for b in blocks], ["Nit."])

    def test_mixed_tag_and_comment(self):
        blocks, tags, only = review_tracker.extract("> +x\n\nLooks good.\n\nAcked-by: A <a@b>\n")
        self.assertEqual(tags, ["Acked-by: A <a@b>"])
        self.assertFalse(only)
        self.assertEqual(len(blocks), 1)


if __name__ == "__main__":
    unittest.main()
