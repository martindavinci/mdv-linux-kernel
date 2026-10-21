<!-- SPDX-License-Identifier: GPL-2.0 -->

# Validate

Run the real tools and record every result in the evidence store. The
store ties each result to the exact code tree; any later change makes the
result `stale`. Nothing counts as tested unless `evidence.py status` shows
`pass` for it.

## Evidence workflow

```
E="python3 <skill-dir>/scripts/evidence.py"
$E run  --name <name> --category <build|static|test|style|review|repro> -- <command...>
$E skip --name <name> --category <cat> --reason "<why it could not run>"
$E status            # exit 1 if anything is fail or stale
$E report > "$STATE/report.md"
```

- `run` executes the command in the repo, saves the full log and records
  exit code, duration and the tree state. Its exit code is the command's.
- All subcommands need `init` first (`references/develop.md`); without it
  they exit 2.
- A check you cannot run is a `skip` with a reason, never omitted and never
  a pass. Typical reasons: no KVM, no hardware, tool not installed.
- An entry is valid only for the exact HEAD commit *and* tree it was
  recorded on. Any amend or rebase makes it `stale`, including
  message-only changes, `b4 prep --edit-cover` / `--cover-from-file` /
  `--auto-to-cc` (they rewrite the series' commits) and `b4 trailers -u`.
  Uncommitted changes to tracked files also make results stale: commit
  first.
- Re-running a check with the same `--name` replaces the old result;
  `status` and `report` show only the latest entry per name.
- So: finish commit messages, cover letter and recipients (the slop gate,
  `references/slop-gate.md`) before the final validation round, then
  re-run everything once. If only messages changed after an expensive run,
  the tree is unchanged: compare `git rev-parse HEAD^{tree}` with the
  entry's `state.tree` in `status --json`. Re-run if you can; otherwise
  present it as stale with identical tree and ask the human to accept it
  (warn + confirm). Never relabel it as pass.
- The human's `git rebase --signoff` also makes everything stale. That is
  expected: the evidence check is done before they sign
  (`references/submit.md`).

Set up once per session (see SKILL.md, Conventions):
```
STATE="$(git rev-parse --path-format=absolute --git-common-dir)/mdv-linux-kernel"
BASE=$(b4 prep --show-info base-commit)
RANGE=$(b4 prep --show-info series-range)
```

## Checks

| Name (suggested) | Category | Command | Required |
|---|---|---|---|
| `checkpatch` | style | `python3 <skill-dir>/scripts/checkpatch_summary.py --run "$RANGE"` | always |
| `fixes-tags` | static | `python3 <skill-dir>/scripts/check_fixes.py "$RANGE"` | if any `Fixes:` |
| `build-objs` | build | build of touched objects with `W=1 C=1`, every patch | always |
| `allmodconfig-W1`, `allyesconfig-W1` + `new-warnings-*` | build | full builds vs baseline | netdev minimum |
| `smatch` | static | smatch on touched files | where available |
| `coccicheck` | static | `make coccicheck MODE=report M=<dir>` | where available |
| `selftest-<name>` / `kunit-<suite>` | test | targeted runtime tests in virtme-ng | runtime changes |
| `repro-before`, `repro-after` | repro | reproducer on base and patched kernels | bug fixes |

### checkpatch

`checkpatch_summary.py --run` runs `checkpatch.pl --strict --show-types`
on the range. A missing `Signed-off-by` is reported as `pending_signoff`
(awaiting the human), not as an error, and on its own does not make the
command exit non-zero. Every remaining ERROR, WARNING or
CHECK must be fixed or justified; `submitting-patches.rst` says you must be
able to justify every violation you leave. Do not "fix" checkpatch noise in
lines the patch does not otherwise touch.

### Fixes: tags

`check_fixes.py` codes: `FIXES_FORMAT`, `FIXES_SHORT_SHA`,
`FIXES_TITLE_MISMATCH` (warn); `FIXES_UNKNOWN_SHA`, `FIXES_AMBIGUOUS_SHA`,
`FIXES_NOT_ANCESTOR` (error); `FIXES_NO_FILE_OVERLAP` (info, exit 0: still
check that the fixed commit really introduced the bug). Regenerate the line with
`git log -1 --pretty=fixes <sha>`. An error here means the tag is wrong;
do not ask for a confirmation to keep a tag pointing at a commit that is
not the cause.

### Touched objects, per patch

Every patch must build on its own. Use a build directory outside the tree
and a config that enables the touched code (allmodconfig is the simplest):

```
KB=$HOME/kbuild/allmod
make O=$KB allmodconfig
OBJS=$(git diff --name-only "$BASE" HEAD -- '*.c' | sed 's/\.c$/.o/')
$E run --name build-objs --category build -- \
  git rebase --exec "make O=$KB W=1 C=1 -j$(nproc) $OBJS" "$(b4 prep --show-info start-commit)"
```

With no edits, the rebase keeps every commit as it is; check that
`git rev-parse HEAD` is unchanged afterwards. If a file only exists after a
later patch, build the objects per patch instead. `C=1` runs sparse on
recompiled files; if an object was already up to date use `C=2`. If the
source tree has an in-tree `.config` or build products, `O=` builds fail
with "source tree is not clean": ask the human before `make mrproper`, it
deletes their config.

### netdev minimum: allmodconfig and allyesconfig, no new warnings

Read the "Testing" section of `maintainer-netdev.rst` from the tree. As of
this writing it asks for `allyesconfig` and `allmodconfig` builds with
`W=1` and no new warnings or failures, on top of the right networking tree.

Only *new* diagnostics count, so build the base first, then the series
incrementally in the same build directory (this is how the netdev CI does
it; see `tests/patch/build_allmodconfig_warn` in
https://github.com/linux-netdev/nipa). Full builds take a long time and
tens of GB; ask the human before starting.

```
KB=$HOME/kbuild/allmod
git switch --detach "$BASE"
make O=$KB allmodconfig
./scripts/config --file $KB/.config -d WERROR
make O=$KB W=1 -j$(nproc) 2>&1 | tee "$STATE/baseline-allmodconfig-$(git rev-parse --short=12 HEAD).log"
git switch -                      # back to the series branch
$E run --name allmodconfig-W1 --category build -- make O=$KB W=1 -j$(nproc)
# the "log" field of the allmodconfig-W1 item in "entries" of: $E status --json
$E run --name new-warnings-allmodconfig --category build -- \
  python3 <skill-dir>/scripts/build_warnings.py diff \
    --baseline "$STATE/baseline-allmodconfig-<base12>.log" --patched <that log>
```

Repeat with `allyesconfig` in its own build
directory. The patched build is incremental, so its log only contains
recompiled files; `build_warnings.py` compares diagnostics by file,
message and flag (not line numbers) and reports only what the patch added.
A baseline log stays usable as long as `BASE` does not change.

### smatch and Coccinelle

- smatch (if installed): run its kernel checker on each touched `.c` file
  as its documentation describes; record with `--category static`.
- Coccinelle: `make coccicheck MODE=report M=<touched dir>`. It reports
  existing issues too; only findings in lines the patch touches matter.

### Runtime tests in virtme-ng

Running selftests on the host tests the host kernel, not the patch. Boot
the patched kernel with virtme-ng. vng builds in the source tree, which
conflicts with `O=` builds, so use a separate worktree:

```
WT=$HOME/src/mlk-wt/tip
git worktree add --detach "$WT" HEAD          # later: git -C "$WT" checkout --detach <tip>
cd "$WT" && vng --build --config tools/testing/selftests/net/config && make headers
make -C tools/testing/selftests TARGETS=net
cd -
$E run --name selftest-<test> --category test -- \
  vng --run "$WT" --user root --cpus 4 -- \
  make -C "$WT/tools/testing/selftests" TARGETS=net TEST_PROGS=<test>.sh TEST_GEN_PROGS="" run_tests
```

Before every re-run, confirm `git -C "$WT" rev-parse HEAD^{tree}` equals
`git rev-parse HEAD^{tree}`; otherwise the VM runs old code. The NIPA wiki
page "How to run netdev selftests CI style" has the exact CI commands
(including a container image). Look for `not ok`, `# SKIP`, and kernel
splats (`Call Trace:`, KASAN, lockdep) in the log; a test that exits 0 with
a splat is a fail. Also try `kernel/configs/debug.config` for locking or
memory changes.

**Choosing tests**: `git grep -l -e <function> -e <sysctl/feature> tools/testing/selftests/net tools/testing/selftests/drivers/net`,
the test files listed in the MAINTAINERS entry of the touched files, and
KUnit suites next to the code (`*_test.c`, `.kunitconfig`). For KUnit:
`./tools/testing/kunit/kunit.py run --kunitconfig=<dir with .kunitconfig>`.
If nothing covers the change, say so and consider writing a test as part
of the series.

### Bug fixes: reproducer before and after

Read "Procedure for finding and fixing bugs" in `coding-assistants.rst`
from the tree and follow it. As of this writing: note the commit ID, prove
the bug with a reproducer (or explain why none is possible), verify the fix
with it, no new warnings, checkpatch clean, a `Fixes:` tag, maintainers
from `get_maintainer.pl`, and an explicit statement of anything not built
or tested.

Build a base kernel in a second worktree (`$HOME/src/mlk-wt/base`,
detached at `$BASE`) and record both runs from the main tree, so the
command line shows which kernel ran. Make each command assert its expected
outcome, so both entries are `pass` when the fix works:

```
B=$HOME/src/mlk-wt/base
$E run --name repro-before --category repro -- vng --run "$B" --user root -- \
  sh -c '<repro command>; dmesg | grep -q "<expected splat, e.g. BUG: KASAN>"'
$E run --name repro-after --category repro -- vng --run "$WT" --user root -- \
  sh -c '<repro command> && ! dmesg | grep -q "<same pattern>"'
```

Adapt the assertion to the bug (a wrong return value, a hang caught by
`timeout`, a counter). If the tripwire fired (`references/security.md`),
the reproducer stays local.

## What goes upstream

From the report, write a short "how it was tested" for the commit message
or below `---`, in the subsystem's style. State only what the report shows,
and name what was not tested:

```
Tested in a virtme-ng VM: the syzbot reproducer triggers the KASAN report
before the patch and runs clean after it; net/udpgro.sh passes. Not tested
on real hardware.
```

## Done when

- [ ] `evidence.py status` exits 0, or every fail/stale/skip is explained
      to the human and confirmed (warn + confirm)
- [ ] `$STATE/report.md` regenerated after the last run
