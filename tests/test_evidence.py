# SPDX-License-Identifier: GPL-2.0
"""Tests for evidence.py."""
from __future__ import annotations

import json
import os
import re
import sys
import unittest

from tests.gitfixture import GitRepo, make_tmpdir, run_script

import evidence
import mlk_common

PASS_CMD = [sys.executable, "-c", "print('hello evidence')"]
FAIL_CMD = [sys.executable, "-c", "import sys; sys.exit(3)"]
PASS_WORD = re.compile(r"\bpass", re.I)


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.repo = GitRepo.create(make_tmpdir(self))
        self.base = self.repo.commit("init", {"net/foo.c": "int foo;\n"})
        self.repo.commit("net: foo: change", {"net/foo.c": "int foo = 1;\n"})
        res = self.ev("init", "--base", self.base)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def ev(self, *args: str):
        sub, rest = args[0], list(args[1:])
        cmd = []
        if "--" in rest:
            i = rest.index("--")
            rest, cmd = rest[:i], rest[i:]
        return run_script("evidence.py", sub, "--repo", self.repo.path, *rest, *cmd)

    def status(self) -> tuple[int, dict]:
        res = self.ev("status", "--json")
        return res.returncode, json.loads(res.stdout)

    def statuses(self) -> dict[str, str]:
        return {e["name"]: e["effective_status"] for e in self.status()[1]["entries"]}

    def store(self) -> dict:
        path = os.path.join(mlk_common.state_dir(self.repo.path), "evidence.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_init_records_base_and_toolchain(self):
        data = self.store()
        self.assertEqual(data["base"], self.base)
        self.assertIn("gcc", data["toolchain"])
        self.assertEqual(data["entries"], [])

    def test_run_records_pass_and_fail(self):
        res = self.ev("run", "--name", "ok", "--category", "test", "--", *PASS_CMD)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("hello evidence", res.stdout)
        res = self.ev("run", "--name", "bad", "--category", "build", "--", *FAIL_CMD)
        self.assertEqual(res.returncode, 3)
        code, payload = self.status()
        self.assertEqual(code, 1)
        self.assertEqual(self.statuses(), {"ok": "pass", "bad": "fail"})
        ok = [e for e in payload["entries"] if e["name"] == "ok"][0]
        self.assertEqual(ok["exit_code"], 0)
        self.assertEqual(ok["category"], "test")
        for key in ("cmd", "started", "duration_s", "log", "state"):
            self.assertIn(key, ok)
        with open(ok["log"], encoding="utf-8") as f:
            self.assertIn("hello evidence", f.read())
        self.assertTrue(ok["log"].startswith(mlk_common.state_dir(self.repo.path)))

    def test_entry_stale_after_amend(self):
        self.ev("run", "--name", "ok", "--category", "test", "--", *PASS_CMD)
        self.assertEqual(self.status()[0], 0)
        self.repo.git("commit", "--amend", "-q", "-m", "x")
        code, _ = self.status()
        self.assertEqual(self.statuses(), {"ok": "stale"})
        self.assertEqual(code, 1)

    def test_entry_stale_when_dirty(self):
        self.ev("run", "--name", "ok", "--category", "test", "--", *PASS_CMD)
        with open(os.path.join(self.repo.path, "net", "foo.c"), "w", newline="") as f:
            f.write("int foo = 2;\n")
        self.assertEqual(self.statuses(), {"ok": "stale"})
        self.assertEqual(self.status()[0], 1)
        self.repo.git("checkout", "--", "net/foo.c")
        self.assertEqual(self.statuses(), {"ok": "pass"})

    def test_rerun_supersedes_stale_entry(self):
        self.ev("run", "--name", "ok", "--category", "test", "--", *PASS_CMD)
        self.repo.git("commit", "--amend", "-q", "-m", "x")
        self.ev("run", "--name", "ok", "--category", "test", "--", *PASS_CMD)
        self.assertEqual(self.status()[0], 0)
        self.assertEqual(self.statuses(), {"ok": "pass"})

    def test_skip_reported_as_skipped(self):
        res = self.ev("skip", "--name", "smatch", "--category", "static", "--reason", "not installed")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.statuses(), {"smatch": "skip"})
        self.assertEqual(self.status()[0], 0)
        report = self.ev("report").stdout
        self.assertIn("SKIPPED: not installed", report)

    def test_report_never_says_pass_for_stale_or_skip(self):
        self.ev("run", "--name", "ok", "--category", "test", "--", *PASS_CMD)
        self.ev("skip", "--name", "smatch", "--category", "static", "--reason", "not installed")
        report = self.ev("report").stdout
        for section in ("State", "Toolchain", "Checks", "Notes"):
            self.assertIn(section, report)
        self.assertEqual(len(PASS_WORD.findall(report)), 1, report)
        self.repo.git("commit", "--amend", "-q", "-m", "x")
        report = self.ev("report").stdout
        self.assertIn("STALE (re-run)", report)
        self.assertIsNone(PASS_WORD.search(report), report)

    def test_init_refuses_overwrite(self):
        res = self.ev("init", "--base", self.base)
        self.assertEqual(res.returncode, 1)
        self.ev("run", "--name", "ok", "--category", "test", "--", *PASS_CMD)
        res = self.ev("init", "--base", self.base, "--force")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.store()["entries"], [])

    def test_notes_append(self):
        self.ev("note", "--key", "comprehension", "--value", "answered 4/5")
        self.ev("note", "--key", "comprehension", "--value", "follow-up answered")
        self.ev("note", "--key", "disclosure", "--value", "cover letter note")
        notes = self.store()["notes"]
        self.assertEqual([n["value"] for n in notes["comprehension"]],
                         ["answered 4/5", "follow-up answered"])
        self.assertEqual(len(notes["disclosure"]), 1)
        self.assertIn("answered 4/5", self.ev("report").stdout)

    def test_bad_category_is_usage_error(self):
        res = self.ev("run", "--name", "x", "--category", "bogus", "--", *PASS_CMD)
        self.assertEqual(res.returncode, 2)


class EffectiveStatusTest(unittest.TestCase):
    STATE = {"head": "h1", "tree": "t1", "dirty": False}

    def entry(self, status="pass", **state):
        return {"status": status, "state": dict(self.STATE, **state)}

    def test_effective_status(self):
        self.assertEqual(evidence.effective_status(self.entry(), self.STATE), "pass")
        self.assertEqual(evidence.effective_status(self.entry("fail"), self.STATE), "fail")
        self.assertEqual(evidence.effective_status(self.entry(tree="t0"), self.STATE), "stale")
        self.assertEqual(evidence.effective_status(self.entry(head="h0"), self.STATE), "stale")
        self.assertEqual(evidence.effective_status(self.entry(dirty=True), self.STATE), "stale")
        self.assertEqual(evidence.effective_status(self.entry(), dict(self.STATE, dirty=True)),
                         "stale")
        self.assertEqual(evidence.effective_status(self.entry("skip", tree="t0"), self.STATE), "skip")

    def test_current_state(self):
        repo = GitRepo.create(make_tmpdir(self))
        repo.commit("init", {"a": "1\n"})
        st = evidence.current_state(repo.path)
        self.assertEqual(st["head"], repo.git("rev-parse", "HEAD"))
        self.assertEqual(st["tree"], repo.git("rev-parse", "HEAD^{tree}"))
        self.assertFalse(st["dirty"])
        with open(os.path.join(repo.path, "untracked"), "w") as f:
            f.write("x")
        self.assertFalse(evidence.current_state(repo.path)["dirty"])
        with open(os.path.join(repo.path, "a"), "w") as f:
            f.write("2\n")
        self.assertTrue(evidence.current_state(repo.path)["dirty"])


if __name__ == "__main__":
    unittest.main()
