<!-- SPDX-License-Identifier: GPL-2.0 -->

# Setup

Goal: a Linux environment that can build, check and boot a kernel, a clone
of the right tree, the human's git identity, b4, and a sending setup the
human has configured themselves. Confirm with the human before every
install, and show the exact command first.

## 1. Prerequisite check

```
python3 <skill-dir>/scripts/env_check.py --json --path <where the tree will live>
```

| Code | Action |
|---|---|
| `NOT_LINUX` | Stop. Point the human to the README prerequisite: on Windows, WSL2 with the agent installed and logged in *inside* WSL2; on macOS, a Linux VM. Do not try to bootstrap from Windows. |
| `MISSING_REQUIRED` | Offer the package commands below. Nothing builds without these. |
| `MISSING_RECOMMENDED` | Offer to install; explain what each one is for (clang/lld: second compiler; sparse, spatch: static checks; b4: series workflow; vng, qemu: boot tests; pahole: BTF). |
| `NO_KVM` | virtme-ng will be very slow or unusable. Runtime tests may have to be recorded as skipped. On WSL2, nested virtualization must be available; check `ls -l /dev/kvm` and the WSL documentation. |
| `LOW_DISK` | allmodconfig/allyesconfig builds need tens of GB. Free space or limit validation and record the skips. |
| `LOW_MEM` | Lower `-j`; large builds may fail or swap. |

On WSL2, keep the kernel tree in the Linux filesystem (e.g. `~/src/linux`),
not under `/mnt/c`: the Windows filesystem is slow from WSL2 and
case-insensitive, and the kernel tree has file names that differ only in
case.

## 2. Packages

Show the command, get a yes for that exact command, then run it (or let the
human run it). Package names drift between releases; if one is not found,
search (`apt-cache search`, `dnf search`, `pacman -Ss`) rather than guessing.

Debian / Ubuntu:
```
sudo apt install build-essential flex bison bc libelf-dev libssl-dev \
  libncurses-dev dwarves perl python3 git clang lld llvm sparse \
  coccinelle qemu-system-x86 pipx
```

Fedora:
```
sudo dnf install gcc make flex bison bc elfutils-libelf-devel openssl-devel \
  ncurses-devel dwarves perl python3 git clang lld llvm sparse \
  coccinelle qemu-system-x86 pipx
```

Arch:
```
sudo pacman -S base-devel bc cpio libelf pahole perl python git clang lld \
  llvm sparse qemu-system-x86 python-pipx
```
On Arch, check whether `coccinelle` is in the official repos or only in the
AUR before suggesting it.

b4 and virtme-ng move faster than distro packages. Prefer:
```
pipx install b4
pipx install virtme-ng
```
then `b4 --version` and `vng --version`. `b4 prep --check` needs b4 0.14 or
later; see the b4 docs for newer features. virtme-ng has its own runtime
dependencies (see its README).

smatch is best effort: it is built from source (repo.or.cz/smatch.git);
follow its own documentation. Record it as skipped if unavailable.

Re-run `env_check.py` after installing.

## 3. Clone the tree

The target tree is the subsystem's tree, not Linus' tree and not
linux-next. Find it in MAINTAINERS (`T:` lines). For netdev, one clone with
both trees as remotes:

```
git clone https://git.kernel.org/pub/scm/linux/kernel/git/netdev/net-next.git ~/src/linux
cd ~/src/linux
git remote rename origin net-next
git remote add net https://git.kernel.org/pub/scm/linux/kernel/git/netdev/net.git
git fetch net
```

Both netdev trees use `main` as their branch (`net-next/main`, `net/main`).
The clone is several GB; tell the human before starting.

Read-only network commands (clone, fetch, `b4 mbox`, `b4 trailers -u`,
lore and patchwork lookups) are fine after telling the human what you are
fetching. Commands that authenticate or send are the human's (section 6).

Then run `policy_snapshot.py` (see SKILL.md, "Follow the tree").

## 4. The human's git identity

Ask the human for the name and email they will use upstream. Never invent,
guess or copy an identity. The DCO requires a known identity ("sorry, no
anonymous contributions", `submitting-patches.rst`); the email must be one
they will read, because review arrives there.

Set it in the kernel repo (or check the global one they already have):
```
git config user.name  "<Name the human gave>"
git config user.email "<email the human gave>"
git config core.abbrev 12
git config pretty.fixes 'Fixes: %h ("%s")'
```
The last two come from `submitting-patches.rst` ("Describe your changes")
and make `git log -1 --pretty=fixes <sha>` print a correct `Fixes:` line.

## 5. b4 in one paragraph

b4 is the kernel.org tool for the contributor workflow. `b4 prep` keeps the
series on a `b4/<name>` branch with a cover letter in an empty commit and
tracks versions; `b4 prep --auto-to-cc` collects recipients from
`get_maintainer.pl`; `b4 prep --check` runs checkpatch; `b4 send` formats,
signs (patatt) and sends the series, then bumps the version; `b4 trailers -u`
picks up `Reviewed-by`/`Acked-by` replies from lore. Docs:
https://b4.docs.kernel.org (contributor section). Series creation is covered
in `references/develop.md`.

## 6. Sending setup (the human runs it)

Explain the options, let the human choose, and let them type every command
that contacts a server or handles credentials. You may inspect the
configuration to check it, but mask secrets:

```
git config --get-regexp '^(sendemail|b4|patatt)\.' | sed -E 's/(smtppass) .*/\1 ***/I'
```

**Option A: own SMTP server.** b4 uses the `sendemail.*` git config if a
`sendemail` section exists (`smtpServer`, `smtpServerPort`,
`smtpEncryption`, `smtpUser`, or `sendmailCmd` for msmtp and similar). Good
if the provider offers SMTP and does not rewrite bodies (company
disclaimers break patches).

**Option B: kernel.org web submission endpoint.** For providers without
usable SMTP. Steps, from the b4 docs ("contributor/send"):
1. An attestation key: an existing PGP key configured as `user.signingKey`,
   or a new ed25519 key with `patatt genkey`, then add the printed
   `[patatt]` section to git config.
2. `git config --global b4.send-endpoint-web https://lkml.kernel.org/_b4_submit`
3. `b4 send --web-auth-new`, wait for the challenge email, then
   `b4 send --web-auth-verify <challenge>`.

Even with option B the human needs a working mail account and a client that
sends plain text without mangling, for review replies
(`Documentation/process/email-clients.rst`).

The first real send is in `references/submit.md`. Do not test the setup by
sending anything yourself.

## 7. Optional tools

Ask before installing; explain that the reviewers send code to an LLM
provider (details in `references/external-review.md`).

- **review-prompts** (Chris Mason): `git clone https://github.com/masoncl/review-prompts`.
  Read `setup.sh` and the `agents/<agent>.sh` file before running it; then
  `./setup.sh claude kernel` (installs into `~/.claude/skills/kernel` and
  `~/.claude/commands`) or `./setup.sh codex kernel` (`~/.codex/skills/kernel`,
  `~/.codex/prompts`).
- **Sashiko**: needs Rust (see its README for the minimum version);
  `cargo install sashiko`, then `sashiko init`. Keep the config in
  `~/.config/sashiko.toml` rather than a `Settings.toml` inside the kernel
  tree. For subscription backends without API keys (Claude Code CLI, Codex
  CLI, GitHub Copilot CLI, Devin CLI) follow `docs/llm-providers.md` in the
  Sashiko repo. If it hits rate limits, lower `review.concurrency`.
- **semcode**: semantic code search used by review-prompts. Build per its
  README (`cargo install --path .` from a clone); index with
  `semcode-index -s .` in the tree.

## 8. State the limits

Before work starts, tell the human what this environment cannot prove, and
plan to record those as skips, not passes:

- hardware the human does not have (a physical NIC driver is not exercised
  by a virtme-ng boot; netdevsim covers only core configuration paths);
- other architectures (cross builds can catch compile errors, not runtime);
- no KVM: runtime tests may be impractical;
- low disk or memory: full allmodconfig/allyesconfig builds may not fit.

## Checklist

- [ ] `env_check.py` has no errors; warnings explained to the human
- [ ] target tree cloned, remotes named, `policy_snapshot.py` run
- [ ] human's identity set by the human's own values; `pretty.fixes` set
- [ ] b4 installed and recent enough
- [ ] sending option chosen and configured by the human (or deferred)
- [ ] optional reviewers installed or explicitly declined
- [ ] limits stated
