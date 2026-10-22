<!-- SPDX-License-Identifier: GPL-2.0 -->

# Validate

Run the real tools and record every result in the evidence store. The
store ties each result to the exact code it ran on; a later change makes
the result `stale`. Nothing counts as tested unless `evidence.py status`
shows `pass` for it.

## Evidence workflow

```
E="python3 <skill-dir>/scripts/evidence.py"
$E require --preset netdev        # once, after init (references/develop.md)
$E run  --name <name> --category <build|static|test|style|review|repro> \
        [--input FILE]... [--timeout SECONDS] -- <command...>
$E skip --name <name> --category <cat> --reason "<why it could not run>"
$E status            # exit 1 if anything is fail, stale or missing, or nothing was recorded
$E report > "$SERIES/report.md"
```

- `run` executes the command in the repo, saves the full log and records
  the command's exit code, duration, toolchain versions, build-related
  environment (`ARCH`, `LLVM`, `CROSS_COMPILE`...) and the tree state. It
  exits 0 if the check passed (or was all skipped) and 1 if it failed (the
  real exit code is in the entry).
- `--input FILE` (repeatable) records the hash of a file the result depends
  on but git does not track: the build directory's `.config`, the kernel
  image a runtime test booted, the baseline log a warning diff compared
  against. Pass them, otherwise a changed config, a rebuilt kernel or a
  different baseline goes unnoticed.
- `--timeout SECONDS` kills the command (and everything it started) and
  records a failure. Use it for VM runs and reproducers that can hang.
- For `--category test` the log is read as well: any failed TAP/KTAP
  (kselftest) or KUnit result, no result at all, or a kernel splat
  (`Call Trace:`, `BUG:`, KASAN/UBSAN/KCSAN, lockdep and RCU warnings,
  hung tasks, kmemleak) makes the entry `fail` even when the command
  exited 0. If every result was skipped, the entry is `skip`, with the
  reason. `--results none` / `--splats ignore` turn this off for commands
  that print no TAP output; say why when you use them.
- All subcommands need `init` first (`references/develop.md`); without it
  they exit 2. Required checks with no entry show as `missing`.
- A check you cannot run is a `skip` with a reason, never omitted and never
  a pass. Typical reasons: no KVM, no hardware, tool not installed.
- What makes an entry `stale`:
  - build, test and repro entries ("code" scope): any change to the code
    of any patch in the series. They depend on the tree of every commit
    in `base..HEAD`, so rewording messages, `b4 prep --edit-cover` /
    `--cover-from-file` / `--auto-to-cc`, `b4 trailers -u` and the human's
    `git rebase --signoff` keep them valid. Moving code between patches
    does not: every patch must build on its own.
  - static, style and review entries ("commits" scope): any amend or
    rebase, since checkpatch and the message checks read the messages.
    When only messages changed, `status` says so.
  - any entry: uncommitted changes and untracked files (an untracked new
    header can make a build pass that the posted patch would fail; commit
    or remove them first), a different toolchain, a changed `--input`
    file.
- Re-running a check with the same `--name` replaces the old result;
  `status` and `report` show only the latest entry per name.
- So: finish commit messages, cover letter and recipients (the slop gate,
  `references/slop-gate.md`) before the final validation round. After
  message-only changes, re-run the quick commit-scope checks. For an
  expensive one (an external review) that shows "only commit messages
  changed", re-run it if the change could matter to it; otherwise present
  it as stale with unchanged code and ask the human to accept it (warn +
  confirm). Never relabel it as pass.

Set up once per session (see SKILL.md, Conventions):
```
STATE="$(cd "$(git rev-parse --git-common-dir)" && pwd)/mdv-linux-kernel"
SERIES=$($E where)
BASE=$(b4 prep --show-info base-commit)
RANGE=$(b4 prep --show-info series-range)
```

## Checks

A pass only counts if the changed code was enabled, built and, for runtime
changes, actually ran: check that with `references/build-config.md`
before recording the result.

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
./scripts/config --file $KB/.config -d WERROR   # allmodconfig enables -Werror
make O=$KB olddefconfig
# kernel .c files the series adds or modifies (not deleted, not tools/ or scripts/)
OBJS=$(git diff --name-only --diff-filter=d "$BASE" HEAD -- '*.c' ':!tools/' ':!scripts/' | sed 's/\.c$/.o/')
echo "$OBJS"
$E run --name build-objs --category build --input "$KB/.config" -- \
  git rebase --exec "make O=$KB W=1 C=1 -j$(nproc) $OBJS" "$(b4 prep --show-info start-commit)"
```

If `OBJS` is empty (the series touches only headers, Kconfig, selftests or
scripts), do not run this: `make` with no targets builds the whole tree for
every patch. Rely on the full builds below, or build the directories whose
headers changed. With no edits, the rebase keeps every commit as it is;
check that `git rev-parse HEAD` is unchanged afterwards. If a build fails
the rebase stops half way: run `git rebase --abort` to return to the
series tip before doing anything else. If a file only exists after a
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
$E run --name allmodconfig-W1 --category build --input "$KB/.config" -- make O=$KB W=1 -j$(nproc)
# the "log" field of the allmodconfig-W1 item in "entries" of: $E status --json
BL="$STATE/baseline-allmodconfig-<base12>.log"
$E run --name new-warnings-allmodconfig --category build --input "$BL" --input <that log> -- \
  python3 <skill-dir>/scripts/build_warnings.py diff --baseline "$BL" --patched <that log>
```

Repeat with `allyesconfig` in its own build
directory. The patched build is incremental, so its log only contains
recompiled files; `build_warnings.py` compares diagnostics by file,
message and flag (not line numbers) and reports only what the patch added.
It also understands modpost (e.g. a missing `MODULE_DESCRIPTION()`),
objtool, linker and Kconfig warnings, and, like the netdev CI, compares the
total count of warning/error lines (`baseline_count`, `patched_count`): a
higher count fails even if no single line could be attributed.
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
$E run --name selftest-<test> --category test --timeout 3600 \
  --input "$WT/.config" --input "$WT/arch/x86/boot/bzImage" -- \
  vng --run "$WT" --user root --cpus 4 -- sh -c \
  'make -C "'"$WT"'/tools/testing/selftests" TARGETS=net TEST_PROGS=<test>.sh TEST_GEN_PROGS="" run_tests; rc=$?; dmesg; exit $rc'
```

Before every re-run, confirm `git -C "$WT" rev-parse HEAD^{tree}` equals
`git rev-parse HEAD^{tree}`; otherwise the VM runs old code. The NIPA wiki
page "How to run netdev selftests CI style" has the exact CI commands
(including a container image). The trailing `dmesg` puts the guest's
kernel log into the evidence log: `evidence.py` fails the entry on a
`not ok` result, on no results at all, and on kernel splats (`Call
Trace:`, KASAN, lockdep...), even when the command exits 0. Still read the
log yourself: a `# SKIP` caused by a missing tool or config is a gap to
fix (the test's `config` file, `vng --configitem`), not a pass. Also try
`kernel/configs/debug.config` for locking or memory changes.

**Choosing tests**: `git grep -l -e <function> -e <sysctl/feature> tools/testing/selftests/net tools/testing/selftests/drivers/net`,
the test files listed in the MAINTAINERS entry of the touched files, and
KUnit suites next to the code (`*_test.c`, `.kunitconfig`). For KUnit:
`./tools/testing/kunit/kunit.py run --kunitconfig=<dir with .kunitconfig>`.
If nothing covers the change, say so and consider writing a test as part
of the series.

### Bug fixes: reproducer before and after

Reading the report, shrinking the reproducer, bisecting and writing the
regression test: `references/debugging.md`.

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
$E run --name repro-before --category repro --timeout 900 --input "$B/arch/x86/boot/bzImage" -- \
  vng --run "$B" --user root -- \
  sh -c '<repro command>; dmesg | grep -q "<expected splat, e.g. BUG: KASAN>"'
$E run --name repro-after --category repro --timeout 900 --input "$WT/arch/x86/boot/bzImage" -- \
  vng --run "$WT" --user root -- \
  sh -c '<repro command> && ! dmesg | grep -q "<same pattern>"'
```

`repro` entries do not scan for splats on their own (the "before" run is
meant to produce one), so the assertion inside the command is what
decides; a timeout counts as a failure.

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

- [ ] `evidence.py status` exits 0 (nothing missing), or every fail/stale/skip is explained
      to the human and confirmed (warn + confirm)
- [ ] `$SERIES/report.md` regenerated after the last run
