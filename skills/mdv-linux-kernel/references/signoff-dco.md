<!-- SPDX-License-Identifier: GPL-2.0 -->

# Sign-off and the DCO

The agent never adds `Signed-off-by` (non-overridable). This phase
explains what the human certifies, checks provenance, and hands them the
command.

## 1. Explain the DCO (before they sign)

Read "Sign your work - the Developer's Certificate of Origin" in
`submitting-patches.rst` from the tree with the human, or show it. In plain
words, by adding `Signed-off-by: Name <email>` they state that:

- (a) they wrote the contribution, at least in part, and may submit it
  under the file's open source license; or
- (b) it is based on earlier work under a compatible open source license
  that they may submit with changes; or
- (c) someone who certified (a), (b) or (c) gave it to them unchanged;
- (d) the contribution and the sign-off, with their name and email, are
  public and kept forever.

`coding-assistants.rst` adds: only humans can certify the DCO; the human
is responsible for reviewing all AI-generated code, for license
compliance, and takes full responsibility for the contribution.

A sign-off uses a known identity: real name, a working email. The commit
author needs a matching `Signed-off-by` (checkpatch checks this).

## 2. Provenance confirmation

Ask, and wait for an answer to each:

1. Did any code come from somewhere else (another project, a blog, Stack
   Overflow, a vendor driver, an AI answer quoting such sources)? If so,
   from where and under which license?
2. Is all of it compatible with GPL-2.0-only (or the file's SPDX license)?
3. Is there anything your employer or a contract restricts?

If the answer to 1 is yes and the license is unknown or incompatible, stop
and rewrite that part, or drop it. Record the answer:
`evidence.py note --key human_confirmations --value "provenance: <answer>"`.

## 3. Preserve what is already there

- Authorship: `From:`/author stays as it is. Do not change the author of a
  commit someone else wrote.
- Existing trailers stay, in order: `Co-developed-by:` (always immediately
  followed by that person's `Signed-off-by:`), `Reported-by:`, `Closes:`,
  `Link:`, `Fixes:`, earlier `Signed-off-by:` lines. The submitter's
  sign-off comes last.
- Keeping an existing human `Signed-off-by` through amend or rebase is not
  adding one.
- No AI identity in `Signed-off-by` or `Co-developed-by`, ever. AI help is
  disclosed in the cover letter (`references/disclosure.md`).

## 4. Check before handover

```
python3 <skill-dir>/scripts/signoff_guard.py check "$BASE..HEAD"
```

| Code | Meaning | Action |
|---|---|---|
| `NO_SNAPSHOT` (warn) | no snapshot was taken | take one now (`signoff_guard.py snapshot "$BASE..HEAD"`); ask the human whether every existing `Signed-off-by` is theirs or legitimately carried |
| `SOB_ADDED` (error) | a `Signed-off-by` appeared after the snapshot | if you added it, remove it (restore the message) and tell the human; if the human added it with their own command, confirm with them, then re-snapshot |
| `AI_IDENTITY` (error) | `Signed-off-by`/`Co-developed-by` looks like an AI | remove it; never acceptable |

`checkpatch_summary.py` reports a missing sign-off as `pending_signoff`:
say "awaiting your sign-off", not "failed".

## 5. The human signs

Give the command in a block labelled for the human. With b4's default
cover strategy (`b4 prep --show-info cover-strategy` prints `commit`),
rebase onto the cover-letter commit so only the patches are touched and
b4's tracking data in the cover commit is left alone:

```
# Run this yourself (the agent must not):
git rebase --signoff "$(b4 prep --show-info start-commit)"
git log --format='%h %s%n%(trailers:key=Signed-off-by)' "$(b4 prep --show-info series-range)"
```

The cover letter already carries their sign-off from `b4 prep -n` /
`--enroll`. With another cover strategy, check the b4 docs for the right
base before rebasing.

`git rebase --signoff` rewrites every patch commit, so all evidence entries
become `stale`. That is expected: `evidence.py status` and the report are
checked before the human signs (`references/submit.md`). Do not ask for a
re-run of everything because of the sign-off alone; do confirm that
`git rev-parse HEAD^{tree}` still equals the tree of the validated state.

## 6. After the human signed

```
python3 <skill-dir>/scripts/signoff_guard.py check "$BASE..HEAD"   # expect SOB_ADDED for the new lines
```
Show the flagged lines; the human confirms each one is theirs. Then
re-snapshot (this overwrites the old snapshot) so any later change is
measured against the signed state; do the same for each vN after the human
has signed it:

```
python3 <skill-dir>/scripts/signoff_guard.py snapshot "$BASE..HEAD"
python3 <skill-dir>/scripts/evidence.py note --key human_confirmations \
  --value "human signed off with git rebase --signoff; lines confirmed"
```

If you amend a signed commit later (revise phase), keep its message and
trailers; `git commit --amend` without `-s` does that.

Next: `references/submit.md`.
