#!/usr/bin/env python3
"""Compare timestamp_mode/phase_lock combinations with fixed TAS profile."""

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


def apply_tas(cycle_ns, front_ns, open_ns, back_ns, phase_ns, base_offset_sec=2):
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
    runtime = ROOT / "_timebase_matrix_runtime.yaml"
    runtime.write_text("\n".join(yml) + "\n", encoding="ascii")
    for _ in range(5):
        r = run(["./keti-tsn", "patch", str(runtime)], cwd=str(KETI_DIR), check=False)
        if r.returncode == 0:
            return
        time.sleep(0.2)
    raise RuntimeError("tas patch failed")


def set_sensor(timestamp_mode: str, phase_lock_enable: bool, phase_lock_offset: int):
    post(f"/api/v1/sensor/cmd/set_config_param?args=timestamp_mode%20{timestamp_mode}")
    post(
        f"/api/v1/sensor/cmd/set_config_param?args=phase_lock_enable%20{'true' if phase_lock_enable else 'false'}"
    )
    post(f"/api/v1/sensor/cmd/set_config_param?args=phase_lock_offset%20{phase_lock_offset}")
    post("/api/v1/sensor/cmd/reinitialize")
    time.sleep(1.6)


def measure(duration_s: float, step_s: float):
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
    ap.add_argument("--duration-s", type=float, default=90.0)
    ap.add_argument("--sample-s", type=float, default=0.5)
    ap.add_argument("--cycle-ns", type=int, default=781_250)
    ap.add_argument("--front-ns", type=int, default=305_625)
    ap.add_argument("--open-ns", type=int, default=150_000)
    ap.add_argument("--back-ns", type=int, default=325_625)
    ap.add_argument("--phase-old-ns", type=int, default=180_000)
    ap.add_argument("--phase-new-ns", type=int, default=300_000)
    args = ap.parse_args()

    cases = [
        {
            "name": "syncin_plock_off_phase180k",
            "timestamp_mode": "TIME_FROM_SYNC_PULSE_IN",
            "phase_lock_enable": False,
            "phase_lock_offset": 0,
            "tas_phase_ns": args.phase_old_ns,
        },
        {
            "name": "ptp_plock_off_phase180k",
            "timestamp_mode": "TIME_FROM_PTP_1588",
            "phase_lock_enable": False,
            "phase_lock_offset": 0,
            "tas_phase_ns": args.phase_old_ns,
        },
        {
            "name": "ptp_plock_on_off0_phase300k",
            "timestamp_mode": "TIME_FROM_PTP_1588",
            "phase_lock_enable": True,
            "phase_lock_offset": 0,
            "tas_phase_ns": args.phase_new_ns,
        },
        {
            "name": "ptp_plock_on_off90k_phase300k",
            "timestamp_mode": "TIME_FROM_PTP_1588",
            "phase_lock_enable": True,
            "phase_lock_offset": 90_000,
            "tas_phase_ns": args.phase_new_ns,
        },
        {
            "name": "syncin_plock_on_off90k_phase300k",
            "timestamp_mode": "TIME_FROM_SYNC_PULSE_IN",
            "phase_lock_enable": True,
            "phase_lock_offset": 90_000,
            "tas_phase_ns": args.phase_new_ns,
        },
    ]

    out_dir = ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = out_dir / f"timebase_mode_matrix_{ts}.json"
    out_md = out_dir / f"timebase_mode_matrix_{ts}.md"

    results = []
    for idx, c in enumerate(cases, start=1):
        print(f"[{idx}/{len(cases)}] {c['name']}")
        set_sensor(c["timestamp_mode"], c["phase_lock_enable"], c["phase_lock_offset"])
        apply_tas(
            args.cycle_ns,
            args.front_ns,
            args.open_ns,
            args.back_ns,
            c["tas_phase_ns"],
        )
        time.sleep(1.0)
        m = measure(args.duration_s, args.sample_s)
        rec = {**c, "summary": m}
        results.append(rec)
        print(
            f"  fc_mean={m['fc_mean']:.3f} fc_p01={m['fc_p01']:.3f} "
            f"fps_mean={m['fps_mean']:.3f} jit={m['jit_mean']:.1f}"
        )

    ranked = sorted(
        results,
        key=lambda x: (
            x["summary"]["fc_p01"],
            x["summary"]["fc_mean"],
            x["summary"]["fps_min"],
        ),
        reverse=True,
    )
    best = ranked[0]
    set_sensor(best["timestamp_mode"], best["phase_lock_enable"], best["phase_lock_offset"])
    apply_tas(
        args.cycle_ns,
        args.front_ns,
        args.open_ns,
        args.back_ns,
        best["tas_phase_ns"],
    )

    obj = {
        "timestamp": ts,
        "duration_s": args.duration_s,
        "entries_ns": [args.front_ns, args.open_ns, args.back_ns],
        "results": results,
        "ranked": ranked,
        "best": best,
    }
    out_json.write_text(json.dumps(obj, indent=2), encoding="ascii")

    lines = [
        "# Timebase/PhaseLock Matrix",
        "",
        f"- source: `{out_json.name}`",
        f"- entries(ns): `{args.front_ns}/{args.open_ns}/{args.back_ns}`",
        "",
        "| rank | case | ts_mode | plock | offset | tas_phase | fc_mean | fc_p01 | fps_mean | jit_mean |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for i, r in enumerate(ranked, start=1):
        s = r["summary"]
        lines.append(
            f"| {i} | {r['name']} | {r['timestamp_mode']} | {int(r['phase_lock_enable'])} | "
            f"{r['phase_lock_offset']} | {r['tas_phase_ns']} | {s['fc_mean']:.3f} | {s['fc_p01']:.3f} | "
            f"{s['fps_mean']:.3f} | {s['jit_mean']:.1f} |"
        )
    lines += [
        "",
        (
            "best: "
            f"{best['name']} "
            f"(fc_p01={best['summary']['fc_p01']:.3f}, "
            f"fc_mean={best['summary']['fc_mean']:.3f}, "
            f"fps_mean={best['summary']['fps_mean']:.3f})"
        ),
    ]
    out_md.write_text("\n".join(lines) + "\n", encoding="ascii")
    print("saved", out_json)
    print("saved", out_md)
    print("best", best["name"])


if __name__ == "__main__":
    main()
