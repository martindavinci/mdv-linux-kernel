<!-- SPDX-License-Identifier: GPL-2.0 -->

# Kernel design: index

Beginner-level maps of how parts of the kernel are built, loaded only when
the human asks how something works or needs it to understand their patch.
Same rule as the rest of the skill: stable concepts here; details, APIs and
rules in `Documentation/` of the checkout and in the code itself. When a
page and the tree disagree, the tree is right.

Explain in the human's language, keep identifiers in English, and point to
real code in their checkout (`git grep`, the function they are touching).

## Pages

| Page | Status | Meanwhile, read in the tree |
|---|---|---|
| [Networking stack](networking-stack.md) | available | `Documentation/networking/` |
| [Execution contexts](contexts.md) | available | `Documentation/networking/napi.rst`, `Documentation/networking/netdevices.rst` |
| [Locking and RCU](locking-rcu.md) | available | `Documentation/locking/`, `Documentation/RCU/` |
| Architecture overview | planned | `Documentation/process/howto.rst`, `Documentation/admin-guide/` |
| Memory management | planned | `Documentation/mm/`, `Documentation/core-api/memory-allocation.rst` |
| Object lifetime and refcounting | planned | `Documentation/core-api/kref.rst`, `Documentation/core-api/refcount-vs-atomic.rst` |
| Deferred work (workqueues, timers) | planned | `Documentation/core-api/workqueue.rst`, `Documentation/timers/` |
| Driver model | planned | `Documentation/driver-api/driver-model/` |
| Processes and scheduler | planned | `Documentation/scheduler/` |
| Filesystems / VFS | planned | `Documentation/filesystems/` |

Subsystem process rules are not here: see `references/netdev.md` for
networking and `references/orientation.md` for the development process.

## Writing a new page (for maintainers of this skill)

- One topic, plain Markdown, SPDX header. Schema: concept, a real netdev
  code path, the rule to respect, typical mistakes and the bug each one
  produces, a comprehension exercise, what to read in the tree.
- Contracts (who holds which lock, who owns an object, teardown order) are
  taught as something to read out of the tree of the revision in use, not
  stated as facts. Say when the page was last checked against the docs.
- Concepts and the path through the code; name the key structs, files and
  functions so the reader can `git grep` them.
- Link the in-tree docs for every detail; do not copy API lists or rules.
- Add it to the table above with status "available".
