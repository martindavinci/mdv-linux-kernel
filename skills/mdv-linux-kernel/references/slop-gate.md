<!-- SPDX-License-Identifier: GPL-2.0 -->

# Slop gate

For maintainers, slop is missing evidence and missing understanding:
patches that do not build, useless cleanups, wrong `Fixes:`, padded commit
messages, untested claims, authors who cannot answer review. This gate
looks for those before anything is posted. Every item is warn + confirm;
the heuristics in it are tools to improve the patch, not acceptance
criteria.

Run it after the first validation round and any external review. Edits
it causes to commit messages, the cover letter or recipients rewrite the
commits and make the style, static and review evidence stale (builds and
tests survive message-only changes), so finish them before the final
validation round (`references/validate.md`). Range:

```
RANGE=$(b4 prep --show-info series-range)
```

## Checklist

### 1. Commit messages

```
python3 <skill-dir>/scripts/commit_msg_lint.py "$RANGE"
```

| Code | Meaning | Usual fix |
|---|---|---|
| `SUBJECT_NO_PREFIX` | no `subsystem: ` prefix | copy the prefix style from `git log --oneline -- <file>` |
| `SUBJECT_TOO_LONG` | over 75 chars | say less; details go in the body |
| `SUBJECT_TRAILING_PERIOD` | ends with `.` | drop it |
| `SUBJECT_NOT_IMPERATIVE` | "fixed/adds/updating..." | "fix", "add", "update" |
| `BODY_MISSING` | no explanation | problem, cause, fix, why this fix |
| `BODY_LINE_TOO_LONG` | over 75 columns | rewrap (quoted logs and URLs are exempt) |
| `BODY_THIS_PATCH` | "This patch/commit ..." | describe the change in imperative mood |
| `LLM_PHRASES` | "comprehensive", "robust", "ensures that", "leverage"... | say the concrete thing, or delete the sentence |
| `MARKDOWN` | headings, `**bold**`, code fences | plain text; indent quoted logs |
| `BULLET_HEAVY` | many bullet lines | prose; bullets only for real lists |

Beyond the codes, read each message as a maintainer would: Does the first
paragraph state the problem? Is there a sentence that only restates the
diff? Would the message make sense in `git log` in five years? Cut
anything that does not help.

### 2. Fixes: tags

```
python3 <skill-dir>/scripts/check_fixes.py "$RANGE"
```
Codes and handling are in `references/validate.md`. Also check the
judgement the script cannot make: open the fixed commit and confirm that it
introduced this bug, not merely touched the same lines. A fix without a
`Fixes:` tag on netdev is a problem too (`maintainer-netdev.rst` requires
it for fixes).

### 3. Self-review with the maintainers' slop indicators

If review-prompts is installed, run its slop pass in a fresh session:
`/kslop` (or "read `<review-prompts>/kernel/slop-indicators.md` and apply
it to <commit>"). Apply its own discipline:
- **cluster rule**: one weak signal is not a finding; need a concrete,
  located instance, ideally repeated or corroborated;
- **compare with neighbouring code**, not an ideal;
- **plausible-reason test**: if a competent kernel developer could have a
  reason, drop it;
- **at most 3 questions** per patch, phrased as questions.

If it is not installed, do the same pass yourself with these tells:
comments that restate code, dead or unused additions, defensive checks for
impossible states, unrelated renames or moves, new helpers duplicating
existing ones, invented abstractions, over-long names.

### 4. Cleanup-only / churn

Look at the diff as a whole: is any part purely cosmetic (whitespace,
reordering, checkpatch fixes on untouched logic, `devm_` conversion)? On
netdev standalone cleanups are discouraged ("Clean-up patches" in
`maintainer-netdev.rst`). Inside a real change, drop cosmetic hunks that
are not needed for it. If the whole series is cleanup, warn strongly and
ask the human to confirm with that warning in view.

### 5. Diff hygiene

- [ ] no unrelated hunks, debug prints, commented-out code, stray files
- [ ] no whitespace-only changes outside the touched code
- [ ] each patch builds (validated) and is one logical change
- [ ] netdev: selftests in the same series but in their own patch
      ("Co-posting selftests" in `maintainer-netdev.rst`)

### 6. Recipients

```
b4 prep --auto-to-cc
b4 prep --show-cover    # inspect the To:/Cc: trailers it added
```
b4 runs `./scripts/get_maintainer.pl` for this. To explain who is who:
`./scripts/get_maintainer.pl -f <file>` shows maintainers, reviewers and
lists with their roles. Do not remove maintainers or lists it found; do
not add people without a reason (a reporter, a reviewer of a previous
version). For netdev, `netdev@vger.kernel.org` must be there. Private
email to maintainers instead of the list is not acceptable on netdev.

### 7. Disclosure and comprehension

Both are part of this gate: `references/comprehension.md` (questions to the
human) and `references/disclosure.md` (transparency note). Do them now,
after the code is final, so the questions and the note match what is sent.

## Recording

Each warning the human accepts:
```
python3 <skill-dir>/scripts/evidence.py note --key human_confirmations \
  --value "<CODE> on <commit>: <human's words>"
```
Optionally record the lint runs as evidence:
`evidence.py run --name commit-msg-lint --category style -- python3 <skill-dir>/scripts/commit_msg_lint.py "$RANGE"`.

Next: `references/signoff-dco.md`, then `references/submit.md`.
