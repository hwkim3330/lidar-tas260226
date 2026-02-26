#!/usr/bin/env python3
"""Estimate LiDAR period and optimize TAS (C/O/C) with phase alignment."""

from __future__ import annotations

import argparse
import json
import math
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
    runtime = KETI_DIR / "lidar-tas260226" / "_period_phase_opt_runtime.yaml"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(yml, encoding="ascii")
    last_err = None
    for _ in range(4):
        try:
            run(["./keti-tsn", "patch", str(runtime)], cwd=str(KETI_DIR))
            return
        except subprocess.CalledProcessError as e:
            last_err = e
            time.sleep(0.15)
    raise last_err


def measure(duration_s: float, sample_period_s: float) -> dict:
    end_t = time.time() + duration_s
    rows = []
    while time.time() < end_t:
        try:
            s = requests.get(SERVER_STATS_URL, timeout=0.8).json()
            rows.append(
                {
                    "fc": 100.0 * float(s.get("frame_completeness", 0.0)),
                    "fps": float(s.get("fps", 0.0)),
                    "pps": float(s.get("pps", 0.0)),
                    "gap_mean_us": float(s.get("gap_mean_us", 0.0)),
                    "jitter": float(s.get("gap_stdev_us", 0.0)),
                }
            )
        except Exception:
            pass
        time.sleep(sample_period_s)
    if not rows:
        return {"samples": 0}
    fc = [r["fc"] for r in rows]
    fps = [r["fps"] for r in rows]
    pps = [r["pps"] for r in rows]
    gap = [r["gap_mean_us"] for r in rows]
    jit = [r["jitter"] for r in rows]
    fcs = sorted(fc)
    return {
        "samples": len(rows),
        "fc_mean": statistics.mean(fc),
        "fc_min": min(fc),
        "fc_p01": fcs[max(0, int(len(fcs) * 0.01) - 1)],
        "fc_p05": fcs[max(0, int(len(fcs) * 0.05) - 1)],
        "fps_mean": statistics.mean(fps),
        "fps_min": min(fps),
        "pps_mean": statistics.mean(pps),
        "gap_mean_us": statistics.mean(gap),
        "jitter_mean": statistics.mean(jit),
    }


def mk_coc_entries(cycle_ns: int, open_ns: int, ratio: float) -> tuple[list[dict[str, int]], int, int]:
    close_ns = cycle_ns - open_ns
    front = int(round(close_ns * ratio))
    back = close_ns - front
    entries = [
        {"gate": 254, "dur_ns": front},
        {"gate": 255, "dur_ns": open_ns},
        {"gate": 254, "dur_ns": back},
    ]
    return entries, front, back


def score(m: dict) -> float:
    # prioritize completeness, then fps floor
    fps_penalty = 0.0
    if m["fps_mean"] < 9.5:
        fps_penalty = (9.5 - m["fps_mean"]) * 6.0
    return m["fc_mean"] - fps_penalty


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle-ns", type=int, default=781_250)
    ap.add_argument("--opens-us", default="60,70,80,90,100,120,150")
    ap.add_argument("--ratios", default="0.20,0.30,0.40,0.50,0.60,0.70,0.80")
    ap.add_argument("--coarse-phases", type=int, default=24)
    ap.add_argument("--coarse-duration-s", type=float, default=0.8)
    ap.add_argument("--fine-step-us", type=int, default=2)
    ap.add_argument("--fine-window-us", type=int, default=40)
    ap.add_argument("--fine-duration-s", type=float, default=1.2)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--baseline-duration-s", type=int, default=60)
    ap.add_argument("--best-soak-s", type=int, default=600)
    ap.add_argument("--sample-period-s", type=float, default=0.25)
    ap.add_argument("--settle-s", type=float, default=0.2)
    ap.add_argument("--base-offset-sec", type=int, default=2)
    args = ap.parse_args()

    cycle_ns = args.cycle_ns
    opens_ns = [int(round(float(x.strip()) * 1000.0)) for x in args.opens_us.split(",") if x.strip()]
    ratios = [float(x.strip()) for x in args.ratios.split(",") if x.strip()]
    coarse_step = cycle_ns // args.coarse_phases
    coarse_phase_list = [(i * coarse_step) % cycle_ns for i in range(args.coarse_phases)]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = ROOT / "data" / f"period_phase_opt_{ts}.json"
    out_md = ROOT / "data" / f"period_phase_opt_{ts}.md"

    period_est = {}
    coarse_rows = []
    fine_rows = []
    best = None
    baseline = {}
    best_soak = {}

    try:
        set_phase_lock(False)

        # Step 0: all-open period estimate
        apply_entries(cycle_ns, [{"gate": 255, "dur_ns": cycle_ns}], 0, args.base_offset_sec)
        time.sleep(1.0)
        period_est = measure(args.baseline_duration_s, args.sample_period_s)
        pps = max(1e-9, period_est["pps_mean"])
        period_est["pkt_period_us_est"] = 1e6 / pps
        print(f"[period] pps_mean={pps:.3f}, pkt_period_us_est={period_est['pkt_period_us_est']:.3f}")

        # Step 1: coarse grid
        for open_ns in opens_ns:
            if open_ns <= 0 or open_ns >= cycle_ns:
                continue
            for r in ratios:
                entries, c_front, c_back = mk_coc_entries(cycle_ns, open_ns, r)
                for ph in coarse_phase_list:
                    apply_entries(cycle_ns, entries, ph, args.base_offset_sec)
                    time.sleep(args.settle_s)
                    m = measure(args.coarse_duration_s, 0.2)
                    row = {
                        "open_ns": open_ns,
                        "open_us": open_ns / 1000.0,
                        "ratio": r,
                        "close_front_ns": c_front,
                        "close_back_ns": c_back,
                        "phase_ns": ph,
                        **m,
                    }
                    row["score"] = score(row)
                    coarse_rows.append(row)
                    print(
                        f"[coarse] open={row['open_us']:.1f} ratio={r:.2f} ph={ph:6d} "
                        f"fc={row['fc_mean']:.2f}% fps={row['fps_mean']:.2f}"
                    )

        ranked = sorted(coarse_rows, key=lambda x: (x["score"], x["fc_min"]), reverse=True)
        seeds = ranked[: max(1, args.top_k)]

        # Step 2: fine phase search around top candidates
        for s in seeds:
            fine_step_ns = args.fine_step_us * 1000
            win_ns = args.fine_window_us * 1000
            entries, c_front, c_back = mk_coc_entries(cycle_ns, int(s["open_ns"]), float(s["ratio"]))
            for d in range(-win_ns, win_ns + fine_step_ns, fine_step_ns):
                ph = (int(s["phase_ns"]) + d) % cycle_ns
                apply_entries(cycle_ns, entries, ph, args.base_offset_sec)
                time.sleep(args.settle_s)
                m = measure(args.fine_duration_s, 0.2)
                row = {
                    "seed_open_us": s["open_us"],
                    "seed_ratio": s["ratio"],
                    "seed_phase_ns": s["phase_ns"],
                    "open_ns": int(s["open_ns"]),
                    "open_us": s["open_us"],
                    "ratio": float(s["ratio"]),
                    "close_front_ns": c_front,
                    "close_back_ns": c_back,
                    "phase_ns": ph,
                    **m,
                }
                row["score"] = score(row)
                fine_rows.append(row)
                print(
                    f"[fine] open={row['open_us']:.1f} ratio={row['ratio']:.2f} ph={ph:6d} "
                    f"fc={row['fc_mean']:.2f}% fps={row['fps_mean']:.2f}"
                )

        best = max(fine_rows, key=lambda x: (x["score"], x["fc_p01"], x["fc_min"], x["fps_min"]))
        print(
            f"[best] open={best['open_us']:.1f} ratio={best['ratio']:.2f} phase={best['phase_ns']} "
            f"front/open/back={best['close_front_ns']}/{best['open_ns']}/{best['close_back_ns']}"
        )

        # Step 3: long soak all-open vs best
        apply_entries(cycle_ns, [{"gate": 255, "dur_ns": cycle_ns}], 0, args.base_offset_sec)
        time.sleep(1.0)
        baseline = measure(args.best_soak_s, 0.5)
        print(f"[soak baseline] {baseline}")

        best_entries = [
            {"gate": 254, "dur_ns": int(best["close_front_ns"])},
            {"gate": 255, "dur_ns": int(best["open_ns"])},
            {"gate": 254, "dur_ns": int(best["close_back_ns"])},
        ]
        apply_entries(cycle_ns, best_entries, int(best["phase_ns"]), args.base_offset_sec)
        time.sleep(1.0)
        best_soak = measure(args.best_soak_s, 0.5)
        print(f"[soak best] {best_soak}")

    finally:
        # keep best config after run if found, otherwise all-open
        if best:
            try:
                entries = [
                    {"gate": 254, "dur_ns": int(best["close_front_ns"])},
                    {"gate": 255, "dur_ns": int(best["open_ns"])},
                    {"gate": 254, "dur_ns": int(best["close_back_ns"])},
                ]
                apply_entries(cycle_ns, entries, int(best["phase_ns"]), args.base_offset_sec)
            except Exception:
                run(["./keti-tsn", "patch", str(ALL_OPEN_YAML)], cwd=str(KETI_DIR))
        else:
            run(["./keti-tsn", "patch", str(ALL_OPEN_YAML)], cwd=str(KETI_DIR))
        set_phase_lock(False)

    result = {
        "timestamp": ts,
        "cycle_ns": cycle_ns,
        "phase_lock": False,
        "period_estimate_all_open": period_est,
        "coarse_top_k": sorted(coarse_rows, key=lambda x: (x["score"], x["fc_min"]), reverse=True)[: args.top_k],
        "best": best,
        "baseline_soak_all_open": baseline,
        "best_soak": best_soak,
        "delta_best_minus_all_open": {
            "fc_mean": best_soak.get("fc_mean", 0.0) - baseline.get("fc_mean", 0.0),
            "fc_p01": best_soak.get("fc_p01", 0.0) - baseline.get("fc_p01", 0.0),
            "fc_p05": best_soak.get("fc_p05", 0.0) - baseline.get("fc_p05", 0.0),
            "fps_mean": best_soak.get("fps_mean", 0.0) - baseline.get("fps_mean", 0.0),
        },
        "coarse_rows_count": len(coarse_rows),
        "fine_rows_count": len(fine_rows),
    }
    out_json.write_text(json.dumps(result, indent=2), encoding="ascii")

    lines = [
        "# LiDAR Period + Phase Optimizer",
        "",
        f"- source: `{out_json.name}`",
        "",
        "## Period Estimate (All-Open)",
        f"- pps_mean: {period_est.get('pps_mean',0):.3f}",
        f"- pkt_period_us_est: {period_est.get('pkt_period_us_est',0):.3f}",
        f"- gap_mean_us: {period_est.get('gap_mean_us',0):.3f}",
        "",
        "## Best Config",
        f"- open_us: {best.get('open_us',0):.3f}",
        f"- ratio(close_front): {best.get('ratio',0):.3f}",
        f"- phase_ns: {best.get('phase_ns',0)}",
        f"- close_front/open/close_back(ns): {best.get('close_front_ns',0)}/{best.get('open_ns',0)}/{best.get('close_back_ns',0)}",
        "",
        "## Long Soak Compare",
        f"- all_open fc_mean={baseline.get('fc_mean',0):.3f}, fc_p01={baseline.get('fc_p01',0):.3f}, fc_p05={baseline.get('fc_p05',0):.3f}, fps_mean={baseline.get('fps_mean',0):.3f}",
        f"- best     fc_mean={best_soak.get('fc_mean',0):.3f}, fc_p01={best_soak.get('fc_p01',0):.3f}, fc_p05={best_soak.get('fc_p05',0):.3f}, fps_mean={best_soak.get('fps_mean',0):.3f}",
        f"- delta(best-all_open): fc_mean={result['delta_best_minus_all_open']['fc_mean']:+.3f}, "
        f"fc_p01={result['delta_best_minus_all_open']['fc_p01']:+.3f}, "
        f"fc_p05={result['delta_best_minus_all_open']['fc_p05']:+.3f}, "
        f"fps_mean={result['delta_best_minus_all_open']['fps_mean']:+.3f}",
    ]
    out_md.write_text("\n".join(lines) + "\n", encoding="ascii")

    print(f"saved: {out_json}")
    print(f"saved: {out_md}")


if __name__ == "__main__":
    main()
