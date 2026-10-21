# Evaluating mdv-linux-kernel

Two layers:

1. **Unit tests** for the helper scripts (`tests/`). Deterministic, run anywhere:

   ```sh
   python3 -m unittest discover -s tests -t . -v
   ```

2. **Behaviour evals** for the skill itself (this directory). An agent with the
   skill installed is put in a situation and its behaviour is checked against
   expectations. These need a real kernel checkout and are run by hand for now.

## Scenario evals

`scenarios.json` is a list of cases:

| Field | Meaning |
|---|---|
| `id` | short name |
| `mode` | `setup`, `find-work` or `have-patch` |
| `setup` | the state to prepare (tree, patch, tools installed or not) |
| `prompt` | what the human says to the agent |
| `expect` | behaviours that must all happen |

How to run a scenario:

1. Prepare the state described in `setup` in a scratch kernel checkout
   (e.g. a `net-next` clone in WSL2 or Linux), on a throwaway branch.
2. Start the agent (Claude Code or Codex, the primary targets) in that
   checkout with the skill installed (`./install.sh`).
3. Send `prompt` verbatim.
4. Mark each `expect` line as met or not. A scenario passes only if all are met.
5. Record the agent, model, skill commit and result.

Run every scenario on both Claude Code and Codex before a release. Gemini CLI and
Copilot CLI are best effort.

## Historical-patch evals

Scenario evals check the gates on made-up cases. Historical evals check them on
real ones:

- **Rejected patches.** Pick netdev patches from lore that maintainers rejected
  for reasons the skill claims to catch (bad `Fixes:`, standalone cleanups,
  missing testing, LLM-style changelogs). Apply them to the base they were
  written against, run the skill in `have-patch` mode and check that the
  matching gate fires.
- **Accepted fixes.** Pick commits with `Fixes:` tags from `net`. Check that the
  skill does not raise false errors on them (warnings are fine if justified).
- **Evidence honesty.** For every run, compare `evidence.py report` with the
  commands actually executed in the session log. Any check reported as passed
  that was not run, or run on a different tree state, is a release blocker.

Keep a short log of results (date, skill commit, agent/model, scenario or
patch, pass/fail, notes) so regressions are visible.
