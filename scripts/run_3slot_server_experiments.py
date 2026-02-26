#!/usr/bin/env python3
"""Long-run 3-slot TAS experiments via web server API (/api/gate_multi, /api/stats)."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

API_GATE_SINGLE = "http://127.0.0.1:8080/api/gate"
API_GATE_MULTI = "http://127.0.0.1:8080/api/gate_multi"
API_STATS = "http://127.0.0.1:8080/api/stats"
API_TAS_STATE = "http://127.0.0.1:8080/api/tas_state"


def apply_single(cycle_us: int, open_us: int) -> None:
    r = requests.post(
        API_GATE_SINGLE,
        json={"cycle_us": cycle_us, "open_us": open_us},
        timeout=4.0,
    )
    d = r.json()
    if not d.get("ok"):
        raise RuntimeError(f"/api/gate failed: {d}")


def apply_multi(cycle_us: int, entries: list[dict[str, int]]) -> None:
    r = requests.post(
        API_GATE_MULTI,
        json={"cycle_us": cycle_us, "entries": entries},
        timeout=4.0,
    )
    d = r.json()
    if not d.get("ok"):
        raise RuntimeError(f"/api/gate_multi failed: {d}")


def collect_stats(duration_s: int, sample_period_s: float) -> list[dict]:
    end = time.time() + duration_s
    out = []
    while time.time() < end:
        try:
            s = requests.get(API_STATS, timeout=0.9).json()
            s["_t"] = time.time()
            out.append(s)
        except Exception:
            pass
        time.sleep(sample_period_s)
    return out


def summarize(samples: list[dict]) -> dict:
    if not samples:
        return {
            "n": 0,
            "complete_avg_pct": 0.0,
            "complete_min_pct": 0.0,
            "complete_p05_pct": 0.0,
            "fps_avg": 0.0,
            "jitter_avg_us": 0.0,
            "jitter_p95_us": 0.0,
            "burst_avg_pct": 0.0,
            "pps_avg": 0.0,
        }
    comp = [100.0 * float(s.get("frame_completeness", 0.0)) for s in samples]
    fps = [float(s.get("fps", 0.0)) for s in samples]
    jit = [float(s.get("gap_stdev_us", 0.0)) for s in samples]
    bur = [float(s.get("burst_pct", 0.0)) for s in samples]
    pps = [float(s.get("pps", 0.0)) for s in samples]

    comp_sorted = sorted(comp)
    jit_sorted = sorted(jit)
    p05 = comp_sorted[max(0, int(len(comp_sorted) * 0.05) - 1)]
    j95 = jit_sorted[min(len(jit_sorted) - 1, int(len(jit_sorted) * 0.95))]

    return {
        "n": len(samples),
        "complete_avg_pct": statistics.mean(comp),
        "complete_min_pct": min(comp),
        "complete_p05_pct": p05,
        "fps_avg": statistics.mean(fps),
        "jitter_avg_us": statistics.mean(jit),
        "jitter_p95_us": j95,
        "burst_avg_pct": statistics.mean(bur),
        "pps_avg": statistics.mean(pps),
    }


def is_stable(summary: dict) -> bool:
    return (
        summary["complete_min_pct"] >= 99.0
        and summary["complete_p05_pct"] >= 99.5
        and summary["fps_avg"] >= 9.5
    )


def mk_entries(o1: int, c: int, o2: int) -> list[dict[str, int]]:
    return [
        {"gate": 255, "duration_us": o1},
        {"gate": 254, "duration_us": c},
        {"gate": 255, "duration_us": o2},
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration-s", type=int, default=60)
    ap.add_argument("--settle-s", type=int, default=6)
    ap.add_argument("--sample-period-s", type=float, default=0.5)
    ap.add_argument("--cycle-us", type=int, default=781)
    args = ap.parse_args()

    cycle = args.cycle_us
    close_150 = cycle - 150
    close_30 = cycle - 30

    tests = [
        {"name": "single_150", "mode": "single", "open_us": 150},
        {"name": "triple150_75_631_75", "mode": "multi", "entries": mk_entries(75, close_150, 75)},
        {"name": "triple150_30_631_120", "mode": "multi", "entries": mk_entries(30, close_150, 120)},
        {"name": "triple150_120_631_30", "mode": "multi", "entries": mk_entries(120, close_150, 30)},
        {"name": "single_30", "mode": "single", "open_us": 30},
        {"name": "triple30_15_751_15", "mode": "multi", "entries": mk_entries(15, close_30, 15)},
        {"name": "triple30_5_751_25", "mode": "multi", "entries": mk_entries(5, close_30, 25)},
        {"name": "triple30_25_751_5", "mode": "multi", "entries": mk_entries(25, close_30, 5)},
    ]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = DATA_DIR / f"three_slot_server_matrix_{ts}.json"
    out_md = DATA_DIR / f"three_slot_server_matrix_{ts}.md"
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    try:
        for i, t in enumerate(tests, start=1):
            print(f"[{i}/{len(tests)}] apply {t['name']}")
            if t["mode"] == "single":
                apply_single(cycle, int(t["open_us"]))
            else:
                apply_multi(cycle, t["entries"])
            time.sleep(args.settle_s)

            samples = collect_stats(args.duration_s, args.sample_period_s)
            summ = summarize(samples)
            tas_state = requests.get(API_TAS_STATE, timeout=1.0).json()
            stable = is_stable(summ)

            result = {
                "name": t["name"],
                "mode": t["mode"],
                "cycle_us": cycle,
                "applied": t,
                "tas_state": tas_state,
                "summary": summ,
                "stable": stable,
                "sample_count": len(samples),
            }
            results.append(result)
            print(
                f"  -> stable={stable} comp_min={summ['complete_min_pct']:.2f}% "
                f"comp_p05={summ['complete_p05_pct']:.2f}% fps={summ['fps_avg']:.2f}"
            )
    finally:
        try:
            apply_single(1000, 1000)
            print("Restored TAS all-open (1000/1000)")
        except Exception as e:
            print(f"Failed to restore TAS baseline: {e}")

    payload = {
        "timestamp": ts,
        "cycle_us": cycle,
        "duration_s": args.duration_s,
        "settle_s": args.settle_s,
        "sample_period_s": args.sample_period_s,
        "results": results,
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# 3-slot TAS Server Matrix",
        "",
        f"- timestamp: `{ts}`",
        f"- cycle_us: `{cycle}`",
        f"- duration per config: `{args.duration_s}s`",
        "",
        "| test | mode | stable | comp_avg% | comp_min% | comp_p05% | fps_avg | jitter_avg_us | jitter_p95_us | burst_avg% | pps_avg |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        s = r["summary"]
        lines.append(
            "| {name} | {mode} | {stable} | {ca:.2f} | {cm:.2f} | {cp:.2f} | {fps:.2f} | {ja:.1f} | {j95:.1f} | {ba:.2f} | {pps:.1f} |".format(
                name=r["name"],
                mode=r["mode"],
                stable="PASS" if r["stable"] else "FAIL",
                ca=s["complete_avg_pct"],
                cm=s["complete_min_pct"],
                cp=s["complete_p05_pct"],
                fps=s["fps_avg"],
                ja=s["jitter_avg_us"],
                j95=s["jitter_p95_us"],
                ba=s["burst_avg_pct"],
                pps=s["pps_avg"],
            )
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Saved: {out_json}")
    print(f"Saved: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

