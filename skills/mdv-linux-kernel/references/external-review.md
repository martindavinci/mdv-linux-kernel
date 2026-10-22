<!-- SPDX-License-Identifier: GPL-2.0 -->

# External review (optional)

Run the same kinds of AI review the maintainers run, before posting, so
the human fixes or answers the findings first. On netdev this is expected:
`maintainer-netdev.rst` ("Review timelines") says authors are strongly
encouraged to run LLM reviews before posting, and must answer the AI
reviews of posted patches like any other review. Read that section from
the tree.

Skip this phase entirely while the security tripwire is active
(`references/security.md`).

## 1. Detect what is installed

| Tool | Check |
|---|---|
| review-prompts, Claude Code | `ls ~/.claude/skills/kernel/SKILL.md ~/.claude/commands/kreview.md` |
| review-prompts, Codex | `ls ~/.codex/skills/kernel/SKILL.md ~/.codex/prompts/kreview.md` |
| review-prompts, other agents | ask the human where `setup.sh` installed it, or where the clone is |
| Sashiko | `command -v sashiko` and a config (`~/.config/sashiko.toml`) |
| semcode (helps review-prompts) | `command -v semcode-index` |

If none is installed: warn that the posted patches will get AI review on
the list anyway, offer `references/setup.md` section 7, and continue with
the skill's own checks if the human declines. Record the decision:
`evidence.py note --key external_review --value "none installed; human declined setup"`.

## 2. Privacy warning (before every first run)

Tell the human, in their language, and wait for a yes:

> These reviewers send the patch and surrounding code and history from
> your repository to the LLM provider they are configured with. That holds
> for Sashiko's local `sashiko review` and for review-prompts. Only run
> them on code you are allowed to share with that provider. Multi-stage
> reviews can use a lot of quota or money.

## 3. Pick a reviewer

Prefer a reviewer that differs from the author: a different model or
provider, or at least a different protocol, and always a fresh context
(not the session that wrote the code). If this session's agent wrote the
code, for example, use Sashiko with a different backend, or run the
review-prompts command in a new session.

## 4. Run

**review-prompts** (in a new agent session, inside the kernel tree):
- one commit: `/kreview <commit>` (defaults to the top commit);
- the series: `/kseries <first>..<last>`, e.g.
  `/kseries $(b4 prep --show-info series-range)`;
- agents without these commands: "Read `<review-prompts>/kernel/review-core.md`
  and review commit <sha>", as the slash command files do.

These run interactively, so save the output to
`$SERIES/review-<tool>-<n>.txt` and record a note (below).

**Sashiko** (non-interactive, record it as evidence):
```
python3 <skill-dir>/scripts/evidence.py run --name sashiko-1 --category review -- \
  sashiko review "$(b4 prep --show-info series-range)"
```
It applies patches in a scratch clone and leaves the working tree alone.
Do not add `--report-preexisting` unless the human wants old bugs too.

## 5. Handle every finding

Each finding is either fixed or rebutted with a reason, the same rule
netdev applies to its AI review ("either debate it or address it"). Keep a
table in `$SERIES/review-findings.md`:

```
# | tool/run | finding (short) | verdict | where / why
1 | sashiko-1 | missing unlock on error path in foo_open() | fixed | patch 2, v1->v2
2 | sashiko-1 | possible NULL deref of priv->dev | rebutted | set in probe before register_netdev(); see foo_probe()
```

- Check each finding against the code before acting; models are often
  wrong, and "fixing" a non-bug adds churn.
- A rebuttal must hold up if a maintainer asks the same question; it is
  also the draft of the reply on the list if the same point comes up after
  posting.
- Fixes change the code: evidence becomes stale; go back to
  `references/validate.md`.
- Ask the human to confirm each rebuttal; it will be their answer upstream.

Record a summary:
```
python3 <skill-dir>/scripts/evidence.py note --key external_review \
  --value "sashiko-1 (backend X): 3 findings, 1 fixed, 2 rebutted (see review-findings.md)"
```

## 6. Know when to stop

Reviews are non-deterministic: a re-run finds different things, and some
findings are noise. Do at most two or three rounds. Stop when a round
brings only rebutted or repeated points. Do not loop until a tool is
silent. Slop findings from these tools are style questions, not acceptance
criteria.

## After posting

netdev runs Sashiko-based review on posted patches (https://sashiko.dev/
and https://netdev-ai.bots.linux.dev/sashiko/; results after about 24h,
per `maintainer-netdev.rst`). Those findings need a reply on the list:
see `references/revise.md`.
