#!/usr/bin/env python3
"""Build a compact catalog of experiment artifacts under data/."""

from __future__ import annotations

import argparse
import datetime as dt
import re
from collections import Counter, defaultdict
from pathlib import Path

TS_RE = re.compile(r"_(\d{8}_\d{6})")


def parse_ts(name: str) -> dt.datetime | None:
    m = TS_RE.search(name)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def category(name: str) -> str:
    if name.startswith("packet_timing_"):
        return "packet_timing"
    if name.startswith("packet_layout_detailed_"):
        return "packet_layout_detailed"
    if name.startswith("packet_layout_"):
        return "packet_layout"
    if name.startswith("mode_packet_matrix_"):
        return "mode_packet_matrix"
    if name.startswith("ptp_"):
        return "ptp"
    if name.startswith("phaselock_"):
        return "phaselock"
    if name.startswith("phase_"):
        return "phase"
    if name.startswith("queue_"):
        return "queue"
    if name.startswith("server_stats_"):
        return "server_stats"
    if name.startswith("three_slot_"):
        return "three_slot"
    if name.startswith("soak_"):
        return "soak"
    if name.startswith("refine_"):
        return "refine"
    if name.startswith("single_lidar_"):
        return "single_lidar"
    if name.startswith("sweep_"):
        return "sweep"
    if name.startswith("open"):
        return "open_phase"
    return "other"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="/home/kim/lidar-tas260226/data")
    p.add_argument("--out", default="/home/kim/lidar-tas260226/data/DATA_CATALOG.md")
    args = p.parse_args()

    ddir = Path(args.data_dir)
    files = sorted([f for f in ddir.iterdir() if f.is_file()])

    ext_counts = Counter(f.suffix.lower() or "(none)" for f in files)
    cat_counts = Counter(category(f.name) for f in files)

    grouped = defaultdict(list)
    for f in files:
        grouped[category(f.name)].append(f)

    lines = []
    lines.append("# Data Catalog")
    lines.append("")
    lines.append(f"- generated_at: `{dt.datetime.now().isoformat(timespec='seconds')}`")
    lines.append(f"- data_dir: `{ddir}`")
    lines.append(f"- total_files: `{len(files)}`")
    lines.append("")

    lines.append("## By Extension")
    for ext, c in sorted(ext_counts.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- `{ext}`: `{c}`")
    lines.append("")

    lines.append("## By Category")
    for cat, c in sorted(cat_counts.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- `{cat}`: `{c}`")
    lines.append("")

    lines.append("## Latest Per Category")
    for cat in sorted(grouped.keys()):
        fs = grouped[cat]
        fs_sorted = sorted(
            fs,
            key=lambda f: (parse_ts(f.name) or dt.datetime.min, f.name),
            reverse=True,
        )
        top = fs_sorted[:5]
        lines.append(f"### {cat}")
        for f in top:
            lines.append(f"- `{f.name}`")
        lines.append("")

    lines.append("## Key Artifacts")
    for key in [
        "mode_packet_matrix_20260227_164427.md",
        "packet_layout_detailed_20260227_164122.md",
        "packet_timing_20260227_170633.md",
        "ptp_final_compare_20260227_152849.md",
        "single_lidar_long_opt_20260226_110713.md",
    ]:
        f = ddir / key
        if f.exists():
            lines.append(f"- `{key}`")
    lines.append("")

    Path(args.out).write_text("\n".join(lines), encoding="ascii")
    print(args.out)


if __name__ == "__main__":
    main()
