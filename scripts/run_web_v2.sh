#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/kim/lidar-tas260226"
cd "$ROOT"

PORT="${PORT:-8081}"
LIDAR_HOST="${LIDAR_HOST:-192.168.6.11}"
LIDAR_PORT="${LIDAR_PORT:-7502}"
KETI_TSN_DIR="${KETI_TSN_DIR:-/home/kim/keti-tsn-cli-new}"

python3 scripts/lidar_tas_server_v2.py \
  --port "$PORT" \
  --lidar-host "$LIDAR_HOST" \
  --lidar-port "$LIDAR_PORT" \
  --keti-tsn-dir "$KETI_TSN_DIR"
