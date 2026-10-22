# SPDX-License-Identifier: GPL-2.0
"""Structural checks on the skill package."""
from __future__ import annotations

import os
import re
import unittest

from tests.gitfixture import REPO_ROOT, SKILL_DIR

SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")
SPDX_CODE = "SPDX-License-Identifier: GPL-2.0"
PATH_RE = re.compile(r"(?<![\w/.-])((?:references|scripts)/[\w./-]+\.(?:md|py|sh))")


def read(path: str) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


def skill_files(subdir: str, exts: tuple[str, ...]) -> list[str]:
    found = []
    root = os.path.join(SKILL_DIR, subdir)
    for dirpath, _, names in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for name in names:
            if name.endswith(exts):
                found.append(os.path.join(dirpath, name))
    return found


def doc_files() -> list[str]:
    return [SKILL_MD] + skill_files("references", (".md",))


class SkillLintTest(unittest.TestCase):
    def test_frontmatter_name(self):
        fm = frontmatter(read(SKILL_MD))
        self.assertEqual(fm.get("name"), "mdv-linux-kernel")
        self.assertRegex(fm["name"], r"^[a-z0-9-]{1,64}$")

    def test_frontmatter_description(self):
        desc = frontmatter(read(SKILL_MD)).get("description", "")
        self.assertTrue(desc)
        self.assertLessEqual(len(desc), 1024)
        self.assertIn("kernel", desc)

    def test_frontmatter_license_and_author(self):
        text = read(SKILL_MD)
        fm = frontmatter(text)
        self.assertEqual(fm.get("license"), "GPL-2.0")
        block = re.match(r"---\n(.*?)\n---\n", text, re.S).group(1)
        self.assertIn("metadata:\n  author: Martin DaVinci <martindavinci@users.noreply.github.com>",
                      block)
        self.assertRegex(block, r'\n  version: "\d+\.\d+\.\d+"')

    def test_skill_md_length(self):
        self.assertLessEqual(len(read(SKILL_MD).splitlines()), 500)

    def test_referenced_paths_exist(self):
        missing = []
        for doc in doc_files():
            for rel in PATH_RE.findall(read(doc)):
                if not os.path.exists(os.path.join(SKILL_DIR, rel)):
                    missing.append(f"{os.path.relpath(doc, SKILL_DIR)} -> {rel}")
        self.assertEqual(missing, [])

    def test_references_reachable(self):
        mentioned = set()
        for doc in doc_files():
            text = read(doc)
            mentioned.update(PATH_RE.findall(text))
            base = os.path.relpath(os.path.dirname(doc), SKILL_DIR).replace(os.sep, "/")
            # Relative links inside references (e.g. "networking-stack.md").
            for rel in re.findall(r"\(([\w./-]+\.md)\)", text):
                mentioned.add(os.path.normpath(f"{base}/{rel}").replace(os.sep, "/"))
        orphans = []
        for ref in skill_files("references", (".md",)):
            rel = os.path.relpath(ref, SKILL_DIR).replace(os.sep, "/")
            if rel not in mentioned:
                orphans.append(rel)
        self.assertEqual(orphans, [])

    def test_spdx_headers(self):
        missing = []
        for dirpath, dirnames, names in os.walk(REPO_ROOT):
            dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", "docs")]
            for name in names:
                path = os.path.join(dirpath, name)
                in_skill = path.startswith(SKILL_DIR)
                if name.endswith((".py", ".sh")) or (in_skill and name.endswith(".md")):
                    # SKILL.md: the header follows the YAML frontmatter.
                    text = re.sub(r"\A---\n.*?\n---\n", "", read(path), count=1, flags=re.S)
                    if SPDX_CODE not in "\n".join(text.splitlines()[:8]):
                        missing.append(os.path.relpath(path, REPO_ROOT))
        self.assertEqual(missing, [])

    def test_no_crlf(self):
        bad = []
        for dirpath, dirnames, names in os.walk(REPO_ROOT):
            dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", "docs")]
            for name in names:
                if name.endswith((".py", ".sh", ".md", ".json")):
                    path = os.path.join(dirpath, name)
                    if "\r\n" in read(path):
                        bad.append(os.path.relpath(path, REPO_ROOT))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
