#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-enp4s0}"

echo "=== iface ==="
ip -br addr show "$IFACE" || true
echo

echo "=== timestamp capability ==="
sudo ethtool -T "$IFACE" 2>/dev/null | sed -n '1,80p' || true
echo

echo "=== processes ==="
ps -ef | grep -E "ptp4l|phc2sys" | grep -v grep || true
echo

echo "=== pmc TIME_STATUS_NP (if ptp4l running) ==="
sudo pmc -u -b 0 "GET TIME_STATUS_NP" 2>/dev/null || true
