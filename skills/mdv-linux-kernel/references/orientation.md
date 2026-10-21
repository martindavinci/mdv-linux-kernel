<!-- SPDX-License-Identifier: GPL-2.0 -->

# How kernel development works

For someone coming from GitHub-style projects. Concepts only; the rules
are in `Documentation/process/` (start with `howto.rst`,
`development-process.rst` and `submitting-patches.rst`) and the tutorial
https://kernelnewbies.org/FirstKernelPatch. Use short pieces of this at the
moment they are needed, not as a lecture. Terms: `references/glossary.md`.

## No pull requests: patches by email

A contribution is a set of commits turned into plain-text emails, one per
commit, usually with a cover letter ("0/N") explaining the series. They go
to the mailing lists and people responsible for the code. Reviewers reply
to the emails, quoting the lines they comment on. Maintainers apply the
emails to their git tree with `git am`.

Why email: it scales across thousands of developers and hundreds of
subsystems, needs no central service, and leaves a public, searchable
record. Every list is archived on https://lore.kernel.org, permanently.

Consequences:
- the email *is* the patch: plain text, no attachments, no HTML, a mail
  setup that does not mangle whitespace;
- the commit message is read on its own, years later, so it must explain
  the why;
- whatever is sent is public and permanent.

## Who decides

- **MAINTAINERS** maps files to people and lists. `M:` maintainers apply
  patches, `R:` reviewers review, `L:` lists receive patches, `T:` the git
  tree, `P:` the subsystem's own rules. `./scripts/get_maintainer.pl`
  reads it for you; `b4 prep --auto-to-cc` uses that.
- **Subsystem maintainers** collect patches in their trees (e.g. netdev's
  `net` and `net-next`).
- **Linus Torvalds** pulls from subsystem trees into **mainline**.

## Where a patch travels

```
your branch -> email to list -> review -> subsystem tree (net-next)
            -> linux-next (daily integration) -> mainline (merge window)
            -> release vX.Y -> stable trees (for fixes)
```

## The release cycle

About every 9-10 weeks a release `vX.Y`. Then a two-week **merge window**
where subsystem trees are pulled into mainline, closed by `-rc1`. Then
weekly `-rcN` releases with fixes only, usually to `-rc7` or `-rc8`, then
the next release. Subsystems that accept new work (like net-next) close
during the merge window. Fixes for the current release go to fix trees
(like `net`) at any time.

## What review looks like

- Reviewers reply inline; questions are normal and not hostile.
- Expect requests for changes; a v2 or v3 is the usual path, not failure.
- Silence for a few days is normal; netdev triages quickly, other
  subsystems may take weeks.
- Answer every comment: with a change, or with a reasoned reply.
- Reviewers give **trailers** when satisfied: `Reviewed-by`, `Acked-by`,
  `Tested-by`. They are added to the commit when the series is applied or
  in your next version.
- Bots review too: build bots, patchwork checks, syzbot, and on netdev an
  AI review that also expects a reply.

## Lifecycle of a patch

1. Prepare: branch, commits, cover letter (`b4 prep`).
2. Check: build, test, checkpatch, self-review.
3. Sign: the human adds `Signed-off-by` (the DCO).
4. Send v1 (`b4 send`); it appears on lore and patchwork.
5. Review: comments, trailers, AI findings.
6. Revise: change code, write a changelog, send v2 as a new thread.
7. Repeat until a maintainer applies it ("applied, thanks" mail, often
   from a bot), or it is rejected.
8. After it is applied, further problems get new patches on top.

## Why b4

Doing all of this with `git format-patch` and `git send-email` works but
is easy to get wrong: version numbers, threading, recipients, changelogs,
collecting trailers. b4 (from kernel.org) automates those steps and can
send through a kernel.org web endpoint when the human has no usable SMTP.

## Why sending is permanent

Lists are mirrored and archived on lore with full headers. Patches,
mistakes and replies stay visible under the sender's name. This is why the
skill makes the human send, and checks the series before they do.

## Further reading (in the tree)

- `Documentation/process/howto.rst`: the overall guide.
- `Documentation/process/development-process.rst` and its chapters
  (`2.Process.rst` cycle, `5.Posting.rst`, `6.Followthrough.rst`).
- `Documentation/process/submitting-patches.rst`: the canonical rules.
- `Documentation/process/email-clients.rst`: mail setups that work.
- `Documentation/process/maintainer-netdev.rst`: netdev specifics
  (`references/netdev.md`).
- How the networking stack is built: `references/kernel-design/index.md`.
