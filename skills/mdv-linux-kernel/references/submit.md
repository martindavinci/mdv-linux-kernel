<!-- SPDX-License-Identifier: GPL-2.0 -->

# Submit (preparation only)

You prepare; the human signs and sends. Nothing in this phase is run by the
agent if it sends mail or contacts a submission endpoint.

## 1. Explain what sending means

Before the first send, tell the human briefly:
- `b4 send` turns each commit into an email and sends the series to the
  lists and people in the cover letter's `To:`/`Cc:`. On the web endpoint
  route it goes through kernel.org; patches are signed with their patatt
  or PGP key.
- Every kernel list is archived on lore.kernel.org, publicly and
  permanently, with their name and email (DCO clause (d)). There is no
  unsend. Mistakes are fixed by a new version, not by deletion.
- On netdev, patchwork picks it up, bots run checks, and AI reviews appear
  after about 24h. Replies are expected.

## 2. Pre-submit checks (the agent may run these; they are local)

```
b4 prep --check                 # checkpatch per patch; missing sign-off is pending, not a failure
b4 prep --show-info             # needs-editing, needs-auto-to-cc, base, prefixes, revision
python3 <skill-dir>/scripts/evidence.py status
python3 <skill-dir>/scripts/signoff_guard.py check "$BASE..HEAD"
python3 <skill-dir>/scripts/policy_snapshot.py --upstream <remote>/main --json
```

Then confirm, item by item:

- [ ] tree: `net` vs `net-next` correct, prefix set, and for net-next the
      tree is open (`references/netdev.md`)
- [ ] base is recent and on the target tree, not linux-next or stable
- [ ] `evidence.py status` exits 0, or each fail/stale/skip was confirmed
- [ ] no `EDITME` left in the cover letter (`needs-editing` false)
- [ ] recipients from `b4 prep --auto-to-cc` reviewed
- [ ] transparency note present (`references/disclosure.md`)
- [ ] comprehension recorded (`references/comprehension.md`)
- [ ] security screen recorded (`references/security.md`), no reproducer
      in public text if the tripwire fired
- [ ] slop gate done; every accepted warning recorded
- [ ] external review findings fixed or rebutted (if run)
- [ ] patch count within the subsystem's limit (netdev: see
      `references/netdev.md`)

Each unchecked item is a warning: warn + confirm.

## 3. Final summary for the human

Regenerate `$STATE/report.md` (`evidence.py report`) and present:

```
Series: <cover subject>  (<N> patches, v<rev>, target <tree>)
Base: <sha> (<date>)
Evidence: <pass count> pass, <skip count> skipped, <fail/stale> fail/stale
  Skipped: <name: reason>
Open findings: <none | list with where they are answered>
Warnings you accepted: <CODE: your words>
Disclosure: <cover-letter note | Assisted-by per tree text>
Comprehension: <outcome>
Security screen: <outcome>
Not tested: <hardware, configs, archs>
Full report: <path to $STATE/report.md>
```

Do not call the series "ready", "validated" or "tested" beyond what this
summary shows.

## 4. Commands for the human

Print this block, labelled clearly. Do not run any of it.

```
# ---- Run these yourself. The agent must not run them. ----

# 1. Add your Signed-off-by (see references/signoff-dco.md), then tell me,
#    so I can re-run the sign-off check and b4 prep --check:
git rebase --signoff "$(b4 prep --show-info start-commit)"

# 2. Look at the exact emails without sending:
b4 send -o /tmp/presend        # writes one file per message; read them
#    (b4 send --dry-run prints the same to the terminal)

# 3. Send only to yourself and check what arrives:
b4 send --reflect

# 4. Send for real. Public and permanent:
b4 send
```

Sections 2 and 3 (evidence status and the report) happen before step 1:
the sign-off rewrites the commits, after which every evidence entry shows
`stale`. That staleness is expected; do not ask for a full re-run because
of it. After step 1, check that `git rev-parse HEAD^{tree}` equals the
tree in the report, re-run `signoff_guard.py check` (expect the human's
new lines, see `references/signoff-dco.md`) and `b4 prep --check`
(sign-off no longer pending), then the human goes on to steps 2-4. If
anything other than the sign-off changed, go back to validation.

## 5. After sending

- b4 tags the sent version and moves the branch to the next revision; the
  cover letter gets a `Changes in vN:` section with a link to what was
  sent.
- Find the series on lore and, for netdev, on patchwork
  (https://patchwork.kernel.org/project/netdevbpf/list/); check its state
  there rather than asking on the list.
- Do not resend or ping early. Wait for review; for netdev, check the AI
  review after about 24h. Then go to `references/revise.md`.
- Once the series is applied: `b4 prep --cleanup` archives the branch.
