<!-- SPDX-License-Identifier: GPL-2.0 -->

# Object lifetime and reference counting

An object may be freed only when nobody can use it any more. In a kernel
with many CPUs, softirqs and work items running at once, "nobody" is hard to
prove. Reference counts are the usual proof: each user holds a reference,
and the last one to drop it frees the object. Many netdev fixes are about
a reference taken too late, dropped twice, or never dropped.

This page gives the vocabulary and the patterns. Who owns a given object,
which lock serialises its lookup and in which order teardown happens are
contracts of that code: read them out of the tree of the revision you work
on, not from here.

Last checked against docs.kernel.org (7.3) in 2026-10. If the tree says
otherwise, the tree is right. Read `locking-rcu.md` first.

## Two ways to keep an object alive

- **RCU** keeps the *memory* valid until the end of a read-side critical
  section. You may not sleep there, and you may not keep the pointer after
  `rcu_read_unlock()`.
- **A reference** keeps the *object* alive until you drop it, across
  sleeps and across contexts.

A common shape combines them: find the object under RCU, take a reference
before leaving the read section, use it, drop the reference.

## struct kref

From `Documentation/core-api/kref.rst`:

- Embed a `struct kref` anywhere in your structure.
- `kref_init()` sets the count to 1. Call it after allocating the object,
  before anyone else can see it.
- Call `kref_get()` *before* passing a pointer to someone else (another
  thread, a work item, a queue). Without a lock this is only valid if you
  already hold a valid pointer, that is, a reference of your own.
- Call `kref_put()` when done. At zero it calls the release function,
  which may not be `NULL` or `kfree()` itself; it is a function you write.
- `kref_put_mutex()` and `kref_put_lock()` call the release function with
  the lock held, for objects that live in a table protected by that lock.

A sketch of the shape (names are illustrative):

```c
struct foo {
	struct kref ref;
	/* ... */
};

static void foo_release(struct kref *ref)
{
	struct foo *f = container_of(ref, struct foo, ref);

	/* undo what was set up, then free */
}

/* done with f: */
kref_put(&f->ref, foo_release);
```

## Getting a reference from a lookup

The hard case is taking a reference when you do *not* already hold one,
such as finding an object in a hash table or list. The document says this
needs serialisation so that a `kref_put()` cannot happen during the
`kref_get()`: otherwise you increment a count that already reached zero
and use an object being freed.

Two answers from the same document:
- Take the lock that protects the table, find the entry, `kref_get()`,
  unlock; and make the *final* put serialised with that lookup too, so the
  count cannot reach zero while another CPU is between finding the entry
  and getting its reference. `kref_put_mutex()` / `kref_put_lock()` do
  this: they take the lock only when the count drops to zero and call the
  release (which unlinks the entry) with it held. Taking the lock only
  inside the release function is too late: the count is already zero and
  a concurrent lookup can still find the entry. Another common design is
  that the table itself holds a reference until the entry is unlinked.
- Use `kref_get_unless_zero()`. Its return value must be checked, and the
  call must be in the same critical section that finds the entry in the
  table. With RCU lookups, the release function must free with
  `kfree_rcu()`, or call `synchronize_rcu()` before `kfree()`, because a
  reader may still be looking at the entry.

## refcount_t, not atomic_t

`Documentation/core-api/refcount-vs-atomic.rst` describes `refcount_t` as a
minimal API for object reference counters. Its memory ordering differs
from the `atomic_t` operations it replaces: for example
`refcount_dec_and_test()` gives RELEASE ordering plus ACQUIRE on success,
and `refcount_inc_not_zero()` a control dependency on success. Mappings
the document lists include `atomic_inc_not_zero()` to
`refcount_inc_not_zero()` and `atomic_dec_and_test()` to
`refcount_dec_and_test()`.

The `refcount_t` implementation also guards against overflow and underflow;
see `include/linux/refcount.h` in your tree for what it does. A new
counter of object references written with `atomic_t` will be questioned
in review.

## Refcounted objects in networking

- **`struct net_device`** is refcounted. Code that keeps a device pointer
  beyond an RCU section or a locked region holds a reference. Find the
  hold and put helpers for it in `include/linux/netdevice.h` of your tree
  and read their comments; they have changed across releases. Then read
  how the unregister path in `net/core/dev.c` deals with references that
  are still held.
- **Sockets** (`struct sock`) are refcounted too; their helpers are in
  `include/net/sock.h`. Check there which ones your code should use.
- **`struct sk_buff`**: from `Documentation/networking/skbuff.rst`,
  "sk_buff.users is a simple refcount", and `skb_shared()` is true when
  `users != 1`. `skb_clone()` creates a new `struct sk_buff` but "None of
  the data buffers get copied"; the `dataref` field of
  `struct skb_shared_info` counts the references to the data. A clone may
  read the shared data; writing to it needs a private copy first.

On top of the counters, skbs follow an ownership rule
(`networking-stack.md`): whoever holds one passes it on or frees it,
exactly once. After passing it on, do not touch it.

## A netdev code path to read

Pick an object your patch touches (a device, a socket, a per-flow entry,
a driver's private structure) and trace its life in your tree:

1. **Creation**: where is it allocated and where is its count set to 1?
2. **Publication**: where is it made visible (added to a list or table,
   assigned to an `__rcu` pointer, registered)? After that point other
   contexts may find it.
3. **Users**: `git grep` the get and put calls for it. Is each get paired
   with a put on every path, including errors?
4. **Teardown**: where is it unpublished, how does the code wait for
   readers (grace period, `napi_disable()`, cancelling work), and where is
   the last reference dropped?

The order in step 4 is the contract. It differs between subsystems and
between releases; read it in the code and the comments next to the struct.

## Typical mistakes and the bug they produce

| Mistake | Result |
|---|---|
| a get without a put on an error path | leak; a device or module that cannot go away |
| an extra put | count reaches zero early: use-after-free |
| `kref_get()` on an entry found without the table lock | race with the last put: use-after-free |
| ignoring the return value of `kref_get_unless_zero()` | use of an object already being released |
| plain `kfree()` in a release when lookups use RCU | use-after-free in a concurrent reader |
| a hand-made counter on `atomic_t` | no overflow protection, weaker ordering than assumed |
| using an skb after passing it on, or freeing it twice | use-after-free, double free (KASAN) |
| writing to the data of a cloned or shared skb | corrupts the packet another user is reading |

## Comprehension exercise

For the object the patch handles, ask the human:
1. Who holds references to it, and where is each one dropped?
2. When the code finds it in a table, what stops it from being freed
   between the lookup and the reference? Point at the lock or the RCU
   section and the `_unless_zero` / `_not_zero` check.
3. In teardown, what is the order of unpublish, wait, and final put, and
   where does the tree show it?
4. If this is an skb: who owns it after each call in the path?

## Read in the tree

`Documentation/core-api/kref.rst`,
`Documentation/core-api/refcount-vs-atomic.rst`,
`include/linux/kref.h`, `include/linux/refcount.h`,
`Documentation/networking/skbuff.rst`,
`include/linux/netdevice.h` and `include/net/sock.h` for the networking
hold/put helpers, `Documentation/RCU/` for deferred freeing.
Related: `memory.md`, `deferred-work.md`.
