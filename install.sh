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

install_copy() {
	local t="$1"
	mkdir -p "$t"
	cp -R "$SRC/." "$t/"
	find "$t" -name __pycache__ -type d -prune -exec rm -rf {} +
	printf 'source=%s\n' "$SRC" >"$t/$MARKER"
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

	if [ -e "$t" ] || [ -L "$t" ]; then
		if is_ours "$t" || [ "$force" -eq 1 ]; then
			rm -rf "$t"
		else
			echo "refusing to replace $t (exists, not ours); use --force" >&2
			status=1
			continue
		fi
	fi

	mkdir -p "$(dirname "$t")"
	if [ "$mode" = "link" ]; then
		if ln -s "$SRC" "$t" 2>/dev/null && [ -L "$t" ]; then
			echo "linked $t -> $SRC"
			continue
		fi
		# Some environments (e.g. Git Bash without native symlinks) copy
		# instead of linking; undo that and make a marked copy.
		rm -rf "$t"
		echo "symlinks unavailable here; copying instead" >&2
	fi
	install_copy "$t"
	echo "copied to $t"
done

exit "$status"
