<!-- SPDX-License-Identifier: GPL-2.0 -->

# Glossary

Short definitions for explaining terms to the human. For rules, follow the
pointer to the in-tree document. Bigger picture: `references/orientation.md`.

**Acked-by:** Trailer from someone (often a maintainer of affected code or a
key user) saying they accept the patch. Less formal than `Reviewed-by`;
may cover only part of a patch. Needs the person's explicit permission.
See `submitting-patches.rst`.

**Assisted-by:** Trailer once required for AI-assisted patches
(`Assisted-by: LLM [tools]`). Removed from mainline docs on 2026-10-14
(`1e035297ffda`); tool use is now described in the cover letter. Some
trees may still require it: `references/disclosure.md`.

**b4:** kernel.org tool for the email workflow: prepare a series
(`b4 prep`), send it (`b4 send`), collect trailers (`b4 trailers`),
download threads (`b4 mbox`). Docs: https://b4.docs.kernel.org.

**cover letter:** The "0/N" email that introduces a series: what, why, how
it was tested, changelog between versions. Not committed to git. For a
single patch, the same text goes below `---`.

**DCO (Developer's Certificate of Origin):** What a `Signed-off-by`
certifies: the right to submit the code under its license, and that the
contribution is public and kept forever. Only humans can certify it.
`submitting-patches.rst`, "Sign your work".

**Fixes:** Trailer naming the commit that introduced the bug:
`Fixes: <12+ char sha> ("<subject>")`. Used by reviewers and the stable
team. Required for fixes on netdev.

**get_maintainer.pl:** `./scripts/get_maintainer.pl` reads MAINTAINERS (and
optionally git history) to list who should receive a patch.

**KASAN:** Kernel Address Sanitizer; a debug option that detects
out-of-bounds and use-after-free accesses and prints a report. syzbot
reports often start with "BUG: KASAN".

**KernelCI:** Distributed test and build system for upstream kernel trees;
reports build, boot and test regressions.

**kselftest:** The kernel's userspace test suites under
`tools/testing/selftests/` (e.g. `net/`). Run against a booted kernel.
`Documentation/dev-tools/kselftest.rst`.

**KUnit:** In-kernel unit test framework; tests run inside the kernel or
UML via `./tools/testing/kunit/kunit.py`. `Documentation/dev-tools/kunit/`.

**linux-next:** Daily merge of all subsystem trees' next-release
branches, to catch conflicts early. Not a base for patches.

**lore:** https://lore.kernel.org, the public archive of kernel mailing
lists. Every posted patch and reply is there, permanently.

**mainline:** Linus Torvalds' tree (torvalds/linux.git), where releases
are made.

**maintainer:** Person responsible for a subsystem or driver who reviews
and applies patches and sends them upstream.

**MAINTAINERS:** File in the tree mapping paths to maintainers (`M:`),
reviewers (`R:`), lists (`L:`), trees (`T:`), status (`S:`) and
subsystem rules (`P:`).

**merge window:** The two weeks after a release when subsystem trees are
merged into mainline; ends with `-rc1`. net-next is closed then.

**net / net-next:** The two netdev trees: `net` for fixes to code already
in mainline, `net-next` for new work for the next release.
`references/netdev.md`.

**patchwork:** Web tracker that indexes patches posted to a list and their
state (netdev: https://patchwork.kernel.org/project/netdevbpf/list/).

**rc (release candidate):** Weekly `-rcN` tags after the merge window;
fixes only. Usually up to rc7 or rc8 before the release.

**Reported-by:** Trailer crediting who reported the bug; usually followed by
`Closes:` with a public link to the report.

**Reviewed-by:** Trailer from someone who reviewed the patch. On netdev it
means "reviewed to the best of my ability". Needs the reviewer's explicit
permission; usually dropped if the patch changes substantially.

**reviewer:** Person listed with `R:` in MAINTAINERS, or anyone who reviews
on the list.

**Signed-off-by:** Trailer certifying the DCO; one per person in the
patch's path, the submitter last. Only the human adds theirs.

**smatch:** Static analysis tool for C, widely used on the kernel; finds
bugs sparse and the compiler miss. Built from source.

**sparse:** Semantic checker for kernel C (address spaces, endianness,
locking annotations); run with `make C=1` or `C=2`.
`Documentation/dev-tools/sparse.rst`.

**stable:** Long-term and stable release trees that receive backported
fixes; driven by `Fixes:` tags and `Cc: stable@vger.kernel.org`.
`Documentation/process/stable-kernel-rules.rst`.

**subsystem tree:** A maintainer's git tree for one area (e.g. net-next),
merged into linux-next and, in the merge window, into mainline.

**syzbot:** Continuous kernel fuzzer (syzkaller) that reports crashes with
reproducers to lists and on https://syzkaller.appspot.com.

**trailer:** A `Key: value` line at the end of a commit message
(`Fixes:`, `Reported-by:`, `Reviewed-by:`, `Signed-off-by:`...).

**virtme-ng (vng):** Tool that builds a kernel and boots it quickly in a
QEMU/KVM VM sharing the host filesystem; used to run tests on the patched
kernel.

**vN:** Version of a posted series (`[PATCH v2 ...]`). Each new version is
a full repost in a new thread, with a changelog; b4 numbers them.

**W=1:** Kernel build option enabling extra compiler warnings. netdev's
minimum is no new warnings in `allmodconfig`/`allyesconfig` builds with
`W=1`.
