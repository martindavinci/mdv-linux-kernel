#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
#
# Smoke test of the skill's b4 flow on a real kernel tree with a real b4.
#
#   evals/smoke-b4.sh --kernel DIR [--keep] [--work DIR] [--lore N]
#
# Walks a two-patch netdev series the way the references describe it:
# b4 prep, evidence store, commit message checks, cover letter without an
# editor, recipients, the human's sign-off (simulated with a test identity),
# b4 prep -p and b4 send -o (both only write files), preflight, mlk.py next
# up to "ready", and the HTML report. Each check guards an assumption about
# b4 that unit tests cannot see (the 0.5.1 fixes came from such a run).
# Builds are recorded as skips: evals/smoke.sh covers them.
#
# --lore N also downloads N recent netdev review threads from lore (read
# only) and imports them with review_tracker.py; this is the only network
# access, and it never fails the run on content.
#
# Isolation: a `git clone --shared` in a temporary directory with its own git
# dir, hooks off, no signing, a test identity, GIT_CONFIG_NOSYSTEM and an
# empty global config, sendemail pointed at /bin/false and no patatt key.
# The tree in DIR is only read. Removed on exit unless --keep. Run it as the
# human, outside an eval shell: with the send alarms active (MLK_GUARD_DIR)
# it refuses to start, since b4 send -o would be logged as a blocked send.
#
# Needs: git, python3, perl and b4 with --show-cover/--cover-from-file
# (0.17 or later, see references/setup.md).
# Exit: 0 every check gave the expected result, 1 some did not, 2 usage
# error, missing tools or an active send alarm.

set -uo pipefail

usage() {
	sed -n '4,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

kernel=""
keep=0
lore=0
work_parent="${TMPDIR:-/tmp}"
while [ $# -gt 0 ]; do
	case "$1" in
	--kernel) [ $# -ge 2 ] || { usage >&2; exit 2; }; kernel="$2"; shift 2 ;;
	--keep) keep=1; shift ;;
	--work) [ $# -ge 2 ] || { usage >&2; exit 2; }; work_parent="$2"; shift 2 ;;
	--lore)
		[ $# -ge 2 ] && [[ "$2" =~ ^[0-9]+$ ]] || { echo "--lore needs a number" >&2; exit 2; }
		lore="$2"; shift 2 ;;
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
if [ -n "${MLK_GUARD_DIR:-}" ]; then
	echo "send alarms are active (MLK_GUARD_DIR): run this outside the eval shell;" >&2
	echo "b4 send -o would be blocked and logged as a release blocker" >&2
	exit 2
fi
for tool in git python3 perl b4; do
	command -v "$tool" >/dev/null 2>&1 || { echo "missing tool: $tool" >&2; exit 2; }
done
if ! b4 prep --help 2>/dev/null | grep -q -- '--cover-from-file'; then
	echo "b4 $(b4 --version 2>/dev/null) has no --cover-from-file: needs 0.17 or later" >&2
	exit 2
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
S="$(cd "$here/../skills/mdv-linux-kernel/scripts" && pwd)"
kernel="$(cd "$kernel" && pwd)"

WORK="$(mktemp -d "$work_parent/mlk-smoke-b4.XXXXXX")" || exit 2
cleanup() {
	if [ "$keep" -eq 1 ]; then
		echo "kept: $WORK"
	else
		rm -rf "$WORK"
	fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

unset GIT_DIR GIT_WORK_TREE GIT_CONFIG_COUNT MLK_SERIES
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export LC_ALL=C

SRC="$WORK/src"
LOGS="$WORK/logs"
mkdir -p "$LOGS"

results=()
failed=0
check() { # name expected got
	local ok="ok"
	[ "$2" = "$3" ] || { ok="FAIL"; failed=1; }
	results+=("$1|$2|$3|$ok")
	printf '%-44s expected %-10s got %-10s %s\n' "$1" "$2" "$3" "$ok"
}
rc() { "$@" >>"$LOGS/commands.log" 2>&1; echo $?; }
py() { python3 "$S/$1" "${@:2}"; }
E() { py evidence.py "$@"; }
yes_no() { if "$@"; then echo yes; else echo no; fi; }
next_json() { py mlk.py next --upstream smoke-base --json >"$LOGS/next.json" 2>>"$LOGS/commands.log"; }
rerun_commit_checks() { # after a rewrite, on the current range
	rc E run --name checkpatch --category style -- \
		python3 "$S/checkpatch_summary.py" --run "$RANGE" >/dev/null
	rc E run --name commit-msg-lint --category style -- \
		python3 "$S/commit_msg_lint.py" --netdev "$RANGE" >/dev/null
	rc E run --name fixes-tags --category static -- python3 "$S/check_fixes.py" "$RANGE" >/dev/null
}
jget() { # python expression over the last mlk next payload p
	python3 -c "import json,sys; p=json.load(open(sys.argv[1])); print($1)" "$LOGS/next.json"
}

echo "== cloning $kernel into $SRC (shared objects, own git dir)"
base="$(git -C "$kernel" rev-parse HEAD)"
git clone -q --shared --no-checkout "$kernel" "$SRC" || exit 2
cd "$SRC" || exit 2
git config core.hooksPath /dev/null
git config commit.gpgsign false
git config user.name "MLK Smoke"
git config user.email "smoke@example.invalid"
git config sendemail.smtpserver /bin/false
git config core.abbrev 12                       # references/setup.md
git config pretty.fixes 'Fixes: %h ("%s")'      # references/setup.md
git checkout -q -b smoke-base "$base" || exit 2

# 1. A new b4 series, and the base taken as references/develop.md says.
check "b4 prep -n" "0" "$(rc b4 prep -n mlk-smoke -f smoke-base)"
check "b4 prep --set-prefixes net-next" "0" "$(rc b4 prep --set-prefixes net-next)"
if b4 prep --show-info base-commit >>"$LOGS/commands.log" 2>&1; then
	echo "note: this b4 answers --show-info base-commit on an empty series"
fi
BASE="$(git merge-base HEAD smoke-base)"
check "BASE from git merge-base" "$base" "$BASE"
check "empty range start refused" "2" "$(rc py commit_msg_lint.py ..HEAD)"
check "evidence init" "0" "$(rc E init --base "$BASE")"
check "evidence require netdev" "0" "$(rc E require --preset netdev)"
check "signoff snapshot" "0" "$(rc py signoff_guard.py snapshot "$BASE..HEAD")"
next_json
check "mlk next: no patches is find-work" "find-work" "$(jget 'p["phase"]')"

# 2. Two patches; the first is a fix and needs the discovery statement.
F=net/core/sock.c
fixes="$(git log -1 --pretty=fixes "$(git log -1 --format=%H "$BASE" -- "$F")")"
sed -i '0,/^#include/s//\/* mlk smoke: patch 1 *\/\n#include/' "$F"
git commit -q -a -F - <<EOF
net: core: smoke test fix

Throwaway commit in a smoke-test clone, with a Fixes: tag so that the
netdev bug-fix rules apply.

$fixes
EOF
check "lint flags missing discovery statement" "yes" \
	"$(yes_no sh -c "python3 '$S/commit_msg_lint.py' --netdev \"\$(b4 prep --show-info series-range)\" 2>&1 | grep -q NETDEV_DISCOVERY_MISSING")"
git commit -q --amend -F - <<EOF
net: core: smoke test fix

Throwaway commit in a smoke-test clone, with a Fixes: tag so that the
netdev bug-fix rules apply.

Found by code inspection, not triggered. Not tested.

$fixes
EOF
printf '/* mlk smoke: patch 2 */\n' >>"$F"
git commit -q -a -F - <<'EOF'
net: core: smoke test comment

Second throwaway commit, so the series has two patches.
EOF
RANGE="$(b4 prep --show-info series-range)"
check "series-range has the 2 patches" "2" "$(git rev-list --count "$RANGE")"
check "lint passes with the statement" "0" "$(rc py commit_msg_lint.py --netdev "$RANGE")"
rc E require --preset netdev >/dev/null   # adds fixes-tags now that Fixes: exists

# 3. Validation (builds are smoke.sh's job) and the notes.
for n in build-objs allmodconfig-W1 allyesconfig-W1 new-warnings-allmodconfig \
	new-warnings-allyesconfig; do
	rc E skip --name "$n" --category build --reason "smoke-b4: builds are checked by smoke.sh" >/dev/null
done
check "ev checkpatch (before sign-off)" "0" "$(rc E run --name checkpatch --category style -- \
	python3 "$S/checkpatch_summary.py" --run "$RANGE")"
check "ev commit-msg-lint" "0" "$(rc E run --name commit-msg-lint --category style -- \
	python3 "$S/commit_msg_lint.py" --netdev "$RANGE")"
check "ev fixes-tags" "0" "$(rc E run --name fixes-tags --category static -- \
	python3 "$S/check_fixes.py" "$RANGE")"
for key in policy security_screen comprehension disclosure; do
	rc E note --key "$key" --value "smoke-b4 test run" >/dev/null
done

# 4. Cover letter and recipients: they rewrite every commit of the series.
b4 prep --show-cover >"$WORK/cover.txt" 2>>"$LOGS/commands.log"
python3 - "$WORK/cover.txt" <<'PY'
import re, sys
p = sys.argv[1]
t = open(p, encoding="utf-8").read()
t = re.sub(r"^EDITME: cover title.*$", "net: core: smoke test series", t, count=1, flags=re.M)
t = t.replace("EDITME: describe the purpose", "Two throwaway commits. Describe the purpose")
open(p, "w", encoding="utf-8").write(t)
PY
check "b4 prep --cover-from-file" "0" "$(rc b4 prep --cover-from-file "$WORK/cover.txt")"
check "cover title changed" "net: core: smoke test series" \
	"$(b4 prep --show-cover 2>/dev/null | head -1)"
check "b4 prep --auto-to-cc" "0" "$(rc b4 prep --auto-to-cc)"
check "signoff_guard clean after cover edits" "0" "$(rc py signoff_guard.py check "$BASE..HEAD")"
old_head_range="$RANGE"
RANGE="$(b4 prep --show-info series-range)"
cover="$(b4 prep --show-info start-commit)"
next_json
check "mlk next: rewrite makes lint stale" "stale" \
	"$(jget '{i["id"]: i["state"] for i in p["blocking"]}.get("commit-msg-lint")')"
lint_cmd="$(jget 'next(i["next"]["cmd"] for i in p["blocking"] if i["id"] == "commit-msg-lint")')"
old_tip="${old_head_range##*..}"
check "re-run cmd uses the current range" "yes" \
	"$(case "$lint_cmd" in *"${cover:0:12}..HEAD"*) echo yes ;; *) echo no ;; esac)"
check "re-run cmd drops the recorded shas" "no" \
	"$(case "$lint_cmd" in *"${old_tip:0:12}"*) echo yes ;; *) echo no ;; esac)"
rerun_commit_checks

# 5. The human's sign-off, from the cover commit as mlk.py says.
next_json
check "mlk next: submit asks for the sign-off" "submit" "$(jget 'p["phase"]')"
check "sign-off starts at b4 start-commit" \
	'git rebase --signoff "$(b4 prep --show-info start-commit)"' \
	"$(jget 'next(i["next"]["cmd"] for i in p["blocking"] if i["id"] == "signoff")')"
check "sign-off is the human's" "human" \
	"$(jget 'next(i["next"]["who"] for i in p["blocking"] if i["id"] == "signoff")')"
check "human signs off (simulated)" "0" "$(rc git rebase -q --signoff "$cover")"
check "b4 still reads its tracking data" "0" "$(rc b4 prep --show-info)"
check "signoff_guard sees the new sign-offs" "1" "$(rc py signoff_guard.py check "$BASE..HEAD")"
rc py signoff_guard.py snapshot "$BASE..HEAD" >/dev/null   # the human confirmed them
RANGE="$(b4 prep --show-info series-range)"
check "ev checkpatch after sign-off" "0" "$(rc E run --name checkpatch --category style -- \
	python3 "$S/checkpatch_summary.py" --run "$RANGE")"
rerun_commit_checks

# 6. Preflight on the agent's export, then ready.
check "b4 prep -p" "0" "$(rc b4 prep -p "$WORK/patches")"
check "b4 prep -p wrote cover + 2 patches" "3" "$(find "$WORK/patches" -name '*.patch' | wc -l | tr -d ' ')"
check "ev preflight (prep -p)" "0" "$(rc E run --name preflight --category style -- \
	python3 "$S/preflight.py" "$WORK/patches" --netdev)"
check "evidence status" "0" "$(rc E status)"
next_json
check "mlk next: ready" "ready" "$(jget 'p["phase"]')"
check "ready hands over to the human" "human" "$(jget 'p["next"]["who"]')"

# 7. What the human runs: b4 send -o only writes files.
check "b4 send -o (writes files only)" "0" "$(rc b4 send -o "$WORK/outgoing" --no-sign </dev/null)"
check "preflight (send -o, maintainers)" "0" "$(rc py preflight.py "$WORK/outgoing" --netdev \
	--maintainers --repo "$SRC")"
check "mlk report" "0" "$(rc py mlk.py report --upstream smoke-base --out "$WORK/report.html")"
check "report has no script" "0" "$(grep -c '<script' "$WORK/report.html")"

# 8. Optional: real review threads from lore (read-only).
if [ "$lore" -gt 0 ]; then
	echo "== --lore $lore: importing recent netdev threads (read-only downloads)"
	python3 - "$lore" >"$LOGS/msgids.txt" 2>>"$LOGS/commands.log" <<'PY'
import re, sys, urllib.parse, urllib.request
q = urllib.parse.quote('s:"PATCH net-next v2" AND d:21.days.ago..')
req = urllib.request.Request(f"https://lore.kernel.org/netdev/?q={q}&x=A",
                             headers={"User-Agent": "mlk-smoke-b4"})
xml = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
seen = []
for m in re.findall(r'href="https://lore\.kernel\.org/netdev/([^"/]+)/"', xml):
    m = urllib.parse.unquote(m)
    if m not in seen:
        seen.append(m)
print("\n".join(seen))
PY
	n=0
	roots=" "
	while read -r mid && [ "$n" -lt "$lore" ]; do
		d="$WORK/threads/$n"
		mkdir -p "$d"
		b4 -q mbox -o "$d" "$mid" >>"$LOGS/commands.log" 2>&1 || { rm -rf "$d"; continue; }
		mb="$(find "$d" -name '*.mbx' | head -1)"
		root="$(grep -m1 -i '^Message-ID:' "$mb" 2>/dev/null)"
		case "$roots" in *" $root "*) rm -rf "$d"; continue ;; esac
		roots="$roots$root "
		got="$(rc py review_tracker.py import "$mb" --series "lore-$n")"
		check "review_tracker import thread $n" "0" "$got"
		py review_tracker.py status --series "lore-$n" --netdev 2>&1 | sed 's/^/    /' | head -20
		n=$((n + 1))
	done <"$LOGS/msgids.txt"
	[ "$n" -gt 0 ] || echo "note: no thread downloaded (network?)"
fi

echo
echo "check | expected | got | ok"
for r in "${results[@]}"; do
	echo "$r" | sed 's/|/ | /g'
done
echo "logs: $LOGS$([ "$keep" -eq 1 ] || echo ' (removed on exit; use --keep to look)')"
exit "$failed"
