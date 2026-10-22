<!-- SPDX-License-Identifier: GPL-2.0 -->

# Reporting a bug or a regression

A good report is a contribution: it lets someone else fix the bug, or
fixes it through a bisection. The human sends it; the agent helps decide
where it goes, gathers what is needed and drafts it. Read the target
tree's `Documentation/admin-guide/reporting-issues.rst` and
`reporting-regressions.rst` when the case is unusual; this page is a
route through them (checked against docs.kernel.org 7.3, 2026-10).

## 1. Route the report first

| Situation | Where it goes |
|---|---|
| Kernel from a distribution or vendor, problem not reproduced with a vanilla kernel | the vendor ("better off ... reporting the issue to your vendor") |
| Something that worked with an older kernel and breaks with a newer one | a **regression**: section 3 |
| Regression inside one stable or longterm series (e.g. 6.12.4 fine, 6.12.6 broken) | stable list plus the regressions list (section 3) |
| Any other bug in mainline | the maintainers and the list of the subsystem, from `MAINTAINERS` |
| Looks like a security issue | stop: `references/security.md` |

Bugzilla is rarely the place: "issues typically need to be sent by mail to
a maintainer and a public mailing list". Use it only if the subsystem's
`MAINTAINERS` entry says so. Find the subsystem from the code involved:

```
./scripts/get_maintainer.pl -f drivers/net/ethernet/intel/e1000e/netdev.c
```

## 2. Check before writing

1. **Duplicates**: search lore (https://lore.kernel.org/all/) for the
   error message and the driver name, and the subsystem's tracker if it
   has one. If a report exists, add your data to that thread instead.
2. **Taint**: `cat /proc/sys/kernel/tainted` should print `0`. Out-of-tree
   modules (DKMS, vendor drivers) taint the kernel: reproduce without them.
3. **Vanilla and recent**: reproduce with a kernel built from kernel.org
   sources, ideally the latest mainline (the latest stable can be "an
   acceptable alternative in some situations"). Building one: same config
   as the distribution kernel, `make olddefconfig`.
4. **Reproduce** with that kernel and write down the exact steps.

A bisection (`references/debugging.md`, step 4) makes a regression report
much stronger, but do not hold the report back for weeks to finish one:
say what you know and offer to bisect.

## 3. Regressions

From `reporting-regressions.rst`: "It's a regression if something running
fine with one Linux kernel works worse or not at all with a newer
version." The kernel's "no regressions" rule makes these a priority.

- Subject starts with `[REGRESSION]`.
- CC the regressions list: `regressions@lists.linux.dev`, plus the
  subsystem's maintainers and list. For a regression inside a stable or
  longterm series, also `stable@vger.kernel.org`.
- Name the last good and the first bad version (or the culprit commit
  after a bisection, and then CC the people in its `Signed-off-by:` lines).
- Regzbot tracking is "optional, but recommended". Add one of these lines
  to the report, on its own:
  ```
  #regzbot introduced: v6.11..v6.12-rc1
  #regzbot introduced: 1f2e3d4c5d
  ```
  Use regzbot for regressions, not for ordinary bugs.

## 4. What the report contains

Plain text, no attachments unless the list accepts them, in English:

1. **A short summary first** (two to five lines): what goes wrong, on
   which kernel, since when, how often.
2. Steps to reproduce, and what you expected instead.
3. Versions: `cat /proc/version`, the last good and first bad kernel.
4. Environment: hardware involved (`lspci -nn` for the device, not the
   whole machine unless relevant), distribution, `uname -mi`.
5. The relevant part of `dmesg` (the first splat with some context
   before it), decoded if possible (`references/debugging.md`, step 1).
6. The `.config` of the kernel that shows the problem, if the list
   accepts it, or a link to it.
7. What you already tried (other kernels, module options, bisection so
   far).

### Masking personal data

Logs contain MAC addresses, IP addresses, serial numbers and home paths.
Mask what identifies you while keeping the structure (`xx:xx:xx:12:34:56`,
`192.0.2.x`, `/home/USER/`), so maintainers can still match lines. Do not
remove whole lines of the trace. `draft_lint.py --kind report` points at
candidates:

```
python3 <skill-dir>/scripts/draft_lint.py "$SERIES/report.txt" --kind report
```

## 5. Hand over and follow up

Put the draft in a file under `$SERIES` (or `$STATE` when there is no
series) and give the human the recipients from section 1. The human sends
it from their own mail client or with `git send-email`; the agent never
sends.

Afterwards: expect questions and requests to test patches
(`references/test-others.md`). Reply on the list, inline. If nothing
happens for a week or two, a short polite ping in the same thread is
normal; for regressions, regzbot shows the status.

## Done when

- [ ] routed (vendor, mainline, regression, stable series) and checked
      for duplicates
- [ ] reproduced on an untainted vanilla kernel, or explained why not
- [ ] summary first; versions, steps, log and config included
- [ ] `draft_lint.py --kind report` clean or each finding answered
- [ ] the human sends it
