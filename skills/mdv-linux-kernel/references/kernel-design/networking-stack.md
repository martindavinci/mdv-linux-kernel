<!-- SPDX-License-Identifier: GPL-2.0 -->

# The networking stack, a map

A beginner's map, to orient before reading code. Function names are
current mainline as of 2026-10; verify them with `git grep` in the
checkout before quoting them, since internals move. Details live in
`Documentation/networking/` (start with `index.rst`). Back to the
[index](index.md); process rules for this area are in `references/netdev.md`.

## Where things live

| Path | What |
|---|---|
| `include/linux/skbuff.h`, `net/core/skbuff.c` | `struct sk_buff`, the packet |
| `include/linux/netdevice.h`, `net/core/dev.c` | `struct net_device`, RX/TX core, NAPI |
| `net/core/` | shared core: rtnetlink, neighbours, flow dissector, namespaces |
| `net/ethernet/`, `net/bridge/`, `net/8021q/` | L2 |
| `net/ipv4/`, `net/ipv6/` | IP, routing, and also TCP/UDP (`tcp*.c`, `udp.c`, `tcp_ipv6.c`) |
| `net/sched/` | traffic control: qdiscs, classifiers, actions |
| `net/netfilter/` | packet filtering hooks, nftables, conntrack |
| `net/netlink/`, `Documentation/netlink/specs/` | netlink core, generic netlink, YNL specs |
| `net/socket.c` | the socket system calls |
| `drivers/net/` | device drivers; `drivers/net/netdevsim/` is the test driver |
| `tools/testing/selftests/net/`, `.../drivers/net/` | selftests |

## The packet: struct sk_buff

An skb is metadata plus pointers into a data buffer: `head`, `data`,
`tail`, `end`. Each layer moves `data` instead of copying:
`skb_push()` prepends a header on transmit, `skb_pull()` strips one on
receive, `skb_put()` appends data. Extra pages and fragments hang off
`skb_shinfo(skb)` (`struct skb_shared_info`). Clones (`skb_clone()`) share
the data buffer, so writing to it requires care.

Ownership is the key idea: whoever holds an skb must pass it on or free
it, exactly once. `kfree_skb()` frees on a drop (and is traced as a drop),
`consume_skb()` frees after normal use. Most skb bugs are leaks,
double-frees or use-after-free on an error path.
Read: `Documentation/networking/skbuff.rst`.

## The interface: struct net_device

One per network interface (physical, virtual, tunnel). A driver fills in
`struct net_device_ops` (`ndo_open`, `ndo_stop`, `ndo_start_xmit`, ...),
then calls `register_netdev()`. Configuration paths (up/down, MTU,
addresses, ethtool) traditionally run under the RTNL lock
(`rtnl_lock()`); recent kernels also have a per-device instance lock
(`netdev_lock()`) for some operations. Which lock protects what is
documented in `Documentation/networking/netdevices.rst`; read it before
touching a driver's configuration path.

## Receive path (simplified)

```
NIC DMA -> IRQ -> napi_schedule()
  -> driver poll(), up to a budget of packets
     -> (XDP program, if attached)
     -> build skb -> napi_gro_receive() / netif_receive_skb()
        -> __netif_receive_skb_core(): taps (tcpdump), VLAN, bridge, ...
           -> protocol handler by ethertype (struct packet_type), e.g. ip_rcv()
              -> netfilter PRE_ROUTING -> routing decision
                 -> ip_local_deliver() -> tcp_v4_rcv() / udp_rcv()
                    -> socket receive queue -> recvmsg() wakes the reader
                 -> or forward -> transmit path
```

**NAPI**: instead of one interrupt per packet, the driver masks the
interrupt and the kernel polls the device in softirq context until it is
drained or the budget is used. Most driver RX code runs in that poll.
Read: `Documentation/networking/napi.rst`.

**GRO** merges consecutive packets of a flow into one large skb on receive,
so upper layers do less work per byte.

## Transmit path (simplified)

```
send()/sendmsg() -> tcp_sendmsg() / udp_sendmsg()
  -> tcp_transmit_skb() -> ip_queue_xmit() -> ip_output()
     -> netfilter POST_ROUTING -> ip_finish_output2() -> neigh_output()
        -> dev_queue_xmit() -> qdisc (net/sched) -> dev_hard_start_xmit()
           -> driver ndo_start_xmit() -> NIC DMA
              -> TX completion (usually in the NAPI poll) frees the skb
```

The driver's `ndo_start_xmit()` must either take ownership of the skb or
return busy; it must never free it and also return busy.

## Protocol layers

- **L2**: Ethernet framing, VLANs, bridging, bonding, the `net_device`.
- **L3**: IPv4/IPv6 input, routing (FIB), fragmentation, ICMP, neighbour
  discovery (ARP/NDISC) in `net/core/neighbour.c` and per family.
- **L4**: TCP, UDP, SCTP, MPTCP... Each family registers its handlers; a
  socket's `struct proto` holds the protocol operations, its
  `struct proto_ops` the socket-level ones.

## Sockets

`struct socket` is the VFS-facing object (a file descriptor);
`struct sock` (`sk`) is the protocol's state. Most protocol code works on
`struct sock`. Socket locking (`lock_sock()`) and softirq-context receive
interact; this is a frequent source of races.

## Netlink

The control plane between userspace and the kernel. rtnetlink
(`net/core/rtnetlink.c`) handles links, addresses and routes
(`ip link`, `ip addr`). Generic netlink lets subsystems define families;
newer families are described in YAML specs in `Documentation/netlink/specs/`
with code generated from them (YNL). Read:
`Documentation/userspace-api/netlink/intro.rst`.

## Network namespaces

`struct net` is one isolated network stack: its own devices, routes,
sockets, sysctls. Code reaches it with `dev_net(dev)` or `sock_net(sk)`.
Per-namespace state is set up through `register_pernet_subsys()` /
`register_pernet_device()`. Forgetting namespaces (using `init_net`, or
global state) is a common review comment. Selftests use namespaces and
veth pairs to build test topologies without hardware.

## Offloads and scaling

NICs can compute checksums, segment large TCP packets (TSO/GSO) and spread
receive load across queues (RSS). The stack decides per skb what the device
can do through feature flags (`NETIF_F_*`). Read:
`Documentation/networking/checksum-offloads.rst`,
`segmentation-offloads.rst`, `netdev-features.rst`, `scaling.rst`.

## Where to go next

- Trace the function your patch touches: `git grep -n <function>`, then
  callers and the lock held at each call site.
- `Documentation/networking/` for the area (`kapi.rst` for the API
  reference, `driver.rst` for driver-writing notes).
- The selftests for the area show how it is meant to be used.
