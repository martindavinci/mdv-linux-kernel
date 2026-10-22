# Changelog

## 0.3.0 (2026-10-22)

Not verified in the field yet: see `evals/VERIFICATION.md`.

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

Not verified in the field yet: see `evals/VERIFICATION.md`.

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
