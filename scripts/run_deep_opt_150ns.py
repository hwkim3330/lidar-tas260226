#!/usr/bin/env python3
"""Long-soak deep optimizer for 781.25us cycle with open=150us."""

import json
import re
import statistics
import subprocess
import time
from datetime import datetime
from pathlib import Path

import requests

KETI_DIR = Path("/home/kim/keti-tsn-cli-new")
FETCH_YAML = Path("/home/kim/lidar-tas/configs/fetch-tas.yaml")
ALL_OPEN_YAML = Path("/home/kim/lidar-tas260226/configs/tas_disable_all_open.yaml")
OUT_DIR = Path("/home/kim/lidar-tas260226/data")
SENSOR_IP = "192.168.6.11"

CYCLE_NS = 781250
OPEN_NS = 150000
CLOSE_TOTAL_NS = CYCLE_NS - OPEN_NS

FRONT_CANDIDATES = [295625, 300625, 305625, 310625, 315625]
PHASE_CANDIDATES = list(range(0, CYCLE_NS, 20000))


def run(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def fetch_switch_time():
    out = run(["./keti-tsn", "fetch", str(FETCH_YAML)], cwd=str(KETI_DIR)).stdout
    m = re.search(
        r"current-time:\s*\n\s*nanoseconds:\s*(\d+)\s*\n\s*seconds:\s*(\d+)",
        out,
        re.M,
    )
    if not m:
        raise RuntimeError("failed to parse current-time from fetch output")
    return int(m.group(2)), int(m.group(1))


def set_phase_lock(enabled):
    value = "true" if enabled else "false"
    run(
        [
            "curl",
            "-sS",
            "--max-time",
            "3",
            "-X",
            "POST",
            f"http://{SENSOR_IP}/api/v1/sensor/cmd/set_config_param?args=phase_lock_enable%20{value}",
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
            f"http://{SENSOR_IP}/api/v1/sensor/cmd/reinitialize",
        ]
    )
    time.sleep(2)


def apply_three_slot(front_ns, phase_ns, offset_sec=2):
    back_ns = CLOSE_TOTAL_NS - front_ns
    sec, ns = fetch_switch_time()
    total = sec * 1_000_000_000 + ns + offset_sec * 1_000_000_000 + phase_ns
    base_sec = total // 1_000_000_000
    base_ns = total % 1_000_000_000

    lines = [
        "- ? \"/ietf-interfaces:interfaces/interface[name='1']/ieee802-dot1q-bridge:bridge-port/ieee802-dot1q-sched-bridge:gate-parameter-table\"",
        "  : gate-enabled: true",
        "    admin-gate-states: 255",
        "    admin-cycle-time:",
        f"      numerator: {CYCLE_NS}",
        "      denominator: 1000000000",
        "    admin-base-time:",
        f"      seconds: {base_sec}",
        f"      nanoseconds: {base_ns}",
        "    admin-control-list:",
        "      gate-control-entry:",
        "        - index: 0",
        "          operation-name: set-gate-states",
        f"          gate-states-value: 254",
        f"          time-interval-value: {front_ns}",
        "        - index: 1",
        "          operation-name: set-gate-states",
        f"          gate-states-value: 255",
        f"          time-interval-value: {OPEN_NS}",
        "        - index: 2",
        "          operation-name: set-gate-states",
        f"          gate-states-value: 254",
        f"          time-interval-value: {back_ns}",
        "    config-change: true",
    ]
    runtime_yaml = KETI_DIR / "lidar-tas260226" / "_deep_opt_runtime.yaml"
    runtime_yaml.write_text("\n".join(lines) + "\n", encoding="ascii")

    for _ in range(5):
        result = run(
            ["./keti-tsn", "patch", str(runtime_yaml)],
            cwd=str(KETI_DIR),
            check=False,
        )
        if result.returncode == 0:
            return back_ns
        time.sleep(0.2)
    raise RuntimeError("keti-tsn patch failed after retries")


def measure(duration_sec, step_sec=0.2):
    end = time.time() + duration_sec
    samples = []
    while time.time() < end:
        try:
            s = requests.get("http://127.0.0.1:8080/api/stats", timeout=0.8).json()
            samples.append(
                (
                    100.0 * s.get("frame_completeness", 0.0),
                    s.get("fps", 0.0),
                    s.get("gap_stdev_us", 0.0),
                    s.get("pps", 0.0),
                )
            )
        except Exception:
            pass
        time.sleep(step_sec)

    if not samples:
        raise RuntimeError("no api/stats samples collected")

    fc = [x[0] for x in samples]
    fps = [x[1] for x in samples]
    jit = [x[2] for x in samples]
    pps = [x[3] for x in samples]
    fc_sorted = sorted(fc)
    return {
        "samples": len(samples),
        "fc_mean": statistics.mean(fc),
        "fc_min": min(fc),
        "fc_p01": fc_sorted[max(0, int(len(fc_sorted) * 0.01) - 1)],
        "fc_p05": fc_sorted[max(0, int(len(fc_sorted) * 0.05) - 1)],
        "fps_mean": statistics.mean(fps),
        "fps_min": min(fps),
        "jit_mean": statistics.mean(jit),
        "pps_mean": statistics.mean(pps),
    }


def score(summary):
    penalty = max(0.0, 9.5 - summary["fps_mean"]) * 8.0
    return summary["fc_mean"] - penalty


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    set_phase_lock(False)

    rows = []
    print("=== coarse search start ===")
    total = len(FRONT_CANDIDATES) * len(PHASE_CANDIDATES)
    for front in FRONT_CANDIDATES:
        for phase in PHASE_CANDIDATES:
            back = apply_three_slot(front, phase)
            time.sleep(0.15)
            summary = measure(0.7, 0.2)
            row = {"front": front, "back": back, "phase_ns": phase, **summary}
            row["score"] = score(row)
            rows.append(row)
            if len(rows) % 20 == 0:
                print(
                    f"[{len(rows)}/{total}] front={front} phase={phase} "
                    f"fc={row['fc_mean']:.2f} fps={row['fps_mean']:.2f}"
                )

    ranked = sorted(rows, key=lambda x: (x["score"], x["fc_p01"], x["fc_min"]), reverse=True)
    top = []
    seen = set()
    for row in ranked:
        key = (row["front"], row["phase_ns"])
        if key in seen:
            continue
        top.append(row)
        seen.add(key)
        if len(top) == 3:
            break

    print("=== top candidates ===")
    for t in top:
        print(t)

    results = []
    run(["./keti-tsn", "patch", str(ALL_OPEN_YAML)], cwd=str(KETI_DIR), check=False)
    time.sleep(1)
    all_open = measure(600, 0.5)
    results.append({"name": "all_open", "summary": all_open})
    print("all_open", all_open)

    for idx, t in enumerate(top, start=1):
        apply_three_slot(t["front"], t["phase_ns"])
        time.sleep(1)
        summary = measure(600, 0.5)
        rec = {
            "name": f"cand{idx}_f{t['front']}_p{t['phase_ns']}",
            "front": t["front"],
            "back": t["back"],
            "phase_ns": t["phase_ns"],
            "summary": summary,
        }
        results.append(rec)
        print(rec["name"], summary)

    best = max(
        results[1:],
        key=lambda x: (
            x["summary"]["fc_p01"],
            x["summary"]["fc_mean"],
            x["summary"]["fps_min"],
        ),
    )
    apply_three_slot(best["front"], best["phase_ns"])
    set_phase_lock(False)

    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    obj = {
        "timestamp": now,
        "cycle_ns": CYCLE_NS,
        "open_ns": OPEN_NS,
        "front_candidates": FRONT_CANDIDATES,
        "phase_candidates": PHASE_CANDIDATES,
        "coarse_rows": rows,
        "top_candidates": top,
        "soak_results": results,
        "best": best,
        "delta_best_minus_all_open": {
            "fc_mean": best["summary"]["fc_mean"] - all_open["fc_mean"],
            "fc_p01": best["summary"]["fc_p01"] - all_open["fc_p01"],
            "fc_p05": best["summary"]["fc_p05"] - all_open["fc_p05"],
            "fps_mean": best["summary"]["fps_mean"] - all_open["fps_mean"],
        },
    }

    out_json = OUT_DIR / f"deep_opt_150ns_{now}.json"
    out_md = OUT_DIR / f"deep_opt_150ns_{now}.md"
    out_json.write_text(json.dumps(obj, indent=2), encoding="ascii")

    lines = [
        "# Deep Opt 150ns",
        "",
        f"- source: `{out_json.name}`",
        "",
        "| config | front | phase_ns | fc_mean | fc_min | fc_p01 | fc_p05 | fps_mean | fps_min |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results:
        s = row["summary"]
        lines.append(
            f"| {row['name']} | {row.get('front', '-')} | {row.get('phase_ns', '-')} "
            f"| {s['fc_mean']:.3f} | {s['fc_min']:.3f} | {s['fc_p01']:.3f} "
            f"| {s['fc_p05']:.3f} | {s['fps_mean']:.3f} | {s['fps_min']:.3f} |"
        )
    lines += [
        "",
        f"best: `{best['name']}` front/open/back={best['front']}/{OPEN_NS}/{best['back']} phase={best['phase_ns']}",
        (
            "delta(best-all_open): "
            f"fc_mean={obj['delta_best_minus_all_open']['fc_mean']:+.3f}, "
            f"fc_p01={obj['delta_best_minus_all_open']['fc_p01']:+.3f}, "
            f"fps_mean={obj['delta_best_minus_all_open']['fps_mean']:+.3f}"
        ),
    ]
    out_md.write_text("\n".join(lines) + "\n", encoding="ascii")

    print("saved", out_json)
    print("saved", out_md)
    print("best", best["name"], best["front"], best["phase_ns"])


if __name__ == "__main__":
    main()
