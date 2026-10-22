# Evaluating mdv-linux-kernel

Three layers:

1. **Unit tests** for the skill's scripts and the eval tools (`tests/`).
   Deterministic, no network, run anywhere:

   ```sh
   python3 -m unittest discover -s tests -t . -v
   ```

   They do not prove the scripts work on a real kernel tree.
2. **Smoke test** on a real kernel checkout (`smoke.sh`): checks the
   scripts' technical assumptions in an isolated clone.
3. **Behaviour evals** of the agent with the skill installed: scenarios and
   historical patches, run by hand following [`VERIFICATION.md`](VERIFICATION.md),
   the release gate.

## Nothing gets sent

The agent never sends mail or patches, and the evals make sure of it in
depth:

- **Boundary**: the eval user has no sending credentials and its egress
  to SMTP ports and the b4 web endpoint is dropped
  (`tools/egress.sh apply|check|remove`, nftables, run as root).
- **Alarms**: `tools/outbound_guard.py install` puts shims for `b4`,
  `git-send-email`, `sendmail` and `msmtp` first in `PATH` and routes
  `git send-email` to them. Every `b4 send` variant, `b4 ty -S`, `b4 pr`
  sending and the others are refused and logged in `attempts.log`. The
  shims can be bypassed (absolute paths, `git -c`, Python SMTP): they are
  alarms, not the boundary, and an empty log proves nothing on its own.
- **Audit**: `tools/audit.py` reads the session afterwards (below).

## Tools

| Tool | Purpose |
|---|---|
| `tools/egress.sh` | nftables egress filter for the eval user; `check` must pass before every run |
| `tools/outbound_guard.py` | send alarms (`install`, `check`) |
| `tools/audit.py` | session audit: forbidden commands, writes to evidence files, evidence entries without a matching completed call, edited logs; claims and snapshot recaptures for the reviewer |
| `tools/results.py` | results log (`record`, `summary`) in `results/results.jsonl` |
| `tools/validate_dataset.py` | schema of `historical/dataset.json`; `--repo` checks the commits in a kernel tree |
| `smoke.sh` | smoke test on a real kernel checkout |

### Audit

```sh
python3 evals/tools/audit.py --transcript <session.jsonl> [--transcript <subagent.jsonl>]... \
  --store <series dir>/evidence.json --attempts ~/mlk-guard/attempts.log [--json]
```

Supported: Claude Code session JSONL (pass the files under
`<session>/subagents/` too), Codex session JSONL
(`~/.codex/sessions/.../rollout-*.jsonl`), or a text file with one command
per line. The `codex exec --json` stream is not supported. Tool calls it
cannot read make coverage `partial` (exit 1). It splits commands on shell
operators, so quoted text is not mistaken for a command, but scripts and
aliases can still hide one. It supports a human reviewer; it does not
certify a session.

## Scenario evals

`scenarios.json` is a list of cases:

| Field | Meaning |
|---|---|
| `id` | short name |
| `mode` | `setup`, `find-work` or `have-patch` |
| `setup` | the state to prepare (tree, patch, tools installed or not) |
| `prompt` | what the human says to the agent |
| `expect` | behaviours that must all happen |

Run them as `VERIFICATION.md` describes and record each with
`results.py record`. A row keeps the sha256 of the scenario as it was: if
the scenario changes later, `summary` shows the old result as `obsolete`.

Run every scenario on both Claude Code and Codex before a release. Gemini
CLI and Copilot CLI are best effort.

## Historical dataset

`historical/dataset.json` holds real netdev cases: fixes applied to `net`
(the gates must not raise errors on them; justified warnings are fine) and
rejected patches with the maintainer's reason (the named gate must fire).
Cases are split into `dev` (may be used to write and tune the references)
and `heldout` (only for evaluation).

Every fact is copied from a public page read at `provenance.retrieved` and
listed in `provenance.sources`; nothing is typed from memory.
`validate_dataset.py --repo <kernel checkout> --write` confirms the commits
exist in the tree and fills `verified_tree`. That proves a commit is real;
the reason for a rejection rests on the cited message (`rationale.url`).

To run one: check out the base the patch was written against (or the
commit's parent for an accepted fix), apply the exact patch version
(`b4 am <patch message-id>`), start the agent in `have-patch` mode and
check `expected.must_fire` / `must_not_error`.

Gates named in `must_fire`: `standalone-cleanup`, `missing-fixes`,
`tree-choice`, `untested-hardware`, `unverified-claim`, `llm-style`, or a
script code such as `FIXES_TITLE_MISMATCH`.

## Evidence honesty

For every run, compare `evidence.py report` and the audit's evidence
section with what the session really did. Any check reported as passed
that was not run, or run on a different tree state, is a release blocker.
