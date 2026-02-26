#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-enp4s0}"

sudo pkill -f "ptp4l.*${IFACE}" 2>/dev/null || true
sudo pkill -f "phc2sys.*${IFACE}" 2>/dev/null || true
echo "stopped ptp on $IFACE (if running)"
