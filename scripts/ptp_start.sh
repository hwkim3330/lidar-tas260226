#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-enp4s0}"
ROLE="${2:-slave}"   # slave | master
DOMAIN="${3:-24}"
LOG_DIR="/tmp/lidar-tas260226-ptp"
mkdir -p "$LOG_DIR"

if ! command -v ptp4l >/dev/null 2>&1 || ! command -v phc2sys >/dev/null 2>&1; then
  echo "ptp4l/phc2sys not found"
  exit 1
fi

if ! ip link show "$IFACE" >/dev/null 2>&1; then
  echo "interface not found: $IFACE"
  exit 1
fi

TS_INFO="$(sudo ethtool -T "$IFACE" 2>/dev/null || true)"
if ! grep -q "hardware-raw-clock" <<<"$TS_INFO"; then
  echo "PTP HW timestamp not supported on $IFACE"
  echo "Use a NIC with 'hardware-raw-clock' capability."
  exit 2
fi

CONF="$LOG_DIR/ptp4l_${IFACE}.conf"
cat > "$CONF" <<EOF
[global]
time_stamping           hardware
network_transport       UDPv4
delay_mechanism         E2E
domainNumber            ${DOMAIN}
summary_interval        1
logging_level           6
EOF

sudo pkill -f "ptp4l.*${IFACE}" 2>/dev/null || true
sudo pkill -f "phc2sys.*${IFACE}" 2>/dev/null || true

PTP_LOG="$LOG_DIR/ptp4l_${IFACE}.log"
PHC_LOG="$LOG_DIR/phc2sys_${IFACE}.log"

if [[ "$ROLE" == "master" ]]; then
  sudo nohup ptp4l -f "$CONF" -i "$IFACE" -m >"$PTP_LOG" 2>&1 &
else
  sudo nohup ptp4l -f "$CONF" -i "$IFACE" -s -m >"$PTP_LOG" 2>&1 &
fi

sleep 1
sudo nohup phc2sys -s "$IFACE" -c CLOCK_REALTIME -w -m >"$PHC_LOG" 2>&1 &

echo "started:"
echo "  iface=$IFACE role=$ROLE domain=$DOMAIN"
echo "  ptp4l_log=$PTP_LOG"
echo "  phc2sys_log=$PHC_LOG"
