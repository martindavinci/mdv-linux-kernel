# SPDX-License-Identifier: GPL-2.0
"""Tests for mlk.py (next and report) on toy kernel-like repositories."""
from __future__ import annotations

import json
import os
import sys
import unittest

from tests.gitfixture import GitRepo, make_tmpdir, run_script

import mlk

KERNEL_FILES = {"MAINTAINERS": "x\n", "Makefile": "VERSION = 7\n",
                "scripts/checkpatch.pl": "#!/usr/bin/perl\n"}
SOB = "Signed-off-by: Test Human <human@example.org>"
FIX = ("net: foo: fix bar\n\nbar() oopses without a PHY. Found by code inspection, "
       "not triggered; not tested.\n\n" + SOB + "\n")


class Series:
    """A kernel-like repo with a base on main and a 'series' branch."""

    def __init__(self, testcase: unittest.TestCase, kernel: bool = True):
        self.repo = GitRepo.create(make_tmpdir(testcase))
        self.base = self.repo.commit("init\n", KERNEL_FILES if kernel else {"README": "x\n"})
        self.repo.git("update-ref", "refs/remotes/origin/main", self.base)
        self.repo.git("switch", "-q", "-c", "series")
        self.tc = testcase

    def next(self, *extra: str) -> tuple[int, dict]:
        res = run_script("mlk.py", "next", "--repo", self.repo.path, "--upstream",
                         "origin/main", "--json", *extra)
        self.tc.assertIn(res.returncode, (0, 1), res.stdout + res.stderr)
        return res.returncode, json.loads(res.stdout)

    def ev(self, *args: str) -> None:
        res = run_script("evidence.py", *args, "--repo", self.repo.path)
        self.tc.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def ev_run(self, name: str, category: str = "style", code: str = "print('ok')") -> None:
        res = run_script("evidence.py", "run", "--repo", self.repo.path, "--name", name,
                         "--category", category, "--", sys.executable, "-c", code)
        self.tc.assertIn(res.returncode, (0, 1), res.stdout + res.stderr)

    def ready_store(self) -> None:
        """Store with every note and a passing required check."""
        self.ev("init", "--base", self.base)
        self.ev("note", "--key", "policy", "--value", "docs read")
        self.ev("require", "--name", "checkpatch", "--category", "style", "--why", "style")
        self.ev_run("checkpatch")
        for key in ("security_screen", "comprehension", "disclosure"):
            self.ev("note", "--key", key, "--value", "done")


def ids(payload: dict) -> list[str]:
    return [it["id"] for it in payload["blocking"]]


class NextTest(unittest.TestCase):
    def test_not_a_kernel_tree(self):
        s = Series(self, kernel=False)
        rc, p = s.next()
        self.assertEqual((rc, p["phase"], ids(p)), (1, "setup", ["kernel-tree"]))
        self.assertFalse(p["blocking"][0]["confirmable"])

    def test_policy_finding_blocks_until_confirmed(self):
        s = Series(self)
        s.repo.commit(FIX, {"net/foo.c": "x\n"})
        s.ev("init", "--base", s.base)
        res = run_script("mlk.py", "next", "--repo", s.repo.path, "--json")
        p = json.loads(res.stdout)
        self.assertEqual((p["phase"], ids(p)), ("setup", ["UPSTREAM_MISSING"]))
        s.ev("note", "--key", "human_confirmations", "--value",
             "UPSTREAM_MISSING: I only have this local tree, proceed")
        p = json.loads(run_script("mlk.py", "next", "--repo", s.repo.path, "--json").stdout)
        self.assertEqual(p["phase"], "develop")
        self.assertEqual([it["id"] for it in p["confirmed"]], ["UPSTREAM_MISSING"])

    def test_doc_finding_cleared_by_policy_note(self):
        s = Series(self)
        doc = "Documentation/process/coding-assistants.rst"
        s.repo.git("switch", "-q", "main")
        s.repo.commit("docs: base rules\n", {doc: "old\n"})
        base = s.repo.git("rev-parse", "HEAD")
        upstream = s.repo.commit("docs: new rules\n", {doc: "new\n"})
        s.repo.git("update-ref", "refs/remotes/origin/main", upstream)
        s.repo.git("switch", "-q", "-C", "series", base)
        s.base = base
        s.repo.commit(FIX, {"lib/foo.c": "x\n"})
        s.ev("init", "--base", base)
        _, p = s.next()
        self.assertEqual((p["phase"], sorted(ids(p))),
                         ("setup", ["BEHIND_UPSTREAM", "DOC_CHANGED_UPSTREAM"]))
        s.ev("note", "--key", "policy", "--value",
             "docs read; findings: DOC_CHANGED_UPSTREAM, read the upstream text; "
             "BEHIND_UPSTREAM told the human")
        _, p = s.next()
        self.assertEqual(ids(p), ["BEHIND_UPSTREAM"])  # the human must confirm this one
        s.ev("note", "--key", "human_confirmations", "--value", "BEHIND_UPSTREAM: rebase later")
        _, p = s.next()
        self.assertEqual(p["phase"], "validate")

    def test_no_commits_is_find_work(self):
        s = Series(self)
        rc, p = s.next()
        self.assertEqual((p["phase"], ids(p)), ("find-work", ["no-commits"]))

    def test_commits_without_store(self):
        s = Series(self)
        s.repo.commit(FIX, {"drivers/net/foo.c": "x\n"})
        _, p = s.next()
        self.assertEqual((p["phase"], ids(p)), ("develop", ["evidence-store"]))
        self.assertIn(f"init --base {s.base[:12]}", p["next"]["cmd"])
        self.assertTrue(p["netdev"])

    def test_walk_to_ready(self):
        s = Series(self)
        s.repo.commit(FIX, {"lib/foo.c": "x\n"})
        s.ev("init", "--base", s.base)
        _, p = s.next()
        self.assertEqual((p["phase"], ids(p)), ("develop", ["policy"]))
        s.ev("note", "--key", "policy", "--value", "docs read")
        _, p = s.next()
        self.assertEqual((p["phase"], ids(p)), ("validate", ["required", "security_screen"]))
        self.assertIn("--preset generic", p["next"]["cmd"])
        self.assertFalse(p["netdev"])
        s.ev("require", "--name", "checkpatch", "--category", "style", "--why", "style")
        _, p = s.next()
        self.assertEqual(ids(p), ["checkpatch", "security_screen"])
        s.ev_run("checkpatch")
        s.ev("note", "--key", "security_screen", "--value", "not security relevant")
        _, p = s.next()
        self.assertEqual((p["phase"], ids(p)), ("gate", ["comprehension", "disclosure"]))
        s.ev("note", "--key", "comprehension", "--value", "answered")
        s.ev("note", "--key", "disclosure", "--value", "cover letter note")
        _, p = s.next()
        self.assertEqual((p["phase"], ids(p)), ("submit", ["preflight"]))
        s.ev_run("preflight")
        rc, p = s.next()
        self.assertEqual((rc, p["phase"], p["blocking"]), (0, "ready", []))
        self.assertEqual(p["next"]["who"], "human")
        self.assertIn("b4 send", p["next"]["cmd"])

    def test_missing_signoff_is_human_and_not_confirmable(self):
        s = Series(self)
        s.repo.commit(FIX.replace(SOB, ""), {"lib/foo.c": "x\n"})
        s.ready_store()
        s.ev("note", "--key", "human_confirmations", "--value", "signoff: skip it")
        _, p = s.next()
        self.assertEqual(p["phase"], "submit")
        sign = next(it for it in p["blocking"] if it["id"] == "signoff")
        self.assertEqual(sign["next"]["who"], "human")
        self.assertIn("--signoff", sign["next"]["cmd"])
        self.assertEqual(p["next"], sign["next"])

    def test_failed_check_can_be_confirmed_missing_cannot(self):
        s = Series(self)
        s.repo.commit(FIX, {"lib/foo.c": "x\n"})
        s.ready_store()
        s.ev("require", "--name", "build", "--category", "build", "--why", "build")
        s.ev_run("checkpatch", code="import sys; sys.exit(1)")
        s.ev("note", "--key", "human_confirmations", "--value",
             "checkpatch: accepted the long line; build: skip")
        _, p = s.next()
        self.assertEqual((p["phase"], ids(p)), ("validate", ["build"]))
        self.assertEqual([it["id"] for it in p["confirmed"]], ["checkpatch"])

    def test_stale_check_suggests_rerun(self):
        s = Series(self)
        s.repo.commit(FIX, {"lib/foo.c": "x\n"})
        s.ready_store()
        s.repo.commit(FIX, {"lib/foo.c": "y\n"})
        _, p = s.next()
        stale = p["blocking"][0]
        self.assertEqual((p["phase"], stale["id"], stale["state"]),
                         ("validate", "checkpatch", "stale"))
        self.assertIn("run --name checkpatch --category style --", stale["next"]["cmd"])

    def test_open_review_comment_is_revise(self):
        s = Series(self)
        s.repo.commit(FIX, {"lib/foo.c": "x\n"})
        s.ready_store()

        def rt(*args: str):
            res = run_script("review_tracker.py", *args, "--repo", s.repo.path)
            self.assertIn(res.returncode, (0, 1), res.stdout + res.stderr)
            return res

        rt("add", "--patch", "1", "--text", "take the lock here", "--author", "Rev")
        _, p = s.next()
        self.assertEqual((p["phase"], ids(p)), ("revise", ["review:manual-1"]))
        sha = s.repo.git("rev-parse", "HEAD")
        rt("propose", "manual-1", "--status", "addressed", "--why", "lock taken",
           "--commit", sha)
        _, p = s.next()
        self.assertEqual(p["blocking"][0]["state"], "proposed")
        self.assertEqual(p["next"]["who"], "human")
        rt("confirm", "manual-1", "--words", "yes, fixed in v2")
        _, p = s.next()
        self.assertEqual(p["phase"], "submit")

    def test_dirty_tree_blocks(self):
        s = Series(self)
        s.repo.commit(FIX, {"lib/foo.c": "x\n"})
        s.ready_store()
        with open(os.path.join(s.repo.path, "lib", "foo.c"), "w") as f:
            f.write("dirty\n")
        _, p = s.next()
        self.assertEqual(p["phase"], "develop")
        self.assertIn("dirty", ids(p))

    def test_human_text_output(self):
        s = Series(self)
        res = run_script("mlk.py", "next", "--repo", s.repo.path, "--upstream", "origin/main")
        self.assertIn("phase find-work", res.stdout)
        self.assertIn("next (agent):", res.stdout)

    def test_human_only_commands_forced(self):
        for cmd in ("b4 send --reflect", "git send-email x", "git rebase --signoff HEAD~1",
                    "b4 prep -n foo", "b4 prep --enroll"):
            self.assertEqual(mlk.item("x", "missing", "y", do="z", cmd=cmd)["next"]["who"],
                             "human", cmd)
        self.assertEqual(mlk.item("x", "missing", "y", do="z", cmd="b4 prep -p d")
                         ["next"]["who"], "agent")


class ReportTest(unittest.TestCase):
    def test_report_is_escaped_and_self_contained(self):
        s = Series(self)
        s.repo.commit("lib: foo: fix <script>alert(1)</script>\n\nBody & more.\n\n" + SOB + "\n",
                      {"lib/foo.c": "<img src=x onerror=alert(1)>\n"})
        s.ready_store()
        s.ev("note", "--key", "human_confirmations", "--value", "LLM_PHRASES: <b>ok</b>")
        out = os.path.join(make_tmpdir(self), "r.html")
        res = run_script("mlk.py", "report", "--repo", s.repo.path, "--upstream", "origin/main",
                         "--out", out)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        with open(out, encoding="utf-8") as f:
            page = f.read()
        self.assertIn("Content-Security-Policy", page)
        self.assertNotIn("<script", page)
        self.assertNotIn("<img", page)
        self.assertNotIn("<b>ok</b>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("LLM_PHRASES: &lt;b&gt;ok&lt;/b&gt;", page)
        self.assertNotIn("src=", page.replace("src=x", ""))
        self.assertNotIn("<link", page)
        self.assertIn("checkpatch", page)

    def test_default_path_in_series_dir(self):
        s = Series(self)
        s.repo.commit(FIX, {"lib/foo.c": "x\n"})
        s.ev("init", "--base", s.base)
        res = run_script("mlk.py", "report", "--repo", s.repo.path, "--upstream", "origin/main")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        path = res.stdout.strip()
        self.assertTrue(path.endswith("report.html"))
        self.assertIn(os.path.join(".git", "mdv-linux-kernel", "series"), path)
        self.assertTrue(os.path.isfile(path))


if __name__ == "__main__":
    unittest.main()
