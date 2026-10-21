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
| Architecture overview | planned | `Documentation/process/howto.rst`, `Documentation/admin-guide/` |
| Memory management | planned | `Documentation/mm/`, `Documentation/core-api/memory-allocation.rst` |
| Locking and RCU | planned | `Documentation/locking/`, `Documentation/RCU/` |
| Driver model | planned | `Documentation/driver-api/driver-model/` |
| Processes and scheduler | planned | `Documentation/scheduler/` |
| Filesystems / VFS | planned | `Documentation/filesystems/` |

Subsystem process rules are not here: see `references/netdev.md` for
networking and `references/orientation.md` for the development process.

## Writing a new page (for maintainers of this skill)

- One topic, about 150-250 lines, plain Markdown, SPDX header.
- Concepts and the path through the code; name the key structs, files and
  functions so the reader can `git grep` them.
- Link the in-tree docs for every detail; do not copy API lists or rules.
- Add it to the table above with status "available".
