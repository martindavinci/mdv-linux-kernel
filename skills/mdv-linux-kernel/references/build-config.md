<!-- SPDX-License-Identifier: GPL-2.0 -->

# Proving the change was built and ran

"It builds" and "tests pass" mean nothing if the changed code was not in
the build or never ran. Before recording a pass, check each step below for
the code the patch touches. Commands are examples: confirm options in the
checkout's docs (`Documentation/kbuild/`, `Documentation/trace/`,
`Documentation/admin-guide/dynamic-debug-howto.rst`).

## 1. Enabled: the option is set in the build's `.config`

Find the option that compiles the file:

```
git grep -n 'foo_main.o' drivers/net/foo/Makefile     # obj-$(CONFIG_FOO) += ...
grep -n 'CONFIG_FOO\b' $KB/.config                    # =y, =m, or "is not set"
```

If it is not set, enable it and let Kconfig resolve dependencies, then
check it really stuck (a missing dependency silently turns it off again):

```
./scripts/config --file $KB/.config -e FOO
make O=$KB olddefconfig
grep -n 'CONFIG_FOO\b' $KB/.config
```

`make O=$KB menuconfig` and its search (`/`) show what an option depends
on. For selftests, the directory's `config` file lists what the tests
need; `vng --build --config <that file>` merges it.

Code inside `#ifdef CONFIG_BAR` or `IS_ENABLED(CONFIG_BAR)` needs that
option too. Check every conditional the patch touches.

## 2. Built: the object was really recompiled

```
make O=$KB W=1 C=1 drivers/net/foo/foo_main.o
ls -l --time-style=full-iso $KB/drivers/net/foo/foo_main.o
```

The object's time must be after your last edit. A build of the whole tree
that finishes "fine" while the object is missing means the option is off.
`allmodconfig` enables almost everything, which is why it is the netdev
default; it still skips code behind options that `allmodconfig` cannot
select (architecture-specific code, `depends on BROKEN`). For code built
only on another architecture, cross-compile (`ARCH=arm64
CROSS_COMPILE=...`, or `LLVM=1` with clang) and say which architectures
were built.

## 3. Loaded: the module is the one you built

When the code is a module:

```
modinfo $KB/drivers/net/foo/foo.ko | head
# inside the VM:
modprobe foo && lsmod | grep foo && cat /sys/module/foo/srcversion
```

Compare `srcversion` (or `vermagic`) with your build. A module loaded from
the host's `/lib/modules` is not your patch.

## 4. Executed: the changed lines ran during the test

Pick the lightest tool that answers "did my code run?":

- **ftrace** (no rebuild if the function is traceable):
  ```
  cd /sys/kernel/tracing
  grep -w foo_xmit available_filter_functions   # inlined or static code may be missing
  echo foo_xmit > set_ftrace_filter && echo function > current_tracer
  # run the test, then:
  cat trace | head; echo nop > current_tracer
  ```
- **dynamic debug**, if the code has `pr_debug()`/`netdev_dbg()`:
  `echo 'file drivers/net/foo/foo_main.c +p' > /proc/dynamic_debug/control`.
- **kprobes / bpftrace** for a line inside a function, or a counter of how
  often a path ran.
- A temporary `pr_info()` is fine while debugging; it must not stay in the
  patch.

Record the check as part of the test entry (the log shows the trace) or
as a note. In the commit message, a short "Tested with ..., the error path
was exercised by ..." is worth more than "tested".

## 5. Reproducing a bot's report

- **kernel test robot** (lkp) mails contain the tree, commit, config link,
  compiler and a "reproduce" section. Use that exact config and compiler
  in a separate build directory; a warning you cannot reproduce with your
  own config is still real.
- **netdev patchwork checks** (NIPA): each check links its log. The build
  checks compare against a baseline the same way `build_warnings.py` does;
  rebuild with the same options (`W=1`, `C=1`, clang via `LLVM=1` when
  the check is the clang one) and run `build_warnings.py diff`.
- **AI reviews** on netdev are reviews, not bots: answer each point
  (`references/revise.md`).

Fix the warning in the patch that introduced it, then re-run the same
check.

## 6. When you do not have the hardware

Say exactly what was and was not tested; never imply a device test:

- "Compile-tested only" (with the configs and architectures) is honest
  and sometimes acceptable for an obvious fix; maintainers often reject
  untested functional changes to drivers, especially for old hardware.
- **netdevsim** (`drivers/net/netdevsim/`) emulates many netdev and
  devlink paths; its selftests are under
  `tools/testing/selftests/drivers/net/netdevsim/`. A netdevsim test proves
  core code, not a real driver.
- Ask on the list or the driver's maintainers for testing; a `Tested-by:`
  is added only when someone sends it.
- If the bug can only be shown on hardware nobody has, reconsider whether
  the patch is worth sending (`references/find-work.md`).

## Done when

- [ ] every touched file is built in at least one recorded config
      (`.config` passed as `--input`)
- [ ] for runtime changes: the changed code ran during a recorded test, or
      it is stated why it could not
- [ ] what was not tested (hardware, architectures) is written down for
      the changelog
