#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
#
# Egress filter for the eval user: the sending boundary for eval sessions.
#
#   egress.sh apply --user NAME [--host HOST]...
#   egress.sh check --user NAME
#   egress.sh remove
#
# apply   drops, for processes of NAME only: TCP to the SMTP ports
#         (25, 465, 587, 2525) and every connection to the addresses of
#         lkml.kernel.org (the b4 web submission endpoint) and of each --host.
#         The rest of the network (the agent's API, git fetch) stays open.
#         Host addresses are resolved now: re-run apply if they change.
# check   verifies the SMTP rule for NAME is loaded and that root can open
#         smtp.gmail.com:587 and lkml.kernel.org:443 while NAME cannot; it
#         fails when the result is inconclusive (no network, DNS, tools).
# remove  deletes the rules.
#
# Run as root, outside the agent session. Needs nft, getent, runuser, timeout.
# Exit: 0 ok, 1 check failed, 2 usage error or not root.

set -euo pipefail

TABLE="mlk_eval"
SMTP_PORTS="25, 465, 587, 2525"
HOSTS=("lkml.kernel.org")

usage() {
	sed -n '4,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

cmd="${1:-}"
[ -n "$cmd" ] || { usage >&2; exit 2; }
shift
user=""
while [ $# -gt 0 ]; do
	case "$1" in
	--user) [ $# -ge 2 ] || { usage >&2; exit 2; }; user="$2"; shift 2 ;;
	--host) [ $# -ge 2 ] || { usage >&2; exit 2; }; HOSTS+=("$2"); shift 2 ;;
	-h|--help) usage; exit 0 ;;
	*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
	esac
done

case "$cmd" in
apply|check|remove) ;;
-h|--help) usage; exit 0 ;;
*) echo "unknown command: $cmd" >&2; usage >&2; exit 2 ;;
esac

[ "$(id -u)" -eq 0 ] || { echo "egress.sh must run as root" >&2; exit 2; }

if [ "$cmd" != remove ]; then
	[ -n "$user" ] || { echo "--user is required" >&2; exit 2; }
	uid="$(id -u "$user" 2>/dev/null)" || { echo "no such user: $user" >&2; exit 2; }
	[ "$uid" -ne 0 ] || { echo "refusing to filter root; use a dedicated eval user" >&2; exit 2; }
fi

addresses() {
	local family="$1" host
	for host in "${HOSTS[@]}"; do
		getent "$family" "$host" | awk '{print $1}'
	done | sort -u | paste -sd, -
}

case "$cmd" in
remove)
	nft delete table inet "$TABLE" 2>/dev/null || true
	echo "removed table inet $TABLE"
	;;
apply)
	v4="$(addresses ahostsv4)"
	v6="$(addresses ahostsv6 | tr ',' '\n' | grep ':' | paste -sd, - || true)"
	rules="meta skuid $uid tcp dport { $SMTP_PORTS } counter reject with tcp reset"
	[ -z "$v4" ] || rules+=$'\n'"    meta skuid $uid ip daddr { $v4 } counter reject"
	[ -z "$v6" ] || rules+=$'\n'"    meta skuid $uid ip6 daddr { $v6 } counter reject"
	nft delete table inet "$TABLE" 2>/dev/null || true
	nft -f - <<EOF
table inet $TABLE {
  chain out {
    type filter hook output priority 0; policy accept;
    $rules
  }
}
EOF
	echo "egress filter applied for $user (uid $uid): SMTP ports, ${HOSTS[*]}"
	[ -n "$v4$v6" ] || echo "warning: no address resolved for ${HOSTS[*]}" >&2
	;;
check)
	# Fails closed: "blocked" is only reported when root (unfiltered) can reach
	# the target and the eval user cannot. Anything inconclusive is a failure.
	for tool in nft runuser timeout getent; do
		command -v "$tool" >/dev/null 2>&1 || { echo "FAIL: missing tool $tool"; exit 1; }
	done
	rules="$(nft list table inet "$TABLE" 2>/dev/null)" || {
		echo "FAIL: table inet $TABLE not loaded (run: egress.sh apply --user $user)"
		exit 1
	}
	# nft may print the uid as a user name.
	if ! grep -Eq "skuid ($uid|$user) tcp dport \{ 25, 465, 587, 2525 \}" <<<"$rules"; then
		echo "FAIL: no SMTP rule for uid $uid in table inet $TABLE"
		exit 1
	fi
	status=0
	for target in smtp.gmail.com/587 lkml.kernel.org/443; do
		shown="${target/\//:}"
		if ! timeout 5 bash -c "exec 3<>/dev/tcp/$target" 2>/dev/null; then
			echo "FAIL: inconclusive, root cannot reach $shown either (DNS or network down?)"
			status=1
		elif runuser -u "$user" -- timeout 5 bash -c "exec 3<>/dev/tcp/$target" 2>/dev/null; then
			echo "FAIL: $user can connect to $shown"
			status=1
		elif ! runuser -u "$user" -- true 2>/dev/null; then
			echo "FAIL: inconclusive, cannot run commands as $user"
			status=1
		else
			echo "ok: root reaches $shown, $user cannot"
		fi
	done
	exit "$status"
	;;
esac
