<!-- SPDX-License-Identifier: GPL-2.0 -->

# Deferred work: workqueues and timers

Some work cannot be done where the need appears. An IRQ handler or a NAPI
poll may not sleep, but resetting a device or talking to firmware does. A
watchdog or a statistics refresh must run later, or every second. The
kernel offers two tools: workqueues (run a function soon, in a context you
choose) and timers (run a function at a given time).

Both create a new path into your code that runs concurrently with
everything else. The bugs are almost always at teardown: the work or timer
still runs, or runs again, after the memory it uses is freed. Which code
queues the work, and in which order a driver stops it, is a contract of
that driver: read it out of the tree of the revision you work on.

Last checked against docs.kernel.org (7.3) in 2026-10. If the tree says
otherwise, the tree is right. Read `contexts.md` first.

## Workqueues

From `Documentation/core-api/workqueue.rst`:

- A work item "holds a pointer to the function that is to be executed
  asynchronously". You initialise it (`INIT_WORK` for a plain work item;
  delayed work has its own initialiser) and queue it on a workqueue.
- Workqueues are served by worker pools: per CPU, one normal and one high
  priority, plus unbound pools not tied to a CPU.
- "A work item can be executed in either a thread or the BH (softirq)
  context." In a normal workqueue it runs in a kernel thread, in process
  context, and may sleep. In a `WQ_BH` workqueue, "BH work items cannot
  sleep".

Flags the document describes, passed when a workqueue is allocated:

| Flag | Meaning |
|---|---|
| `WQ_BH` | items run in softirq context; no sleeping |
| `WQ_UNBOUND` | served by special worker pools, not bound to a CPU |
| `WQ_MEM_RECLAIM` | "All wq which might be used in the memory reclaim paths MUST have this flag set" |
| `WQ_HIGHPRI` | served by the high priority pool |
| `WQ_CPU_INTENSIVE` | items that use a lot of CPU do not hold back others |

`max_active` limits how many items of one workqueue can run at once (the
document explains how this applies to bound and unbound workqueues). It
recommends 0, which selects the default of 1024; the maximum is 2048. If
items must run one at a time in queue order, use
`alloc_ordered_workqueue()`: a normal workqueue does not guarantee order.

The system workqueues are shared by everyone. The document warns that a
heavy user "may saturate" them "and potentially lead to deadlock"; such
users should allocate their own workqueue. The queue and allocation
functions, with their kernel-doc, are in `include/linux/workqueue.h` of
your tree.

## Waiting for and cancelling work

- `flush_work()` waits until a work item has finished.
- `cancel_work_sync()` removes a pending item and waits for a running
  one; `cancel_delayed_work_sync()` does the same for delayed work.
- These wait, so for non-BH workqueues they need a context that may sleep.

Waiting is a lock dependency. If you hold a lock while waiting for a work
item, and the work handler takes the same lock, both wait forever. The
classic netdev case is waiting under RTNL for work that takes RTNL
(`locking-rcu.md`).

## Timers

- **`struct timer_list`**, the ordinary timer. Its callback runs in
  softirq context, so it must not sleep (`contexts.md`). The functions to
  set up, modify, delete and shut down a timer were renamed in recent
  releases. Read `include/linux/timer.h` in your tree for the current
  names and their comments; do not copy them from old tutorials or old
  drivers.
- **hrtimers**, from `Documentation/timers/hrtimers.rst`: high resolution
  timers work at 64-bit nanosecond resolution with `ktime_t` and are kept
  in a red-black tree. The document describes `hrtimer_try_to_cancel()`
  and `hrtimer_cancel()` as "roughly equivalent to timer_delete() and
  timer_delete_sync()". As with `timer_list`, check
  `include/linux/hrtimer.h` in your tree for the current API.

The difference between a plain delete and its `_sync` form matters at
teardown: only the synchronous one waits for a callback that is running on
another CPU.

## A netdev code path to read

Many NIC drivers have a "service task", a reset task scheduled from an
error path, or a periodic statistics or link-check work item. Find them in
the driver you change, or in `drivers/net/netdevsim/`:

```
git grep -nE 'INIT_(DELAYED_)?WORK|alloc_ordered_workqueue|timer_list' drivers/net/<driver>/
```

For each one, answer from the code:
1. **Who queues it?** An IRQ handler, the NAPI poll, a timer, an ethtool
   or netlink handler, the work handler itself (self-requeueing)?
2. **What does it touch?** Rings, the private struct, the `net_device`?
   Which locks does it take? Does it take RTNL?
3. **How is it stopped?** Find it in `ndo_stop` and in the remove path.
   In which order does the driver stop the sources that queue it, cancel
   it, and free what it uses?

The safe order, in general terms: first stop everything that can queue or
re-arm (disable the IRQ and NAPI, tell the handler not to requeue, shut
the timer down), then cancel synchronously, then free. A work item that
requeues itself, or a timer whose callback re-arms it, can come back
after a single cancel if its source is still active. Check how your tree
handles this for the specific driver; do not assume.

## The rule to respect

- Know the context the handler runs in: process context for normal
  workqueues, softirq for `WQ_BH` items and timer callbacks.
- Everything the handler uses must outlive it. If it uses an object, the
  object holds a reference or teardown waits for the handler
  (`lifetime-refcount.md`).
- At teardown: stop the sources, cancel with the `_sync` form, then free.
- Never wait for work while holding a lock the work takes.
- Heavy or reclaim-path users get their own workqueue with the right
  flags.

## Typical mistakes and the bug they produce

| Mistake | Result |
|---|---|
| free the private struct, then cancel the work | use-after-free in the handler |
| plain timer delete instead of the synchronous form at teardown | callback still running on another CPU: use-after-free |
| cancel once, but the handler or the IRQ requeues it | work runs after teardown: use-after-free |
| `cancel_work_sync()` under RTNL for work that takes RTNL | hang at `ip link set down` or module removal |
| sleeping in a timer callback or a `WQ_BH` item | "sleeping function called from invalid context" |
| assuming a normal workqueue runs items in order | rare races between two items |
| long or blocking work on a system workqueue | delays other users; possible deadlock |
| old timer function names copied from a tutorial | build failure, or a wrong function in your tree |

## Comprehension exercise

For each work item or timer the patch adds or changes, ask the human:
1. In which context does the handler run, and may it sleep there?
2. Who queues or arms it, and can that happen during teardown?
3. Show the teardown order in the tree: stop sources, cancel, free.
4. Does any path wait for this work while holding a lock the work takes?

## Read in the tree

`Documentation/core-api/workqueue.rst`,
`Documentation/timers/` (including `hrtimers.rst`),
`include/linux/workqueue.h`, `include/linux/timer.h`,
`include/linux/hrtimer.h`, and the teardown functions of the driver you
change. Related: `contexts.md`, `locking-rcu.md`, `lifetime-refcount.md`.
