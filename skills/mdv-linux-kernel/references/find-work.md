<!-- SPDX-License-Identifier: GPL-2.0 -->

# Find work / have patch

Two entry modes:

- **find-work**: the human wants something to do. Propose a few real
  candidates, each checked against the safeguards below.
- **have-patch**: the human already has a commit, a branch or an idea. Skip
  to "Have patch" at the end.

The aim is one useful, defensible patch, not volume. Do not push a beginner
toward many small cosmetic patches.

## Ask first

Before searching, ask (briefly):
- which area interests them (a protocol, a driver, tooling, selftests);
- what hardware they have (NIC model, architecture) and whether KVM works;
- how much time they have and how comfortable they are with C and git;
- whether they want a fix (`net`) or new work (`net-next`).

## Sources

| Source | Where | What to look for |
|---|---|---|
| syzbot | https://syzkaller.appspot.com/upstream/s/net (or the subsystem's page) | Open bugs with a C reproducer, recent "Last" date, no fix commit, empty or stalled "Discussions". |
| netdev CI | https://netdev.bots.linux.dev/status.html, flakes view https://netdev.bots.linux.dev/flakes.html?br-pfx=net-next | Selftests failing or flaky for a reason nobody has posted a fix for. |
| bugzilla | https://bugzilla.kernel.org (Networking product) | Reports with enough detail to reproduce; many are stale, check the date and lore. |
| KernelCI | https://dashboard.kernelci.org | Build or boot regressions in the target tree. |
| lore / patchwork | https://lore.kernel.org/netdev/, https://patchwork.kernel.org/project/netdevbpf/list/ | Reviewer requests ("someone should..."), RFCs that stalled, and to check that nobody is already on it. |
| Explicit TODOs | `git grep -n -E 'TODO|FIXME' -- <area>` | Only if `git log -L`/`git blame` and lore show the TODO is still wanted. Most are stale. |
| Missing tests | selftests or KUnit for code that has none | Useful and low-risk, but check on lore that a test of that kind is wanted and where it belongs (`tools/testing/selftests/net`, `drivers/net`, KUnit). |

Reading these is fine. Do not post, comment or claim anything on the
human's behalf.

## Discouraged

Warn (warn + confirm) and steer away from:

- **checkpatch-only or style-only cleanups**, local variable reordering,
  `devm_` conversions and similar churn not part of other work. netdev
  discourages these explicitly: read the "Clean-up patches" section of
  `Documentation/process/maintainer-netdev.rst`. Spelling and grammar fixes
  are not discouraged there.
- **drive-by patches on old, unmaintained code** (old drivers, obsolete
  protocols): little value, hard to test, often no one to review.
- **"theoretical" fixes** without a demonstrated problem: maintainers spend
  time disproving them.
- **anything requiring hardware the human lacks**, unless the change is
  build-only and says so (`references/build-config.md`, "When you do not
  have the hardware").

For a syzbot or crash candidate, `references/debugging.md` walks from the
report to a reproducer, the culprit commit and a regression test; do its
steps 1–2 before promising the human the bug is fixable.

## Candidate safeguards

Run every check before proposing a candidate. If one fails, drop the
candidate or present it with the failure stated.

- [ ] **Not already fixed or in progress.** Search lore for the function,
      the syzbot title or the error message; check patchwork for pending
      patches; check `git log <remote>/main -- <files>` for a recent fix.
- [ ] **Still reproducible on the current target tree**, or at least the
      code path is unchanged since the report (`git log -L` on the function).
- [ ] **Hardware**: can it be exercised in virtme-ng, netdevsim, veth or a
      selftest? If it needs a device the human lacks, say so.
- [ ] **Difficulty**: realistic for a first patch (contained change, one
      subsystem, understandable failure). Say why.
- [ ] **Real problem**: a user-visible symptom, crash, wrong behaviour,
      failing test or measurable regression.
- [ ] **Security**: if it looks like a vulnerability, the tripwire applies
      (`references/security.md`) before anything else.

## Candidate card

Present each candidate like this (in the human's language, terms in
English):

```
Candidate: <one-line title>
What: <the bug or task, 2-3 lines>
Why it matters: <who is affected, symptom>
Still open: <evidence: lore search date, syzbot last seen, no fix in tree>
Files: <paths>
Tree: net | net-next  (why)
Difficulty: <low/medium/high + reason>
Hardware: <none / netdevsim / veth / needs X>
Verification plan: <reproducer, selftest, KUnit, build-only>
Risks: <what could make it a dead end>
```

Propose two or three, recommend one, let the human pick.

## Have patch

1. Look at what exists: `git log --oneline <remote>/main..HEAD`,
   `git show --stat`, the idea's description.
2. Run the same safeguards (already fixed? discouraged category? security?).
3. Check it is based on the right tree (`net` for fixes to code already in
   mainline, `net-next` for new work); see `references/netdev.md`.
4. Continue with `references/develop.md`: the human enrolls the branch with
   `b4 prep --enroll <base>`, then the evidence store and sign-off snapshot
   are created before you change anything.

If the existing patch already carries a `Signed-off-by`, that is the
human's (or someone else's) existing trailer: preserve it, do not add or
duplicate one.
