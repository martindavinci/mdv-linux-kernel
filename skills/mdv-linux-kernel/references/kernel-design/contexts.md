<!-- SPDX-License-Identifier: GPL-2.0 -->

# Execution contexts: who runs your code, and may it sleep?

Many beginner networking bugs are context bugs: code that sleeps where it
must not, allocates with the wrong flags, or assumes it runs alone. Before
changing a function, the human should be able to say *in which context it
runs*. This page teaches how to find out; the contract itself always comes
from the tree you work on (docs of that revision, comments, callers).

Last checked against docs.kernel.org (7.3) in 2026-10. If the tree says
otherwise, the tree is right.

## The contexts

| Context | Typical networking code | May sleep? |
|---|---|---|
| Process context | syscalls (`setsockopt`, `ioctl`), netlink handlers, `ndo_open`/`ndo_stop`, workqueue items, threaded NAPI and busy-poll threads (but see NAPI below) | yes, unless something below is in effect |
| Softirq / BH | packet receive (`NET_RX`), transmit completion, timers, most NAPI polling | no |
| Hard interrupt | the device's IRQ handler: acknowledge, schedule NAPI, return | no |
| "Atomic" inside process context | while holding a spinlock, inside `rcu_read_lock()`, with BHs or preemption disabled | no |

"May not sleep" means: no `mutex_lock()`, no `msleep()`, no allocation that
can wait (`GFP_KERNEL`), no `copy_from_user()`, nothing that can block.
Doing it anyway gives "BUG: sleeping function called from invalid
context" (with `CONFIG_DEBUG_ATOMIC_SLEEP`), a lockdep report, or a rare
hang in production.

## NAPI: one poll function, several contexts

From `Documentation/networking/napi.rst`: NAPI processing "usually happens
in the software interrupt context", but it can also run in a per-instance
kernel thread (threaded NAPI, `napi/<ifname>-<id>`), from a user thread
that busy-polls a socket or calls `epoll_wait()`, and from a busy-polling
kthread. The poll method "may be called without the driver scheduling the
instance (unless the instance is disabled)".

So write the poll function for the strictest case, softirq: it must not
sleep, even if on your machine it happened to run in a thread.

Rules the same page states, which bugs often break:
- process at most `budget` Rx packets (Tx completions are not limited);
  with `budget == 0`, no Rx work at all, and page pool / XDP "cannot be
  used at all";
- return the work done; if work remains, return exactly `budget`;
- never call `napi_complete_done()` when `budget` is 0, and do not touch
  the instance's data after it;
- `napi_disable()` waits for ownership to be released, not for the poll
  method to exit, and calling it twice in a row hangs.

## Finding the context of a function

1. **The callback tables.** `Documentation/networking/netdevices.rst`
   lists, per `net_device_ops` callback, the lock held and the context.
   Examples from the 7.3 text: `ndo_start_xmit` runs under the
   `__netif_tx_lock` spinlock with BHs disabled (or in BH), and with
   interrupts disabled under netconsole; `ndo_get_stats64` is "atomic
   (can't sleep under RCU)"; `ndo_set_rx_mode` runs under a spinlock with
   BHs disabled; `ndo_open`/`ndo_stop` run in process context under
   `rtnl_lock()`. Read the table in your checkout before relying on any of
   these.
2. **The callers.** `git grep -n 'foo_reset('` and walk up: an IRQ
   handler, a timer callback, `napi_schedule()`, a workqueue item
   (`INIT_WORK`), a netlink or ethtool handler. A function called from
   both process and BH context must obey the stricter one.
3. **The annotations.** `might_sleep()` at the top of a function says it
   may sleep; `lockdep_assert_held()`, `ASSERT_RTNL()` and comments such
   as "Must be called with ... held" state the caller's obligations.
4. **Prove it at runtime**: run the test with `CONFIG_DEBUG_ATOMIC_SLEEP`,
   `CONFIG_PROVE_LOCKING` (see `kernel/configs/debug.config`); a sleeping
   call in the wrong context is reported even if it did not hang.

Do not "fix" a context problem by testing `in_interrupt()` or
`in_softirq()` and branching; maintainers reject that. Move the work to
the right context instead (a workqueue for things that sleep).

## Memory allocation flags follow the context

- `GFP_KERNEL`: may sleep; process context only.
- `GFP_ATOMIC` (or `GFP_NOWAIT`): never sleeps, may fail; in atomic
  context. Handle the failure (drop the packet, retry later). Not under a
  `raw_spinlock_t` or with hard interrupts really disabled on PREEMPT_RT,
  where no allocation is allowed (`Documentation/locking/locktypes.rst`).
- Networking helpers pick the flags for you in their context, for example
  skb allocation helpers meant for NAPI or for drivers; check their
  comments in the tree.

`Documentation/core-api/memory-allocation.rst` explains the flags.

## Typical mistakes and the bug they produce

| Mistake | Result |
|---|---|
| `mutex_lock()` or `GFP_KERNEL` in `ndo_get_stats64` or `ndo_start_xmit` | "sleeping function called from invalid context", possible deadlock |
| `msleep()` / waiting for firmware in the NAPI poll | the same, plus stalled Rx on the CPU |
| more than `budget` Rx packets, or Rx work with budget 0 | NAPI warnings, starvation, crashes with netpoll |
| touching the ring after `napi_complete_done()` | race with the next poll: use-after-free or corrupted ring |
| teardown frees rings before `napi_disable()` / IRQ free | use-after-free from a poll that still runs |

## Comprehension exercise

Pick the function the patch changes and ask the human:
1. Which context(s) can call it? Show the call path from the tree.
2. Can anything in it sleep? Point at the line if so.
3. If it allocates memory, why are those flags right in that context?
4. For a NAPI or teardown change: what stops the poll function from
   running while the memory it uses is freed?

## Read in the tree

`Documentation/networking/napi.rst`,
`Documentation/networking/netdevices.rst`,
`Documentation/core-api/memory-allocation.rst`,
`Documentation/kernel-hacking/hacking.rst` (contexts in general),
`Documentation/locking/` for what each lock implies
(`locking-rcu.md` next).
