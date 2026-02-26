#!/usr/bin/env python3
"""Refine 781.25us TAS by sweeping open width and phase robustness."""

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


def set_phase_lock(enable: bool):
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


def build_yaml(cycle_ns: int, entries: list[dict[str, int]], base_sec: int, base_nsec: int) -> str:
    lines = [
        "- ? \"/ietf-interfaces:interfaces/interface[name='1']/ieee802-dot1q-bridge:bridge-port/ieee802-dot1q-sched-bridge:gate-parameter-table\"",
        "  : gate-enabled: true",
        "    admin-gate-states: 255",
        "    admin-cycle-time:",
        f"      numerator: {cycle_ns}",
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
                f"          time-interval-value: {int(e['dur_ns'])}",
            ]
        )
    lines.append("    config-change: true")
    return "\n".join(lines) + "\n"


def apply_entries(cycle_ns: int, entries: list[dict[str, int]], phase_ns: int, base_offset_sec: int):
    sec, nsec = get_switch_time()
    total_ns = sec * 1_000_000_000 + nsec + base_offset_sec * 1_000_000_000 + phase_ns
    base_sec = total_ns // 1_000_000_000
    base_nsec = total_ns % 1_000_000_000

    yml = build_yaml(cycle_ns, entries, base_sec, base_nsec)
    runtime = KETI_DIR / "lidar-tas260226" / "_781p25_refine_runtime.yaml"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(yml, encoding="ascii")
    run(["./keti-tsn", "patch", str(runtime)], cwd=str(KETI_DIR))


def measure(duration_s: float, interval_s: float) -> dict:
    t_end = time.monotonic() + duration_s
    fc_vals = []
    fps_vals = []
    while time.monotonic() < t_end:
        try:
            s = requests.get(SERVER_STATS_URL, timeout=0.8).json()
            fc_vals.append(100.0 * float(s.get("frame_completeness", 0.0)))
            fps_vals.append(float(s.get("fps", 0.0)))
        except Exception:
            pass
        time.sleep(interval_s)
    if not fc_vals:
        return {"fc_mean": 0.0, "fc_min": 0.0, "fps_mean": 0.0, "pass": False}
    fc_mean = statistics.mean(fc_vals)
    fps_mean = statistics.mean(fps_vals)
    return {
        "fc_mean": fc_mean,
        "fc_min": min(fc_vals),
        "fps_mean": fps_mean,
        "pass": (fc_mean >= 99.9 and fps_mean >= 9.5),
    }


def entries_single(open_ns: int, cycle_ns: int) -> list[dict[str, int]]:
    return [{"gate": 255, "dur_ns": open_ns}, {"gate": 254, "dur_ns": cycle_ns - open_ns}]


def entries_split(open_ns: int, cycle_ns: int) -> list[dict[str, int]]:
    o1 = open_ns // 2
    o2 = open_ns - o1
    return [{"gate": 255, "dur_ns": o1}, {"gate": 254, "dur_ns": cycle_ns - open_ns}, {"gate": 255, "dur_ns": o2}]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle-ns", type=int, default=781_250)
    ap.add_argument("--open-us-list", default="50,60,70,80,100,120,150")
    ap.add_argument("--phases", type=int, default=20)
    ap.add_argument("--duration", type=float, default=1.8)
    ap.add_argument("--interval", type=float, default=0.2)
    ap.add_argument("--settle", type=float, default=0.35)
    ap.add_argument("--base-offset-sec", type=int, default=2)
    args = ap.parse_args()

    cycle_ns = args.cycle_ns
    open_ns_list = [int(round(float(x.strip()) * 1000.0)) for x in args.open_us_list.split(",") if x.strip()]
    phase_step_ns = cycle_ns // args.phases
    phase_ns_list = [(i * phase_step_ns) % cycle_ns for i in range(args.phases)]

    cases = []
    for open_ns in open_ns_list:
        if open_ns <= 0 or open_ns >= cycle_ns:
            continue
        cases.append((f"single_{open_ns/1000:.2f}us", open_ns, entries_single(open_ns, cycle_ns)))
        cases.append((f"split_{open_ns/1000:.2f}us", open_ns, entries_split(open_ns, cycle_ns)))

    rows = []
    try:
        for phase_lock in (False, True):
            set_phase_lock(phase_lock)
            for name, open_ns, entries in cases:
                for phase_ns in phase_ns_list:
                    apply_entries(cycle_ns, entries, phase_ns, args.base_offset_sec)
                    time.sleep(args.settle)
                    m = measure(args.duration, args.interval)
                    rows.append(
                        {
                            "phase_lock": phase_lock,
                            "case": name,
                            "open_ns": open_ns,
                            "phase_ns": phase_ns,
                            **m,
                        }
                    )
                    print(
                        f"pl={int(phase_lock)} {name:16s} ph={phase_ns:6d} "
                        f"fc={m['fc_mean']:.2f}% min={m['fc_min']:.2f}% fps={m['fps_mean']:.2f}"
                    )
    finally:
        run(["./keti-tsn", "patch", str(ALL_OPEN_YAML)], cwd=str(KETI_DIR))
        set_phase_lock(False)

    summary = []
    for phase_lock in (False, True):
        for name, open_ns, _ in cases:
            grp = [r for r in rows if r["phase_lock"] == phase_lock and r["case"] == name]
            if not grp:
                continue
            fc = [r["fc_mean"] for r in grp]
            summary.append(
                {
                    "phase_lock": phase_lock,
                    "case": name,
                    "open_ns": open_ns,
                    "fc_min": min(fc),
                    "fc_mean": statistics.mean(fc),
                    "fc_max": max(fc),
                    "pass_count": sum(1 for r in grp if r["pass"]),
                    "total": len(grp),
                }
            )

    best = max(summary, key=lambda s: (s["pass_count"] / s["total"], s["fc_min"], s["fc_mean"]))
    payload = {
        "cycle_ns": cycle_ns,
        "phase_step_ns": phase_step_ns,
        "rows": rows,
        "summary": summary,
        "best": best,
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = ROOT / "data" / f"refine_781p25_open_{ts}.json"
    out_md = ROOT / "data" / f"refine_781p25_open_{ts}.md"
    out_json.write_text(json.dumps(payload, indent=2), encoding="ascii")

    lines = [
        "# 781.25us Open-Width Refinement",
        "",
        f"- source: `{out_json.name}`",
        "",
        "| phase_lock | case | open_us | fc_min | fc_mean | fc_max | pass>=99.9/fps>=9.5 |",
        "|:---:|---|---:|---:|---:|---:|---:|",
    ]
    for s in summary:
        lines.append(
            f"| {'on' if s['phase_lock'] else 'off'} | {s['case']} | {s['open_ns']/1000.0:.2f} | "
            f"{s['fc_min']:.2f} | {s['fc_mean']:.2f} | {s['fc_max']:.2f} | {s['pass_count']}/{s['total']} |"
        )
    lines.extend(
        [
            "",
            f"best_case: `{best['case']}` (open_us={best['open_ns']/1000.0:.2f}, "
            f"phase_lock={'on' if best['phase_lock'] else 'off'})",
        ]
    )
    out_md.write_text("\n".join(lines) + "\n", encoding="ascii")

    print(f"saved: {out_json}")
    print(f"saved: {out_md}")
    print(f"best: {best}")


if __name__ == "__main__":
    main()

