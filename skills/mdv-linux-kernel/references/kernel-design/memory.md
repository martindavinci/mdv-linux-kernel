<!-- SPDX-License-Identifier: GPL-2.0 -->

# Memory allocation in networking code

Every allocation in the kernel answers two questions: which allocator, and
with which flags. The flags depend on the context the code runs in, and the
free function depends on the allocator. Getting either wrong gives a
sleeping-in-atomic warning, a leak, or memory corruption. This page teaches
the choices and how to check them; the detailed rules live in
`Documentation/core-api/memory-allocation.rst` of the tree you work on.

Last checked against docs.kernel.org (7.3) in 2026-10. If the tree says
otherwise, the tree is right. Read `contexts.md` first: the flags follow
the context.

## The default, and when it changes

The document starts with a default: "very likely you should use
`kzalloc(<size>, GFP_KERNEL);`". Change it only for a reason you can name.

- **`GFP_KERNEL`** "implies GFP_RECLAIM": the allocator may reclaim memory
  and wait, so "the calling context must be allowed to sleep". Process
  context only, outside spinlocks and RCU read sections.
- **Atomic context** (an interrupt handler, softirq, under a spinlock): the
  document says to "use GFP_NOWAIT", which "prevents direct reclaim and IO
  or filesystem operations". It can fail; the caller must handle that.
- **`GFP_ATOMIC`** is for the case where "accessing memory reserves is
  justified": a "non sleeping allocation with an expensive fallback". It
  dips into reserves other users may need, so reaching for it by reflex
  in every atomic path is a review comment waiting to happen.
- **`GFP_KERNEL_ACCOUNT`** for allocations that should be charged to the
  caller's memory cgroup. The document is strict here: allocations
  triggered by untrusted userspace "must have `__GFP_ACCOUNT` bit set".
  A netlink or `setsockopt` handler that allocates a size chosen by the
  user is the typical case.
- **`GFP_NOIO` / `GFP_NOFS`** have been replaced by scope APIs since 4.12;
  read the section of the document for the names in your tree.
- **`GFP_DMA` / `GFP_DMA32`**: avoid them unless the hardware really needs
  that zone. DMA mappings for devices go through the DMA API
  (`Documentation/core-api/` in your tree), not through these flags.

On PREEMPT_RT no allocation is allowed under a `raw_spinlock_t`, not even a
non-sleeping one (`Documentation/locking/locktypes.rst`, and
`locking-rcu.md`).

## Choosing the allocator, and the matching free

| Allocator | Memory | Free with |
|---|---|---|
| `kmalloc()`, `kzalloc()` | physically contiguous; good for objects smaller than a page | `kfree()` or `kvfree()` |
| `vmalloc()` | virtually contiguous, not physically contiguous | `vfree()` or `kvfree()` |
| `kvmalloc()` | tries kmalloc, then falls back to vmalloc; "may return memory that is not physically contiguous" | `kvfree()` |

For arrays and structures with a trailing array, use the helpers the
document lists instead of multiplying sizes by hand: `kmalloc_array()`,
`kcalloc()`, `krealloc()`, `struct_size()`, `array_size()`. An open-coded
`n * size` where `n` comes from userspace or firmware can overflow and
allocate a buffer smaller than the code believes.

`kvmalloc()` suits large tables whose size depends on configuration (for
example a table sized by the number of queues or entries a user asks for),
where a large physically contiguous block may not be available. It does not
suit memory a device reads by physical address.

## The packet's memory: struct sk_buff

From `Documentation/networking/skbuff.rst`: "struct sk_buff itself is a
metadata structure and does not hold any packet data". The data lives in
a separate head buffer, laid out as:

```
| headroom | data | tailroom | struct skb_shared_info |
                                  -> page frags (page, offset, length)
                                  -> optional frag_list of more skbs
```

`struct skb_shared_info` "holds an array of pointers to read-only data in
the (page, offset, length) format". So one packet can be a linear part plus
several page fragments, plus a chain of other skbs. Code that assumes all
the bytes are in the linear part reads garbage or past the buffer on
fragmented packets. The helpers that make bytes linear or safe to write are
in `include/linux/skbuff.h`; read their comments in your tree before using
one.

Who owns an skb and when it may be freed is a lifetime question:
`lifetime-refcount.md`.

## A netdev code path

Take a NIC driver, or `drivers/net/netdevsim/` if you have no hardware, and
find its allocations:

```
git grep -nE 'k[mz]alloc|kcalloc|kvmalloc|vmalloc' drivers/net/netdevsim/
```

Two places show the contrast:

1. **Bring-up** (`ndo_open`, probe, ring resize from ethtool). These run
   in process context (`contexts.md` gives the 7.3 table for `ndo_open`),
   so `GFP_KERNEL` with `kcalloc()` for the ring array is normal. A ring
   count set by the user is a size from userspace: check it is bounded and
   that the allocation helper protects the multiplication.
2. **The datapath refill** (the NAPI poll putting new Rx buffers on the
   ring). This runs in softirq context, so it must not sleep. Look at how
   the driver allocates there and what it does when the allocation fails:
   usually it leaves the slot empty and retries on the next poll, instead
   of dropping the ring or looping.

Verify each claim in your tree: open the function, find its callers, and
check the context as `contexts.md` describes. Drivers differ, and many
datapath allocations go through networking or page pool helpers that pick
the flags themselves.

## The rule to respect

- The flag matches the strictest context that can reach the call.
- The free matches the allocator, on every path, exactly once.
- Every allocation can fail; the failure path is part of the patch.
- Sizes from userspace or hardware are bounded and computed with the
  helpers; userspace-triggered allocations are accounted.
- Error paths free in reverse order of allocation.

## Typical mistakes and the bug they produce

| Mistake | Result |
|---|---|
| `GFP_KERNEL` in the NAPI poll, under a spinlock, or inside `rcu_read_lock()` | "sleeping function called from invalid context", rare hang |
| `GFP_ATOMIC` everywhere "to be safe" | drains reserves under pressure; reviewers ask why |
| no check of the return value | NULL dereference under memory pressure |
| `kfree()` on memory from `vmalloc()` or `kvmalloc()` | memory corruption or a crash, often far from the bug |
| `kmalloc(n * size, ...)` with `n` from userspace | integer overflow, undersized buffer, out-of-bounds write |
| userspace-sized allocation without accounting | a user can pin kernel memory outside their cgroup limit |
| an error path that skips a free, or frees twice | leak (kmemleak) or double free (KASAN) |
| reading packet bytes as if the skb were linear | out-of-bounds read on fragmented packets |

Build and test with `kernel/configs/debug.config`; KASAN and kmemleak are
described under `Documentation/dev-tools/` in your tree.

## Comprehension exercise

For each allocation the patch adds or moves, ask the human:
1. In which context does this line run, and why are these flags right
   there? Show the call path.
2. Which function frees it, and on which paths (success, every error)?
3. Where does the size come from? Can it overflow or be chosen by the
   user?
4. What happens if the allocation fails at this point?

## Read in the tree

`Documentation/core-api/memory-allocation.rst`,
`Documentation/networking/skbuff.rst`,
`Documentation/mm/` (how memory management is built),
`Documentation/locking/locktypes.rst` (PREEMPT_RT),
`include/linux/slab.h` and `include/linux/gfp.h` (with the headers they
include) for the current declarations and their kernel-doc. Next:
`lifetime-refcount.md`.
