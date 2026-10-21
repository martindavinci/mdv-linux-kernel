# SPDX-License-Identifier: GPL-2.0
"""Tests for policy_snapshot.py."""
from __future__ import annotations

import json
import os
import time
import unittest

from tests.gitfixture import GitRepo, make_tmpdir, run_script

import policy_snapshot

CODING_ASSISTANTS = "Documentation/process/coding-assistants.rst"


def codes(payload: dict) -> list[str]:
    return sorted(f["code"] for f in payload["findings"])


def doc(payload: dict, path: str) -> dict:
    return [d for d in payload["docs"] if d["path"] == path][0]


class PolicySnapshotTest(unittest.TestCase):
    def setUp(self):
        self.repo = GitRepo.create(make_tmpdir(self))
        self.repo.commit("init", {CODING_ASSISTANTS: "AI rules\n", "MAINTAINERS": "M: x\n"})

    def snap(self, **kwargs) -> dict:
        return policy_snapshot.snapshot(self.repo.path, **kwargs)

    def make_upstream(self, files: dict[str, str]) -> None:
        self.repo.git("checkout", "-q", "-b", "upstream")
        self.repo.commit("upstream change", files)
        self.repo.git("checkout", "-q", "main")

    def test_policy_docs_list(self):
        self.assertEqual(policy_snapshot.POLICY_DOCS, [
            "Documentation/process/coding-assistants.rst",
            "Documentation/process/generated-content.rst",
            "Documentation/process/submitting-patches.rst",
            "Documentation/process/development-process.rst",
            "Documentation/process/maintainer-netdev.rst",
            "Documentation/process/security-bugs.rst",
            "MAINTAINERS",
        ])

    def test_missing_doc_is_info(self):
        payload = self.snap()
        missing = [f for f in payload["findings"] if f["code"] == "DOC_MISSING"]
        self.assertEqual(len(missing), 5)
        self.assertEqual({f["severity"] for f in missing}, {"info"})
        present = doc(payload, CODING_ASSISTANTS)
        self.assertTrue(present["exists"])
        self.assertEqual(present["last_commit"], self.repo.git("rev-parse", "HEAD"))
        self.assertFalse(present["locally_modified"])
        self.assertFalse(doc(payload, "MAINTAINERS")["locally_modified"])
        self.assertEqual(payload["head"], self.repo.git("rev-parse", "HEAD"))
        self.assertIsNone(payload["upstream"])
        self.assertIn("UPSTREAM_MISSING", codes(payload))
        self.assertNotIn("STALE_CHECKOUT", codes(payload))

    def test_locally_modified_doc(self):
        with open(os.path.join(self.repo.path, "MAINTAINERS"), "w", newline="") as f:
            f.write("M: changed\n")
        payload = self.snap()
        self.assertTrue(doc(payload, "MAINTAINERS")["locally_modified"])
        modified = [f for f in payload["findings"] if f["code"] == "DOC_MODIFIED"]
        self.assertEqual(len(modified), 1)
        self.assertEqual(modified[0]["severity"], "warn")
        self.assertIn("MAINTAINERS", modified[0]["message"] + (modified[0]["detail"] or ""))

    def test_stale_checkout(self):
        old = int(time.time()) - 60 * 86400
        self.repo.commit("old", {"README": "x\n"},
                         env={"GIT_COMMITTER_DATE": f"@{old} +0000"})
        payload = self.snap()
        self.assertGreaterEqual(payload["age_days"], 59)
        self.assertIn("STALE_CHECKOUT", codes(payload))
        self.assertNotIn("STALE_CHECKOUT", codes(self.snap(max_age_days=90)))

    def test_upstream_changed_doc(self):
        self.make_upstream({CODING_ASSISTANTS: "AI rules v2\n"})
        payload = self.snap(upstream="upstream")
        self.assertEqual(payload["upstream"], {"ref": "upstream", "behind": 1})
        self.assertTrue(doc(payload, CODING_ASSISTANTS)["changed_upstream"])
        self.assertFalse(doc(payload, "MAINTAINERS")["changed_upstream"])
        changed = [f for f in payload["findings"] if f["code"] == "DOC_CHANGED_UPSTREAM"]
        self.assertEqual(len(changed), 1)
        self.assertIn(f"git show upstream:{CODING_ASSISTANTS}", changed[0]["message"])

    def test_behind_upstream(self):
        self.make_upstream({"net/foo.c": "x\n"})
        payload = self.snap(upstream="upstream")
        self.assertEqual(payload["upstream"]["behind"], 1)
        self.assertIn("BEHIND_UPSTREAM", codes(payload))
        self.assertNotIn("DOC_CHANGED_UPSTREAM", codes(payload))
        self.assertNotIn("UPSTREAM_MISSING", codes(payload))

    def test_upstream_missing(self):
        payload = self.snap(upstream="nosuch")
        self.assertIsNone(payload["upstream"])
        self.assertIn("UPSTREAM_MISSING", codes(payload))

    def test_cli(self):
        self.make_upstream({"net/foo.c": "x\n"})
        res = run_script("policy_snapshot.py", "--repo", self.repo.path, "--upstream", "main",
                         "--json")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(codes(json.loads(res.stdout)), ["DOC_MISSING"] * 5)
        res = run_script("policy_snapshot.py", "--repo", self.repo.path, "--upstream", "upstream")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("BEHIND_UPSTREAM", res.stdout)
        res = run_script("policy_snapshot.py", "--repo", os.path.join(self.repo.path, "missing"))
        self.assertEqual(res.returncode, 2)


if __name__ == "__main__":
    unittest.main()
