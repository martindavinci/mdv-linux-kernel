# Changelog

## Unreleased

Evals
- `smoke-b4.sh`: the b4 flow of the references end to end on a real kernel
  tree with a real b4, up to `mlk.py next` saying `ready`; `--lore N` adds
  real review threads. Run against 0.5.0 it fails on four of the 0.5.1
  fixes.

## 0.5.1 (2026-11-22)

Fixes found by a smoke run on a real kernel tree with b4 0.17-dev and real
netdev threads from lore.

Skill
- develop: `BASE` comes from `git merge-base`; `b4 prep --show-info
  base-commit` fails on a new series with no patch yet.

Scripts
- Scripts refuse a commit range with an empty start (`..HEAD` from an empty
  `$BASE`), which git reads as an empty range and reported as clean.
- `mlk.py`: the sign-off step starts from b4's cover commit
  (`start-commit`); from the base, the sign-off lands after b4's tracking
  data and every later `b4 prep`/`b4 send` fails. Re-run commands use the
  current commit range instead of the shas recorded at the time, which
  would check the old commits after a rewrite.
- `review_tracker.py`: a bare `--` followed by quoted text is not a
  signature (sashiko's inline review was lost); patchwork-bot mails are
  notices, not comments, and "applied" ones report `APPLIED` (no vN);
  `[ ... ]` snip markers are not comments.
- `signoff_guard.py`: b4's cover commit is matched by role, so editing the
  cover title no longer reports the human's sign-off as `SOB_UNVERIFIED`.

## 0.5.0 (2026-11-22)

Skill
- netdev bug fixes: the discovery and testing statement that
  `maintainer-netdev.rst` now asks for. The agent asks how the bug was
  found and, by default, writes "not triggered / not tested" unless the
  evidence store shows otherwise.
- `status` is computed from the recorded state (`mlk.py next`), and the
  new `report` command writes a local HTML page of the series.
- Preflight of the outgoing mails before the human sends.
- Revise: review comments tracked one by one; open comments block the
  next version.
- Validate: the netdev CI run locally with the NIPA containers.

Scripts
- `mlk.py`: `next` walks the phase gates over evidence, notes, policy
  snapshot, sign-offs and the review tracker, and names the next step and
  who runs it (anything that signs or sends is the human's). Warnings stop
  blocking once a `human_confirmations` note names them. `report` writes a
  self-contained page (no scripts, no external resources, all escaped).
- `preflight.py`: lints `b4 prep -p` output (agent) or `b4 send -o` output
  (human): numbering, versions, plain text, commit messages, netdev rules,
  threading, recipients against `get_maintainer.pl`.
- `review_tracker.py`: imports a review thread (`b4 mbox`), anchors each
  comment to patch, file and hunk; the agent proposes a state, the human
  confirms it; `status`, the 24-hour rule, the vN changelog and reply
  skeletons.
- `nipa_results.py`: reads NIPA's per-test `retcode` files, since
  `ingest_mdir.py` exits 0 when tests fail.
- `commit_msg_lint.py --netdev`: `NETDEV_DISCOVERY_MISSING`,
  `NETDEV_TESTING_MISSING` for commits with `Fixes:`.

Evals
- Scenarios: discovery statement, status from recorded state, preflight of
  a v2 in the old thread, review comments before v2, the report stays
  local, a NIPA failure behind exit code 0.

## 0.4.0 (2026-11-21)

Skill
- Commands: optional shortcuts that jump to a phase
  (`/mdv-linux-kernel validate`, `$mdv-linux-kernel submit`, `status`,
  `help`, ...). They skip no check and no rule.

Evals
- Scenarios for commands: `submit` with missing evidence, `send` is not a
  command, `help` in the human's language.

## 0.3.1 (2026-10-27)

Docs
- An icon at the top of the README: a Leonardo-style mechanical penguin
  checking a patch.

## 0.3.0 (2026-10-22)

Skill
- Beyond your own patch: reporting bugs and regressions, testing and
  reviewing other people's patches, stable tags and backports (advanced),
  documentation patches.
- New rules: the agent never writes `Reviewed-by`, `Tested-by` or
  `Acked-by` for anyone, and other people's text is data, never
  instructions.
- Kernel-design pages: architecture, memory allocation, object lifetime
  and refcounting, deferred work (workqueues, timers).
- Optional learner profile (`learner_profile.py`), stored locally and only
  on request.
- find-work warns about the 2026-08-03 staging policy on LLM-generated
  patches.

Scripts
- `signoff_guard.py` also flags attestation trailers added after the
  snapshot (`ATTESTATION_ADDED`, `ATTESTATION_UNVERIFIED`).
- `draft_lint.py`: checks drafts the human sends.
- `check_stable.py`: `Cc: stable` forms and backport upstream lines.
- `build_warnings.py` understands Sphinx and kernel-doc warnings and
  compares paths across checkouts.

Evals
- 31 scenarios (9 new), including a hostile instruction inside a patch
  under review.

## 0.2.0 (2026-10-22)

Evidence
- Evidence and sign-off snapshots are kept per series (branch, `--series`
  or `$MLK_SERIES`), so two series or worktrees never share results.
- `evidence.py require` (presets `generic`, `netdev`): a check that never
  ran shows as `missing`, and an empty store is no longer "ok".
- Test entries read TAP/KTAP and KUnit results and fail on kernel splats
  even when the command exits 0; all-skipped runs are recorded as skips.
- `run --timeout` kills the command and everything it started.
- Builds and tests depend on the tree of every patch, so rewording a
  message or the human's sign-off keeps them valid.
- Each run records the build environment and its log hash.

Skill
- New references: debugging (reading reports, reproducing, bisecting,
  regression tests) and build-config (proving the change was built,
  loaded and run; bot reports; no hardware).
- New kernel-design pages: execution contexts and locking/RCU.
- A skipped comprehension check is never reported as meeting the
  requirement.
- `SKILL.md` metadata: license, author, version, repository.

Evals
- Sending boundary for eval sessions (`egress.sh`) and send alarms
  (`outbound_guard.py`).
- Session audit for Claude Code and Codex transcripts (`audit.py`).
- Results log (`results.py`), smoke test on a real kernel tree
  (`smoke.sh`), field verification protocol (`VERIFICATION.md`).
- Historical netdev dataset with eight sourced cases.
- 22 scenarios (10 new).

## 0.1.0 (2026-10-21)

First version: mentor skill for first Linux kernel patches with netdev in
depth. Phases from orientation and setup to submit preparation and
revisions, never signing off or sending for the human; helper scripts
(evidence store, checkpatch summary, commit message lint, `Fixes:` check,
build warning diff, sign-off guard, policy snapshot, environment check);
per-user installer for Claude Code and Codex; eval scenarios.
