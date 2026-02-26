#!/usr/bin/env python3
"""781us-cycle TAS sweep: start with wide open window and reduce it step-by-step."""
import argparse
import json
import os
import re
import socket
import statistics
import subprocess
import time
from datetime import datetime


LIDAR_PORT = 7502
EXPECTED_PPS = 1280.0
TC0_OPEN = 0x01
ALL_OPEN = 0xFF
TC0_CLOSED = 0xFE


def run_cmd(cmd, cwd=None, timeout=30):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def get_tai_now():
    # date +%s returns system epoch seconds; in most PTP setups this is sufficient for base-time scheduling.
    r = run_cmd(["date", "+%s"])
    if r.returncode != 0:
        raise RuntimeError(f"failed to get current time: {r.stderr.strip()}")
    return int(r.stdout.strip())


def get_switch_current_time(keti_dir, fetch_yaml):
    r = run_cmd(["./keti-tsn", "fetch", fetch_yaml], cwd=keti_dir, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"failed to fetch switch time: {r.stderr.strip()}")
    txt = r.stdout
    m = re.search(
        r"current-time:\s*\n\s*nanoseconds:\s*(\d+)\s*\n\s*seconds:\s*(\d+)",
        txt,
        re.MULTILINE,
    )
    if not m:
        raise RuntimeError("failed to parse switch current-time from fetch output")
    nsec = int(m.group(1))
    sec = int(m.group(2))
    return sec, nsec


def build_tas_yaml(cycle_us, open_us, base_sec, base_nsec):
    close_us = cycle_us - open_us
    if close_us < 0:
        raise ValueError("open_us must be <= cycle_us")

    open_ns = open_us * 1000
    close_ns = close_us * 1000
    cycle_ns = cycle_us * 1000

    if open_us == 0:
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
      gate-control-entry:
        - index: 0
          operation-name: set-gate-states
          gate-states-value: {TC0_CLOSED}
          time-interval-value: {cycle_ns}
    config-change: true
"""

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
      gate-control-entry:
        - index: 0
          operation-name: set-gate-states
          gate-states-value: {TC0_OPEN}
          time-interval-value: {open_ns}
        - index: 1
          operation-name: set-gate-states
          gate-states-value: {TC0_CLOSED}
          time-interval-value: {close_ns}
    config-change: true
"""


def patch_tas(keti_dir, yaml_content):
    yaml_path = os.path.join(keti_dir, "lidar-tas260226", "_runtime_tas.yaml")
    os.makedirs(os.path.dirname(yaml_path), exist_ok=True)
    with open(yaml_path, "w", encoding="ascii") as f:
        f.write(yaml_content)
    r = run_cmd(["./keti-tsn", "patch", yaml_path], cwd=keti_dir)
    ok = (r.returncode == 0) and ("Failed" not in r.stdout)
    return ok, r.stdout, r.stderr


def measure_udp(duration_sec, rcvbuf_bytes):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf_bytes)
    sock.bind(("0.0.0.0", LIDAR_PORT))
    sock.settimeout(0.2)

    ts = []
    t0 = time.monotonic()
    while True:
        if time.monotonic() - t0 >= duration_sec:
            break
        try:
            _data, _addr = sock.recvfrom(65535)
            ts.append(time.perf_counter())
        except socket.timeout:
            continue
    sock.close()

    out = {
        "packets": len(ts),
        "pps": 0.0,
        "completeness_pct": 0.0,
        "gap_mean_us": 0.0,
        "gap_stdev_us": 0.0,
        "gap_p99_us": 0.0,
        "gap_max_us": 0.0,
        "burst_pct": 0.0,
    }
    if len(ts) < 3:
        return out

    elapsed = ts[-1] - ts[0]
    pps = len(ts) / elapsed if elapsed > 0 else 0.0
    gaps = [(ts[i + 1] - ts[i]) * 1e6 for i in range(len(ts) - 1)]
    gaps_sorted = sorted(gaps)
    p99_idx = int(0.99 * (len(gaps_sorted) - 1))

    out["pps"] = pps
    out["completeness_pct"] = min(100.0, (pps / EXPECTED_PPS) * 100.0)
    out["gap_mean_us"] = statistics.mean(gaps)
    out["gap_stdev_us"] = statistics.stdev(gaps) if len(gaps) > 1 else 0.0
    out["gap_p99_us"] = gaps_sorted[p99_idx]
    out["gap_max_us"] = max(gaps)
    out["burst_pct"] = sum(1 for g in gaps if g < 50.0) * 100.0 / len(gaps)
    return out


def build_open_list(start_open_us, min_open_us, step_us):
    vals = []
    cur = start_open_us
    while cur >= min_open_us:
        vals.append(cur)
        cur -= step_us
    if not vals or vals[-1] != min_open_us:
        vals.append(min_open_us)
    return vals


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--keti-dir", default="/home/kim/keti-tsn-cli-new")
    p.add_argument("--cycle-us", type=int, default=781)
    p.add_argument("--start-open-us", type=int, default=200)
    p.add_argument("--min-open-us", type=int, default=36)
    p.add_argument("--step-us", type=int, default=4)
    p.add_argument("--duration", type=int, default=20)
    p.add_argument("--settle", type=float, default=1.0)
    p.add_argument("--rcvbuf", type=int, default=16 * 1024 * 1024)
    p.add_argument("--base-time-mode", choices=["zero", "host-future", "switch-future", "tai-future"], default="zero")
    p.add_argument("--base-time-offset-sec", type=int, default=2)
    p.add_argument("--phase-offset-ns", type=int, default=0)
    p.add_argument("--phase-sweep-step-us", type=int, default=0)
    p.add_argument("--fetch-yaml", default="/home/kim/lidar-tas/configs/fetch-tas.yaml")
    p.add_argument("--output", default="")
    return p.parse_args()


def main():
    a = parse_args()
    if a.start_open_us > a.cycle_us:
        raise SystemExit("start-open-us must be <= cycle-us")
    if a.min_open_us < 0:
        raise SystemExit("min-open-us must be >= 0")
    if a.step_us < 1:
        raise SystemExit("step-us must be >= 1")

    open_list = build_open_list(a.start_open_us, a.min_open_us, a.step_us)
    cycle_ns = a.cycle_us * 1000
    phase_list_ns = [a.phase_offset_ns % cycle_ns]
    if a.phase_sweep_step_us > 0:
        step_ns = a.phase_sweep_step_us * 1000
        phase_list_ns = list(range(0, cycle_ns, step_ns))
        open_list = [a.start_open_us]

    results = []

    print("=== TAS 781us wide->narrow sweep ===")
    print(f"cycle={a.cycle_us}us  open={open_list[0]}..{open_list[-1]} step={a.step_us}us")
    print(f"duration={a.duration}s settle={a.settle}s base-time={a.base_time_mode}")
    if a.phase_sweep_step_us > 0:
        print(f"phase sweep: step={a.phase_sweep_step_us}us count={len(phase_list_ns)}")
    else:
        print(f"phase offset: {phase_list_ns[0]}ns")

    test_cases = []
    for open_us in open_list:
        for phase_ns in phase_list_ns:
            test_cases.append((open_us, phase_ns))

    for idx, (open_us, phase_ns) in enumerate(test_cases, start=1):
        close_us = a.cycle_us - open_us
        if a.base_time_mode in ("tai-future", "host-future"):
            base_sec = get_tai_now() + a.base_time_offset_sec
            base_nsec = phase_ns
        elif a.base_time_mode == "switch-future":
            sw_sec, sw_nsec = get_switch_current_time(a.keti_dir, a.fetch_yaml)
            total_ns = sw_sec * 1_000_000_000 + sw_nsec + a.base_time_offset_sec * 1_000_000_000 + phase_ns
            base_sec = total_ns // 1_000_000_000
            base_nsec = total_ns % 1_000_000_000
        else:
            base_sec = 0
            base_nsec = phase_ns

        yml = build_tas_yaml(a.cycle_us, open_us, base_sec, base_nsec)
        ok, stdout, stderr = patch_tas(a.keti_dir, yml)
        if not ok:
            print(f"[{idx}/{len(test_cases)}] open={open_us}us phase={phase_ns}ns patch FAILED")
            print(stdout[-300:])
            print(stderr[-300:])
            continue

        time.sleep(a.settle)
        m = measure_udp(a.duration, a.rcvbuf)
        m.update({
            "cycle_us": a.cycle_us,
            "open_us": open_us,
            "close_us": close_us,
            "open_pct": round(open_us * 100.0 / a.cycle_us, 2),
            "base_time_mode": a.base_time_mode,
            "base_time_sec": base_sec,
            "base_time_nsec": base_nsec,
            "phase_offset_ns": phase_ns,
        })
        results.append(m)
        print(
            f"[{idx}/{len(test_cases)}] open={open_us:>3} close={close_us:>3} phase={phase_ns:>6}ns "
            f"comp={m['completeness_pct']:6.2f}% pps={m['pps']:7.1f} "
            f"sd={m['gap_stdev_us']:7.1f}us p99={m['gap_p99_us']:7.1f}us"
        )

    if not results:
        raise SystemExit("no successful measurements")

    os.makedirs("/home/kim/lidar-tas260226/data", exist_ok=True)
    if a.output:
        out = a.output
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = f"/home/kim/lidar-tas260226/data/sweep_781_wide_to_narrow_{ts}.json"

    with open(out, "w", encoding="ascii") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
