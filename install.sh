#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
#
# Install the mdv-linux-kernel skill for the current user.
#
#   ./install.sh [--agent claude|codex|all] [--copy] [--force] [--uninstall] [--home DIR]
#
# claude -> <home>/.claude/skills/mdv-linux-kernel
# codex  -> <home>/.agents/skills/mdv-linux-kernel
#
# Default is a symlink to this checkout, so `git pull` updates the skill.
# Never installs into a kernel tree.
#
# Exit: 0 success, 1 refused (existing target not ours), 2 usage error.

set -euo pipefail

NAME="mdv-linux-kernel"
MARKER=".mdv-linux-kernel-install"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/skills/$NAME"

agent="all"
mode="link"
force=0
uninstall=0
home="${HOME:-}"

usage() {
	sed -n '4,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
	case "$1" in
	--agent) [ $# -ge 2 ] || { usage >&2; exit 2; }; agent="$2"; shift 2 ;;
	--copy) mode="copy"; shift ;;
	--force) force=1; shift ;;
	--uninstall) uninstall=1; shift ;;
	--home) [ $# -ge 2 ] || { usage >&2; exit 2; }; home="$2"; shift 2 ;;
	-h|--help) usage; exit 0 ;;
	*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
	esac
done

case "$agent" in
claude) targets=("$home/.claude/skills/$NAME") ;;
codex) targets=("$home/.agents/skills/$NAME") ;;
all) targets=("$home/.claude/skills/$NAME" "$home/.agents/skills/$NAME") ;;
*) echo "unknown agent: $agent (use claude, codex or all)" >&2; exit 2 ;;
esac

[ -n "$home" ] || { echo "HOME is not set; use --home" >&2; exit 2; }
[ -f "$SRC/SKILL.md" ] || { echo "skill source not found: $SRC" >&2; exit 2; }

# True if $1 is an install made by this script (symlink here, or marked copy).
is_ours() {
	local t="$1"
	if [ -L "$t" ]; then
		[ "$(cd "$t" 2>/dev/null && pwd -P)" = "$(cd "$SRC" && pwd -P)" ]
	else
		[ -f "$t/$MARKER" ]
	fi
}

copy_to() {
	local d="$1"
	mkdir -p "$d" &&
		cp -R "$SRC/." "$d/" &&
		# Test hook: simulate a copy failing half way.
		[ -z "${MDV_INSTALL_FAIL_COPY:-}" ] &&
		find "$d" -name __pycache__ -type d -prune -exec rm -rf {} + &&
		printf 'source=%s\n' "$SRC" >"$d/$MARKER" &&
		[ -f "$d/SKILL.md" ]
}

# Build the new install next to the target; print its path.
stage() {
	local s="$1.mdv-new.$$"
	rm -rf "$s"
	if [ "$mode" = "link" ]; then
		if ln -s "$SRC" "$s" 2>/dev/null && [ -L "$s" ]; then
			echo "$s"
			return 0
		fi
		# Some environments (e.g. Git Bash without native symlinks) copy
		# instead of linking; undo that and make a marked copy.
		rm -rf "$s"
		echo "symlinks unavailable here; copying instead" >&2
	fi
	if copy_to "$s"; then
		echo "$s"
		return 0
	fi
	rm -rf "$s"
	return 1
}

# Replace $1 with staged $2, restoring the old install on failure.
swap() {
	local t="$1" s="$2" old="$1.mdv-old.$$"
	if [ -e "$t" ] || [ -L "$t" ]; then
		mv "$t" "$old" || return 1
	fi
	if mv "$s" "$t"; then
		rm -rf "$old"
		return 0
	fi
	rm -rf "$s"
	if [ -e "$old" ] || [ -L "$old" ]; then
		mv "$old" "$t"
	fi
	return 1
}

status=0
for t in "${targets[@]}"; do
	if [ "$uninstall" -eq 1 ]; then
		if [ ! -e "$t" ] && [ ! -L "$t" ]; then
			continue
		elif is_ours "$t"; then
			rm -rf "$t"
			echo "removed $t"
		else
			echo "refusing to remove $t: not installed by this script" >&2
			status=1
		fi
		continue
	fi

	if { [ -e "$t" ] || [ -L "$t" ]; } && ! is_ours "$t" && [ "$force" -ne 1 ]; then
		echo "refusing to replace $t (exists, not ours); use --force" >&2
		status=1
		continue
	fi

	mkdir -p "$(dirname "$t")"
	if ! staged="$(stage "$t")"; then
		echo "failed to prepare $t; previous install (if any) left untouched" >&2
		status=1
		continue
	fi
	if ! swap "$t" "$staged"; then
		echo "failed to replace $t; previous install restored" >&2
		status=1
		continue
	fi
	if [ -L "$t" ]; then
		echo "linked $t -> $SRC"
	else
		echo "copied to $t"
	fi
done

exit "$status"
