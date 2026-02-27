#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/kim/lidar-tas260226"
cd "$ROOT"

HOST="${HOST:-192.168.6.11}"
PORT="${PORT:-7502}"
DURATION_S="${DURATION_S:-86400}"  # 24h
LOG_DIR="${LOG_DIR:-$ROOT/data}"
mkdir -p "$LOG_DIR"

TS="$(date +%Y%m%d_%H%M%S)"
LOG="$LOG_DIR/packet_soak_24h_${TS}.log"

echo "[start] 24h packet soak host=$HOST port=$PORT duration=$DURATION_S" | tee -a "$LOG"
python3 scripts/analyze_lidar_packet_timing.py \
  --host "$HOST" \
  --port "$PORT" \
  --duration-s "$DURATION_S" | tee -a "$LOG"
echo "[done] $(date -Iseconds)" | tee -a "$LOG"
