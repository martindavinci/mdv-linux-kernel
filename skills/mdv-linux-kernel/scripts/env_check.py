#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Check that this machine can build and test a Linux kernel.

Usage: env_check.py [--json] [--path DIR]

--path is where the kernel checkout lives (used for the free-disk check).
Exit: 0 ready, 1 problems found, 2 usage error.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from typing import Callable

import mlk_common
from mlk_common import Finding

REQUIRED = ["git", "make", "gcc", "flex", "bison", "bc", "perl", "python3"]
RECOMMENDED = ["clang", "ld.lld", "sparse", "spatch", "b4", "vng", "pahole", "qemu-system-x86_64"]
OPTIONAL = ["smatch", "sashiko", "semcode-index"]
# Development headers the kernel build needs: name -> (any of these headers, package hint).
LIBRARIES = {
    "libelf": (["/usr/include/gelf.h", "/usr/include/libelf.h"],
               "libelf-dev on Debian/Ubuntu, elfutils-libelf-devel on Fedora"),
    "openssl": (["/usr/include/openssl/opensslv.h"],
                "libssl-dev on Debian/Ubuntu, openssl-devel on Fedora"),
}
MIN_DISK_GB = 50
MIN_MEM_GB = 8


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _disk_free_gb(path: str) -> float:
    return round(shutil.disk_usage(path).free / 1024 ** 3, 1)


def _mem_gb(meminfo: str | None) -> float | None:
    for line in (meminfo or "").splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return round(int(parts[1]) / 1024 ** 2, 1)
    return None


def _cpus(cpuinfo: str | None) -> int | None:
    count = sum(1 for line in (cpuinfo or "").splitlines() if line.startswith("processor"))
    return count or os.cpu_count()


def check_environment(which: Callable[[str], str | None] = shutil.which,
                      read_text: Callable[[str], str | None] = _read_text,
                      exists: Callable[[str], bool] = os.path.exists,
                      disk_free_gb: Callable[[str], float] = _disk_free_gb,
                      platform: str = sys.platform,
                      path: str = ".") -> dict:
    is_linux = platform.startswith("linux")
    findings: list[Finding] = []
    tools = {group: {name: which(name) for name in names}
             for group, names in (("required", REQUIRED), ("recommended", RECOMMENDED),
                                  ("optional", OPTIONAL))}
    libraries = {name: any(exists(h) for h in headers)
                 for name, (headers, _hint) in LIBRARIES.items()}
    if not is_linux:
        findings.append(Finding(
            "NOT_LINUX", "error",
            f"this is not Linux ({platform}): kernel work needs Linux. On Windows follow the "
            "README WSL2 prerequisite (wsl --install -d Ubuntu) and run the agent inside WSL"))
        system = {"is_linux": False, "is_wsl": False, "kvm": False, "cpus": os.cpu_count(),
                  "mem_gb": None, "disk_free_gb": None}
        return {"system": system, "tools": tools, "libraries": libraries,
                "findings": [f.to_dict() for f in findings]}

    try:
        disk = disk_free_gb(path)
    except OSError:
        disk = None
    system = {
        "is_linux": True,
        "is_wsl": "microsoft" in (read_text("/proc/version") or "").lower(),
        "kvm": exists("/dev/kvm"),
        "cpus": _cpus(read_text("/proc/cpuinfo")),
        "mem_gb": _mem_gb(read_text("/proc/meminfo")),
        "disk_free_gb": disk,
    }
    for group, code, severity, why in (
            ("required", "MISSING_REQUIRED", "error", "needed to build the kernel"),
            ("recommended", "MISSING_RECOMMENDED", "warn", "needed for full validation")):
        missing = [name for name, where in tools[group].items() if not where]
        if missing:
            findings.append(Finding(code, severity, f"missing {group} tools ({why})",
                                    detail=", ".join(missing)))
    for name, found in libraries.items():
        if not found:
            headers, hint = LIBRARIES[name]
            findings.append(Finding(
                "MISSING_LIBRARY", "error",
                f"{name} development headers missing (needed to build the kernel): "
                f"install {hint}", detail=" or ".join(headers)))
    if not system["kvm"]:
        findings.append(Finding(
            "NO_KVM", "warn",
            "/dev/kvm not available: virtme-ng will be very slow or unusable "
            "(on WSL2 enable nested virtualization)"))
    if disk is not None and disk < MIN_DISK_GB:
        findings.append(Finding("LOW_DISK", "warn",
                                f"{disk} GB free at {path}; allow at least {MIN_DISK_GB} GB"))
    if system["mem_gb"] is not None and system["mem_gb"] < MIN_MEM_GB:
        findings.append(Finding("LOW_MEM", "warn",
                                f"{system['mem_gb']} GB RAM; at least {MIN_MEM_GB} GB is advised"))
    return {"system": system, "tools": tools, "libraries": libraries,
            "findings": [f.to_dict() for f in findings]}


def human(payload: dict) -> str:
    s = payload["system"]
    lines = [f"linux={s['is_linux']} wsl={s['is_wsl']} kvm={s['kvm']} cpus={s['cpus']} "
             f"mem_gb={s['mem_gb']} disk_free_gb={s['disk_free_gb']}"]
    for group, names in payload["tools"].items():
        found = [n for n, p in names.items() if p]
        lines.append(f"{group}: {len(found)}/{len(names)} found")
    missing = [name for name, ok in payload["libraries"].items() if not ok]
    lines.append(f"libraries: {'missing ' + ', '.join(missing) if missing else 'ok'}")
    return "\n".join(lines) + "\n" + mlk_common.format_findings(payload["findings"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check the kernel development environment.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--path", default=".", help="kernel checkout location (disk check)")
    args = ap.parse_args(argv)
    payload = check_environment(path=args.path)
    mlk_common.emit(payload, args.json, human)
    failing = [f for f in payload["findings"] if f["severity"] != "info"]
    return mlk_common.EXIT_FINDINGS if failing else mlk_common.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
