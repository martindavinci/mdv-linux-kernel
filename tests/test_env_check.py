# SPDX-License-Identifier: GPL-2.0
"""Tests for env_check.py (fakes only, no real system access)."""
from __future__ import annotations

import json
import unittest

from tests.gitfixture import run_script

import env_check

ALL_TOOLS = (env_check.REQUIRED + env_check.RECOMMENDED + env_check.OPTIONAL)
WSL_VERSION = "Linux version 5.15.153.1-microsoft-standard-WSL2 (root@1234) (gcc 11.2.0)\n"
NATIVE_VERSION = "Linux version 6.8.0-45-generic (buildd@lcy02) (gcc 13.2.0)\n"
MEMINFO_32G = "MemTotal:       32768000 kB\nMemFree:        1000000 kB\n"
CPUINFO = "processor\t: 0\nmodel name\t: x\n\nprocessor\t: 1\nmodel name\t: x\n"


def run(missing=(), files=None, paths=("/dev/kvm",), disk=200.0, platform="linux"):
    files = {"/proc/version": NATIVE_VERSION, "/proc/meminfo": MEMINFO_32G,
             "/proc/cpuinfo": CPUINFO, **(files or {})}
    return env_check.check_environment(
        which=lambda name: None if name in missing else f"/usr/bin/{name}",
        read_text=files.get,
        exists=lambda p: p in paths,
        disk_free_gb=lambda p: disk,
        platform=platform,
        path="/home/u/linux",
    )


def codes(result: dict) -> list[str]:
    return sorted(f["code"] for f in result["findings"])


class EnvCheckTest(unittest.TestCase):
    def test_tool_groups(self):
        self.assertEqual(env_check.REQUIRED,
                         ["git", "make", "gcc", "flex", "bison", "bc", "perl", "python3"])
        self.assertEqual(env_check.RECOMMENDED, ["clang", "ld.lld", "sparse", "spatch", "b4",
                                                 "vng", "pahole", "qemu-system-x86_64"])
        self.assertEqual(env_check.OPTIONAL, ["smatch", "sashiko", "semcode-index"])

    def test_all_good(self):
        res = run()
        self.assertEqual(res["findings"], [])
        system = res["system"]
        self.assertTrue(system["is_linux"])
        self.assertFalse(system["is_wsl"])
        self.assertTrue(system["kvm"])
        self.assertEqual(system["cpus"], 2)
        self.assertAlmostEqual(system["mem_gb"], 31.2, places=1)
        self.assertEqual(system["disk_free_gb"], 200.0)
        self.assertEqual(res["tools"]["required"]["git"], "/usr/bin/git")

    def test_not_linux(self):
        res = run(platform="win32")
        self.assertFalse(res["system"]["is_linux"])
        self.assertEqual(codes(res), ["NOT_LINUX"])
        self.assertEqual(res["findings"][0]["severity"], "error")
        self.assertIn("README", res["findings"][0]["message"])

    def test_wsl_detected(self):
        res = run(files={"/proc/version": WSL_VERSION})
        self.assertTrue(res["system"]["is_wsl"])
        self.assertEqual(res["findings"], [])

    def test_missing_required_and_recommended(self):
        res = run(missing={"bison", "flex", "b4", "smatch"})
        by_code = {f["code"]: f for f in res["findings"]}
        self.assertEqual(sorted(by_code), ["MISSING_RECOMMENDED", "MISSING_REQUIRED"])
        self.assertEqual(by_code["MISSING_REQUIRED"]["severity"], "error")
        self.assertEqual(by_code["MISSING_RECOMMENDED"]["severity"], "warn")
        self.assertIn("bison", by_code["MISSING_REQUIRED"]["detail"])
        self.assertIn("flex", by_code["MISSING_REQUIRED"]["detail"])
        self.assertIn("b4", by_code["MISSING_RECOMMENDED"]["detail"])
        self.assertIsNone(res["tools"]["optional"]["smatch"])

    def test_no_kvm(self):
        res = run(paths=())
        self.assertFalse(res["system"]["kvm"])
        self.assertEqual(codes(res), ["NO_KVM"])
        self.assertEqual(res["findings"][0]["severity"], "warn")

    def test_low_disk_and_mem(self):
        res = run(disk=20.0, files={"/proc/meminfo": "MemTotal:        4000000 kB\n"})
        self.assertEqual(codes(res), ["LOW_DISK", "LOW_MEM"])

    def test_unreadable_proc_files(self):
        res = run(files={"/proc/meminfo": None, "/proc/version": None})
        self.assertIsNone(res["system"]["mem_gb"])
        self.assertFalse(res["system"]["is_wsl"])
        self.assertEqual(res["findings"], [])

    def test_cli_outputs_json(self):
        res = run_script("env_check.py", "--json")
        self.assertIn(res.returncode, (0, 1), res.stderr)
        payload = json.loads(res.stdout)
        self.assertIn("system", payload)
        self.assertIn("findings", payload)


if __name__ == "__main__":
    unittest.main()
