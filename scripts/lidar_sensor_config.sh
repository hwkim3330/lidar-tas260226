#!/usr/bin/env bash
set -euo pipefail

HOST="${1:-192.168.6.11}"
UDP_DEST="${2:-192.168.6.1}"
TS_MODE="${3:-TIME_FROM_PTP_1588}"   # TIME_FROM_PTP_1588 | TIME_FROM_SYNC_PULSE_IN | TIME_FROM_INTERNAL_OSC

api_post() {
  local path="$1"
  curl -sS --max-time 3 -X POST "http://${HOST}${path}"
}

cfg_get() {
  curl -sS --max-time 3 "http://${HOST}/api/v1/sensor/config"
}

echo "[1/4] set udp_dest=${UDP_DEST}"
api_post "/api/v1/sensor/cmd/set_config_param?args=udp_dest%20${UDP_DEST}" >/dev/null

echo "[2/4] set timestamp_mode=${TS_MODE}"
api_post "/api/v1/sensor/cmd/set_config_param?args=timestamp_mode%20${TS_MODE}" >/dev/null

echo "[3/4] reinitialize"
api_post "/api/v1/sensor/cmd/reinitialize" >/dev/null
sleep 1

echo "[4/4] verify"
cfg_get | jq -r '{udp_dest, udp_port_lidar, operating_mode, timestamp_mode}'
