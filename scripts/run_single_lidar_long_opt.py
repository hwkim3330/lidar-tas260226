#!/usr/bin/env python3
"""Long-run optimization for single LiDAR TAS open window at cycle 781us."""
import argparse
import json
import statistics
import subprocess
from datetime import datetime
from pathlib import Path


ROOT = Path("/home/kim/lidar-tas260226")
SWEEP = ROOT / "scripts" / "tas_781_wide_to_narrow.py"
KETI_DIR = "/home/kim/keti-tsn-cli-new"
ALL_OPEN = ROOT / "configs" / "tas_disable_all_open.yaml"


def run(cmd, cwd=None):
    subprocess.run(cmd, check=True, cwd=cwd)


def run_one(open_us, phase_ns, duration_s, settle_s, idx):
    out = ROOT / "data" / f"_tmp_single_opt_{idx}.json"
    cmd = [
        "python3",
        str(SWEEP),
        "--keti-dir",
        KETI_DIR,
        "--duration",
        str(duration_s),
        "--settle",
        str(settle_s),
        "--start-open-us",
        str(open_us),
        "--min-open-us",
        str(open_us),
        "--step-us",
        "1",
        "--base-time-mode",
        "switch-future",
        "--base-time-offset-sec",
        "2",
        "--phase-offset-ns",
        str(phase_ns),
        "--output",
        str(out),
    ]
    run(cmd)
    row = json.loads(out.read_text(encoding="ascii"))[0]
    out.unlink(missing_ok=True)
    return row


def parse_opens(s):
    vals = []
    for tok in s.split(","):
        tok = tok.strip()
        if not tok:
            continue
        vals.append(int(tok))
    if not vals:
        raise ValueError("empty opens list")
    return vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--opens", default="180,170,164,160,156,152,150,148,146,144,142,140")
    ap.add_argument("--phase-ns", type=int, default=0)
    ap.add_argument("--duration", type=int, default=30)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--settle", type=float, default=0.5)
    ap.add_argument("--pass-completeness", type=float, default=99.9)
    args = ap.parse_args()

    opens = parse_opens(args.opens)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = ROOT / "data" / f"single_lidar_long_opt_{ts}.json"
    out_md = ROOT / "data" / f"single_lidar_long_opt_{ts}.md"

    raw = []
    idx = 0
    for open_us in opens:
        for rep in range(1, args.repeats + 1):
            idx += 1
            row = run_one(open_us, args.phase_ns, args.duration, args.settle, idx)
            row["repeat"] = rep
            raw.append(row)
            print(
                f"open={open_us:>3} rep={rep}/{args.repeats} "
                f"comp={row['completeness_pct']:.2f}% pps={row['pps']:.1f}"
            )

    # restore all-open after test
    run(["./keti-tsn", "patch", str(ALL_OPEN)], cwd=KETI_DIR)

    grouped = {}
    for r in raw:
        grouped.setdefault(int(r["open_us"]), []).append(r)

    summary = []
    for open_us in sorted(grouped.keys(), reverse=True):
        rows = grouped[open_us]
        comps = [r["completeness_pct"] for r in rows]
        ppss = [r["pps"] for r in rows]
        pass_all = all(c >= args.pass_completeness for c in comps)
        summary.append(
            {
                "open_us": open_us,
                "repeats": len(rows),
                "comp_min": min(comps),
                "comp_mean": statistics.mean(comps),
                "comp_max": max(comps),
                "pps_min": min(ppss),
                "pps_mean": statistics.mean(ppss),
                "pass_all": pass_all,
            }
        )

    stable = [s for s in summary if s["pass_all"]]
    recommended = min(stable, key=lambda x: x["open_us"]) if stable else None

    result = {
        "phase_ns": args.phase_ns,
        "duration_s": args.duration,
        "repeats": args.repeats,
        "pass_completeness": args.pass_completeness,
        "summary": summary,
        "recommended_min_stable_open_us": recommended["open_us"] if recommended else None,
        "raw": raw,
    }
    out_json.write_text(json.dumps(result, indent=2), encoding="ascii")

    lines = []
    lines.append("# Single LiDAR Long Optimization")
    lines.append("")
    lines.append(f"- phase_ns: `{args.phase_ns}`")
    lines.append(f"- duration: `{args.duration}s` per run")
    lines.append(f"- repeats: `{args.repeats}`")
    lines.append(f"- pass threshold: `completeness >= {args.pass_completeness}%` on all repeats")
    if recommended:
        lines.append(f"- recommended min stable open: `{recommended['open_us']} us`")
    else:
        lines.append("- recommended min stable open: `not found`")
    lines.append("")
    lines.append("| open_us | comp_min | comp_mean | comp_max | pps_min | pps_mean | pass_all |")
    lines.append("|---:|---:|---:|---:|---:|---:|:---:|")
    for s in summary:
        lines.append(
            f"| {s['open_us']} | {s['comp_min']:.2f} | {s['comp_mean']:.2f} | {s['comp_max']:.2f} | "
            f"{s['pps_min']:.1f} | {s['pps_mean']:.1f} | {'Y' if s['pass_all'] else 'N'} |"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="ascii")

    print(f"saved: {out_json}")
    print(f"saved: {out_md}")


if __name__ == "__main__":
    main()
