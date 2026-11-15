#!/usr/bin/env bash
# Deleting the namespaces also removes the veth interfaces inside them.
for ns in ns-a ns-router ns-b; do ip netns del "$ns" 2>/dev/null || true; done
echo "topology down"