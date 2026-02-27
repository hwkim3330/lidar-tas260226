#!/usr/bin/env python3
"""2D sweep: sensor phase_lock_offset x TAS base-time phase."""

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
ALL_OPEN_YAML = ROOT / "configs" / "tas_disable_all_open.yaml"
SENSOR_IP = "192.168.6.11"
STATS_URL = "http://127.0.0.1:8080/api/stats"


def run(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def post(path):
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


def apply_tas(cycle_ns, front_ns, open_ns, back_ns, phase_ns, base_offset_sec):
    sec, nsec = get_switch_time()
    total = sec * 1_000_000_000 + nsec + base_offset_sec * 1_000_000_000 + phase_ns
    bsec = total // 1_000_000_000
    bnsec = total % 1_000_000_000
    yml = [
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
    runtime = ROOT / "_phaselock_tas_2d_runtime.yaml"
    runtime.write_text("\n".join(yml) + "\n", encoding="ascii")
    last = None
    for _ in range(5):
        r = run(["./keti-tsn", "patch", str(runtime)], cwd=str(KETI_DIR), check=False)
        if r.returncode == 0:
            return
        last = r
        time.sleep(0.2)
    err = last.stderr.strip() if last else "unknown"
    raise RuntimeError(f"tas patch failed: {err}")


def set_sensor(timestamp_mode: str, phase_lock_enable: bool, phase_lock_offset: int):
    post(f"/api/v1/sensor/cmd/set_config_param?args=timestamp_mode%20{timestamp_mode}")
    post(
        f"/api/v1/sensor/cmd/set_config_param?args=phase_lock_enable%20{'true' if phase_lock_enable else 'false'}"
    )
    post(f"/api/v1/sensor/cmd/set_config_param?args=phase_lock_offset%20{phase_lock_offset}")
    post("/api/v1/sensor/cmd/reinitialize")
    time.sleep(1.5)


def measure(duration_s=1.2, step_s=0.2):
    end = time.time() + duration_s
    rows = []
    while time.time() < end:
        try:
            s = requests.get(STATS_URL, timeout=0.8).json()
            rows.append(
                (
                    100.0 * float(s.get("frame_completeness", 0.0)),
                    float(s.get("fps", 0.0)),
                    float(s.get("gap_stdev_us", 0.0)),
                    float(s.get("pps", 0.0)),
                )
            )
        except Exception:
            pass
        time.sleep(step_s)
    if not rows:
        return {"samples": 0, "fc_mean": 0.0, "fc_p01": 0.0, "fps_mean": 0.0, "jit_mean": 0.0, "pps_mean": 0.0}
    fc = [x[0] for x in rows]
    fps = [x[1] for x in rows]
    jit = [x[2] for x in rows]
    pps = [x[3] for x in rows]
    fcs = sorted(fc)
    return {
        "samples": len(rows),
        "fc_mean": statistics.mean(fc),
        "fc_min": min(fc),
        "fc_p01": fcs[max(0, int(len(fcs) * 0.01) - 1)],
        "fc_p05": fcs[max(0, int(len(fcs) * 0.05) - 1)],
        "fps_mean": statistics.mean(fps),
        "fps_min": min(fps),
        "jit_mean": statistics.mean(jit),
        "pps_mean": statistics.mean(pps),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle-ns", type=int, default=781_250)
    ap.add_argument("--front-ns", type=int, default=305_625)
    ap.add_argument("--open-ns", type=int, default=150_000)
    ap.add_argument("--back-ns", type=int, default=325_625)
    ap.add_argument("--phase-step-ns", type=int, default=20_000)
    ap.add_argument("--phase-lock-offsets", default="0,90000,180000,270000")
    ap.add_argument("--timestamp-mode", default="TIME_FROM_PTP_1588")
    ap.add_argument("--duration-s", type=float, default=1.2)
    ap.add_argument("--sample-s", type=float, default=0.2)
    ap.add_argument("--settle-s", type=float, default=0.25)
    ap.add_argument("--base-offset-sec", type=int, default=2)
    args = ap.parse_args()

    offsets = [int(x.strip()) for x in args.phase_lock_offsets.split(",") if x.strip()]
    phases = list(range(0, args.cycle_ns, args.phase_step_ns))
    out_dir = ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = out_dir / f"phaselock_tas_2d_{ts}.json"
    out_md = out_dir / f"phaselock_tas_2d_{ts}.md"

    rows = []
    print("apply baseline all-open")
    run(["./keti-tsn", "patch", str(ALL_OPEN_YAML)], cwd=str(KETI_DIR), check=False)
    set_sensor(args.timestamp_mode, True, 0)
    time.sleep(1.0)
    baseline = measure(duration_s=30.0, step_s=0.5)

    total = len(offsets) * len(phases)
    n = 0
    for off in offsets:
        set_sensor(args.timestamp_mode, True, off)
        for ph in phases:
            n += 1
            apply_tas(
                args.cycle_ns,
                args.front_ns,
                args.open_ns,
                args.back_ns,
                ph,
                args.base_offset_sec,
            )
            time.sleep(args.settle_s)
            m = measure(duration_s=args.duration_s, step_s=args.sample_s)
            row = {"phase_lock_offset": off, "tas_phase_ns": ph, **m}
            row["score"] = row["fc_p01"] + 0.1 * row["fc_mean"]
            rows.append(row)
            if n % 20 == 0:
                print(
                    f"[{n}/{total}] off={off:6d} ph={ph:6d} "
                    f"fc={row['fc_mean']:.2f} p01={row['fc_p01']:.2f} fps={row['fps_mean']:.2f}"
                )

    ranked = sorted(rows, key=lambda x: (x["fc_p01"], x["fc_mean"], x["fps_min"]), reverse=True)
    top = ranked[:10]
    best = top[0] if top else {}

    if best:
        set_sensor(args.timestamp_mode, True, int(best["phase_lock_offset"]))
        apply_tas(
            args.cycle_ns,
            args.front_ns,
            args.open_ns,
            args.back_ns,
            int(best["tas_phase_ns"]),
            args.base_offset_sec,
        )

    obj = {
        "timestamp": ts,
        "sensor_ip": SENSOR_IP,
        "timestamp_mode": args.timestamp_mode,
        "cycle_ns": args.cycle_ns,
        "entries_ns": [args.front_ns, args.open_ns, args.back_ns],
        "phase_lock_offsets": offsets,
        "tas_phases": phases,
        "baseline_all_open": baseline,
        "rows": rows,
        "top10": top,
        "best": best,
    }
    out_json.write_text(json.dumps(obj, indent=2), encoding="ascii")

    lines = [
        "# PhaseLock x TAS 2D Sweep",
        "",
        f"- source: `{out_json.name}`",
        f"- timestamp_mode: `{args.timestamp_mode}`",
        f"- entries(ns): `{args.front_ns}/{args.open_ns}/{args.back_ns}`",
        "",
        "## baseline (all-open, 30s)",
        f"- fc_mean: {baseline.get('fc_mean',0):.3f}",
        f"- fc_p01: {baseline.get('fc_p01',0):.3f}",
        f"- fps_mean: {baseline.get('fps_mean',0):.3f}",
        "",
        "| rank | phase_lock_offset | tas_phase_ns | fc_mean | fc_p01 | fps_mean | fps_min |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for i, r in enumerate(top[:10], start=1):
        lines.append(
            f"| {i} | {r['phase_lock_offset']} | {r['tas_phase_ns']} | {r['fc_mean']:.3f} | {r['fc_p01']:.3f} | {r['fps_mean']:.3f} | {r['fps_min']:.3f} |"
        )
    if best:
        lines += [
            "",
            "best:",
            (
                f"- phase_lock_offset={best['phase_lock_offset']}, "
                f"tas_phase_ns={best['tas_phase_ns']}, "
                f"fc_p01={best['fc_p01']:.3f}, fc_mean={best['fc_mean']:.3f}, fps_mean={best['fps_mean']:.3f}"
            ),
        ]
    out_md.write_text("\n".join(lines) + "\n", encoding="ascii")
    print("saved", out_json)
    print("saved", out_md)


if __name__ == "__main__":
    main()
