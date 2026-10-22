<!-- SPDX-License-Identifier: GPL-2.0 -->

# Locking and RCU in networking code

Who may touch this object, while holding what, and when may it be freed?
Most use-after-free, deadlock and race fixes in netdev answer exactly this.
This page gives the vocabulary and how to read the rules out of the tree;
it does not replace them. The contract of a given structure lives in its
code, its comments and the docs of the revision you work on.

Last checked against docs.kernel.org (7.3) in 2026-10. Read
`execution contexts` first (`contexts.md`).

## Spinlocks: short, never sleep

- `spin_lock()` / `spin_unlock()`: mutual exclusion between CPUs; while
  held, no sleeping.
- `spin_lock_bh()`: also disables softirqs on this CPU. Needed when the
  same lock is taken from process context *and* from BH (NAPI, timers,
  Tx completion): otherwise a softirq can interrupt the holder on the same
  CPU and spin forever on a lock that CPU already holds.
- `spin_lock_irqsave()`: also disables hard interrupts; needed when an IRQ
  handler takes the lock.

Rule of thumb: every place that takes a lock must use the variant for the
most interrupting context that also takes it. lockdep reports a mismatch
as "inconsistent lock state" when the debug configs are on.

**PREEMPT_RT changes these meanings.** With `CONFIG_PREEMPT_RT`,
`spinlock_t` becomes a sleeping lock, `spin_lock_irqsave()` does not
disable hard interrupts, and only `raw_spinlock_t` keeps the strict
non-sleeping behaviour (where even `GFP_ATOMIC` allocations are not
allowed). Code that is correct on a normal build can be wrong on RT.
`Documentation/locking/locktypes.rst` describes the rules; read it before
touching interrupt-disabling or raw locks.

## Mutexes: may sleep, process context only

`mutex_lock()` can sleep, so it is never allowed in BH, IRQ, under a
spinlock or inside an RCU read section. Configuration paths (netlink,
ethtool, `ndo_open`) use mutexes; the data path (xmit, NAPI) does not.

## RTNL and the netdev instance lock

- **RTNL** (`rtnl_lock()`) is the big configuration lock of networking.
  `Documentation/networking/netdevices.rst` (7.3): "Most ndo callbacks
  not specified in the list above are running under rtnl_lock". Code that
  requires it says so with `ASSERT_RTNL()` or a lockdep assertion.
- **The netdev instance lock** protects one device. The same document:
  ndos run "without holding the instance lock for most drivers"; drivers
  that set `request_ops_lock`, implement queue management ops or the
  shaper API are "ops locked" and get most ndos under the instance lock.
  Its naming convention: "dev_xxx/netdev_xxx functions handle acquiring
  the instance lock themselves, while the netif_xxx functions assume that
  the driver has already acquired the instance lock" (for example
  `dev_set_mtu()` vs `netif_set_mtu()`). Find the lock helpers themselves
  with `git grep -n 'netdev_lock' include/`; they changed in recent
  releases.

Never take RTNL from the data path. A classic deadlock: under RTNL, wait
for a work item (`cancel_work_sync()`, `flush_workqueue()`) whose handler
itself takes RTNL.

## RCU: readers without locks, frees after a grace period

From `Documentation/RCU/whatisRCU.rst`, the core API: `rcu_read_lock()`,
`rcu_read_unlock()`, `synchronize_rcu()` / `call_rcu()`,
`rcu_assign_pointer()`, `rcu_dereference()`; and `kfree_rcu()` instead of
a hand-written `call_rcu()` callback that only frees.

- **Readers** take `rcu_read_lock()`, load the pointer with
  `rcu_dereference()` and use it until `rcu_read_unlock()`. "It is illegal
  to block while in an RCU read-side critical section."
- **Updaters** serialise among themselves with a normal lock, publish
  with `rcu_assign_pointer()` (or the `_rcu` list helpers), and free the
  old object only after a grace period: `synchronize_rcu()` (sleeps),
  `call_rcu()` or `kfree_rcu()`.
- Pointers meant for RCU are annotated `__rcu`; a build with `C=1` (sparse)
  reports plain accesses to them. Under the update-side lock, use
  `rcu_dereference_protected(p, lockdep_is_held(&lock))` and let lockdep
  check the claim.

Many networking lookups (devices, sockets, routes, neighbours) are RCU
readers in the data path; the objects they find are often also
refcounted. RCU keeps the memory alive inside the read section; a
reference keeps the object alive after it. Know which one your code
relies on.

## Reading the contract out of the tree

For a structure or field the patch touches:
1. Find its declaration and read the comments next to the lock and the
   fields ("protected by", "RCU", "under rtnl").
2. `git grep` every writer of the field: which lock do they hold?
3. Look for assertions: `lockdep_assert_held()`, `ASSERT_RTNL()`,
   `rcu_dereference_protected(..., lockdep_is_held(...))`, sparse
   `__must_hold()` / `__acquires()` annotations.
4. Check teardown: what makes sure no reader (NAPI, timer, work, RCU
   reader) still uses the object when it is freed?
5. Run the tests with `kernel/configs/debug.config` (lockdep, RCU
   checking, KASAN): a clean run with these on is evidence; without them
   it says little about locking.

## Typical mistakes and the bug they produce

| Mistake | Result |
|---|---|
| `spin_lock()` in process context on a lock the NAPI poll also takes | deadlock on one CPU (lockdep: inconsistent lock state) |
| `list_del_rcu()` then `kfree()` at once | use-after-free in a concurrent reader |
| `mutex_lock()` / `GFP_KERNEL` inside `rcu_read_lock()` | sleeping in an RCU read section |
| locks A then B on one path, B then A on another | ABBA deadlock (lockdep: possible circular locking dependency) |
| `cancel_work_sync()` under RTNL on work that takes RTNL | hang at device teardown |
| reading an `__rcu` pointer without `rcu_dereference()` | sparse warning, compiler may reload or tear the pointer |

## Comprehension exercise

For the data the patch touches, ask the human:
1. Which lock (or RCU) protects it, and where in the tree does it say so?
2. Which contexts take that lock, and is the `_bh`/`_irqsave` variant
   right everywhere?
3. When the object is freed, how is it guaranteed that nobody still uses
   it? Point at the grace period, the refcount drop or the
   `napi_disable()`/`cancel_work_sync()`.
4. Did a lockdep-enabled run exercise the changed path?

## Read in the tree

`Documentation/locking/` (spinlocks, mutex design, lockdep design),
`Documentation/RCU/whatisRCU.rst`, `Documentation/RCU/checklist.rst`,
`Documentation/networking/netdevices.rst`, and the comments of the
structures you change.
