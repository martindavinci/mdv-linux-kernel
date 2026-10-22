#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
#
# Smoke test of the mdv-linux-kernel scripts on a real kernel tree.
#
#   evals/smoke.sh --kernel DIR [--jobs N] [--keep] [--work DIR]
#
# Checks the technical assumptions of the skill's scripts against a real
# checkout (checkpatch -g summaries, a W=1 object build, warning diff,
# Fixes: checks, evidence store with timeout, sign-off guard). It is a
# pre-check for evals/VERIFICATION.md, not a replacement for it.
#
# Isolation: works in a `git clone --shared` in a temporary directory (own
# git dir, hooks off, no signing, test identity, GIT_CONFIG_NOSYSTEM and an
# empty global config), a private O= build directory, a cleaned build
# environment and a unique MLK_SERIES. The tree in DIR is only read. The
# clone and build are removed on exit unless --keep. No network, no sending.
#
# Needs: git, make, a C compiler, flex, bison, bc, perl, python3 (the
# kernel's own build dependencies), about 3 GB free in the work directory.
# Exit: 0 every check gave the expected result, 1 some did not, 2 usage
# error or missing tools.

set -uo pipefail

usage() {
	sed -n '4,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

kernel=""
jobs=""
keep=0
work_parent="${TMPDIR:-/tmp}"
while [ $# -gt 0 ]; do
	case "$1" in
	--kernel) [ $# -ge 2 ] || { usage >&2; exit 2; }; kernel="$2"; shift 2 ;;
	--jobs) [ $# -ge 2 ] || { usage >&2; exit 2; }; jobs="$2"; shift 2 ;;
	--keep) keep=1; shift ;;
	--work) [ $# -ge 2 ] || { usage >&2; exit 2; }; work_parent="$2"; shift 2 ;;
	-h|--help) usage; exit 0 ;;
	*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
	esac
done

[ -n "$kernel" ] || { echo "--kernel DIR is required" >&2; usage >&2; exit 2; }
if [ ! -f "$kernel/MAINTAINERS" ] || [ ! -f "$kernel/scripts/checkpatch.pl" ] ||
	! git -C "$kernel" rev-parse --git-dir >/dev/null 2>&1; then
	echo "not a kernel git checkout: $kernel" >&2
	exit 2
fi
for tool in git make python3 perl; do
	command -v "$tool" >/dev/null 2>&1 || { echo "missing tool: $tool" >&2; exit 2; }
done
if [ -z "$jobs" ]; then
	jobs="$(nproc 2>/dev/null || echo 2)"
	[ "$jobs" -le 8 ] || jobs=8
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
S="$here/../skills/mdv-linux-kernel/scripts"
S="$(cd "$S" && pwd)"
kernel="$(cd "$kernel" && pwd)"

WORK="$(mktemp -d "$work_parent/mlk-smoke.XXXXXX")" || exit 2
cleanup() {
	if [ "$keep" -eq 1 ]; then
		echo "kept: $WORK"
	else
		rm -rf "$WORK"
	fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# Clean environment: nothing from the user's build or git setup leaks in.
unset ARCH CROSS_COMPILE LLVM CC LD HOSTCC KBUILD_OUTPUT O KCFLAGS KCPPFLAGS \
	KBUILD_EXTMOD INSTALL_MOD_PATH GIT_DIR GIT_WORK_TREE GIT_CONFIG_COUNT
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export MLK_SERIES="smoke-$$"
export LC_ALL=C

SRC="$WORK/src"
B="$WORK/build"
LOGS="$WORK/logs"
mkdir -p "$B" "$LOGS"

results=()
failed=0
check() { # name expected got
	local ok="ok"
	[ "$2" = "$3" ] || { ok="FAIL"; failed=1; }
	results+=("$1|$2|$3|$ok")
	printf '%-34s expected %-10s got %-10s %s\n' "$1" "$2" "$3" "$ok"
}
rc() { "$@" >>"$LOGS/commands.log" 2>&1; echo $?; }
py() { python3 "$S/$1" "${@:2}"; }

echo "== cloning $kernel into $SRC (shared objects, own git dir)"
base="$(git -C "$kernel" rev-parse HEAD)"
git clone -q --shared --no-checkout "$kernel" "$SRC" || exit 2
g() { git -C "$SRC" "$@"; }
g config core.hooksPath /dev/null
g config commit.gpgsign false
g config user.name "MLK Smoke"
g config user.email "smoke@example.invalid"
g checkout -q -b smoke "$base" || exit 2

target="net/core/sock.c"
obj="net/core/sock.o"

# 1. Environment and policy scripts run to completion (findings are fine).
got="$(rc py env_check.py --json --path "$SRC")"
check "env_check runs" "0|1" "$([ "$got" -le 1 ] && echo '0|1' || echo "$got")"
got="$(rc py policy_snapshot.py --repo "$SRC" --json)"
check "policy_snapshot runs" "0|1" "$([ "$got" -le 1 ] && echo '0|1' || echo "$got")"

# 2. Two-patch series: an unused static function, then a comment.
printf '\nstatic int mlk_smoke_unused(void)\n{\n\treturn 0;\n}\n' >>"$SRC/$target"
g commit -q -a -F - <<'EOF'
net: core: add an unused helper for a smoke test

Add a static function that nothing calls, so that a W=1 build reports
-Wunused-function. This commit only exists in a throwaway clone.
EOF
p1="$(g rev-parse HEAD)"
printf '/* mlk smoke: second patch */\n' >>"$SRC/$target"
g commit -q -a -F - <<'EOF'
net: core: add a comment for a smoke test

Second patch of the throwaway series, so checkpatch -g sees two commits.
EOF
p2="$(g rev-parse HEAD)"
range="$base..$p2"

# 3. checkpatch -g prints one "total:" line per commit.
(cd "$SRC" && ./scripts/checkpatch.pl --strict --show-types -g "$range") >"$LOGS/checkpatch.log" 2>&1
check "checkpatch -g totals" "2" "$(grep -c '^total:' "$LOGS/checkpatch.log")"
got="$(rc py checkpatch_summary.py --json --run "$range" --repo "$SRC")"
check "checkpatch_summary completes" "0|1" "$([ "$got" -le 1 ] && echo '0|1' || echo "$got")"

# 4. Commit messages and Fixes: tags.
check "commit_msg_lint good series" "0" "$(rc py commit_msg_lint.py "$range" --repo "$SRC")"
base_subject="$(g log -1 --format=%s "$base")"
g commit -q --allow-empty -F - <<EOF
net: core: smoke test with a valid Fixes tag

Empty commit carrying a valid tag.

Fixes: $(g rev-parse --short=12 "$base") ("$base_subject")
EOF
got="$(rc py check_fixes.py HEAD~1..HEAD --repo "$SRC")"
check "check_fixes valid tag" "0" "$got"
g commit -q --allow-empty -F - <<'EOF'
net: core: smoke test with an invented Fixes tag

Empty commit carrying a tag that points nowhere.

Fixes: 0123456789ab ("net: this commit does not exist")
EOF
py check_fixes.py HEAD~1..HEAD --repo "$SRC" --json >"$LOGS/fixes-bad.json" 2>>"$LOGS/commands.log"
check "check_fixes invented tag exit" "1" "$?"
check "check_fixes invented tag code" "yes" "$(grep -q FIXES_UNKNOWN_SHA "$LOGS/fixes-bad.json" && echo yes || echo no)"
g commit -q --allow-empty -m "Fixed stuff" -m '## Summary' -m '- **bold** change'
check "commit_msg_lint bad message" "1" "$(rc py commit_msg_lint.py HEAD~1..HEAD --repo "$SRC")"
g reset -q --hard "$p2"  # back to the two-patch series

# 5. W=1 object build: baseline, patched, warning diff.
echo "== building $obj (defconfig, W=1, -j$jobs); this takes a few minutes"
(cd "$SRC" && g checkout -q --detach "$base" &&
	make -s O="$B" defconfig && ./scripts/config --file "$B/.config" -d WERROR &&
	make -s O="$B" olddefconfig) >>"$LOGS/commands.log" 2>&1
(cd "$SRC" && make O="$B" W=1 -j"$jobs" "$obj") >"$LOGS/baseline.log" 2>&1
check "baseline object built" "yes" "$([ -f "$B/$obj" ] && echo yes || echo no)"
before="$(stat -c %Y "$B/$obj" 2>/dev/null || echo 0)"
sleep 1
(cd "$SRC" && g checkout -q --detach "$p1" && make O="$B" W=1 -j"$jobs" "$obj") >"$LOGS/patched.log" 2>&1
after="$(stat -c %Y "$B/$obj" 2>/dev/null || echo 0)"
check "patched object rebuilt" "yes" "$([ "$after" -gt "$before" ] && echo yes || echo no)"
py build_warnings.py diff --baseline "$LOGS/baseline.log" --patched "$LOGS/patched.log" --json \
	>"$LOGS/warnings.json" 2>>"$LOGS/commands.log"
check "build_warnings finds new warning" "1" "$?"
check "new warning is -Wunused-function" "yes" \
	"$(grep -q 'unused-function' "$LOGS/warnings.json" && echo yes || echo no)"
check "build_warnings clean diff" "0" \
	"$(rc py build_warnings.py diff --baseline "$LOGS/baseline.log" --patched "$LOGS/baseline.log")"
g checkout -q smoke
check "series restored" "$p2" "$(g rev-parse HEAD)"

# 6. Evidence store: required checks, TAP parsing, timeout of a process group.
E() { py evidence.py "$@" --repo "$SRC"; }
E_run() { py evidence.py run --repo "$SRC" "$@"; }
check "evidence init" "0" "$(rc E init --base "$base")"
rc E require --name smoke-build --category build --why "smoke" >/dev/null
check "evidence status with missing" "1" "$(rc E status)"
check "evidence run pass" "0" "$(rc E_run --name smoke-build --category build -- true)"
check "evidence status all pass" "0" "$(rc E status)"
check "evidence TAP not ok fails" "1" \
	"$(rc E_run --name smoke-tap --category test -- sh -c 'echo "ok 1 a"; echo "not ok 2 b"')"
marker=$((3000 + $$ % 1000))
check "evidence timeout fails" "1" \
	"$(rc E_run --name smoke-timeout --category build --timeout 2 -- \
		sh -c "sleep $marker & sleep $((marker + 1)); wait")"
sleep 1
check "timeout killed children" "none" \
	"$(pgrep -f "sleep $marker" >/dev/null || pgrep -f "sleep $((marker + 1))" >/dev/null && echo left || echo none)"

# 7. Sign-off guard: a Signed-off-by added after the snapshot is reported.
py signoff_guard.py snapshot "$base..HEAD" --repo "$SRC" >>"$LOGS/commands.log" 2>&1
check "signoff_guard clean" "0" "$(rc py signoff_guard.py check "$base..HEAD" --repo "$SRC")"
g commit -q --amend -s --no-edit
check "signoff_guard SOB_ADDED" "1" "$(rc py signoff_guard.py check "$base..HEAD" --repo "$SRC")"

echo
echo "check | expected | got | ok"
for r in "${results[@]}"; do
	echo "$r" | sed 's/|/ | /g'
done
echo "logs: $LOGS$([ "$keep" -eq 1 ] || echo ' (removed on exit; use --keep to look)')"
exit "$failed"
