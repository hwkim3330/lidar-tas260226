#!/usr/bin/env python3
"""NS-level TAS alignment: absolute close_front_ns + phase_ns search."""

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
    runtime = KETI_DIR / "lidar-tas260226" / "_ns_fine_runtime.yaml"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(yml, encoding="ascii")
    last = None
    for _ in range(4):
        try:
            run(["./keti-tsn", "patch", str(runtime)], cwd=str(KETI_DIR))
            return
        except subprocess.CalledProcessError as e:
            last = e
            time.sleep(0.15)
    raise last


def measure(duration_s: float, sample_s: float) -> dict:
    end = time.time() + duration_s
    rows = []
    while time.time() < end:
        try:
            s = requests.get(SERVER_STATS_URL, timeout=0.8).json()
            rows.append(
                {
                    "fc": 100.0 * float(s.get("frame_completeness", 0.0)),
                    "fps": float(s.get("fps", 0.0)),
                    "pps": float(s.get("pps", 0.0)),
                    "gap_mean_us": float(s.get("gap_mean_us", 0.0)),
                    "jit": float(s.get("gap_stdev_us", 0.0)),
                }
            )
        except Exception:
            pass
        time.sleep(sample_s)
    if not rows:
        return {"samples": 0}
    fc = [r["fc"] for r in rows]
    fps = [r["fps"] for r in rows]
    pps = [r["pps"] for r in rows]
    gap = [r["gap_mean_us"] for r in rows]
    jit = [r["jit"] for r in rows]
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
        "jit_mean": statistics.mean(jit),
    }


def mk_entries(cycle_ns: int, open_ns: int, close_front_ns: int) -> tuple[list[dict[str, int]], int]:
    close_total = cycle_ns - open_ns
    close_back = close_total - close_front_ns
    if close_front_ns <= 0 or close_back <= 0:
        raise ValueError("invalid close split")
    return [
        {"gate": 254, "dur_ns": close_front_ns},
        {"gate": 255, "dur_ns": open_ns},
        {"gate": 254, "dur_ns": close_back},
    ], close_back


def score(m: dict) -> float:
    p = 0.0
    if m["fps_mean"] < 9.5:
        p += (9.5 - m["fps_mean"]) * 8.0
    return m["fc_mean"] - p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle-ns", type=int, default=781_250)
    ap.add_argument("--opens-us", default="50,60,70,80,100,120,150")
    ap.add_argument("--delta-range-ns", type=int, default=200_000)
    ap.add_argument("--delta-step-ns", type=int, default=20_000)
    ap.add_argument("--phase-step-ns", type=int, default=20_000)
    ap.add_argument("--coarse-duration-s", type=float, default=0.6)
    ap.add_argument("--coarse-sample-s", type=float, default=0.2)
    ap.add_argument("--coarse-settle-s", type=float, default=0.2)
    ap.add_argument("--fine-window-ns", type=int, default=20_000)
    ap.add_argument("--fine-step-ns", type=int, default=1_000)
    ap.add_argument("--fine-duration-s", type=float, default=0.9)
    ap.add_argument("--fine-sample-s", type=float, default=0.2)
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--baseline-s", type=int, default=60)
    ap.add_argument("--soak-s", type=int, default=300)
    ap.add_argument("--base-offset-sec", type=int, default=2)
    args = ap.parse_args()

    cycle_ns = args.cycle_ns
    opens_ns = [int(round(float(x.strip()) * 1000.0)) for x in args.opens_us.split(",") if x.strip()]
    phase_list = list(range(0, cycle_ns, args.phase_step_ns))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = ROOT / "data" / f"ns_fine_align_{ts}.json"
    out_md = ROOT / "data" / f"ns_fine_align_{ts}.md"

    period_est = {}
    coarse = []
    fine = []
    baseline = {}
    best_soak = {}
    best = None

    try:
        set_phase_lock(False)

        # baseline/period estimate
        apply_entries(cycle_ns, [{"gate": 255, "dur_ns": cycle_ns}], 0, args.base_offset_sec)
        time.sleep(1.0)
        period_est = measure(args.baseline_s, 0.25)
        if period_est.get("pps_mean", 0) > 0:
            period_est["pkt_period_us_est"] = 1e6 / period_est["pps_mean"]
        else:
            period_est["pkt_period_us_est"] = 0.0
        print(
            f"[period] pps_mean={period_est.get('pps_mean',0):.3f}, "
            f"pkt_period_us_est={period_est.get('pkt_period_us_est',0):.3f}"
        )

        # coarse: absolute close_front_ns + phase_ns
        for open_ns in opens_ns:
            if open_ns <= 0 or open_ns >= cycle_ns:
                continue
            close_total = cycle_ns - open_ns
            center = close_total // 2
            d_start = -min(args.delta_range_ns, center - 1)
            d_end = min(args.delta_range_ns, close_total - center - 1)
            for d in range(d_start, d_end + 1, args.delta_step_ns):
                c_front = center + d
                try:
                    entries, c_back = mk_entries(cycle_ns, open_ns, c_front)
                except ValueError:
                    continue
                for ph in phase_list:
                    apply_entries(cycle_ns, entries, ph, args.base_offset_sec)
                    time.sleep(args.coarse_settle_s)
                    m = measure(args.coarse_duration_s, args.coarse_sample_s)
                    row = {
                        "open_ns": open_ns,
                        "open_us": open_ns / 1000.0,
                        "delta_ns": d,
                        "close_front_ns": c_front,
                        "close_back_ns": c_back,
                        "phase_ns": ph,
                        **m,
                    }
                    row["score"] = score(row)
                    coarse.append(row)
                    print(
                        f"[coarse] open={row['open_us']:.1f} front={c_front} back={c_back} "
                        f"ph={ph:6d} fc={row['fc_mean']:.2f}% fps={row['fps_mean']:.2f}"
                    )

        seeds = sorted(coarse, key=lambda x: (x["score"], x["fc_p01"], x["fc_min"]), reverse=True)[: args.top_k]

        # fine around seeds (true ns-level)
        for s in seeds:
            for d2 in range(-args.fine_window_ns, args.fine_window_ns + 1, args.fine_step_ns):
                c_front = int(s["close_front_ns"]) + d2
                try:
                    entries, c_back = mk_entries(cycle_ns, int(s["open_ns"]), c_front)
                except ValueError:
                    continue
                for p2 in range(-args.fine_window_ns, args.fine_window_ns + 1, args.fine_step_ns):
                    ph = (int(s["phase_ns"]) + p2) % cycle_ns
                    apply_entries(cycle_ns, entries, ph, args.base_offset_sec)
                    time.sleep(args.coarse_settle_s)
                    m = measure(args.fine_duration_s, args.fine_sample_s)
                    row = {
                        "seed_open_us": s["open_us"],
                        "seed_front": s["close_front_ns"],
                        "seed_phase": s["phase_ns"],
                        "open_ns": int(s["open_ns"]),
                        "open_us": s["open_us"],
                        "close_front_ns": c_front,
                        "close_back_ns": c_back,
                        "phase_ns": ph,
                        **m,
                    }
                    row["score"] = score(row)
                    fine.append(row)
                    print(
                        f"[fine] open={row['open_us']:.1f} front={c_front} back={c_back} ph={ph:6d} "
                        f"fc={row['fc_mean']:.2f}% fps={row['fps_mean']:.2f}"
                    )

        best = max(fine, key=lambda x: (x["score"], x["fc_p01"], x["fc_min"], x["fps_min"]))
        print(
            f"[best] open={best['open_us']:.1f} front/open/back={best['close_front_ns']}/{best['open_ns']}/{best['close_back_ns']} "
            f"phase={best['phase_ns']}"
        )

        # soak compare
        apply_entries(cycle_ns, [{"gate": 255, "dur_ns": cycle_ns}], 0, args.base_offset_sec)
        time.sleep(1.0)
        baseline = measure(args.soak_s, 0.5)
        print(f"[soak all-open] {baseline}")

        best_entries, _ = mk_entries(cycle_ns, int(best["open_ns"]), int(best["close_front_ns"]))
        apply_entries(cycle_ns, best_entries, int(best["phase_ns"]), args.base_offset_sec)
        time.sleep(1.0)
        best_soak = measure(args.soak_s, 0.5)
        print(f"[soak best] {best_soak}")

    finally:
        if best:
            try:
                entries, _ = mk_entries(cycle_ns, int(best["open_ns"]), int(best["close_front_ns"]))
                apply_entries(cycle_ns, entries, int(best["phase_ns"]), args.base_offset_sec)
            except Exception:
                run(["./keti-tsn", "patch", str(ALL_OPEN_YAML)], cwd=str(KETI_DIR))
        else:
            run(["./keti-tsn", "patch", str(ALL_OPEN_YAML)], cwd=str(KETI_DIR))
        set_phase_lock(False)

    out = {
        "timestamp": ts,
        "cycle_ns": cycle_ns,
        "period_estimate_all_open": period_est,
        "best": best,
        "coarse_top_k": sorted(coarse, key=lambda x: (x["score"], x["fc_p01"]), reverse=True)[: args.top_k],
        "baseline_soak_all_open": baseline,
        "best_soak": best_soak,
        "delta_best_minus_all_open": {
            "fc_mean": best_soak.get("fc_mean", 0.0) - baseline.get("fc_mean", 0.0),
            "fc_p01": best_soak.get("fc_p01", 0.0) - baseline.get("fc_p01", 0.0),
            "fc_p05": best_soak.get("fc_p05", 0.0) - baseline.get("fc_p05", 0.0),
            "fps_mean": best_soak.get("fps_mean", 0.0) - baseline.get("fps_mean", 0.0),
        },
        "coarse_count": len(coarse),
        "fine_count": len(fine),
    }
    out_json.write_text(json.dumps(out, indent=2), encoding="ascii")

    lines = [
        "# NS Fine Alignment",
        "",
        f"- source: `{out_json.name}`",
        "",
        f"- period_est_pps: {period_est.get('pps_mean',0):.3f}",
        f"- period_est_us: {period_est.get('pkt_period_us_est',0):.3f}",
        "",
        f"- best open_us: {best.get('open_us',0):.3f}",
        f"- best close_front/open/close_back(ns): {best.get('close_front_ns',0)}/{best.get('open_ns',0)}/{best.get('close_back_ns',0)}",
        f"- best phase_ns: {best.get('phase_ns',0)}",
        "",
        f"- soak all_open: fc_mean={baseline.get('fc_mean',0):.3f}, fc_p01={baseline.get('fc_p01',0):.3f}, fps_mean={baseline.get('fps_mean',0):.3f}",
        f"- soak best    : fc_mean={best_soak.get('fc_mean',0):.3f}, fc_p01={best_soak.get('fc_p01',0):.3f}, fps_mean={best_soak.get('fps_mean',0):.3f}",
        f"- delta(best-all_open): fc_mean={out['delta_best_minus_all_open']['fc_mean']:+.3f}, "
        f"fc_p01={out['delta_best_minus_all_open']['fc_p01']:+.3f}, "
        f"fc_p05={out['delta_best_minus_all_open']['fc_p05']:+.3f}, "
        f"fps_mean={out['delta_best_minus_all_open']['fps_mean']:+.3f}",
    ]
    out_md.write_text("\n".join(lines) + "\n", encoding="ascii")

    print(f"saved: {out_json}")
    print(f"saved: {out_md}")


if __name__ == "__main__":
    main()

