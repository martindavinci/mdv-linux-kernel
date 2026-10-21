# SPDX-License-Identifier: GPL-2.0
"""Throwaway git repositories for tests."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.join(REPO_ROOT, "skills", "mdv-linux-kernel")
SCRIPTS_DIR = os.path.join(SKILL_DIR, "scripts")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


class GitRepo:
    def __init__(self, path: str):
        self.path = path

    @classmethod
    def create(cls, tmpdir: str) -> "GitRepo":
        path = os.path.join(tmpdir, "repo")
        os.makedirs(path)
        repo = cls(path)
        repo.git("init", "-q", "-b", "main")
        repo.git("config", "user.name", "Test Human")
        repo.git("config", "user.email", "human@example.org")
        repo.git("config", "core.autocrlf", "false")
        repo.git("config", "commit.gpgsign", "false")
        return repo

    def git(self, *args: str, env: dict[str, str] | None = None) -> str:
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        res = subprocess.run(
            ["git", *args],
            cwd=self.path,
            capture_output=True,
            text=True,
            env=full_env,
        )
        if res.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr}")
        return res.stdout.rstrip("\n")

    def commit(self, message: str, files: dict[str, str] | None = None,
               env: dict[str, str] | None = None) -> str:
        for rel, content in (files or {}).items():
            full = os.path.join(self.path, rel)
            os.makedirs(os.path.dirname(full) or self.path, exist_ok=True)
            with open(full, "w", newline="") as f:
                f.write(content)
            self.git("add", rel)
        msg_file = os.path.join(self.path, ".git", "TEST_MSG")
        with open(msg_file, "w", newline="") as f:
            f.write(message)
        self.git("commit", "-q", "--allow-empty", "--cleanup=verbatim",
                 "-F", msg_file, env=env)
        return self.git("rev-parse", "HEAD")


def _force_remove(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def make_tmpdir(testcase: unittest.TestCase) -> str:
    """Temporary directory removed at test cleanup (git objects are read-only on Windows)."""
    path = tempfile.mkdtemp(prefix="mlk-test-")
    if sys.version_info >= (3, 12):
        testcase.addCleanup(shutil.rmtree, path, onexc=_force_remove)
    else:
        testcase.addCleanup(shutil.rmtree, path, onerror=_force_remove)
    return path


def run_script(name: str, *args: str, cwd: str | None = None,
               stdin: str | None = None) -> subprocess.CompletedProcess:
    """Run skills/mdv-linux-kernel/scripts/<name> with the current interpreter."""
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, name), *args],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
