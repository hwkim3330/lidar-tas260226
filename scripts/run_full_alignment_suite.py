#!/usr/bin/env python3
"""Run full LiDAR-TAS alignment suite and save a compact markdown summary."""
import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path


ROOT = Path("/home/kim/lidar-tas260226")
TAS_SWEEP = ROOT / "scripts" / "tas_781_wide_to_narrow.py"
KETI_DIR = Path("/home/kim/keti-tsn-cli-new")
ALL_OPEN_YAML = ROOT / "configs" / "tas_disable_all_open.yaml"


def run(cmd, cwd=None):
    print("+", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, cwd=cwd)


def load_json(path):
    with open(path, "r", encoding="ascii") as f:
        return json.load(f)


def pick_best_phase(rows):
    ranked = sorted(
        rows,
        key=lambda r: (
            r.get("completeness_pct", 0.0),
            r.get("pps", 0.0),
            -r.get("gap_stdev_us", 1e9),
        ),
        reverse=True,
    )
    return ranked[0]


def find_min_open_100(rows):
    ok = [r for r in rows if r.get("completeness_pct", 0.0) >= 99.9]
    if not ok:
        return None
    return min(ok, key=lambda r: r.get("open_us", 10**9))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration-phase", type=int, default=5)
    ap.add_argument("--duration-open", type=int, default=5)
    ap.add_argument("--phase-step-us", type=int, default=20)
    ap.add_argument("--phase-open-us", type=int, default=120)
    ap.add_argument("--open-start-us", type=int, default=200)
    ap.add_argument("--open-min-us", type=int, default=0)
    ap.add_argument("--open-step-us", type=int, default=4)
    ap.add_argument("--settle", type=float, default=0.5)
    ap.add_argument("--base-time-offset-sec", type=int, default=2)
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    phase_out = ROOT / "data" / f"phase_sweep_{ts}.json"
    open_out = ROOT / "data" / f"open_sweep_best_phase_{ts}.json"
    summary_out = ROOT / "data" / f"alignment_summary_{ts}.md"

    run(
        [
            "python3",
            str(TAS_SWEEP),
            "--keti-dir",
            str(KETI_DIR),
            "--duration",
            str(args.duration_phase),
            "--settle",
            str(args.settle),
            "--start-open-us",
            str(args.phase_open_us),
            "--min-open-us",
            str(args.phase_open_us),
            "--step-us",
            "1",
            "--base-time-mode",
            "switch-future",
            "--base-time-offset-sec",
            str(args.base_time_offset_sec),
            "--phase-sweep-step-us",
            str(args.phase_step_us),
            "--output",
            str(phase_out),
        ]
    )

    phase_rows = load_json(phase_out)
    best_phase_row = pick_best_phase(phase_rows)
    best_phase_ns = int(best_phase_row["phase_offset_ns"])
    print(f"best phase_ns={best_phase_ns} comp={best_phase_row['completeness_pct']:.2f}%")

    run(
        [
            "python3",
            str(TAS_SWEEP),
            "--keti-dir",
            str(KETI_DIR),
            "--duration",
            str(args.duration_open),
            "--settle",
            str(args.settle),
            "--start-open-us",
            str(args.open_start_us),
            "--min-open-us",
            str(args.open_min_us),
            "--step-us",
            str(args.open_step_us),
            "--base-time-mode",
            "switch-future",
            "--base-time-offset-sec",
            str(args.base_time_offset_sec),
            "--phase-offset-ns",
            str(best_phase_ns),
            "--output",
            str(open_out),
        ]
    )

    open_rows = load_json(open_out)
    min_open = find_min_open_100(open_rows)

    run(["./keti-tsn", "patch", str(ALL_OPEN_YAML)], cwd=str(KETI_DIR))

    lines = []
    lines.append("# LiDAR TAS Alignment Summary")
    lines.append("")
    lines.append(f"- phase sweep file: `{phase_out}`")
    lines.append(f"- open sweep file: `{open_out}`")
    lines.append(f"- best phase: `{best_phase_ns} ns`")
    lines.append(
        f"- best phase result: completeness `{best_phase_row['completeness_pct']:.2f}%`, "
        f"pps `{best_phase_row['pps']:.1f}`"
    )
    if min_open is None:
        lines.append("- minimum open for >=99.9%: `not found`")
    else:
        lines.append(
            f"- minimum open for >=99.9%: `{int(min_open['open_us'])} us` "
            f"(pps `{min_open['pps']:.1f}`)"
        )
    lines.append("")
    lines.append("## Top Phase Candidates")
    lines.append("")
    lines.append("| phase_ns | completeness_pct | pps | gap_stdev_us |")
    lines.append("|---:|---:|---:|---:|")
    for r in sorted(phase_rows, key=lambda x: x.get("completeness_pct", 0.0), reverse=True)[:8]:
        lines.append(
            f"| {int(r['phase_offset_ns'])} | {r['completeness_pct']:.2f} | "
            f"{r['pps']:.1f} | {r['gap_stdev_us']:.1f} |"
        )

    lines.append("")
    lines.append("## Open Sweep (excerpt)")
    lines.append("")
    lines.append("| open_us | completeness_pct | pps |")
    lines.append("|---:|---:|---:|")
    for r in open_rows[: min(20, len(open_rows))]:
        lines.append(f"| {int(r['open_us'])} | {r['completeness_pct']:.2f} | {r['pps']:.1f} |")

    summary_out.write_text("\n".join(lines) + "\n", encoding="ascii")
    print(f"summary: {summary_out}")


if __name__ == "__main__":
    main()
