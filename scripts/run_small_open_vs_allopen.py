#!/usr/bin/env python3
"""Compare all-open vs small-open C/O/C with phase search and soak tests."""

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
    runtime = KETI_DIR / "lidar-tas260226" / "_small_open_vs_allopen_runtime.yaml"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(yml, encoding="ascii")
    run(["./keti-tsn", "patch", str(runtime)], cwd=str(KETI_DIR))


def measure(duration_s: float, sample_period_s: float) -> dict:
    end = time.time() + duration_s
    rows = []
    while time.time() < end:
        try:
            s = requests.get(SERVER_STATS_URL, timeout=0.8).json()
            rows.append(
                {
                    "fc": 100.0 * float(s.get("frame_completeness", 0.0)),
                    "fps": float(s.get("fps", 0.0)),
                    "jitter": float(s.get("gap_stdev_us", 0.0)),
                    "pps": float(s.get("pps", 0.0)),
                }
            )
        except Exception:
            pass
        time.sleep(sample_period_s)
    if not rows:
        return {"samples": 0}
    fc = [r["fc"] for r in rows]
    fps = [r["fps"] for r in rows]
    jit = [r["jitter"] for r in rows]
    pps = [r["pps"] for r in rows]
    fcs = sorted(fc)
    return {
        "samples": len(rows),
        "fc_mean": statistics.mean(fc),
        "fc_min": min(fc),
        "fc_p01": fcs[max(0, int(len(fcs) * 0.01) - 1)],
        "fc_p05": fcs[max(0, int(len(fcs) * 0.05) - 1)],
        "fps_mean": statistics.mean(fps),
        "fps_min": min(fps),
        "jitter_mean": statistics.mean(jit),
        "pps_mean": statistics.mean(pps),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle-ns", type=int, default=781_250)
    ap.add_argument("--opens-us", default="30,40,50,60")
    ap.add_argument("--phases", type=int, default=20)
    ap.add_argument("--search-duration-s", type=float, default=1.5)
    ap.add_argument("--soak-duration-s", type=int, default=180)
    ap.add_argument("--sample-period-s", type=float, default=0.5)
    ap.add_argument("--settle-s", type=float, default=0.4)
    ap.add_argument("--base-offset-sec", type=int, default=2)
    ap.add_argument("--close-front-ratios", default="0.5,0.4,0.6,0.3,0.7")
    args = ap.parse_args()

    cycle_ns = args.cycle_ns
    opens_us = [int(x.strip()) for x in args.opens_us.split(",") if x.strip()]
    phase_step = cycle_ns // args.phases
    phase_list = [(i * phase_step) % cycle_ns for i in range(args.phases)]
    close_front_ratios = [float(x.strip()) for x in args.close_front_ratios.split(",") if x.strip()]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = ROOT / "data" / f"allopen_vs_smallopen_{ts}.json"
    out_md = ROOT / "data" / f"allopen_vs_smallopen_{ts}.md"

    phase_search = []
    soak_results = []

    try:
        set_phase_lock(False)

        # baseline all-open soak
        print("soak: all-open baseline")
        apply_entries(cycle_ns, [{"gate": 255, "dur_ns": cycle_ns}], 0, args.base_offset_sec)
        time.sleep(1.0)
        base = measure(args.soak_duration_s, args.sample_period_s)
        soak_results.append({"name": "all_open", "open_us": cycle_ns / 1000.0, "phase_ns": 0, "summary": base})
        print(f"  all-open -> {base}")

        # find best split+phase per small-open C/O/C then soak
        for open_us in opens_us:
            open_ns = open_us * 1000
            if open_ns <= 0 or open_ns >= cycle_ns:
                continue
            close_ns = cycle_ns - open_ns
            print(f"search: open={open_us}us (phase + close_front_ratio)")
            rows = []
            for ratio in close_front_ratios:
                c_front = int(round(close_ns * ratio))
                c_back = close_ns - c_front
                if c_front <= 0 or c_back <= 0:
                    continue
                entries = [
                    {"gate": 254, "dur_ns": c_front},
                    {"gate": 255, "dur_ns": open_ns},
                    {"gate": 254, "dur_ns": c_back},
                ]
                for ph in phase_list:
                    apply_entries(cycle_ns, entries, ph, args.base_offset_sec)
                    time.sleep(args.settle_s)
                    m = measure(args.search_duration_s, 0.2)
                    row = {
                        "open_us": open_us,
                        "close_front_ratio": ratio,
                        "close_front_ns": c_front,
                        "close_back_ns": c_back,
                        "phase_ns": ph,
                        **m,
                    }
                    rows.append(row)
                    print(
                        f"  r={ratio:.2f} ph={ph:6d} fc={m.get('fc_mean',0):.2f}% "
                        f"fps={m.get('fps_mean',0):.2f}"
                    )
            best = max(rows, key=lambda r: (r.get("fc_mean", 0.0), r.get("fps_mean", 0.0)))
            phase_search.extend(rows)
            print(
                f"  best for {open_us}us -> ratio={best['close_front_ratio']:.2f}, "
                f"phase={best['phase_ns']}ns"
            )

            entries = [
                {"gate": 254, "dur_ns": int(best["close_front_ns"])},
                {"gate": 255, "dur_ns": open_ns},
                {"gate": 254, "dur_ns": int(best["close_back_ns"])},
            ]
            apply_entries(cycle_ns, entries, int(best["phase_ns"]), args.base_offset_sec)
            time.sleep(1.0)
            s = measure(args.soak_duration_s, args.sample_period_s)
            soak_results.append(
                {
                    "name": f"coc_open_{open_us}us",
                    "open_us": open_us,
                    "phase_ns": int(best["phase_ns"]),
                    "close_front_ratio": float(best["close_front_ratio"]),
                    "close_front_ns": int(best["close_front_ns"]),
                    "close_back_ns": int(best["close_back_ns"]),
                    "summary": s,
                }
            )
            print(
                f"  soak {open_us}us ratio={best['close_front_ratio']:.2f} "
                f"({best['close_front_ns']}/{open_ns}/{best['close_back_ns']}) -> {s}"
            )

    finally:
        run(["./keti-tsn", "patch", str(ALL_OPEN_YAML)], cwd=str(KETI_DIR))
        set_phase_lock(False)

    # delta vs all-open baseline
    baseline = next((x for x in soak_results if x["name"] == "all_open"), None)
    for x in soak_results:
        if not baseline or x["name"] == "all_open":
            x["delta_vs_allopen"] = {}
            continue
        b = baseline["summary"]
        s = x["summary"]
        x["delta_vs_allopen"] = {
            "fc_mean": s.get("fc_mean", 0.0) - b.get("fc_mean", 0.0),
            "fc_p01": s.get("fc_p01", 0.0) - b.get("fc_p01", 0.0),
            "fc_p05": s.get("fc_p05", 0.0) - b.get("fc_p05", 0.0),
            "fps_mean": s.get("fps_mean", 0.0) - b.get("fps_mean", 0.0),
        }

    payload = {
        "timestamp": ts,
        "cycle_ns": cycle_ns,
        "phase_lock": False,
        "phase_search": phase_search,
        "soak_results": soak_results,
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="ascii")

    lines = [
        "# All-Open vs Small-Open (C/O/C)",
        "",
        f"- source: `{out_json.name}`",
        f"- cycle: `{cycle_ns}ns`",
        "",
        "| config | ratio | phase_ns | close_f/open/close_b(ns) | fc_mean | fc_min | fc_p01 | fc_p05 | fps_mean | fps_min | d_fc_mean | d_fc_p01 | d_fc_p05 |",
        "|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for x in soak_results:
        s = x["summary"]
        d = x.get("delta_vs_allopen", {})
        trio = "-"
        ratio = "-"
        if x["name"] != "all_open":
            ratio = f"{x.get('close_front_ratio', 0.0):.2f}"
            trio = f"{x.get('close_front_ns',0)}/{int(x.get('open_us',0)*1000)}/{x.get('close_back_ns',0)}"
        lines.append(
            f"| {x['name']} | {ratio} | {x.get('phase_ns',0)} | {trio} | {s.get('fc_mean',0):.3f} | {s.get('fc_min',0):.3f} | "
            f"{s.get('fc_p01',0):.3f} | {s.get('fc_p05',0):.3f} | {s.get('fps_mean',0):.3f} | {s.get('fps_min',0):.3f} | "
            f"{d.get('fc_mean',0):+.3f} | {d.get('fc_p01',0):+.3f} | {d.get('fc_p05',0):+.3f} |"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="ascii")

    print(f"saved: {out_json}")
    print(f"saved: {out_md}")


if __name__ == "__main__":
    main()
