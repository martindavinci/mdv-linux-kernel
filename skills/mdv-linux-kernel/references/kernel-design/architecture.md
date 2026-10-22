<!-- SPDX-License-Identifier: GPL-2.0 -->

# Kernel architecture, a map

A conceptual map of the kernel for someone about to change one small part
of it. It is not a source of rules: it says where things are and how they
connect, so the human can find the code and the documentation that do
hold the rules. Back to the [index](index.md); the networking part in more
detail is in [networking-stack.md](networking-stack.md).

Last checked against docs.kernel.org (7.3) in 2026-10. If the tree says
otherwise, the tree is right. Directory names and boundaries move between
releases; check them with `ls` and `git grep` in your checkout.

## User space and the kernel

Programs run in user space with limited privileges. The kernel runs with
full access to the hardware and memory, and offers its services through a
fixed boundary: system calls (and interfaces built on them, such as files
in `/proc` and `/sys`, netlink sockets, `ioctl`). A program cannot call a
kernel function directly; it asks through that boundary, and the kernel
checks the request before acting.

That boundary is what users depend on. Changing how a system call, a
netlink attribute or a sysfs file behaves can break programs that already
exist. Interfaces visible to user space are described under
`Documentation/ABI/` and `Documentation/userspace-api/` in your tree; read
them before touching one.

The kernel "is written mostly in C, with some architecture-dependent parts
written in assembly". It does not use the C library: helpers such as string
functions are the kernel's own, in `lib/` and `include/`. Newer code may
also be in Rust (`rust/` and some drivers).

## The core subsystems

| Subsystem | Does what | Look in |
|---|---|---|
| Process management and scheduler | creates and ends tasks, decides which runs on each CPU | `kernel/` (scheduler under `kernel/sched/`), `Documentation/scheduler/` |
| Memory management | page allocation, virtual memory, slab allocators, reclaim | `mm/`, `Documentation/mm/`, and `memory.md` |
| VFS and filesystems | one file API over many filesystems | `fs/`, `Documentation/filesystems/` |
| Networking | sockets, protocols, packet path, netlink | `net/`, `drivers/net/`, `Documentation/networking/` |
| Device drivers and the driver model | talk to hardware; buses, devices and drivers bound together | `drivers/`, `Documentation/driver-api/` |
| Architecture code | boot, entry from user space, interrupts, low-level memory setup per CPU family | `arch/<arch>/` |

Smaller pieces sit beside these: the block layer (`block/`), cryptography
(`crypto/`), security modules (`security/`), inter-process communication
(`ipc/`), `io_uring/`, virtualisation support (`virt/`), sound (`sound/`).

Subsystems lean on each other. A network driver uses the driver model to
bind to its device, the memory allocator for its rings, the scheduler when
a work item sleeps, and the networking core for everything packet-related.

## How a system call travels (high level)

```
program calls a C library wrapper (for example send())
  -> CPU switches to kernel mode; arch entry code (arch/<arch>/)
     -> system call table -> the handler (sockets: net/socket.c)
        -> subsystem code (net/ipv4/, net/core/, ...)
           -> maybe a driver (drivers/net/)
  <- result returned; CPU back in user mode
```

To find a handler in your tree, start from its name:
`git grep -n 'sendto' net/socket.c`, then follow the calls.

## How a packet travels (high level)

```
NIC receives a frame -> driver (drivers/net/) in IRQ, then NAPI poll
  -> networking core (net/core/) -> protocol (net/ipv4/, net/ipv6/)
     -> socket receive queue; the scheduler wakes the waiting program
        -> the program's recv() system call copies the data out
```

The packet crosses three subsystems (drivers, networking, scheduler) and
three execution contexts (hard IRQ, softirq, process). The full path with
function names is in `networking-stack.md`; the contexts in `contexts.md`.

## The source tree

At the top level of a 7.3-era tree you will find roughly:

| Directory | Contents |
|---|---|
| `arch/` | architecture-specific code |
| `block/` | block layer |
| `crypto/` | cryptographic API and algorithms |
| `drivers/` | device drivers, grouped by type |
| `fs/` | VFS and filesystems |
| `include/` | headers (`include/linux/`, `include/net/`, `include/uapi/` for user-visible definitions) |
| `init/` | boot-time initialisation |
| `io_uring/` | the io_uring interface |
| `ipc/` | inter-process communication |
| `kernel/` | core: scheduler, signals, timers, locking, RCU, ... |
| `lib/` | helper library code |
| `mm/` | memory management |
| `net/` | networking |
| `rust/` | Rust support |
| `samples/` | example code |
| `scripts/` | build and maintenance scripts |
| `security/` | security modules |
| `sound/` | audio |
| `tools/` | user-space tools and selftests (`tools/testing/selftests/`) |
| `usr/` | early user-space image support |
| `virt/` | virtualisation support |
| `Documentation/` | the documentation, rendered at docs.kernel.org |

Run `ls` in your checkout; this list changes over time.

## Who owns which file

`MAINTAINERS` maps files to people and mailing lists. Before changing or
sending anything, find the entries for the files you touch;
`scripts/get_maintainer.pl` reads it for you. The process around this is
in `references/netdev.md` and `references/orientation.md`.

`Documentation/` is the authority for how things work and how to work.
Tutorials, blog posts and older books describe older kernels; when they
disagree with `Documentation/` of your revision, the tree wins.

## Cross-cutting topics

Some ideas apply in every subsystem and cause most beginner bugs:
- who runs the code and whether it may sleep: `contexts.md`;
- who may touch data and under which lock: `locking-rcu.md`;
- allocating and freeing memory: `memory.md`;
- when an object may be freed: `lifetime-refcount.md`;
- work done later or periodically: `deferred-work.md`.

## Typical mistakes and the bug they produce

| Mistake | Result |
|---|---|
| fixing a generic problem in one `arch/` directory only | other architectures keep the bug, or the build breaks there |
| changing a user-visible interface "to fix it" | existing programs break; the patch is rejected |
| editing a widely included header without building other users | build failures in subsystems you did not test |
| following an old tutorial instead of `Documentation/` of your tree | removed or renamed APIs, a patch that does not build |
| sending to lists picked by guess instead of `MAINTAINERS` | the patch reaches the wrong people and is ignored |

## Comprehension exercise

Before the human writes code, ask:
1. Which subsystem owns the file the patch changes, and which
   `MAINTAINERS` entry covers it?
2. Does the change cross the user space boundary (a system call, netlink,
   sysfs, `include/uapi/`)? If so, who depends on the current behaviour?
3. Draw the path from user space (or from the hardware) to the changed
   function, naming the subsystems on the way.
4. Which of the cross-cutting pages above applies to the change?

## Read in the tree

`Documentation/process/howto.rst`, `Documentation/admin-guide/`,
`Documentation/core-api/`, `Documentation/driver-api/`,
`Documentation/networking/`, `Documentation/ABI/`, the `MAINTAINERS` file,
and `ls` of the top-level directory.
