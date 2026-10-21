<!-- SPDX-License-Identifier: GPL-2.0 -->

# Revise (vN)

Review is a conversation on the list. The human answers; you help them
understand the comments, change the code, and draft replies. Read the
resubmission rules from the tree: "Respond to review comments" and "Don't
get discouraged - or impatient" in `submitting-patches.rst`, and for netdev
"Changes requested", "Partial resends", "Resending after review" and
"Handling misapplied patches" in `maintainer-netdev.rst`.

## 1. Collect the feedback

- The thread on lore: `b4 mbox <message-id>` downloads it locally (read
  only); or read it at `https://lore.kernel.org/<list>/<message-id>/`.
- netdev: the patchwork state (Changes requested, Accepted, Deferred,
  ...), the AI review on https://sashiko.dev/ and
  https://netdev-ai.bots.linux.dev/sashiko/, and the patchwork check
  results.
- New trailers given on the list: the **human** runs `b4 trailers -u`,
  which writes them into the matching commits. It rewrites commits and
  takes trailers from replies as they are, which can include a
  `Signed-off-by`, so it is not something you run. It matches by
  patch-id, so it should run before code changes; after changes see
  `--fuzzy` in the b4 docs. Afterwards, run `signoff_guard.py check`,
  show the human any new `Signed-off-by`/`Co-developed-by`, and once they
  confirm, take a fresh `signoff_guard.py snapshot "$BASE..HEAD"`.

## 2. Map every comment

Keep `$STATE/review-v<N>.md`:

```
# | from | patch | comment (short) | action | status
1 | reviewer A | 2/3 | lock not held in foo_set() | change: take rtnl in caller | done
2 | AI review | 1/3 | possible leak on error | reply: freed in foo_free(), see line X | drafted
3 | reviewer B | 0/3 | split cleanup out | change: new patch 1/4 | done
```

Each comment gets a code change or a reasoned reply; often both (the
commit message should answer questions reviewers had to ask). Nothing is
silently ignored. Check every comment against the code before agreeing:
reviewers, and especially AI reviews, can be wrong. Disagreeing politely
with evidence is normal.

## 3. Draft replies (the human sends them)

Rules from `submitting-patches.rst` (verify there): reply inline under the
quoted text, trim quotes that are not needed, no top-posting, plain text.
Style: short, specific, thank once, no apologies paragraphs, no Markdown,
no bullet-point essays.

```
On <date>, <Reviewer> wrote:
> foo_set() is called without rtnl here, isn't it?

Right, the netlink path takes it but the sysfs path does not. I will
take rtnl in foo_store() in v2.

> Also, can priv->dev be NULL at this point?

No, it is set in foo_probe() before register_netdev() and cleared only
after unregister_netdev(), so foo_set() cannot see NULL.
```

The human edits and sends the reply from their own mail client. You never
send. Reviewers who commented should be in Cc of the next version
(`submitting-patches.rst`).

## 4. Make the changes

- Amend the affected commits (`git commit --amend`, or `git rebase -i`
  with fixup); keep authors and existing trailers.
- **Reassess trailers**: a `Reviewed-by`, `Acked-by` or `Tested-by` given
  for the old version usually does not survive a substantial change to
  that patch. Drop it, or keep it only for trivial changes, and say which
  in the changelog.
- Any amend (and the human's `b4 trailers -u`) makes evidence stale. Re-run
  validation (`references/validate.md`) under the same check names, which
  replaces the old results, then the slop gate and the sign-off check. The
  human's existing `Signed-off-by` lines stay; new commits in the series
  need the human's sign-off, after which you take a fresh
  `signoff_guard.py snapshot` (`references/signoff-dco.md`).
- If the rebase onto a newer tree is needed, see `references/develop.md`
  (re-init evidence for the new base).

## 5. Changelog and version

b4 already moved the branch to the next revision after `b4 send` and added
to the cover letter:

```
Changes in v2:
- EDITME: describe what is new in this series revision.
- Link to v1: https://lore.kernel.org/r/...
```

Fill it in: one terse line per change, naming the patch and who asked for
it when relevant. Do not bump versions by hand; b4 tracks them
(`b4 prep --show-revision`). For a single patch the changelog ends up
below `---`. Check the diff against what was sent:

```
b4 prep --compare-to v1
```

## 6. When to post the next version

Fallback summary, verify in the tree:
- send the **whole series** again, as a **new thread** (not a reply to the
  old one), with a link to the previous version in the changelog;
- address **all** feedback; do not post while discussion of the previous
  version is still going, unless a reviewer asks;
- netdev: wait **at least 24 hours** between postings, and do not wait
  weeks either;
- generic: wait at least a week before resending an unchanged series that
  got no reply; a resend of an unmodified series is marked `RESEND`;
- no "ping"/"bump" emails on netdev; check patchwork, then ask a concrete
  question if the next step is unclear.

## 7. After the series is applied

The tree history is fixed. Problems found later are fixed with new,
incremental patches on top (with a `Fixes:` tag), not with a vN of the
applied series. A revert is a patch too, with a reason.

Then the sending steps are the same as `references/submit.md`.
