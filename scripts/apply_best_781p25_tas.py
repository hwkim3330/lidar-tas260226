#!/usr/bin/env python3
"""Apply the current best 781.25us TAS profile for single Ouster LiDAR."""

from __future__ import annotations

import argparse
import re
import subprocess
import time
from pathlib import Path


ROOT = Path("/home/kim/lidar-tas260226")
KETI_DIR = Path("/home/kim/keti-tsn-cli-new")
FETCH_YAML = Path("/home/kim/lidar-tas/configs/fetch-tas.yaml")
SENSOR_HOST = "192.168.6.11"


def run(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def get_switch_time():
    out = run(["./keti-tsn", "fetch", str(FETCH_YAML)], cwd=str(KETI_DIR)).stdout
    m = re.search(
        r"current-time:\s*\n\s*nanoseconds:\s*(\d+)\s*\n\s*seconds:\s*(\d+)",
        out,
        re.M,
    )
    if not m:
        raise RuntimeError("failed to parse switch current-time from fetch output")
    return int(m.group(2)), int(m.group(1))


def build_yaml(cycle_ns, front_ns, open_ns, back_ns, base_sec, base_ns):
    return "\n".join(
        [
            "- ? \"/ietf-interfaces:interfaces/interface[name='1']/ieee802-dot1q-bridge:bridge-port/ieee802-dot1q-sched-bridge:gate-parameter-table\"",
            "  : gate-enabled: true",
            "    admin-gate-states: 255",
            "    admin-cycle-time:",
            f"      numerator: {cycle_ns}",
            "      denominator: 1000000000",
            "    admin-base-time:",
            f"      seconds: {base_sec}",
            f"      nanoseconds: {base_ns}",
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
            "",
        ]
    )


def set_phase_lock(enable):
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
        ],
        check=False,
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
        ],
        check=False,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle-ns", type=int, default=781_250)
    ap.add_argument("--front-ns", type=int, default=305_625)
    ap.add_argument("--open-ns", type=int, default=150_000)
    ap.add_argument("--back-ns", type=int, default=325_625)
    ap.add_argument("--phase-ns", type=int, default=180_000)
    ap.add_argument("--base-offset-sec", type=int, default=2)
    ap.add_argument("--disable-phase-lock", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.front_ns + args.open_ns + args.back_ns != args.cycle_ns:
        raise SystemExit("invalid durations: front + open + back must equal cycle")

    sec, nsec = get_switch_time()
    total_ns = sec * 1_000_000_000 + nsec + args.base_offset_sec * 1_000_000_000 + args.phase_ns
    base_sec = total_ns // 1_000_000_000
    base_ns = total_ns % 1_000_000_000

    yml = build_yaml(
        args.cycle_ns,
        args.front_ns,
        args.open_ns,
        args.back_ns,
        base_sec,
        base_ns,
    )
    runtime = ROOT / "_best_781p25_runtime.yaml"
    runtime.write_text(yml, encoding="ascii")

    print(
        "profile:",
        f"cycle={args.cycle_ns} front/open/back={args.front_ns}/{args.open_ns}/{args.back_ns}",
        f"phase={args.phase_ns} offset={args.base_offset_sec}s",
    )
    print("runtime_yaml:", runtime)

    if args.dry_run:
        print("dry-run: no patch applied")
        return

    last = None
    for _ in range(5):
        res = run(["./keti-tsn", "patch", str(runtime)], cwd=str(KETI_DIR), check=False)
        if res.returncode == 0:
            print("patch: ok")
            if args.disable_phase_lock:
                set_phase_lock(False)
                time.sleep(1.0)
                print("sensor phase_lock_enable: set false")
            return
        last = res
        time.sleep(0.2)

    err = last.stderr.strip() if last and last.stderr else "unknown"
    raise SystemExit(f"patch failed: {err}")


if __name__ == "__main__":
    main()
