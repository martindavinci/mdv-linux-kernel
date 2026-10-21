<!-- SPDX-License-Identifier: GPL-2.0 -->

# Comprehension check

The submitter must understand and be able to defend the patch.
`generated-content.rst` says so in plain words, and on netdev the author
must answer review on the list, including AI review. This check finds the
gaps before a maintainer does.

Run it on the final code (after validation and external review), before
sign-off. The human can skip it or proceed without good answers, but only
after the strong warning and an explicit written confirmation.

## 1. Ask 3 to 5 questions

Derive them from this diff, the why-log and the touched code. Ask one at a
time, in the human's language, and let them answer without hints. Pick
from:

- **What breaks without it?** "What does a user see if this patch is not
  applied? How did you (or syzbot) trigger it?"
- **Why this fix?** "Why move `napi_disable()` before the free instead of
  adding a NULL check in `foo_poll()`?"
- **Locking and lifetime.** "Which lock protects `priv->rx_ring` here? Can
  `foo_poll()` run concurrently with `foo_close()`? Who frees the skb on
  this error path?"
- **Callers and context.** "Who calls this function? Can it sleep there?
  Is it reachable from userspace, and with which privileges?"
- **Error paths.** "What happens if `foo_alloc()` fails halfway?"
- **The Fixes: target.** "What did commit <sha> change that introduced the
  bug?"
- **Testing.** "How do you know it works? What did the test not cover?"

Do not ask about trivia (names of macros, line numbers). Skip questions on
parts that are mechanically obvious; say that you skipped them.

## 2. Evaluate honestly

For each answer: correct, partly correct (say what is missing), or wrong.
Do not soften a wrong answer into "partly". If the answer is wrong because
the code is wrong, that is a finding: go back to `references/develop.md`.

When an answer is weak, explain the point and ask the human to say it back
in their own words, or point them to the code to read. Understanding
reached this way is fine; the goal is that they can answer a reviewer.

## 3. If the human wants to skip or cannot answer

Show the strong warning. Quote the in-tree text, read from the target
tree at this moment, not from this file:

```
grep -n -B1 -A4 "expected to understand" Documentation/process/generated-content.rst
```

As of mainline 2026-10 the passage reads:

> You are expected to understand and to be able to defend everything you
> submit. If you are unable to do so, then do not submit the resulting
> changes.
>
> If you do so anyway, maintainers are entitled to reject your series
> without detailed review.

If the target tree's text differs, quote the tree. If the patch is for
netdev, add: authors are expected to reply to every review on the list,
including the AI reviews posted about 24h after submission
(`maintainer-netdev.rst`, "Review timelines").

Then tell them what proceeding means: they sign the DCO for code they
cannot explain, and the questions above are the ones a reviewer is likely
to ask in public.

To proceed, require an explicit written confirmation in their own words,
for example:

> I take responsibility for this submission.

"ok", "yes", "go ahead" or a thumbs-up is not enough. If they refuse to
write it, do not go on to submission preparation.

## 4. Record

```
python3 <skill-dir>/scripts/evidence.py note --key comprehension \
  --value "5 questions: 4 correct, 1 partly (error path in foo_open, explained). Not skipped."
```
or, when skipped:
```
python3 <skill-dir>/scripts/evidence.py note --key comprehension \
  --value "skipped after strong warning; human wrote: 'I take responsibility for this submission'"
```

Do not put the answers or the outcome in anything sent upstream; this is
for the human.
