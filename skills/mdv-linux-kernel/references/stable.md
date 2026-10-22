<!-- SPDX-License-Identifier: GPL-2.0 -->

# Stable kernels: tags, requests and backports

**Advanced.** Marking a fix for stable is easy; preparing a backport is
not. Resolving a conflict in an older kernel needs to understand the code
of that older version, and a cherry-pick that applies cleanly proves
nothing. Do backports only with a human who can explain the old code, and
say so if they cannot.

Read `Documentation/process/stable-kernel-rules.rst` from the tree; this
page follows it (checked against docs.kernel.org 7.3, 2026-10).

## 1. What stable takes

From the rules: the patch "or an equivalent fix must already exist in
Linux mainline (upstream)", it "cannot be bigger than 100 lines, with
context", and it "must be obviously correct and tested". Fixes for real
problems, not cleanups or new features.

netdev: `maintainer-netdev.rst` says explicit `Cc: stable` tags were once
not wanted for netdev but that "is no longer the case today": follow the
standard rules and "make sure you include appropriate Fixes tags".

## 2. The three ways in

1. **Tag the fix** (preferred): in the sign-off area of the mainline patch,
   ```
   Cc: stable@vger.kernel.org
   ```
   Optional notes after `#`, in the documented forms:
   ```
   Cc: <stable@vger.kernel.org> # 6.6.x
   Cc: <stable@vger.kernel.org> # 3.3.x: a1f84a3: sched: Check for idle
   Cc: <stable@vger.kernel.org> # after -rc3
   ```
   The middle form lists a prerequisite commit (one line each, then the
   plain tag). `Cc: stable` is a trailer, not an email recipient. A fix
   that must not be picked automatically uses
   `Cc: <stable+noautosel@kernel.org> # reason`.
2. **Ask after the merge**: mail `stable@vger.kernel.org` with the subject
   of the patch, the commit ID, why it should be applied and to which
   versions.
3. **Send a backport**: when the fix does not apply to an older series.

`check_stable.py` checks the tag forms (and that a stable fix has a
`Fixes:`):

```
python3 <skill-dir>/scripts/check_stable.py "$RANGE"
```

## 3. A "FAILED: patch ... failed to apply" mail

The stable team sends these when a tagged fix does not apply to a series.
Before writing any code, find out which case it is:

1. **Already there?** Search the stable branch for the upstream subject
   and for an equivalent fix: `git log --oneline v6.6.50.. -- <files>`,
   `git log --grep='<subject>' stable/linux-6.6.y`.
2. **Not applicable?** Does the buggy code exist in that version? Check
   the commit named in `Fixes:`: `git merge-base --is-ancestor <sha>
   stable/linux-6.6.y`. If it is not there, reply that the series is not
   affected, with that evidence.
3. **Missing prerequisites?** Find the upstream commits the fix depends on
   (`git log` on the touched lines between the series and the fix). Small,
   clearly needed prerequisites can be listed; anything bigger is a
   different conversation.
4. **Needs adapting**: only then write a backport (section 4).

Draft the reply in the same thread, inline, with what you found.

## 4. Preparing a backport

```
git switch -c backport/<name>-6.6 stable/linux-6.6.y
git cherry-pick -x <upstream sha>      # resolve conflicts by understanding the old code
```

- The changelog keeps the upstream text and gets a separate line with the
  upstream commit, in one of the documented forms:
  ```
  commit <full upstream sha1> upstream.
  ```
  or `[ Upstream commit <full upstream sha1> ]`.
- Every deviation from upstream "must be very clearly documented and
  justified": a short note in brackets saying what was changed and why
  (for example a renamed helper in the older series).
- Keep the original trailers; the human adds their own `Signed-off-by`.
- Validate on **each** target series, as for any patch
  (`references/validate.md`): build, the tests that cover the change, and
  the reproducer before and after on that series' kernel.

```
python3 <skill-dir>/scripts/check_stable.py "<base>..HEAD" --backport
```

The subject should stay the upstream subject; the series and version go
in the email prefix (b4 or `git format-patch --subject-prefix='PATCH 6.6'`).

## 5. Also check newer series

A fix backported to 6.1 that is missing from 6.6 creates a regression for
people upgrading. If you backport to an older series, check every newer
supported series has the fix too, and say so in the mail.

## Done when

- [ ] the fix is upstream (or an equivalent is), and the series is affected
- [ ] tag forms pass `check_stable.py`; backports have the upstream line
- [ ] each deviation is documented; validated on each target series
- [ ] the human understands the adapted code and sends the mail
