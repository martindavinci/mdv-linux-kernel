<!-- SPDX-License-Identifier: GPL-2.0 -->

# Develop

You are a guided co-author: you may write code, but the human must end up
understanding every line (`references/comprehension.md` will check). Work
in small steps and explain each one as you make it.

## 1. Create the series (the human runs this)

Explain first: a kernel contribution is a *patch series*, a set of emails,
one per commit, usually with a cover letter ("patch 0/N") that explains the
whole. There are no pull requests; maintainers apply the emails. b4 keeps
the series on a branch and the cover letter in an empty commit.

Both commands below write the human's `Signed-off-by` into the cover
letter, so the human runs them (non-overridable rule 1):

```
# new work, forked from the target tree
b4 prep -n <short-descriptive-name> -f net-next/main     # or net/main for a fix

# existing commits on the current branch
b4 prep --enroll net-next/main
```

The name becomes part of the change-id that tracks the series across
versions; "fix" or "stuff" is a bad name.

Then you may set the tree prefix (local, no sign-off involved):
```
b4 prep --set-prefixes net-next     # or net
```

## 2. Start the evidence store and the sign-off snapshot

Immediately after the human's `b4 prep`, before you change any commit:

```
BASE=$(b4 prep --show-info base-commit)
python3 <skill-dir>/scripts/evidence.py init --base "$BASE"
python3 <skill-dir>/scripts/evidence.py require --preset netdev   # or generic outside netdev
python3 <skill-dir>/scripts/signoff_guard.py snapshot "$BASE..HEAD"
python3 <skill-dir>/scripts/evidence.py note --key policy --value "<policy_snapshot summary>"
SERIES=$(python3 <skill-dir>/scripts/evidence.py where)
```

Both the store and the snapshot belong to the current branch (the b4 prep
branch): another series, or another worktree, gets its own. On a detached
HEAD the scripts refuse to guess; pass `--series <branch>`.

`require` declares the checks the series cannot go without; a required
check with no result shows as `missing` and `evidence.py status` fails
until it is run, or recorded as a `skip` with a reason. The `netdev`
preset adds the builds `maintainer-netdev.rst` asks for; both presets add
`fixes-tags` when a patch carries `Fixes:` (re-run `require --preset ...`
after adding one). Add what this patch needs on top, for example:

```
python3 <skill-dir>/scripts/evidence.py require --name selftest-udpgro --category test \
  --why "changes the UDP GRO path"
python3 <skill-dir>/scripts/evidence.py require --name repro-before --category repro --why "bug fix"
python3 <skill-dir>/scripts/evidence.py require --name repro-after --category repro --why "bug fix"
```

The snapshot records every `Signed-off-by` that exists now (the human's,
in the cover letter, plus any on enrolled commits). Later checks flag any
new one. Taking a snapshot again overwrites the previous one; do that only
at the points named in `references/signoff-dco.md`.

`evidence.py init` refuses to overwrite an existing store (exit 1). Use
`--force` only when starting a new series, or when the series moves to a
new base: save the old report first
(`evidence.py report > "$SERIES/report-<old-base>.md"`), then
`evidence.py init --base <new base> --force`. A new version (vN) on the
same base keeps the store; re-running checks under the same names
replaces the old results.

## 3. Writing the change

- **One logical change per patch.** Each patch must build and work on its
  own (`submitting-patches.rst`, "Separate your changes"); `git bisect`
  lands on every commit. Moving code and changing it are separate patches.
  On netdev, selftests for a change go in the same series, in their own
  patch.
- **Follow `Documentation/process/coding-style.rst` and the surrounding
  code.** When the file has its own conventions, match them. netdev has
  extra style rules; see `references/netdev.md`.
- **Smallest change that fixes the problem.** No drive-by renames, no
  reformatting of untouched lines, no new helpers "for clarity", no
  defensive checks for conditions that cannot happen. Each of these is a
  review question and a slop signal.
- **Comments only where the code cannot say it** (why, not what). Match the
  comment style of the file.
- **Explain as you go.** After each edit, tell the human in two or three
  sentences what changed and why, and point at the line. If they ask for
  less, shorten it, but do not stop.
- **Commits are made without `-s`.** Use `git commit` (or `--amend`) with
  the human's configured identity as author. Never set an AI as author or
  co-developer.

## 4. The why-log

Keep a running log at `$SERIES/why-log.md` (outside the tree). One entry per
decision:

```
<date> <file>:<function>
Changed: <what>
Why: <the reason, with evidence: trace, test, code reference>
Rejected: <alternatives and why not>
Open: <questions for the human or for reviewers>
```

It feeds the commit message, the cover letter, the comprehension questions
and later replies to reviewers. Show it to the human; it is theirs.

## 5. Commit message

Read "Describe your changes" and "The canonical patch format" in
`submitting-patches.rst` from the tree; for netdev also "Preparing changes"
in `maintainer-netdev.rst`. In short, as a fallback to verify there:

- **Subject**: `<subsystem prefix>: <imperative summary>`, about 70-75
  characters at most. Find the prefix with
  `git log --oneline -20 -- <file>` and copy what recent commits use.
- **Body**: the problem (user-visible symptom for a bug: log line, oops,
  wrong behaviour), the cause, the fix, and why this fix is the right one.
  Imperative mood ("Make foo do bar"), not "This patch". Wrap at 75
  columns. No Markdown, no bullet lists of obvious points, no summary
  paragraph, no adjectives like "robust" or "comprehensive".
- **Trailers** at the end, no blank lines between them:
  - `Fixes: <12+ char sha> ("<exact subject>")`: generate it with
    `git log -1 --pretty=fixes <sha>`; never type it from memory. Only for
    a commit that really introduced the bug; verify by reading that commit
    (finding it: `references/debugging.md`, step 4).
  - `Reported-by:` + `Closes: <public URL>` for reported bugs (syzbot gives
    the exact `Reported-by` line in its report). Private trackers are not
    allowed.
  - `Link:` for relevant public discussion.
  - Tags naming other people (`Reviewed-by`, `Tested-by`, `Acked-by`,
    `Co-developed-by`) need their explicit permission; `Cc:`,
    `Reported-by:` and `Suggested-by:` have looser rules. Read "Tagging
    people requires permission" in `submitting-patches.rst`.
  - No `Signed-off-by`: the human adds it in `references/signoff-dco.md`.
- If a tool found the problem (syzbot, smatch, Coccinelle, an LLM review),
  say so in the commit message: `generated-content.rst` asks for that.
  How AI helped write the patch goes in the cover letter
  (`references/disclosure.md`).

Example shape (not content to copy):

```
net: foo: fix use-after-free in foo_close()

foo_close() frees priv->rx_ring before napi_disable() returns, so a
NAPI poll running on another CPU can still dereference it. syzbot
reported:

  BUG: KASAN: slab-use-after-free in foo_poll+0x1a4/0x3c0

Disable NAPI before releasing the ring, which matches the order used
in foo_stop().

Fixes: 0123456789ab ("net: foo: add NAPI support")
Reported-by: syzbot+0123456789abcdef0123@syzkaller.appspotmail.com
Closes: https://syzkaller.appspot.com/bug?extid=0123456789abcdef0123
```

## 6. Cover letter

With b4 0.17 or later (`b4 --version`), edit it without an editor,
keeping the human's `Signed-off-by` line exactly as it is:

```
b4 prep --show-cover > "$SERIES/cover.txt"
# edit "$SERIES/cover.txt"
b4 prep --cover-from-file "$SERIES/cover.txt"
python3 <skill-dir>/scripts/signoff_guard.py check "$BASE..HEAD"
```

With an older b4, write the draft to `$SERIES/cover-draft.txt` and ask the
human to run `b4 prep --edit-cover` and paste it in (keeping their
`Signed-off-by`); then run the sign-off check. Do not script the editor.

Lines starting with `#` are dropped by b4. Content: what the series does
and why, how the patches relate, how it was tested (from the evidence
report), and the transparency note (`references/disclosure.md`). For a
single patch, b4 puts the cover text below the `---` of the patch, where it
does not enter git history.

With b4's default cover strategy the cover letter is a commit at the start
of the series, so editing it rewrites every patch commit and makes the
style, static and review evidence stale (`references/validate.md`). Write the cover letter before
the final validation round.

## Done when

- [ ] the change is split into logical, buildable patches
- [ ] each patch has a commit message the human has read and agrees with
- [ ] the why-log covers every non-obvious decision
- [ ] no `Signed-off-by` added by you (`signoff_guard.py check` clean)

Next: `references/validate.md`.
