# SPDX-License-Identifier: GPL-2.0
"""Tests for evals/tools/results.py."""
from __future__ import annotations

import contextlib
import io
import json
import os
import unittest

from tests.gitfixture import make_tmpdir

import results

SCENARIOS = [
    {"id": "a", "mode": "have-patch", "setup": "s", "prompt": "p", "expect": ["x", "y", "z"]},
    {"id": "b", "mode": "setup", "setup": "s", "prompt": "p", "expect": ["only"]},
]


class ResultsTest(unittest.TestCase):
    def setUp(self):
        tmp = make_tmpdir(self)
        self.file = os.path.join(tmp, "results", "results.jsonl")
        self.scen = os.path.join(tmp, "scenarios.json")
        self.write_scenarios(SCENARIOS)

    def write_scenarios(self, data):
        with open(self.scen, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def call(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = results.main([args[0], "--file", self.file, "--scenarios", self.scen,
                                 *args[1:]])
        return code, out.getvalue(), err.getvalue()

    def record(self, scenario="a", met="1,2,3", unmet=None, agent="claude-code", *extra):
        args = ["record", "--scenario", scenario, "--agent", agent, "--agent-version", "2.1",
                "--model", "m", "--skill-commit", "abc"]
        if met:
            args += ["--met", met]
        if unmet:
            args += ["--unmet", unmet]
        return self.call(*args, *extra)

    def rows(self):
        return results.read_rows(self.file)

    def test_hash_ignores_key_order(self):
        a = {"id": "x", "expect": ["1"], "mode": "setup"}
        b = {"mode": "setup", "expect": ["1"], "id": "x"}
        self.assertEqual(results.scenario_hash(a), results.scenario_hash(b))
        self.assertNotEqual(results.scenario_hash(a), results.scenario_hash(dict(a, id="y")))

    def test_record_row(self):
        code, out, err = self.record("a", "1,3", "2", "claude-code", "--note", "2:asked twice",
                                     "--audit", "a.json", "--audit-ok", "yes")
        self.assertEqual(code, 0, err)
        self.assertIn("FAIL", out)
        row = self.rows()[0]
        self.assertEqual(row["scenario_sha256"], results.scenario_hash(SCENARIOS[0]))
        self.assertEqual([e["met"] for e in row["expect"]], [True, False, True])
        self.assertEqual(row["expect"][1]["note"], "asked twice")
        self.assertFalse(row["pass"])
        self.assertTrue(row["audit"]["ok"])
        self.assertEqual(row["skill_commit"], "abc")

    def test_record_validation(self):
        self.assertEqual(self.record("nope")[0], 2)
        self.assertEqual(self.record("a", "1,2")[0], 2)          # 3 missing
        self.assertEqual(self.record("a", "1,2,3", "3")[0], 2)   # duplicate
        self.assertEqual(self.record("a", "1,2,4")[0], 2)        # out of range
        self.assertEqual(self.record("a", "1,x,3")[0], 2)
        self.assertEqual(self.record("a", "1,2,3", None, "c", "--note", "9:x")[0], 2)
        self.assertEqual(self.rows(), [])

    def test_summary_and_obsolete(self):
        self.record("a", "1,2,3", None, "claude-code")
        self.record("b", None, "1", "codex")
        self.record("a", "1,2", "3", "codex", "--audit-ok", "yes")
        table = results.summarise(self.rows(), results.load_scenarios(self.scen))
        self.assertEqual(table[("a", "claude-code")], "pass")
        self.assertEqual(table[("b", "codex")], "FAIL")
        self.assertEqual(table[("a", "codex")], "FAIL")
        code, out, _ = self.call("summary")
        self.assertEqual(code, 0)
        self.assertIn("claude-code", out)
        changed = [dict(SCENARIOS[0], expect=["x", "y", "z changed"]), SCENARIOS[1]]
        self.write_scenarios(changed)
        table = results.summarise(self.rows(), results.load_scenarios(self.scen))
        self.assertEqual(table[("a", "claude-code")], "obsolete")
        self.assertEqual(table[("b", "codex")], "FAIL")

    def test_obsolete_row_does_not_hide_current_result(self):
        self.record("a", "1,2,3", None, "codex")
        rows = self.rows()
        stale = dict(rows[0], scenario_sha256="0" * 64, **{"pass": False})
        table = results.summarise(rows + [stale], results.load_scenarios(self.scen))
        self.assertEqual(table[("a", "codex")], "pass")
        table = results.summarise([stale], results.load_scenarios(self.scen))
        self.assertEqual(table[("a", "codex")], "obsolete")

    def test_failed_audit_fails_the_row(self):
        self.record("b", "1", None, "codex", "--audit-ok", "no")
        table = results.summarise(self.rows(), results.load_scenarios(self.scen))
        self.assertEqual(table[("b", "codex")], "FAIL (audit)")

    def test_empty_summary(self):
        code, out, _ = self.call("summary")
        self.assertEqual(code, 0)
        self.assertIn("no results", out)


if __name__ == "__main__":
    unittest.main()
