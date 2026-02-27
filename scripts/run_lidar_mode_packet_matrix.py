#!/usr/bin/env python3
"""Run packet timing matrix across LiDAR modes with real UDP capture.

For each mode:
1) set lidar_mode + reinitialize
2) wait settle
3) capture UDP packets on 7502
4) compute pps/dt stats and save per-mode histogram
5) write summary json/md
"""

from __future__ import annotations

import argparse
import json
import socket
import statistics
import struct
import time
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import requests

DOC_URL = (
    "https://static.ouster.dev/sensor-docs/image_route1/image_route2/"
    "sensor_data/sensor-data.html#lidar-data-packet-format"
)

MODES = ["512x10", "512x20", "1024x10", "1024x20", "2048x10"]


def channel_block_bytes(profile: str) -> int:
    p = profile.strip().upper()
    if p == "RNG19_RFL8_SIG16_NIR16":
        return 12
    if p == "RNG15_RFL8_NIR8":
        return 4
    if p == "RNG19_RFL8_SIG16_NIR16_DUAL":
        return 16
    return 12


def get_json(host: str, path: str, timeout: float = 3.0) -> dict:
    return requests.get(f"http://{host}{path}", timeout=timeout).json()


def post(host: str, path: str, timeout: float = 5.0) -> dict:
    r = requests.post(f"http://{host}{path}", timeout=timeout)
    if r.headers.get("content-type", "").startswith("application/json"):
        return r.json()
    return {"text": r.text, "status_code": r.status_code}


def query_sensor(host: str) -> tuple[dict, dict]:
    cfg = get_json(host, "/api/v1/sensor/config")
    md = get_json(host, "/api/v1/sensor/metadata")
    return cfg, md


def expected_packet_size(cfg: dict, md: dict) -> int:
    profile = str(cfg.get("udp_profile_lidar", "RNG19_RFL8_SIG16_NIR16"))
    cpp = int(cfg.get("columns_per_packet", 16))
    ppc = int(md["lidar_data_format"]["pixels_per_column"])
    hdr = 32
    col_hdr = 12
    ftr = 32
    chb = channel_block_bytes(profile)
    return hdr + cpp * (col_hdr + ppc * chb) + ftr


def expected_pps(cfg: dict, md: dict) -> float:
    cpp = int(cfg.get("columns_per_packet", 16))
    cols = int(md["lidar_data_format"]["columns_per_frame"])
    mode = str(cfg.get("lidar_mode", "2048x10"))
    hz = float(mode.split("x")[1])
    return (cols / cpp) * hz


def set_mode(host: str, mode: str, settle_s: float) -> tuple[dict, dict]:
    post(host, f"/api/v1/sensor/cmd/set_config_param?args=lidar_mode%20{mode}")
    post(host, "/api/v1/sensor/cmd/reinitialize")
    time.sleep(settle_s)
    return query_sensor(host)


def capture_udp(port: int, duration_s: float, rcvbuf: int) -> list[dict]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(1.0)
    t0 = time.perf_counter()
    rows = []

    while time.perf_counter() - t0 < duration_s:
        try:
            data, _ = sock.recvfrom(65535)
            t = time.perf_counter()
            frame_id = None
            packet_type = None
            if len(data) >= 4:
                packet_type, frame_id = struct.unpack_from("<HH", data, 0)
            rows.append({"t_s": t, "len": len(data), "frame_id": frame_id, "packet_type": packet_type})
        except socket.timeout:
            continue

    sock.close()
    return rows


def summarize(rows: list[dict], pps_expected: float, size_expected: int) -> dict:
    if len(rows) < 4:
        raise RuntimeError("not enough packets captured")
    dts_us = [(rows[i]["t_s"] - rows[i - 1]["t_s"]) * 1e6 for i in range(1, len(rows))]
    lens = [r["len"] for r in rows]
    tspan = rows[-1]["t_s"] - rows[0]["t_s"]
    pps_meas = (len(rows) - 1) / tspan if tspan > 0 else 0.0

    dsort = sorted(dts_us)
    n = len(dsort)
    def pct(x: float) -> float:
        return dsort[min(n - 1, int(n * x))]

    return {
        "packets": len(rows),
        "span_s": tspan,
        "pps_expected": pps_expected,
        "pps_measured": pps_meas,
        "pps_error_pct": ((pps_meas - pps_expected) / pps_expected * 100.0) if pps_expected > 0 else 0.0,
        "packet_size_expected": size_expected,
        "packet_size_min": min(lens),
        "packet_size_max": max(lens),
        "dt_expected_us": (1e6 / pps_expected) if pps_expected > 0 else 0.0,
        "dt_mean_us": statistics.mean(dts_us),
        "dt_stdev_us": statistics.pstdev(dts_us),
        "dt_p50_us": pct(0.50),
        "dt_p95_us": pct(0.95),
        "dt_p99_us": pct(0.99),
        "dt_min_us": min(dts_us),
        "dt_max_us": max(dts_us),
    }


def save_dt_hist(rows: list[dict], out_png: Path, title: str) -> None:
    dts_us = [(rows[i]["t_s"] - rows[i - 1]["t_s"]) * 1e6 for i in range(1, len(rows))]
    fig, ax = plt.subplots(figsize=(7.2, 4.1))
    ax.hist(dts_us, bins=120)
    ax.set_title(title)
    ax.set_xlabel("Inter-packet delta (us)")
    ax.set_ylabel("count")
    ax.grid(alpha=0.2)
    plt.tight_layout()
    fig.savefig(out_png, dpi=140)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.6.11")
    ap.add_argument("--port", type=int, default=7502)
    ap.add_argument("--duration-s", type=float, default=20.0)
    ap.add_argument("--settle-s", type=float, default=4.0)
    ap.add_argument("--outdir", default="/home/kim/lidar-tas260226/data")
    ap.add_argument("--modes", nargs="*", default=MODES)
    ap.add_argument("--restore-mode", default="1024x20")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"mode_packet_matrix_{ts}"

    rows = []

    for mode in args.modes:
        cfg, md = set_mode(args.host, mode, args.settle_s)
        pps_exp = expected_pps(cfg, md)
        size_exp = expected_packet_size(cfg, md)
        cap = capture_udp(args.port, args.duration_s, 8 * 1024 * 1024)
        summary = summarize(cap, pps_exp, size_exp)

        hist_png = outdir / f"{stem}_{mode}_dt_hist.png"
        save_dt_hist(cap, hist_png, f"{mode} dt histogram")

        rows.append(
            {
                "mode_requested": mode,
                "mode_active": cfg.get("lidar_mode"),
                "udp_profile_lidar": cfg.get("udp_profile_lidar"),
                "columns_per_packet": cfg.get("columns_per_packet"),
                "columns_per_frame": md["lidar_data_format"]["columns_per_frame"],
                "pixels_per_column": md["lidar_data_format"]["pixels_per_column"],
                "summary": summary,
                "dt_hist_png": str(hist_png),
            }
        )
        print(f"[done] {mode} pps={summary['pps_measured']:.3f} dt_mean={summary['dt_mean_us']:.3f}us")

    if args.restore_mode:
        try:
            set_mode(args.host, args.restore_mode, args.settle_s)
            restored = args.restore_mode
        except Exception:
            restored = "failed"
    else:
        restored = "none"

    out = {
        "timestamp": ts,
        "doc_reference": DOC_URL,
        "duration_s": args.duration_s,
        "settle_s": args.settle_s,
        "modes": args.modes,
        "restored_mode": restored,
        "rows": rows,
    }

    p_json = outdir / f"{stem}.json"
    p_md = outdir / f"{stem}.md"

    p_json.write_text(json.dumps(out, indent=2), encoding="ascii")

    lines = [
        "# LiDAR Mode Packet Matrix",
        "",
        f"- docs: {DOC_URL}",
        f"- duration per mode: `{args.duration_s}s`",
        f"- settle after mode switch: `{args.settle_s}s`",
        f"- restored mode: `{restored}`",
        "",
        "| mode | active | pps_exp | pps_meas | pps_err(%) | pkt_size_exp | pkt_size_min/max | dt_exp(us) | dt_mean(us) | dt_p95(us) | dt_p99(us) |",
        "|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|",
    ]

    for r in rows:
        s = r["summary"]
        lines.append(
            f"| {r['mode_requested']} | {r['mode_active']} | {s['pps_expected']:.3f} | {s['pps_measured']:.3f} | {s['pps_error_pct']:+.3f} | {s['packet_size_expected']} | {s['packet_size_min']}/{s['packet_size_max']} | {s['dt_expected_us']:.3f} | {s['dt_mean_us']:.3f} | {s['dt_p95_us']:.3f} | {s['dt_p99_us']:.3f} |"
        )

    lines.append("")
    lines.append("## Histograms")
    for r in rows:
        lines.append(f"- {Path(r['dt_hist_png']).name}")

    p_md.write_text("\n".join(lines), encoding="ascii")

    print(p_json)
    print(p_md)


if __name__ == "__main__":
    main()
