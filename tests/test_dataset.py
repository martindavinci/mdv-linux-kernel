# SPDX-License-Identifier: GPL-2.0
"""Tests for evals/tools/validate_dataset.py and the shipped dataset."""
from __future__ import annotations

import contextlib
import io
import json
import os
import unittest

from tests.gitfixture import REPO_ROOT, GitRepo, make_tmpdir

import validate_dataset as vd

DATASET = os.path.join(REPO_ROOT, "evals", "historical", "dataset.json")


def accepted(**over) -> dict:
    case = {
        "id": "acc-1", "kind": "accepted-fix", "split": "dev", "tree": "net", "base": None,
        "commit": "a" * 40, "subject": "net: foo: fix bar", "patch": "123.456-1-x@example.org",
        "lore": "https://lore.kernel.org/r/123.456-1-x@example.org",
        "fixes": 'Fixes: 0123456789ab ("net: foo: add bar")',
        "expected": {"must_fire": [], "must_not_error": True},
        "rationale": {"quote": "applied", "url": "https://lore.kernel.org/r/n@k"},
        "provenance": {"retrieved": "2026-10-22", "sources": ["https://patchwork.kernel.org/x"],
                       "verified_web": "2026-10-22", "verified_tree": None},
    }
    case.update(over)
    return case


def rejected(**over) -> dict:
    case = accepted(id="rej-1", kind="rejected", commit=None, fixes=None,
                    expected={"must_fire": ["standalone-cleanup"], "must_not_error": False},
                    rationale={"quote": "we do not accept checkpatch fixes",
                               "url": "https://lore.kernel.org/r/q@k"})
    case.update(over)
    return case


class SchemaTest(unittest.TestCase):
    def errors(self, *cases) -> list[str]:
        return vd.validate(list(cases))

    def test_valid_cases(self):
        self.assertEqual(self.errors(accepted(), rejected()), [])

    def test_shipped_dataset_is_valid(self):
        with open(DATASET, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(vd.validate(data), [])
        self.assertGreaterEqual(len(data), 6)
        self.assertEqual({c["split"] for c in data}, {"dev", "heldout"})
        for c in data:  # facts were read from a source, never typed from memory
            self.assertTrue(c["provenance"]["sources"])
            self.assertIsNone(c["provenance"]["verified_tree"])

    def test_rules(self):
        bad = [
            (accepted(id="Bad Id"), "id must"),
            (accepted(kind="maybe"), "kind must"),
            (accepted(split="train"), "split must"),
            (accepted(tree="linux-next"), "tree must"),
            (accepted(commit="abc"), "full 40-hex"),
            (accepted(patch="<a@b>"), "message-id"),
            (accepted(lore="https://example.org/x"), "lore.kernel.org"),
            (accepted(fixes="Fixes: abc"), "Fixes: <12+ hex>"),
            (accepted(expected={"must_fire": [], "must_not_error": False}), "must_not_error must be true"),
            (accepted(expected={"must_fire": ["nonsense gate"], "must_not_error": True}), "must_fire"),
            (rejected(expected={"must_fire": [], "must_not_error": False}), "must name the gate"),
            (rejected(rationale={"quote": None, "url": None}), "reviewer's quote"),
            (rejected(commit="a" * 40), "commit must be null"),
            (accepted(provenance={"retrieved": "2026-10-22", "sources": []}), "provenance.sources"),
            (accepted(provenance={"retrieved": "yesterday", "sources": ["https://x"]}), "YYYY-MM-DD"),
            (accepted(provenance={"sources": ["https://x"]}), "retrieved is required"),
        ]
        for case, needle in bad:
            errs = self.errors(case)
            self.assertTrue(any(needle in e for e in errs), (needle, errs))
        self.assertTrue(any("duplicate id" in e for e in self.errors(accepted(), accepted())))
        self.assertEqual(vd.validate({}), ["dataset must be a JSON list"])

    def test_malformed_types_are_findings_not_crashes(self):
        errs = self.errors(accepted(id=[]), accepted(id="x", kind=["a"], split={}, tree=1),
                           accepted(id="y", expected={"must_fire": 3, "must_not_error": True}))
        self.assertTrue(any("id must" in e for e in errs))
        self.assertTrue(any("kind must" in e for e in errs))
        self.assertTrue(any("must_fire" in e for e in errs))

    def test_script_codes_allowed_in_must_fire(self):
        case = rejected(expected={"must_fire": ["FIXES_TITLE_MISMATCH"], "must_not_error": False})
        self.assertEqual(self.errors(case), [])


class TreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tmpdir(self)
        self.repo = GitRepo.create(self.tmp)
        self.base = self.repo.commit("net: foo: add bar", {"net/foo.c": "int foo;\n"})
        self.fix = self.repo.commit("net: foo: fix bar", {"net/foo.c": "int foo = 1;\n"})
        self.file = os.path.join(self.tmp, "dataset.json")

    def write(self, cases):
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(cases, f)

    def main(self, *args) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = vd.main(["--file", self.file, *args])
        return code, out.getvalue()

    def test_verified_in_tree_and_write(self):
        case = accepted(commit=self.fix, fixes=f'Fixes: {self.base[:12]} ("net: foo: add bar")')
        self.write([case, rejected()])
        code, out = self.main("--repo", self.repo.path)
        self.assertEqual(code, 0, out)
        self.assertIn("verified in tree: 1", out)
        with open(self.file, encoding="utf-8") as f:
            self.assertIsNone(json.load(f)[0]["provenance"]["verified_tree"])
        code, _ = self.main("--repo", self.repo.path, "--write")
        self.assertEqual(code, 0)
        with open(self.file, encoding="utf-8") as f:
            self.assertRegex(json.load(f)[0]["provenance"]["verified_tree"], r"^\d{4}-\d{2}-\d{2}$")

    def test_tree_mismatches(self):
        cases = [accepted(id="missing", commit="b" * 40),
                 accepted(id="subject", commit=self.fix, subject="net: other"),
                 accepted(id="fixes", commit=self.fix,
                          fixes='Fixes: cccccccccccc ("net: nowhere")')]
        self.write(cases)
        code, out = self.main("--repo", self.repo.path)
        self.assertEqual(code, 1)
        for needle in ("not in", "subject in tree", "Fixes: target"):
            self.assertIn(needle, out)

    def test_write_needs_repo(self):
        self.write([accepted()])
        self.assertEqual(self.main("--write")[0], 2)


if __name__ == "__main__":
    unittest.main()
