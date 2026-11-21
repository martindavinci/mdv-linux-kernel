# SPDX-License-Identifier: GPL-2.0
"""Tests for nipa_results.py (layout as written by NIPA's core/test.py)."""
from __future__ import annotations

import json
import os
import unittest

from tests.gitfixture import make_tmpdir, run_script


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        f.write(text)


def test_dir(base: str, rel: str, retcode: int | None, desc: str = "") -> None:
    path = os.path.join(base, rel)
    os.makedirs(path, exist_ok=True)
    write(os.path.join(path, "summary"), f"==========\n{rel} - X\n")
    if retcode is not None:
        write(os.path.join(path, "retcode"), str(retcode))
    if desc:
        write(os.path.join(path, "desc"), desc + "\n")


class NipaResultsTest(unittest.TestCase):
    def make_run(self, series_id: str = "1", done: bool = True) -> tuple[str, str]:
        root = make_tmpdir(self)
        series = os.path.join(root, series_id)
        test_dir(series, "tree_selection", 0, "net-next")
        test_dir(series, "series_format", 0)
        test_dir(series, "7/build_allmodconfig_warn", 0, "Errors and warnings before: 0 after: 0")
        test_dir(series, "8/build_allmodconfig_warn", 0)
        if done:
            write(os.path.join(series, ".tester_done"), "")
        return root, series

    def run_json(self, root: str, *extra: str):
        res = run_script("nipa_results.py", root, "--json", *extra)
        return res, (json.loads(res.stdout) if res.stdout.strip() else None)

    def test_all_okay(self):
        root, _ = self.make_run()
        res, data = self.run_json(root)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(data["findings"], [])
        self.assertEqual(sorted((r["test"], r["patch"]) for r in data["results"]),
                         [("build_allmodconfig_warn", 1), ("build_allmodconfig_warn", 2),
                          ("series_format", None)])

    def test_warning_failure_pending_and_missing_retcode(self):
        root, series = self.make_run()
        test_dir(series, "8/build_clang", 250, "new warnings")
        test_dir(series, "8/checkpatch", 1, "line too long")
        test_dir(series, "7/maintainers", 111)
        test_dir(series, "7/broken", None)
        res, data = self.run_json(root)
        self.assertEqual(res.returncode, 1)
        codes = sorted((f["code"], f["severity"]) for f in data["findings"])
        self.assertEqual(codes, [("TEST_FAILED", "error"), ("TEST_FAILED", "error"),
                                 ("TEST_PENDING", "warn"), ("TEST_WARNING", "warn")])

    def test_apply_failed(self):
        root = make_tmpdir(self)
        series = os.path.join(root, "1")
        test_dir(series, "tree_selection", 0)
        test_dir(series, "apply", 1, "Patch 2 does not apply")
        write(os.path.join(series, ".tester_done"), "")
        res, data = self.run_json(root)
        self.assertEqual(res.returncode, 1)
        self.assertEqual([f["code"] for f in data["findings"]], ["APPLY_FAILED"])
        self.assertFalse(data["applied"])

    def test_incomplete_run(self):
        root, _ = self.make_run(done=False)
        res, data = self.run_json(root)
        self.assertEqual(res.returncode, 1)
        self.assertIn("INCOMPLETE", [f["code"] for f in data["findings"]])

    def test_latest_series_and_explicit_id(self):
        root, _ = self.make_run("1")
        series2 = os.path.join(root, "2")
        test_dir(series2, "series_format", 1, "bad")
        write(os.path.join(series2, ".tester_done"), "")
        res, data = self.run_json(root)
        self.assertEqual((res.returncode, data["series_id"]), (1, "2"))
        res, data = self.run_json(root, "--series-id", "1")
        self.assertEqual((res.returncode, data["series_id"]), (0, "1"))

    def test_usage_errors(self):
        root = make_tmpdir(self)
        self.assertEqual(run_script("nipa_results.py", root).returncode, 2)
        self.assertEqual(run_script("nipa_results.py", os.path.join(root, "x")).returncode, 2)

    def test_human_output(self):
        root, series = self.make_run()
        test_dir(series, "8/checkpatch", 1, "line too long")
        res = run_script("nipa_results.py", root)
        self.assertIn("FAILED   checkpatch [patch 2] - line too long", res.stdout)


if __name__ == "__main__":
    unittest.main()
