<!-- SPDX-License-Identifier: GPL-2.0 -->

# netdev (networking)

netdev is the networking subsystem and its mailing list,
`netdev@vger.kernel.org` (archive: https://lore.kernel.org/netdev/). It
covers `net/` and `drivers/net/`, except areas with their own list and
tree (wireless, for example). Maintainers are in MAINTAINERS under
"NETWORKING [GENERAL]" and "NETWORKING DRIVERS"; the `P:` line there points
to the subsystem rules, `Documentation/process/maintainer-netdev.rst`.

**Always read `maintainer-netdev.rst` from the target tree.** It changes
often. Everything below is a fallback summary of mainline as of 2026-10,
to verify there, not a rule source.

For how the stack itself works, see
`references/kernel-design/networking-stack.md`.

## Fallback summary (verify in maintainer-netdev.rst)

### Trees

- `net`: fixes for code already in mainline (the current release and rcs).
- `net-next`: new features, cleanups, anything else, for the next release.
- Git: `https://git.kernel.org/pub/scm/linux/kernel/git/netdev/net.git` and
  `.../netdev/net-next.git`, branch `main`.
- Mark the target in the subject: `[PATCH net]` / `[PATCH net-next]`. With
  b4: `b4 prep --set-prefixes net` or `net-next`.
- Test on top of the right networking tree, not on linux-next or stable.

### net-next closed

net-next is closed during the two-week merge window (from a final `vX.Y`
release until `-rc1`). Do not send net-next patches while it is closed;
RFCs are welcome any time. Check:
https://netdev.bots.linux.dev/net-next.html, or the top tag of Linus' tree
(a final release tag without `-rcN` usually means the merge window is
open). While closed: hold the series, send it as RFC, or, only if it is a
genuine fix, target `net`.

### Fixes

A `Fixes:` tag is required for fixes, whichever tree. Fixes go to `net`;
the selftest for the fix goes in the same series and tree (the test patch
may lack a `Fixes:`). The commit message gives the user-visible symptom,
the cause, and why this fix is best. netdev follows the standard stable
rules (`stable-kernel-rules.rst`) for `Cc: stable@vger.kernel.org`.

### Series and pace

- No series longer than 15 patches; split it.
- No more than 15 patches outstanding per tree across all your series.
- Wait at least 24 hours between postings of a series; do not wait weeks
  either.
- Resend the whole series as a new thread, with a changelog linking the
  previous version. Do not resend only changed patches.
- No "ping"/"bump" mails; check patchwork, then ask a concrete question.
- No private email to maintainers; discussion stays on the list.

### Style

- Local variables ordered longest line to shortest ("reverse xmas tree").
  If dependencies prevent it, move the initialisation out of line.
- In existing code with non-standard formatting, new code follows the
  current guidelines.
- Skeptical of auto-cleanup: `devm_` acceptable, not preferred; `guard()`
  discouraged in functions longer than 20 lines (`scoped_guard()` reads
  better; plain lock/unlock still weakly preferred); direct `__free()` in
  core and drivers discouraged; same for mid-function declarations.

### Cleanups

Standalone simple cleanups are discouraged: checkpatch fixes, variable
reordering, `devm_` conversions, outside the context of other work.
Spelling and grammar fixes are not discouraged.

### Testing

- Minimum: `allyesconfig` and `allmodconfig` builds with `W=1`, no new
  warnings or failures (`references/validate.md` has the baseline method).
- Ideally runtime testing specific to the change, and selftests under
  `tools/testing/selftests/net` (or `drivers/net`) or KUnit tests in the
  series.
- netdevsim can exercise driver-facing APIs without hardware; tests should
  run on netdevsim and on real devices. netdevsim alone is not a user: new
  APIs need a real driver implementation.
- New netdevsim features need selftests.

### Patchwork, CI and AI review

- Queue: https://patchwork.kernel.org/project/netdevbpf/list/ (states:
  New/Under review, Changes requested, Accepted, Deferred, Superseded,
  ...).
- The CI checks are wrappers around kernel scripts
  (https://github.com/linux-netdev/nipa/tree/master/tests). Do not post
  just to get CI results; test locally first.
- LLM reviews of posted patches: https://sashiko.dev/ and
  https://netdev-ai.bots.linux.dev/sashiko/, available after about 24h.
  Authors are expected to look at them and reply on the list, either
  debating or addressing each point, and are strongly encouraged to run
  LLM review before posting (`references/external-review.md`). Large
  series that trigger lots of AI feedback get little attention.
- `pw-bot: changes-requested` in a reply marks your own series as needing
  changes; optional, see the doc before using it.

### Bug reports and AI-found bugs

`maintainer-netdev.rst` does not have its own rules for reports; the
generic ones apply. For bugs found with AI help, the procedure in
`coding-assistants.rst` (reproducer, tested fix, explicit statement of what
was not verified) and `security-bugs.rst` apply. See
`references/security.md` for the tripwire.

## Things beginners get wrong on netdev

- sending to net-next during the merge window;
- fixes without `Fixes:`, or targeting net-next;
- checkpatch-only patches to old drivers;
- reposting within 24h, or reposting while discussion is ongoing;
- ignoring the AI review because "it's just a bot";
- missing the netdev list or a driver maintainer in Cc
  (`b4 prep --auto-to-cc`).
