#!/usr/bin/env python3
"""Long soak comparison for 781.25us TAS entry ordering."""

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
    runtime = KETI_DIR / "lidar-tas260226" / "_781p25_soak_runtime.yaml"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(yml, encoding="ascii")
    run(["./keti-tsn", "patch", str(runtime)], cwd=str(KETI_DIR))


def soak(duration_s: int, sample_period_s: float, progress_name: str) -> tuple[list[dict], dict]:
    end_t = time.time() + duration_s
    rows = []
    next_log = time.time() + 10
    while time.time() < end_t:
        now = time.time()
        try:
            s = requests.get(SERVER_STATS_URL, timeout=0.8).json()
            rows.append(
                {
                    "t": now,
                    "fc_pct": 100.0 * float(s.get("frame_completeness", 0.0)),
                    "fps": float(s.get("fps", 0.0)),
                    "jitter_us": float(s.get("gap_stdev_us", 0.0)),
                    "pps": float(s.get("pps", 0.0)),
                }
            )
        except Exception:
            pass
        if now >= next_log:
            if rows:
                fc_recent = rows[-1]["fc_pct"]
                fps_recent = rows[-1]["fps"]
                print(f"[{progress_name}] t+{int(duration_s - (end_t - now))}s fc={fc_recent:.2f}% fps={fps_recent:.2f}")
            next_log = now + 10
        time.sleep(sample_period_s)

    if not rows:
        return rows, {"samples": 0}
    fc = [r["fc_pct"] for r in rows]
    fps = [r["fps"] for r in rows]
    jit = [r["jitter_us"] for r in rows]
    pps = [r["pps"] for r in rows]
    fc_sorted = sorted(fc)
    p01 = fc_sorted[max(0, int(len(fc_sorted) * 0.01) - 1)]
    p05 = fc_sorted[max(0, int(len(fc_sorted) * 0.05) - 1)]
    return rows, {
        "samples": len(rows),
        "fc_mean": statistics.mean(fc),
        "fc_min": min(fc),
        "fc_p01": p01,
        "fc_p05": p05,
        "fps_mean": statistics.mean(fps),
        "fps_min": min(fps),
        "jitter_mean_us": statistics.mean(jit),
        "pps_mean": statistics.mean(pps),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration-s", type=int, default=600)
    ap.add_argument("--sample-period-s", type=float, default=0.5)
    ap.add_argument("--settle-s", type=int, default=8)
    ap.add_argument("--cycle-ns", type=int, default=781_250)
    ap.add_argument("--phase-ns", type=int, default=0)
    ap.add_argument("--base-offset-sec", type=int, default=2)
    args = ap.parse_args()

    cycle = args.cycle_ns

    # open_total=150us 비교
    cfgs = [
        (
            "open_close_open_75_631p25_75",
            [
                {"gate": 255, "dur_ns": 75_000},
                {"gate": 254, "dur_ns": 631_250},
                {"gate": 255, "dur_ns": 75_000},
            ],
        ),
        (
            "close_open_close_315p625_150_315p625",
            [
                {"gate": 254, "dur_ns": 315_625},
                {"gate": 255, "dur_ns": 150_000},
                {"gate": 254, "dur_ns": 315_625},
            ],
        ),
    ]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = ROOT / "data" / f"soak_781p25_order_compare_{ts}.json"
    out_md = ROOT / "data" / f"soak_781p25_order_compare_{ts}.md"

    results = []
    try:
        set_phase_lock(False)
        for name, entries in cfgs:
            print(f"apply: {name}")
            apply_entries(cycle, entries, args.phase_ns, args.base_offset_sec)
            time.sleep(args.settle_s)
            rows, summary = soak(args.duration_s, args.sample_period_s, name)
            results.append({"name": name, "entries": entries, "summary": summary, "rows": rows})
            print(f"done: {name} -> {summary}")
    finally:
        run(["./keti-tsn", "patch", str(ALL_OPEN_YAML)], cwd=str(KETI_DIR))
        set_phase_lock(False)

    # 우선순위: p01 > min > mean > fps_min
    best = max(
        results,
        key=lambda r: (
            r["summary"].get("fc_p01", 0.0),
            r["summary"].get("fc_min", 0.0),
            r["summary"].get("fc_mean", 0.0),
            r["summary"].get("fps_min", 0.0),
        ),
    )

    payload = {
        "timestamp": ts,
        "cycle_ns": cycle,
        "phase_ns": args.phase_ns,
        "phase_lock": False,
        "duration_s_each": args.duration_s,
        "results": results,
        "best": {"name": best["name"], "summary": best["summary"], "entries": best["entries"]},
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="ascii")

    lines = [
        "# 781.25us Long Soak Order Compare",
        "",
        f"- source: `{out_json.name}`",
        f"- duration(each): `{args.duration_s}s`",
        "",
        "| config | fc_mean | fc_min | fc_p01 | fc_p05 | fps_mean | fps_min | jitter_mean_us | pps_mean |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        s = r["summary"]
        lines.append(
            f"| {r['name']} | {s.get('fc_mean',0):.3f} | {s.get('fc_min',0):.3f} | {s.get('fc_p01',0):.3f} | "
            f"{s.get('fc_p05',0):.3f} | {s.get('fps_mean',0):.3f} | {s.get('fps_min',0):.3f} | "
            f"{s.get('jitter_mean_us',0):.1f} | {s.get('pps_mean',0):.1f} |"
        )
    lines.extend(["", f"best: `{best['name']}`"])
    out_md.write_text("\n".join(lines) + "\n", encoding="ascii")

    print(f"saved: {out_json}")
    print(f"saved: {out_md}")
    print(f"best: {best['name']}")


if __name__ == "__main__":
    main()

