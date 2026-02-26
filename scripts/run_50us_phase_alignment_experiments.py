#!/usr/bin/env python3
"""Phase alignment experiments for 50us windows (single and 3-slot)."""

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
SERVER_STATS_URL = "http://127.0.0.1:8080/api/stats"
SENSOR_HOST = "192.168.6.11"
EXPECTED_PPS = 1280.0
ALL_OPEN = 0xFF


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)


def get_switch_time():
    r = run(["./keti-tsn", "fetch", str(FETCH_YAML)], cwd=str(KETI_DIR))
    m = re.search(
        r"current-time:\s*\n\s*nanoseconds:\s*(\d+)\s*\n\s*seconds:\s*(\d+)",
        r.stdout,
        re.MULTILINE,
    )
    if not m:
        raise RuntimeError("cannot parse switch current-time")
    return int(m.group(2)), int(m.group(1))


def set_sensor_phase_lock(enable: bool):
    val = "true" if enable else "false"
    run(
        [
            "curl",
            "-sS",
            "--max-time",
            "3",
            "-X",
            "POST",
            f"http://{SENSOR_HOST}/api/v1/sensor/cmd/set_config_param?args=phase_lock_enable%20{val}",
        ]
    )
    run(
        [
            "curl",
            "-sS",
            "--max-time",
            "3",
            "-X",
            "POST",
            f"http://{SENSOR_HOST}/api/v1/sensor/cmd/reinitialize",
        ]
    )
    time.sleep(2.0)


def build_yaml(cycle_us: int, entries: list[dict[str, int]], base_sec: int, base_nsec: int) -> str:
    lines = [
        "- ? \"/ietf-interfaces:interfaces/interface[name='1']/ieee802-dot1q-bridge:bridge-port/ieee802-dot1q-sched-bridge:gate-parameter-table\"",
        "  : gate-enabled: true",
        f"    admin-gate-states: {ALL_OPEN}",
        "    admin-cycle-time:",
        f"      numerator: {cycle_us * 1000}",
        "      denominator: 1000000000",
        "    admin-base-time:",
        f"      seconds: {base_sec}",
        f"      nanoseconds: {base_nsec}",
        "    admin-control-list:",
        "      gate-control-entry:",
    ]
    for i, e in enumerate(entries):
        lines.extend(
            [
                f"        - index: {i}",
                "          operation-name: set-gate-states",
                f"          gate-states-value: {int(e['gate'])}",
                f"          time-interval-value: {int(e['duration_us']) * 1000}",
            ]
        )
    lines.append("    config-change: true")
    return "\n".join(lines) + "\n"


def apply_tas_entries(cycle_us: int, entries: list[dict[str, int]], phase_ns: int, base_offset_sec: int):
    s, ns = get_switch_time()
    total = s * 1_000_000_000 + ns + base_offset_sec * 1_000_000_000 + phase_ns
    base_sec = total // 1_000_000_000
    base_nsec = total % 1_000_000_000
    yml = build_yaml(cycle_us, entries, base_sec, base_nsec)
    runtime = KETI_DIR / "lidar-tas260226" / "_50us_phase_runtime.yaml"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(yml, encoding="ascii")
    run(["./keti-tsn", "patch", str(runtime)], cwd=str(KETI_DIR))


def measure_stats(duration_s: float, interval_s: float) -> dict:
    t_end = time.monotonic() + duration_s
    pps_vals = []
    fc_vals = []
    fps_vals = []
    jit_vals = []
    while time.monotonic() < t_end:
        try:
            s = requests.get(SERVER_STATS_URL, timeout=0.8).json()
            pps_vals.append(float(s.get("pps", 0.0)))
            fc_vals.append(float(s.get("frame_completeness", 0.0)))
            fps_vals.append(float(s.get("fps", 0.0)))
            jit_vals.append(float(s.get("gap_stdev_us", 0.0)))
        except Exception:
            pass
        time.sleep(interval_s)
    if not pps_vals:
        return {
            "samples": 0,
            "pps_mean": 0.0,
            "comp_est_pct": 0.0,
            "frame_comp_mean_pct": 0.0,
            "frame_comp_min_pct": 0.0,
            "fps_mean": 0.0,
            "jitter_mean_us": 0.0,
        }
    return {
        "samples": len(pps_vals),
        "pps_mean": statistics.mean(pps_vals),
        "comp_est_pct": min(100.0, statistics.mean(pps_vals) / EXPECTED_PPS * 100.0),
        "frame_comp_mean_pct": 100.0 * statistics.mean(fc_vals),
        "frame_comp_min_pct": 100.0 * min(fc_vals),
        "fps_mean": statistics.mean(fps_vals),
        "jitter_mean_us": statistics.mean(jit_vals),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle-us", type=int, default=781)
    ap.add_argument("--phase-step-us", type=int, default=20)
    ap.add_argument("--duration", type=float, default=2.0)
    ap.add_argument("--interval", type=float, default=0.2)
    ap.add_argument("--settle", type=float, default=0.4)
    ap.add_argument("--base-offset-sec", type=int, default=2)
    args = ap.parse_args()

    cycle = args.cycle_us
    close50 = cycle - 50
    cases = [
        ("single50", [{"gate": 255, "duration_us": 50}, {"gate": 254, "duration_us": close50}]),
        ("triple50_25_731_25", [{"gate": 255, "duration_us": 25}, {"gate": 254, "duration_us": close50}, {"gate": 255, "duration_us": 25}]),
        ("triple50_10_731_40", [{"gate": 255, "duration_us": 10}, {"gate": 254, "duration_us": close50}, {"gate": 255, "duration_us": 40}]),
        ("triple50_40_731_10", [{"gate": 255, "duration_us": 40}, {"gate": 254, "duration_us": close50}, {"gate": 255, "duration_us": 10}]),
    ]
    phase_ns_list = [x * 1000 for x in range(0, cycle, args.phase_step_us)]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = ROOT / "data" / f"phase_align_50us_{ts}.json"
    out_md = ROOT / "data" / f"phase_align_50us_{ts}.md"

    rows = []
    try:
        for phase_lock in (False, True):
            set_sensor_phase_lock(phase_lock)
            for name, entries in cases:
                for phase_ns in phase_ns_list:
                    apply_tas_entries(cycle, entries, phase_ns, args.base_offset_sec)
                    time.sleep(args.settle)
                    m = measure_stats(args.duration, args.interval)
                    row = {
                        "phase_lock_enable": phase_lock,
                        "case": name,
                        "phase_offset_ns": phase_ns,
                        **m,
                    }
                    row["pass_fc_99_9"] = row["frame_comp_mean_pct"] >= 99.9
                    rows.append(row)
                    print(
                        f"plock={int(phase_lock)} {name:>20} phase={phase_ns:>6} "
                        f"fc={row['frame_comp_mean_pct']:.2f}% min={row['frame_comp_min_pct']:.2f}% fps={row['fps_mean']:.2f}"
                    )
    finally:
        run(["./keti-tsn", "patch", str(ALL_OPEN_YAML)], cwd=str(KETI_DIR))
        set_sensor_phase_lock(False)

    summary = []
    for phase_lock in (False, True):
        for name, _ in cases:
            grp = [r for r in rows if r["phase_lock_enable"] == phase_lock and r["case"] == name]
            if not grp:
                continue
            fc = [r["frame_comp_mean_pct"] for r in grp]
            summary.append(
                {
                    "phase_lock_enable": phase_lock,
                    "case": name,
                    "fc_min": min(fc),
                    "fc_mean": statistics.mean(fc),
                    "fc_max": max(fc),
                    "pass_99_9_count": sum(1 for r in grp if r["pass_fc_99_9"]),
                    "total": len(grp),
                }
            )

    payload = {"rows": rows, "summary": summary}
    out_json.write_text(json.dumps(payload, indent=2), encoding="ascii")

    lines = ["# 50us Phase Alignment Matrix", "", f"- source: `{out_json.name}`", ""]
    lines.append("| phase_lock | case | fc_min | fc_mean | fc_max | pass>=99.9 |")
    lines.append("|:---:|---|---:|---:|---:|---:|")
    for s in summary:
        lines.append(
            f"| {'on' if s['phase_lock_enable'] else 'off'} | {s['case']} | "
            f"{s['fc_min']:.2f} | {s['fc_mean']:.2f} | {s['fc_max']:.2f} | "
            f"{s['pass_99_9_count']}/{s['total']} |"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="ascii")

    print(f"saved: {out_json}")
    print(f"saved: {out_md}")


if __name__ == "__main__":
    main()

