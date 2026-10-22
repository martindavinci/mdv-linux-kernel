# SPDX-License-Identifier: GPL-2.0
"""Tests for learner_profile.py."""
from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from unittest import mock

from tests.gitfixture import make_tmpdir

import learner_profile as lp


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_tmpdir(self)
        self.path = os.path.join(self.dir, "cfg", "mdv-linux-kernel", "profile.json")

    def call(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = lp.main([args[0], "--path", self.path, *args[1:]])
        return code, out.getvalue(), err.getvalue()

    def data(self) -> dict:
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def test_show_missing_does_not_create(self):
        code, out, _ = self.call("show")
        self.assertEqual(code, 0)
        self.assertIn("works without one", out)
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.call("show", "--json")[1].strip(), "null")

    def test_commands_need_a_profile(self):
        for args in (("set-level", "less"), ("add-concept", "dco"), ("export",)):
            code, _, err = self.call(*args)
            self.assertEqual(code, 1, args)
            self.assertIn("only if the human wants it", err)
        self.assertFalse(os.path.exists(self.path))

    def test_lifecycle(self):
        self.assertEqual(self.call("init", "--level", "more", "--language", "it")[0], 0)
        self.assertEqual(self.call("init", "--level", "less")[0], 1)  # exists
        self.assertEqual(self.call("add-concept", "merge-window")[0], 0)
        self.assertEqual(self.call("add-concept", "dco")[0], 0)
        self.assertEqual(self.call("add-concept", "dco")[0], 0)
        self.assertEqual(self.call("set-level", "less")[0], 0)
        data = self.data()
        self.assertEqual(data["level"], "less")
        self.assertEqual(data["language"], "it")
        self.assertEqual(data["concepts"], ["dco", "merge-window"])
        self.assertEqual(set(data), lp.FIELDS)
        self.assertEqual(self.call("forget-concept", "dco")[0], 0)
        self.assertEqual(self.data()["concepts"], ["merge-window"])
        exported = json.loads(self.call("export")[1])
        self.assertEqual(exported["concepts"], ["merge-window"])
        self.assertEqual(self.call("init", "--level", "normal", "--force")[0], 0)
        self.assertEqual(self.data()["concepts"], [])

    def test_reset_removes_file(self):
        self.call("init", "--level", "normal")
        code, out, _ = self.call("reset")
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.call("reset")[0], 0)

    def test_validation(self):
        self.call("init", "--level", "normal")
        self.assertEqual(self.call("add-concept", "Bad Concept")[0], 2)
        self.assertEqual(self.call("set-language", "italiano!!")[0], 2)
        self.assertEqual(self.call("init", "--level", "normal", "--language", "x y", "--force")[0], 2)

    def test_unknown_fields_dropped(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "level": "expert", "concepts": ["dco", "BAD", 3],
                       "employer": "ACME", "skill_estimate": 0.9}, f)
        code, _, err = self.call("add-concept", "napi")
        self.assertEqual(code, 0)
        self.assertIn("dropping unknown fields", err)
        data = self.data()
        self.assertNotIn("employer", data)
        self.assertNotIn("skill_estimate", data)
        self.assertEqual(data["level"], "normal")
        self.assertEqual(data["concepts"], ["dco", "napi"])

    def test_field_shapes_enforced(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"version": {"note": "works at ACME"}, "level": "less",
                       "language": "it, lives in Rome", "created": "my diary entry",
                       "updated": "2026-10-22T10:00:00Z", "concepts": []}, f)
        data = json.loads(self.call("export")[1])
        self.assertEqual(data["version"], 1)
        self.assertIsNone(data["language"])
        self.assertNotIn("created", data)
        self.assertEqual(data["updated"], "2026-10-22T10:00:00Z")

    def test_default_path(self):
        with mock.patch.dict(os.environ, {"MLK_PROFILE": "", "XDG_CONFIG_HOME": self.dir}):
            self.assertEqual(lp.default_path(),
                             os.path.join(self.dir, "mdv-linux-kernel", "profile.json"))
        with mock.patch.dict(os.environ, {"MLK_PROFILE": "/x/p.json"}):
            self.assertEqual(lp.default_path(), "/x/p.json")


if __name__ == "__main__":
    unittest.main()
