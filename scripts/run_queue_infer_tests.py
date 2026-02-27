#!/usr/bin/env python3
"""Infer queue backlog scale from time-to-degrade near TAS boundary."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path("/home/kim/lidar-tas260226")
KETI_DIR = Path("/home/kim/keti-tsn-cli-new")
FETCH_YAML = Path("/home/kim/lidar-tas/configs/fetch-tas.yaml")
SENSOR_IP = "192.168.6.11"
STATS_URL = "http://127.0.0.1:8080/api/stats"


def run(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def post(path: str):
    return run(
        ["curl", "-sS", "--max-time", "3", "-X", "POST", f"http://{SENSOR_IP}{path}"],
        check=False,
    )


def get_switch_time():
    out = run(["./keti-tsn", "fetch", str(FETCH_YAML)], cwd=str(KETI_DIR)).stdout
    m = re.search(
        r"current-time:\s*\n\s*nanoseconds:\s*(\d+)\s*\n\s*seconds:\s*(\d+)",
        out,
        re.M,
    )
    if not m:
        raise RuntimeError("failed to parse switch current-time")
    return int(m.group(2)), int(m.group(1))


def apply_tas(cycle_ns: int, front_ns: int, open_ns: int, back_ns: int, phase_ns: int, base_offset_sec: int):
    sec, nsec = get_switch_time()
    total = sec * 1_000_000_000 + nsec + base_offset_sec * 1_000_000_000 + phase_ns
    bsec = total // 1_000_000_000
    bnsec = total % 1_000_000_000
    lines = [
        "- ? \"/ietf-interfaces:interfaces/interface[name='1']/ieee802-dot1q-bridge:bridge-port/ieee802-dot1q-sched-bridge:gate-parameter-table\"",
        "  : gate-enabled: true",
        "    admin-gate-states: 255",
        "    admin-cycle-time:",
        f"      numerator: {cycle_ns}",
        "      denominator: 1000000000",
        "    admin-base-time:",
        f"      seconds: {bsec}",
        f"      nanoseconds: {bnsec}",
        "    admin-control-list:",
        "      gate-control-entry:",
        "        - index: 0",
        "          operation-name: set-gate-states",
        "          gate-states-value: 254",
        f"          time-interval-value: {front_ns}",
        "        - index: 1",
        "          operation-name: set-gate-states",
        "          gate-states-value: 255",
        f"          time-interval-value: {open_ns}",
        "        - index: 2",
        "          operation-name: set-gate-states",
        "          gate-states-value: 254",
        f"          time-interval-value: {back_ns}",
        "    config-change: true",
    ]
    runtime = ROOT / "_queue_infer_runtime.yaml"
    runtime.write_text("\n".join(lines) + "\n", encoding="ascii")
    for _ in range(5):
        r = run(["./keti-tsn", "patch", str(runtime)], cwd=str(KETI_DIR), check=False)
        if r.returncode == 0:
            return
        time.sleep(0.2)
    raise RuntimeError("tas patch failed")


def set_sensor_baseline():
    post("/api/v1/sensor/cmd/set_config_param?args=timestamp_mode%20TIME_FROM_SYNC_PULSE_IN")
    post("/api/v1/sensor/cmd/set_config_param?args=phase_lock_enable%20false")
    post("/api/v1/sensor/cmd/set_config_param?args=phase_lock_offset%200")
    post("/api/v1/sensor/cmd/reinitialize")
    time.sleep(1.8)


def collect_window(
    duration_s: float,
    step_s: float,
    fail_fc_pct: float,
    fail_fps: float,
    warmup_s: float,
    fail_consecutive: int,
):
    t0 = time.time()
    end = t0 + duration_s
    rows = []
    t_drop = None
    fail_streak = 0
    while time.time() < end:
        now = time.time()
        try:
            s = requests.get(STATS_URL, timeout=0.8).json()
            rec = {
                "t_rel_s": now - t0,
                "fc_pct": 100.0 * float(s.get("frame_completeness", 0.0)),
                "fps": float(s.get("fps", 0.0)),
                "jit_us": float(s.get("gap_stdev_us", 0.0)),
                "pps": float(s.get("pps", 0.0)),
                "bw_mbps": float(s.get("bandwidth_mbps", 0.0)),
            }
            rows.append(rec)
            if rec["t_rel_s"] >= warmup_s and t_drop is None:
                is_fail = rec["fc_pct"] < fail_fc_pct or rec["fps"] < fail_fps
                if is_fail:
                    fail_streak += 1
                else:
                    fail_streak = 0
                if fail_streak >= fail_consecutive:
                    t_drop = rec["t_rel_s"]
        except Exception:
            pass
        time.sleep(step_s)
    return rows, t_drop


def summarize(rows):
    fc = [r["fc_pct"] for r in rows]
    fps = [r["fps"] for r in rows]
    jit = [r["jit_us"] for r in rows]
    pps = [r["pps"] for r in rows]
    bw = [r["bw_mbps"] for r in rows]
    fcs = sorted(fc)
    return {
        "samples": len(rows),
        "fc_mean": statistics.mean(fc),
        "fc_min": min(fc),
        "fc_p01": fcs[max(0, int(len(fcs) * 0.01) - 1)],
        "fc_p05": fcs[max(0, int(len(fcs) * 0.05) - 1)],
        "fps_mean": statistics.mean(fps),
        "fps_min": min(fps),
        "jit_mean_us": statistics.mean(jit),
        "pps_mean": statistics.mean(pps),
        "bw_mean_mbps": statistics.mean(bw),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle-ns", type=int, default=781_250)
    ap.add_argument("--stable-open-us", type=float, default=146.0)
    ap.add_argument("--test-opens-us", default="145,144,143")
    ap.add_argument("--phase-ns", type=int, default=180_000)
    ap.add_argument("--close-front-ratio", type=float, default=0.48433359788359786)  # 305625/(781250-150000)
    ap.add_argument("--duration-s", type=float, default=120.0)
    ap.add_argument("--step-s", type=float, default=0.2)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--fail-fc-pct", type=float, default=95.0)
    ap.add_argument("--fail-fps", type=float, default=9.5)
    ap.add_argument("--warmup-s", type=float, default=5.0)
    ap.add_argument("--fail-consecutive", type=int, default=3)
    ap.add_argument("--base-offset-sec", type=int, default=2)
    args = ap.parse_args()

    set_sensor_baseline()
    test_opens_us = [float(x.strip()) for x in args.test_opens_us.split(",") if x.strip()]
    cycle_s = args.cycle_ns / 1e9
    link_Bps = 1e9 / 8.0
    stable_open_s = args.stable_open_us * 1e-6

    all_rows = []
    print("queue inference run start")
    for open_us in test_opens_us:
        open_ns = int(round(open_us * 1000.0))
        close_total = args.cycle_ns - open_ns
        front_ns = int(round(close_total * args.close_front_ratio))
        back_ns = close_total - front_ns
        if front_ns <= 0 or back_ns <= 0:
            continue

        for r in range(args.repeats):
            apply_tas(args.cycle_ns, front_ns, open_ns, back_ns, args.phase_ns, args.base_offset_sec)
            time.sleep(1.0)
            rows, t_drop = collect_window(
                args.duration_s,
                args.step_s,
                args.fail_fc_pct,
                args.fail_fps,
                args.warmup_s,
                args.fail_consecutive,
            )
            s = summarize(rows)
            deficit_s = max(0.0, stable_open_s - open_us * 1e-6)
            deficit_B_per_cycle = deficit_s * link_Bps
            deficit_Bps = deficit_B_per_cycle / cycle_s
            q_est_bytes = None if t_drop is None else deficit_Bps * t_drop
            rec = {
                "open_us": open_us,
                "repeat": r + 1,
                "entries_ns": [front_ns, open_ns, back_ns],
                "t_drop_s": t_drop,
                "deficit_B_per_cycle_vs_stable": deficit_B_per_cycle,
                "deficit_Bps_vs_stable": deficit_Bps,
                "q_est_bytes": q_est_bytes,
                "summary": s,
            }
            all_rows.append(rec)
            print(
                f"open={open_us:.1f} rep={r+1} "
                f"fc_p01={s['fc_p01']:.2f} fps_mean={s['fps_mean']:.2f} "
                f"t_drop={t_drop}"
            )

    # Restore current known-safe setting.
    apply_tas(args.cycle_ns, 305_625, 150_000, 325_625, 180_000, args.base_offset_sec)
    set_sensor_baseline()

    q_vals = [r["q_est_bytes"] for r in all_rows if r["q_est_bytes"] is not None]
    q_summary = {
        "n": len(q_vals),
        "mean": statistics.mean(q_vals) if q_vals else None,
        "min": min(q_vals) if q_vals else None,
        "max": max(q_vals) if q_vals else None,
        "p50": statistics.median(q_vals) if q_vals else None,
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = ROOT / "data" / f"queue_infer_{ts}.json"
    out_md = ROOT / "data" / f"queue_infer_{ts}.md"
    obj = {
        "timestamp": ts,
        "params": vars(args),
        "rows": all_rows,
        "q_est_bytes_summary": q_summary,
    }
    out_json.write_text(json.dumps(obj, indent=2), encoding="ascii")

    lines = [
        "# Queue Inference (Boundary Time-to-Drop)",
        "",
        f"- source: `{out_json.name}`",
        f"- stable_open_us(ref): `{args.stable_open_us}`",
        "",
        "| open_us | rep | fc_p01 | fps_mean | t_drop_s | deficit_Bps_vs_stable | q_est_bytes |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in all_rows:
        s = r["summary"]
        td = "-" if r["t_drop_s"] is None else f"{r['t_drop_s']:.3f}"
        qe = "-" if r["q_est_bytes"] is None else f"{r['q_est_bytes']:.1f}"
        lines.append(
            f"| {r['open_us']:.1f} | {r['repeat']} | {s['fc_p01']:.3f} | {s['fps_mean']:.3f} | "
            f"{td} | {r['deficit_Bps_vs_stable']:.1f} | {qe} |"
        )
    lines += [
        "",
        "q_est_bytes summary (where t_drop observed):",
        f"- n: {q_summary['n']}",
        f"- mean: {q_summary['mean']}",
        f"- min: {q_summary['min']}",
        f"- max: {q_summary['max']}",
        f"- p50: {q_summary['p50']}",
    ]
    out_md.write_text("\n".join(lines) + "\n", encoding="ascii")
    print("saved", out_json)
    print("saved", out_md)


if __name__ == "__main__":
    main()
