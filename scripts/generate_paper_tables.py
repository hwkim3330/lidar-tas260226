#!/usr/bin/env python3
"""Generate paper-ready result tables from existing experiment JSON files."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path("/home/kim/lidar-tas260226")
DATA = ROOT / "data"
PAPER = ROOT / "paper"


def load_json(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def fmt(v: float) -> str:
    return f"{v:.3f}"


def table_deep_opt(lines: list[str]):
    src = "deep_opt_150ns_20260226_170215.json"
    d = load_json(src)
    lines += [
        "## Table A. 600s Soak Comparison (All-open vs Deep-optimized)",
        f"- source: `data/{src}`",
        "",
        "| config | front(ns) | phase(ns) | fc_mean(%) | fc_p01(%) | fps_mean |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in d["soak_results"]:
        s = row["summary"]
        lines.append(
            f"| {row['name']} | {row.get('front', '-')} | {row.get('phase_ns', '-')} "
            f"| {fmt(s['fc_mean'])} | {fmt(s['fc_p01'])} | {fmt(s['fps_mean'])} |"
        )
    delta = d["delta_best_minus_all_open"]
    lines += [
        "",
        (
            "best-all_open delta: "
            f"fc_mean={delta['fc_mean']:+.3f}, "
            f"fc_p01={delta['fc_p01']:+.3f}, "
            f"fps_mean={delta['fps_mean']:+.3f}"
        ),
        "",
    ]


def table_front_compare(lines: list[str]):
    src = "fine_front_compare_20260226_161307.json"
    d = load_json(src)
    lines += [
        "## Table B. 120s Front/Back Split Sensitivity (open=150us, phase=0)",
        f"- source: `data/{src}`",
        "",
        "| config | entries(ns) | fc_mean(%) | fc_p01(%) | fps_mean |",
        "|---|---|---:|---:|---:|",
    ]
    for row in d["results"]:
        s = row["summary"]
        entries = row.get("entries", "-")
        lines.append(
            f"| {row['name']} | `{entries}` | {fmt(s['fc_mean'])} | {fmt(s['fc_p01'])} | {fmt(s['fps_mean'])} |"
        )
    lines += ["", ""]


def table_small_open(lines: list[str]):
    src = "allopen_vs_smallopen_20260226_151542.json"
    d = load_json(src)
    lines += [
        "## Table C. Small-open Limitation (All-open vs 30/40/50us)",
        f"- source: `data/{src}`",
        "",
        "| config | open(us) | phase(ns) | fc_mean(%) | fc_p01(%) | fps_mean | delta_fc_mean |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in d["soak_results"]:
        s = row["summary"]
        dlt = row.get("delta_vs_allopen", {})
        lines.append(
            f"| {row['name']} | {row.get('open_us', '-')} | {row.get('phase_ns', '-')} "
            f"| {fmt(s['fc_mean'])} | {fmt(s['fc_p01'])} | {fmt(s['fps_mean'])} | {dlt.get('fc_mean', 0):+.3f} |"
        )
    lines += ["", ""]


def table_boundary(lines: list[str]):
    src = "single_lidar_long_opt_20260226_110713.json"
    d = load_json(src)
    lines += [
        "## Table D. Long-run Boundary Near 150us (phase=0)",
        f"- source: `data/{src}`",
        "",
        "| open(us) | repeats | comp_min(%) | comp_mean(%) | pass_all |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in d["summary"]:
        lines.append(
            f"| {row['open_us']} | {row['repeats']} | {fmt(row['comp_min'])} | {fmt(row['comp_mean'])} | {row['pass_all']} |"
        )
    lines += ["", ""]


def main():
    PAPER.mkdir(parents=True, exist_ok=True)
    out = PAPER / "results_tables.md"

    lines = ["# Paper Result Tables", ""]
    table_deep_opt(lines)
    table_front_compare(lines)
    table_small_open(lines)
    table_boundary(lines)

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
