<!-- SPDX-License-Identifier: GPL-2.0 -->

# Disclosure of tool use

Default: a short, factual transparency note in the cover letter. Follow
the target tree's text; never omit a disclosure the tree requires; never
invent or shrink tool use.

## History in two lines

Commit `78d979db6cef` added an `Assisted-by:` tag requirement, later
simplified to `Assisted-by: LLM [tools]` (`816d9992d9ed`). On 2026-10-14
mainline removed it (`1e035297ffda`, "docs: remove Assisted-by references")
after the 2026 Maintainers Summit concluded that how tools were used
belongs in the cover letter; `generated-content.rst` was left unchanged and
still asks for transparency.

Subsystem trees and older bases may still carry the old text. That is why
the procedure reads the target tree.

## Procedure

1. Read from the **target tree** (the remote the patch goes to, e.g.
   `git show net-next/main:Documentation/process/coding-assistants.rst`
   and `.../generated-content.rst`), plus the subsystem's
   `maintainer-*.rst`. `policy_snapshot.py` tells you when local copies
   differ from upstream.
2. Decide which case applies:
   - **No `Assisted-by:` requirement** (mainline today): write the
     transparency note below, in the cover letter.
   - **Target tree still requires `Assisted-by:`**: add the tag in exactly
     the format that tree's `coding-assistants.rst` gives (it was
     `Assisted-by: LLM [TOOL1] [TOOL2]` before removal), and still write
     the cover-letter note: `generated-content.rst` asks for transparency
     in cover letters and changelogs either way.
   - **Subsystem has its own rule**: follow it.
3. Separately, `generated-content.rst` asks that a tool used to *find* the
   problem be named in the changelog (the commit message): "Found with
   smatch", syzbot's `Reported-by:`, "found during an LLM-assisted
   review". Keep that in the commit message.
4. Show the note to the human; they must agree it is accurate. They may
   make it more detailed; do not let it become vaguer than the facts.
5. Record: `evidence.py note --key disclosure --value "<case>; note in cover letter; human approved"`.

## What the note answers

The questions in `generated-content.rst` ("Guidelines"):
- which tools were used (agent, model if known, plus Coccinelle, smatch...);
- the input to the tools: the Coccinelle script, the prompts, or for long
  sessions a summary of the prompts and of the help received;
- which parts of the submission the tools affected;
- how the submission was tested, and with which tools.

## Template (cover letter)

Plain text, a few lines, no heading markup (b4 drops lines starting with
`#`). Adapt; do not pad.

```
This series was written with help from <agent> (<model, if known>). The
assistant proposed the fix in patch 2 after I described the syzbot
report and pointed it at foo_close(); I reworked the error path and
wrote patch 1 by hand. It also drafted the commit messages, which I
edited. Prompts, in short: reproduce the report in virtme-ng, find why
the ring is freed while NAPI can still run, propose the minimal fix.

Testing: syzbot reproducer before/after in virtme-ng, net selftest
udpgro.sh, allmodconfig/allyesconfig W=1 builds with no new warnings.
Not tested on real foo hardware.
```

For a Coccinelle-generated change, include the semantic patch itself (or a
link to it) rather than describing it.

## Single patch without cover letter

b4 places the cover text of a one-patch series below the `---` line of the
patch, where reviewers see it and `git am` drops it. Put the same note
there (via `b4 prep --edit-cover` or `--cover-from-file`). Without b4,
write it by hand below `---`.

## Do not

- omit the note because "everyone uses AI" or because the tag was removed;
- describe the assistant's role as smaller than it was ("minor help") or
  larger, to share blame;
- list the assistant in `Signed-off-by` or `Co-developed-by`;
- invent prompts after the fact; summarise what actually happened, using
  the why-log;
- add Markdown or long sections; maintainers read this in a mail client.
