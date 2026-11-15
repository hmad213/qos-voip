#!/usr/bin/env bash
# Creates: ns-a (10.0.1.2) <-> ns-router (10.0.1.1 / 10.0.2.1) <-> ns-b (10.0.2.2)
# Safe to re-run: removes any leftover namespaces first.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

# clean slate so the script can be re-run safely
for ns in ns-a ns-router ns-b; do ip netns del "$ns" 2>/dev/null || true; done

# namespaces
for ns in ns-a ns-router ns-b; do ip netns add "$ns"; done

# veth pairs: a <-> router <-> b
ip link add veth-a type veth peer name veth-r1
ip link add veth-b type veth peer name veth-r2
ip link set veth-a  netns ns-a
ip link set veth-r1 netns ns-router
ip link set veth-r2 netns ns-router
ip link set veth-b  netns ns-b

# addresses
ip -n ns-a      addr add 10.0.1.2/24 dev veth-a
ip -n ns-router addr add 10.0.1.1/24 dev veth-r1
ip -n ns-router addr add 10.0.2.1/24 dev veth-r2
ip -n ns-b      addr add 10.0.2.2/24 dev veth-b

# bring everything up
for ns in ns-a ns-router ns-b; do ip -n "$ns" link set lo up; done
ip -n ns-a      link set veth-a  up
ip -n ns-router link set veth-r1 up
ip -n ns-router link set veth-r2 up
ip -n ns-b      link set veth-b  up

# routing: endpoints send everything via the router, router forwards
ip -n ns-a route add default via 10.0.1.1
ip -n ns-b route add default via 10.0.2.1
ip netns exec ns-router sysctl -qw net.ipv4.ip_forward=1

echo "topology up"