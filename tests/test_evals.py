# SPDX-License-Identifier: GPL-2.0
"""Shape checks for evals/scenarios.json."""
from __future__ import annotations

import json
import os
import unittest

from tests.gitfixture import REPO_ROOT


class ScenariosTest(unittest.TestCase):
    def test_scenarios_shape(self):
        with open(os.path.join(REPO_ROOT, "evals", "scenarios.json"), encoding="utf-8") as f:
            scenarios = json.load(f)
        self.assertGreaterEqual(len(scenarios), 22)
        ids = set()
        for s in scenarios:
            self.assertEqual(set(s), {"id", "mode", "setup", "prompt", "expect"}, s.get("id"))
            self.assertIn(s["mode"], {"setup", "find-work", "have-patch"})
            self.assertTrue(s["expect"] and all(isinstance(e, str) for e in s["expect"]))
            self.assertNotIn(s["id"], ids)
            ids.add(s["id"])


if __name__ == "__main__":
    unittest.main()
