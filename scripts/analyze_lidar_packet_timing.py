#!/usr/bin/env python3
"""Capture and analyze LiDAR UDP packet timing with Ouster packet-size formulas."""

from __future__ import annotations

import argparse
import json
import socket
import statistics
import struct
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import requests


DOC_URL = (
    "https://static.ouster.dev/sensor-docs/image_route1/image_route2/"
    "sensor_data/sensor-data.html#lidar-data-packet-format"
)


def query_sensor(host: str) -> tuple[dict, dict]:
    cfg = requests.get(f"http://{host}/api/v1/sensor/config", timeout=3).json()
    md = requests.get(f"http://{host}/api/v1/sensor/metadata", timeout=3).json()
    return cfg, md


def channel_block_bytes(profile: str) -> int:
    # Ouster docs: words per pixel (single=3, low-rate=1, dual=4); 4 bytes/word.
    p = profile.strip().upper()
    if p == "RNG19_RFL8_SIG16_NIR16":
        return 12
    if p == "RNG15_RFL8_NIR8":
        return 4
    if p == "RNG19_RFL8_SIG16_NIR16_DUAL":
        return 16
    # Fallback for unknown/custom profile.
    return 12


def expected_packet_size(cfg: dict, md: dict) -> int:
    profile = str(cfg.get("udp_profile_lidar", "RNG19_RFL8_SIG16_NIR16"))
    cpp = int(cfg.get("columns_per_packet", 16))
    ppc = int(md["lidar_data_format"]["pixels_per_column"])
    hdr = 32
    meas_hdr = 12
    footer = 32
    blk = channel_block_bytes(profile)
    return hdr + cpp * (meas_hdr + ppc * blk) + footer


def expected_pps(cfg: dict, md: dict) -> float:
    cpp = int(cfg.get("columns_per_packet", 16))
    cols = int(md["lidar_data_format"]["columns_per_frame"])
    mode = str(cfg.get("lidar_mode", "2048x10"))
    try:
        hz = float(mode.split("x")[1])
    except Exception:
        hz = 10.0
    packets_per_frame = cols / cpp
    return packets_per_frame * hz


def capture_udp(port: int, duration_s: float, rcvbuf: int) -> list[dict]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(1.0)
    start = time.perf_counter()
    rows: list[dict] = []
    while time.perf_counter() - start < duration_s:
        try:
            data, _ = sock.recvfrom(65535)
            t = time.perf_counter()
            frame_id = None
            packet_type = None
            if len(data) >= 4:
                packet_type, frame_id = struct.unpack_from("<HH", data, 0)
            rows.append(
                {
                    "t_s": t,
                    "len": len(data),
                    "packet_type": packet_type,
                    "frame_id": frame_id,
                }
            )
        except socket.timeout:
            continue
    sock.close()
    return rows


def build_metrics(rows: list[dict], pps_expected: float) -> dict:
    if len(rows) < 3:
        raise RuntimeError("not enough packets captured")
    t0 = rows[0]["t_s"]
    for r in rows:
        r["t_rel_s"] = r["t_s"] - t0
    dts_us = []
    for i in range(1, len(rows)):
        dts_us.append((rows[i]["t_s"] - rows[i - 1]["t_s"]) * 1e6)
    lengths = [r["len"] for r in rows]
    frame_ids = [r["frame_id"] for r in rows if r["frame_id"] is not None]
    frame_counts = Counter(frame_ids)
    dts_sorted = sorted(dts_us)
    n = len(dts_sorted)
    p50 = dts_sorted[int(n * 0.50)]
    p95 = dts_sorted[int(n * 0.95)]
    p99 = dts_sorted[int(n * 0.99)]
    span_s = rows[-1]["t_s"] - rows[0]["t_s"]
    pps_measured = (len(rows) - 1) / span_s if span_s > 0 else 0.0
    expected_dt_us = 1e6 / pps_expected if pps_expected > 0 else 0.0
    return {
        "packets": len(rows),
        "span_s": span_s,
        "pps_measured": pps_measured,
        "pps_expected": pps_expected,
        "pps_error_pct": ((pps_measured - pps_expected) / pps_expected * 100.0)
        if pps_expected > 0
        else 0.0,
        "dt_expected_us": expected_dt_us,
        "dt_mean_us": statistics.mean(dts_us),
        "dt_stdev_us": statistics.pstdev(dts_us),
        "dt_min_us": min(dts_us),
        "dt_p50_us": p50,
        "dt_p95_us": p95,
        "dt_p99_us": p99,
        "dt_max_us": max(dts_us),
        "packet_len_mean": statistics.mean(lengths),
        "packet_len_min": min(lengths),
        "packet_len_max": max(lengths),
        "frame_count_samples": len(frame_counts),
        "packets_per_frame_median": statistics.median(frame_counts.values())
        if frame_counts
        else 0,
        "packets_per_frame_min": min(frame_counts.values()) if frame_counts else 0,
        "packets_per_frame_max": max(frame_counts.values()) if frame_counts else 0,
    }


def save_plots(rows: list[dict], outdir: Path, stem: str) -> dict:
    dts_us = [(rows[i]["t_s"] - rows[i - 1]["t_s"]) * 1e6 for i in range(1, len(rows))]
    lens = [r["len"] for r in rows]
    frame_ids = [r["frame_id"] for r in rows if r["frame_id"] is not None]
    frame_counts = Counter(frame_ids)

    p1 = outdir / f"{stem}_dt_series.png"
    p2 = outdir / f"{stem}_dt_hist.png"
    p3 = outdir / f"{stem}_len_hist.png"
    p4 = outdir / f"{stem}_frame_counts.png"

    plt.figure(figsize=(11, 3.2))
    plt.plot(dts_us, linewidth=0.6)
    plt.title("Inter-packet Delta Time (us) - Time Series")
    plt.xlabel("Packet Index")
    plt.ylabel("Delta (us)")
    plt.tight_layout()
    plt.savefig(p1, dpi=130)
    plt.close()

    plt.figure(figsize=(6.8, 4.0))
    plt.hist(dts_us, bins=120)
    plt.title("Inter-packet Delta Time Histogram")
    plt.xlabel("Delta (us)")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(p2, dpi=130)
    plt.close()

    plt.figure(figsize=(6.8, 4.0))
    plt.hist(lens, bins=max(10, min(120, len(set(lens)))))
    plt.title("UDP Payload Length Histogram")
    plt.xlabel("Payload Length (bytes)")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(p3, dpi=130)
    plt.close()

    # Show first 120 frame-id buckets for readability.
    keys = sorted(frame_counts.keys())[:120]
    vals = [frame_counts[k] for k in keys]
    plt.figure(figsize=(10, 3.3))
    plt.bar(range(len(keys)), vals, width=0.85)
    plt.title("Packets per Frame-ID (first 120 IDs)")
    plt.xlabel("Frame Index (ordered)")
    plt.ylabel("Packets")
    plt.tight_layout()
    plt.savefig(p4, dpi=130)
    plt.close()

    return {
        "dt_series_png": str(p1),
        "dt_hist_png": str(p2),
        "len_hist_png": str(p3),
        "frame_counts_png": str(p4),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.6.11")
    ap.add_argument("--port", type=int, default=7502)
    ap.add_argument("--duration-s", type=float, default=60.0)
    ap.add_argument("--rcvbuf", type=int, default=8 * 1024 * 1024)
    ap.add_argument("--outdir", default="/home/kim/lidar-tas260226/data")
    args = ap.parse_args()

    cfg, md = query_sensor(args.host)
    pkt_size_exp = expected_packet_size(cfg, md)
    pps_exp = expected_pps(cfg, md)
    rows = capture_udp(args.port, args.duration_s, args.rcvbuf)
    metrics = build_metrics(rows, pps_exp)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"packet_timing_{ts}"
    plots = save_plots(rows, outdir, stem)

    data = {
        "timestamp": ts,
        "doc_reference": DOC_URL,
        "sensor_config": {
            "udp_profile_lidar": cfg.get("udp_profile_lidar"),
            "columns_per_packet": cfg.get("columns_per_packet"),
            "lidar_mode": cfg.get("lidar_mode"),
            "timestamp_mode": cfg.get("timestamp_mode"),
            "phase_lock_enable": cfg.get("phase_lock_enable"),
            "phase_lock_offset": cfg.get("phase_lock_offset"),
        },
        "metadata_format": {
            "pixels_per_column": md["lidar_data_format"]["pixels_per_column"],
            "columns_per_frame": md["lidar_data_format"]["columns_per_frame"],
            "udp_profile_lidar": md["lidar_data_format"]["udp_profile_lidar"],
            "columns_per_packet": md["lidar_data_format"]["columns_per_packet"],
        },
        "expected": {
            "packet_size_bytes_formula": pkt_size_exp,
            "packet_rate_hz_formula": pps_exp,
        },
        "measured": metrics,
        "plots": plots,
    }

    out_json = outdir / f"{stem}.json"
    out_md = outdir / f"{stem}.md"
    out_json.write_text(json.dumps(data, indent=2), encoding="ascii")

    lines = [
        "# LiDAR Packet Timing Analysis",
        "",
        f"- source: `{out_json.name}`",
        f"- docs: {DOC_URL}",
        "",
        "## Config",
        f"- udp_profile_lidar: `{cfg.get('udp_profile_lidar')}`",
        f"- columns_per_packet: `{cfg.get('columns_per_packet')}`",
        f"- lidar_mode: `{cfg.get('lidar_mode')}`",
        f"- timestamp_mode: `{cfg.get('timestamp_mode')}`",
        f"- phase_lock_enable: `{cfg.get('phase_lock_enable')}`",
        f"- phase_lock_offset: `{cfg.get('phase_lock_offset')}`",
        "",
        "## Expected (from docs formula)",
        f"- packet_size_bytes: `{pkt_size_exp}`",
        f"- packet_rate_hz: `{pps_exp:.3f}`",
        f"- inter_packet_expected_us: `{(1e6/pps_exp):.3f}`",
        "",
        "## Measured",
        f"- packets: `{metrics['packets']}`",
        f"- pps_measured: `{metrics['pps_measured']:.3f}`",
        f"- pps_error_pct: `{metrics['pps_error_pct']:+.3f}%`",
        f"- dt_mean_us: `{metrics['dt_mean_us']:.3f}`",
        f"- dt_stdev_us: `{metrics['dt_stdev_us']:.3f}`",
        f"- dt_p50/p95/p99_us: `{metrics['dt_p50_us']:.3f}` / `{metrics['dt_p95_us']:.3f}` / `{metrics['dt_p99_us']:.3f}`",
        f"- dt_min/max_us: `{metrics['dt_min_us']:.3f}` / `{metrics['dt_max_us']:.3f}`",
        f"- packet_len_mean/min/max: `{metrics['packet_len_mean']:.1f}` / `{metrics['packet_len_min']}` / `{metrics['packet_len_max']}`",
        f"- packets_per_frame median/min/max: `{metrics['packets_per_frame_median']}` / `{metrics['packets_per_frame_min']}` / `{metrics['packets_per_frame_max']}`",
        "",
        "## Graphs",
        f"- inter-packet series: `{Path(plots['dt_series_png']).name}`",
        f"- inter-packet histogram: `{Path(plots['dt_hist_png']).name}`",
        f"- payload length histogram: `{Path(plots['len_hist_png']).name}`",
        f"- packets-per-frame chart: `{Path(plots['frame_counts_png']).name}`",
        "",
    ]
    out_md.write_text("\n".join(lines), encoding="ascii")
    print(out_json)
    print(out_md)


if __name__ == "__main__":
    main()
