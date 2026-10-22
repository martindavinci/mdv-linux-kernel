<!-- SPDX-License-Identifier: GPL-2.0 -->

# Debugging a kernel bug

From a crash report to a fix you can defend: read the report, reproduce
it, shrink the reproducer, find the commit that introduced the bug, and
write the test that proves the fix. Commands are examples: check each
tool's `--help` and the in-tree docs named here, in the checkout you work
on.

In-tree reading: `Documentation/admin-guide/bug-hunting.rst`,
`Documentation/admin-guide/bug-bisect.rst`,
`Documentation/dev-tools/kasan.rst`, `Documentation/locking/lockdep-design.rst`,
`Documentation/dev-tools/kselftest.rst`.

## 1. Read the report before touching code

Teach the human to read it with you, top to bottom:

| Part | What it tells you |
|---|---|
| First line (`BUG: KASAN: slab-use-after-free in foo_xmit+0x1a/0x80`, `WARNING: CPU: 2 PID: 51 at net/core/dev.c:...`, `general protection fault`) | the kind of bug and where it was detected (not always where it was caused) |
| `Tainted:` flags | whether the kernel was already in a strange state (out-of-tree module, earlier warning) |
| `RIP:` / `pc :` line | the instruction that faulted, as `function+offset/size` |
| `Call Trace:` | who called it, innermost first; `?` entries are guesses, ignore them at first |
| KASAN `Allocated by task` / `Freed by task` | the two other stacks of a use-after-free: often the real story |
| lockdep chain (`-> #1 (...)`) | the order in which locks were taken on each path |

Decode addresses into file:line with the matching build (same commit,
same config):

```
./scripts/decode_stacktrace.sh $KB/vmlinux < oops.txt
./scripts/faddr2line $KB/vmlinux foo_xmit+0x1a/0x80
```

Both need debug info (`CONFIG_DEBUG_INFO_*`). If the report comes from a
bot, use the bot's config and commit, not yours.

Ask the human: "In your own words, what happened, and in which order?"
before proposing a fix. A fix for the line in `RIP:` is often wrong: the
bug is where the object was freed too early, or the lock not taken.

## 2. Reproduce it

- **syzbot**: the report page has the kernel commit, the `.config`, a C
  reproducer (or a syz program) and the console log. Build that commit
  with that config in a separate build directory and run the reproducer in
  virtme-ng (`references/validate.md`, runtime tests). syzbot can also
  test a patch for you, but that is an email command: the human sends it,
  never the agent.
- **Your own crash**: keep the exact steps as a script from the start.
  Run it twice; a bug that reproduces one time in ten needs a loop and a
  count, and the commit message must say how often it failed before and
  after.
- **No reproducer at all**: say so. `coding-assistants.rst` asks you to
  prove the bug or explain why you cannot. A fix for a bug nobody can
  trigger ("this pointer could be NULL") needs a reason why it can happen;
  maintainers reject theoretical fixes, especially ones that read like a
  tool's guess (see `references/slop-gate.md`).

Record the runs with `evidence.py` (`repro-before` on the base kernel,
`repro-after` on the patched one).

## 3. Shrink the reproducer

A small reproducer is the best explanation of a bug. Remove one thing at
a time and re-run after every removal; keep it only if the bug still
shows:

1. drop syscalls, threads, sleeps and options that are not needed;
2. replace random values by the one that triggers it;
3. turn a C reproducer into a few shell commands (`ip`, `tc`, `ss`,
   a tiny program) when possible: those can become a selftest.

Stop when every line is needed. The human should be able to explain each
remaining line.

## 4. Find the commit that introduced the bug

You need it for the `Fixes:` tag and to choose the tree. In order of cost:

```
git log -L :foo_xmit:drivers/net/foo/foo_main.c     # history of one function
git log -S 'foo_ring_free' --oneline -- drivers/net/foo/   # commits adding/removing a string
git blame -L <start>,<end> drivers/net/foo/foo_main.c
```

Read the candidate commit and ask: did the bug exist before it? If you
cannot tell by reading, bisect with the reproducer. A script that builds
and boots the kernel under test gives `git bisect run` an answer per
commit (exit 0 good, 1 bad, 125 skip):

```
BW=$HOME/src/mlk-wt/bisect
git worktree add --detach "$BW" <bad>
cp <the bug's .config> "$BW/.config"             # same config as the report
cat > "$SERIES/bisect.sh" <<'EOF'
#!/bin/sh
make -s olddefconfig >/dev/null || exit 125
vng --build >/dev/null 2>&1 || exit 125          # kernel in this worktree
vng --run . --user root -- sh -c '<repro>; ! dmesg | grep -q "<splat pattern>"'
EOF
chmod +x "$SERIES/bisect.sh"
cd "$BW"
git bisect start <bad> <good>
git bisect run "$SERIES/bisect.sh"
git bisect reset
cd -
```

Always in a separate worktree, never in the series branch: bisect checks
out other commits. Each step is a full kernel build, so ask the human
before starting and add `--timeout` to anything that can hang. Check the
result by reading the commit: bisect finds where the *symptom* starts,
which can be a commit that only made an older bug visible. Then write the
tag with `git log -1 --pretty=fixes <sha>` and verify it with
`check_fixes.py`.

The culprit also decides the tree: `git merge-base --is-ancestor <sha>
net/main` false means the code exists only in `net-next`, so the fix
targets `net-next`. Read what `maintainer-netdev.rst` says about `Fixes:`
in that case; maintainers have asked to drop the tag when the bug was only
latent.

## 5. Write the test that proves the fix

Prefer a test that fails before the fix and passes after it:

- **kselftest** (`tools/testing/selftests/net/`, `drivers/net/`): a shell
  script using the directory's `lib.sh` helpers, listed in the
  `Makefile` (`TEST_PROGS`), with the options it needs in the directory's
  `config` file. Exit codes follow `kselftest.rst` (pass, fail, skip).
  Copy the structure of a neighbouring test rather than inventing one.
- **KUnit** for logic that can run without a network (parsers, helpers):
  next to the code, with a `.kunitconfig`.
- Show both runs in the evidence store: the test on the base kernel (it
  must fail; record it as `repro`, asserting the failure) and on the
  patched kernel (`test`).

The test usually goes in the same series, as its own patch after the fix.
If no test is possible, say why in the cover letter.

## 6. Explain it in the commit message

The changelog tells the story in the order the human found it: what goes
wrong (the user-visible effect), why (the sequence of events, the object
or lock involved), how the fix changes that, and how it was tested. Keep
the decoded trace short (the lines that matter). Name the tool that found
the bug (`generated-content.rst`): syzbot's `Reported-by:`, "found with
KASAN", "found during an LLM-assisted review".

## Done when

- [ ] the human can explain the bug in their own words, including the
      sequence of events
- [ ] reproduced before, not reproduced after (or explained why there is
      no reproducer)
- [ ] culprit commit found and read; `Fixes:` verified; tree chosen
- [ ] a regression test exists, or the reason it does not is written down
