#!/usr/bin/env python3
"""Run TAS experiments while 3D web server is running, using /api/stats metrics."""
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
TC0_OPEN = 0x01
TC0_CLOSE = 0xFE


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


def build_tas_yaml(cycle_us, open_us, base_sec, base_nsec):
    cycle_ns = cycle_us * 1000
    open_ns = open_us * 1000
    close_ns = cycle_ns - open_ns
    if open_us <= 0:
        entries = f"""
        - index: 0
          operation-name: set-gate-states
          gate-states-value: {TC0_CLOSE}
          time-interval-value: {cycle_ns}"""
    else:
        entries = f"""
        - index: 0
          operation-name: set-gate-states
          gate-states-value: {TC0_OPEN}
          time-interval-value: {open_ns}
        - index: 1
          operation-name: set-gate-states
          gate-states-value: {TC0_CLOSE}
          time-interval-value: {close_ns}"""

    return f"""- ? "/ietf-interfaces:interfaces/interface[name='1']/ieee802-dot1q-bridge:bridge-port/ieee802-dot1q-sched-bridge:gate-parameter-table"
  : gate-enabled: true
    admin-gate-states: {ALL_OPEN}
    admin-cycle-time:
      numerator: {cycle_ns}
      denominator: 1000000000
    admin-base-time:
      seconds: {base_sec}
      nanoseconds: {base_nsec}
    admin-control-list:
      gate-control-entry:{entries}
    config-change: true
"""


def apply_tas(cycle_us, open_us, phase_ns, base_offset_sec):
    sec, nsec = get_switch_time()
    total_ns = sec * 1_000_000_000 + nsec + base_offset_sec * 1_000_000_000 + phase_ns
    base_sec = total_ns // 1_000_000_000
    base_nsec = total_ns % 1_000_000_000

    yml = build_tas_yaml(cycle_us, open_us, base_sec, base_nsec)
    runtime = KETI_DIR / "lidar-tas260226" / "_server_stats_runtime.yaml"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(yml, encoding="ascii")
    run(["./keti-tsn", "patch", str(runtime)], cwd=str(KETI_DIR))


def set_sensor(phase_lock_enable):
    val = "true" if phase_lock_enable else "false"
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


def measure_stats(duration_s, interval_s):
    t_end = time.monotonic() + duration_s
    pps_vals = []
    fc_vals = []
    gs_vals = []
    while time.monotonic() < t_end:
        try:
            s = requests.get(SERVER_STATS_URL, timeout=0.8).json()
            pps_vals.append(float(s.get("pps", 0.0)))
            fc_vals.append(float(s.get("frame_completeness", 0.0)))
            gs_vals.append(float(s.get("gap_stdev_us", 0.0)))
        except Exception:
            pass
        time.sleep(interval_s)

    if not pps_vals:
        return {
            "samples": 0,
            "pps_mean": 0.0,
            "pps_min": 0.0,
            "completeness_pct_est": 0.0,
            "frame_comp_mean": 0.0,
            "gap_stdev_mean": 0.0,
        }
    pps_mean = statistics.mean(pps_vals)
    return {
        "samples": len(pps_vals),
        "pps_mean": pps_mean,
        "pps_min": min(pps_vals),
        "completeness_pct_est": min(100.0, pps_mean / EXPECTED_PPS * 100.0),
        "frame_comp_mean": statistics.mean(fc_vals) if fc_vals else 0.0,
        "gap_stdev_mean": statistics.mean(gs_vals) if gs_vals else 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle-us", type=int, default=781)
    ap.add_argument("--opens", default="144,146,150")
    ap.add_argument("--phase-step-us", type=int, default=40)
    ap.add_argument("--duration", type=float, default=2.5)
    ap.add_argument("--interval", type=float, default=0.2)
    ap.add_argument("--settle", type=float, default=0.3)
    ap.add_argument("--base-offset-sec", type=int, default=2)
    args = ap.parse_args()

    opens = [int(x.strip()) for x in args.opens.split(",") if x.strip()]
    phase_ns_list = [x * 1000 for x in range(0, args.cycle_us, args.phase_step_us)]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = ROOT / "data" / f"server_stats_matrix_{ts}.json"
    out_md = ROOT / "data" / f"server_stats_matrix_{ts}.md"

    rows = []
    for phase_lock in (False, True):
        set_sensor(phase_lock)
        for open_us in opens:
            for phase_ns in phase_ns_list:
                apply_tas(args.cycle_us, open_us, phase_ns, args.base_offset_sec)
                time.sleep(args.settle)
                m = measure_stats(args.duration, args.interval)
                m.update(
                    {
                        "phase_lock_enable": phase_lock,
                        "open_us": open_us,
                        "phase_offset_ns": phase_ns,
                    }
                )
                rows.append(m)
                print(
                    f"plock={int(phase_lock)} open={open_us:>3} phase={phase_ns:>6} "
                    f"comp={m['completeness_pct_est']:.2f}% pps={m['pps_mean']:.1f}"
                )

    # restore defaults
    run(["./keti-tsn", "patch", str(ALL_OPEN_YAML)], cwd=str(KETI_DIR))
    set_sensor(False)

    summary = []
    for phase_lock in (False, True):
        for open_us in opens:
            grp = [r for r in rows if r["phase_lock_enable"] == phase_lock and r["open_us"] == open_us]
            comps = [r["completeness_pct_est"] for r in grp]
            summary.append(
                {
                    "phase_lock_enable": phase_lock,
                    "open_us": open_us,
                    "comp_min": min(comps),
                    "comp_mean": statistics.mean(comps),
                    "comp_max": max(comps),
                    "pass_99_9_count": sum(1 for c in comps if c >= 99.9),
                    "total": len(comps),
                }
            )

    result = {"rows": rows, "summary": summary}
    out_json.write_text(json.dumps(result, indent=2), encoding="ascii")

    lines = ["# Server Stats Matrix", "", f"- source: `{out_json.name}`", ""]
    lines.append("| phase_lock | open_us | comp_min | comp_mean | comp_max | pass>=99.9 |")
    lines.append("|:---:|---:|---:|---:|---:|---:|")
    for s in summary:
        lines.append(
            f"| {'on' if s['phase_lock_enable'] else 'off'} | {s['open_us']} | "
            f"{s['comp_min']:.2f} | {s['comp_mean']:.2f} | {s['comp_max']:.2f} | "
            f"{s['pass_99_9_count']}/{s['total']} |"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="ascii")
    print(f"saved: {out_json}")
    print(f"saved: {out_md}")


if __name__ == "__main__":
    main()
