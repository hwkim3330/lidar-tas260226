#!/usr/bin/env python3
"""Generate detailed byte-level LiDAR packet diagrams for current Ouster config.

Features:
- Packet-level byte map with absolute offsets
- Column-level detailed map (header + 16 channel blocks)
- First-column channel offset table figure
- MTU fragmentation and 1GbE on-wire timing (with IFG)
- Optional lidar_mode set/reinitialize before drawing
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import requests
from matplotlib.patches import Rectangle

DOC_URL = (
    "https://static.ouster.dev/sensor-docs/image_route1/image_route2/"
    "sensor_data/sensor-data.html#lidar-data-packet-format"
)

SUPPORTED_MODES = ["512x10", "512x20", "1024x10", "1024x20", "2048x10"]


def channel_block_bytes(profile: str) -> int:
    p = profile.strip().upper()
    if p == "RNG19_RFL8_SIG16_NIR16":
        return 12
    if p == "RNG15_RFL8_NIR8":
        return 4
    if p == "RNG19_RFL8_SIG16_NIR16_DUAL":
        return 16
    return 12


def get_json(host: str, path: str, timeout: float = 3.0) -> dict:
    return requests.get(f"http://{host}{path}", timeout=timeout).json()


def post(host: str, path: str, timeout: float = 5.0) -> dict:
    r = requests.post(f"http://{host}{path}", timeout=timeout)
    if r.headers.get("content-type", "").startswith("application/json"):
        return r.json()
    return {"text": r.text, "status_code": r.status_code}


def set_mode_if_requested(host: str, mode: str) -> None:
    if not mode:
        return
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    post(host, f"/api/v1/sensor/cmd/set_config_param?args=lidar_mode%20{mode}")
    post(host, "/api/v1/sensor/cmd/reinitialize")
    time.sleep(2.0)


def query_sensor(host: str) -> tuple[dict, dict]:
    cfg = get_json(host, "/api/v1/sensor/config")
    md = get_json(host, "/api/v1/sensor/metadata")
    return cfg, md


def expected_pps(cfg: dict, md: dict) -> float:
    cpp = int(cfg.get("columns_per_packet", 16))
    cols = int(md["lidar_data_format"]["columns_per_frame"])
    mode = str(cfg.get("lidar_mode", "2048x10"))
    try:
        hz = float(mode.split("x")[1])
    except Exception:
        hz = 10.0
    return (cols / cpp) * hz


def ip_fragment_payloads(udp_payload: int, mtu: int = 1500) -> list[int]:
    # IP payload includes UDP header(8) on first fragment stream.
    udp_len = udp_payload + 8
    max_ip_payload = mtu - 20
    out = []
    rem = udp_len
    while rem > 0:
        x = min(max_ip_payload, rem)
        out.append(x)
        rem -= x
    return out


def onwire_us_for_fragment(ip_payload: int, vlan: bool = True) -> float:
    # bytes on wire at 1GbE:
    # preamble+SFD(8) + L2(14 or 18 with vlan) + IP packet(20+payload) + FCS(4) + IFG(12)
    l2 = 18 if vlan else 14
    wire_bytes = 8 + l2 + 20 + ip_payload + 4 + 12
    return wire_bytes * 8 / 1000.0


def build_layout(cfg: dict, md: dict) -> dict:
    profile = str(cfg.get("udp_profile_lidar", "RNG19_RFL8_SIG16_NIR16"))
    cpp = int(cfg.get("columns_per_packet", 16))
    ppc = int(md["lidar_data_format"]["pixels_per_column"])
    chb = channel_block_bytes(profile)

    packet_header = 32
    col_header = 12
    packet_footer = 32
    col_channels = ppc * chb
    col_total = col_header + col_channels
    packet_total = packet_header + cpp * col_total + packet_footer

    pps = expected_pps(cfg, md)

    return {
        "profile": profile,
        "lidar_mode": cfg.get("lidar_mode", "unknown"),
        "timestamp_mode": cfg.get("timestamp_mode", "unknown"),
        "columns_per_packet": cpp,
        "pixels_per_column": ppc,
        "channel_block_bytes": chb,
        "packet_header_bytes": packet_header,
        "column_header_bytes": col_header,
        "column_channels_bytes": col_channels,
        "column_total_bytes": col_total,
        "packet_footer_bytes": packet_footer,
        "packet_payload_bytes": packet_total,
        "packet_rate_hz": pps,
        "packet_interval_us": (1e6 / pps) if pps > 0 else 0.0,
    }


def fig_packet_absolute(layout: dict, out_png: Path) -> None:
    total = layout["packet_payload_bytes"]
    ph = layout["packet_header_bytes"]
    cpp = layout["columns_per_packet"]
    ctot = layout["column_total_bytes"]
    pf = layout["packet_footer_bytes"]

    fig, ax = plt.subplots(figsize=(15, 3.2))
    y = 0

    ax.add_patch(Rectangle((0, y), ph, 1.0, facecolor="#1f77b4", edgecolor="black"))
    ax.text(ph / 2, y + 0.5, f"Packet Header\n0..{ph-1}", ha="center", va="center", fontsize=8, color="white")

    col_start = ph
    col_end = ph + cpp * ctot - 1
    ax.add_patch(Rectangle((col_start, y), cpp * ctot, 1.0, facecolor="#2ca02c", edgecolor="black"))
    ax.text(
        col_start + (cpp * ctot) / 2,
        y + 0.5,
        f"Columns Region ({cpp} x {ctot}B)\n{col_start}..{col_end}",
        ha="center",
        va="center",
        fontsize=8,
        color="white",
    )

    footer_start = ph + cpp * ctot
    ax.add_patch(Rectangle((footer_start, y), pf, 1.0, facecolor="#ff7f0e", edgecolor="black"))
    ax.text(
        footer_start + pf / 2,
        y + 0.5,
        f"Packet Footer\n{footer_start}..{total-1}",
        ha="center",
        va="center",
        fontsize=8,
        color="black",
    )

    # Mark first few and last columns absolute offsets.
    marks = [0, 1, 2, cpp - 1]
    for i in marks:
        s = ph + i * ctot
        e = s + ctot - 1
        ax.axvline(s, color="white", linewidth=0.9, alpha=0.9)
        ax.text(s + ctot / 2, y + 1.06, f"col{i}\n{s}..{e}", ha="center", va="bottom", fontsize=7)

    ax.set_xlim(0, total)
    ax.set_ylim(-0.1, 1.5)
    ax.set_yticks([])
    ax.set_xlabel("Absolute Byte Offset in UDP Payload")
    ax.set_title(f"Detailed Packet Byte Map ({total}B, {layout['profile']})")
    ax.grid(axis="x", alpha=0.2)
    plt.tight_layout()
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def fig_column_detailed(layout: dict, out_png: Path) -> None:
    ctot = layout["column_total_bytes"]
    chb = layout["channel_block_bytes"]
    ppc = layout["pixels_per_column"]
    chdr = layout["column_header_bytes"]

    fig, ax = plt.subplots(figsize=(14, 3.2))

    ax.add_patch(Rectangle((0, 0), chdr, 1, facecolor="#1f77b4", edgecolor="black"))
    ax.text(chdr / 2, 0.5, f"Col Header\n0..{chdr-1}", ha="center", va="center", fontsize=8, color="white")

    for ch in range(ppc):
        s = chdr + ch * chb
        e = s + chb - 1
        fc = "#9467bd" if (ch % 2 == 0) else "#8c564b"
        ax.add_patch(Rectangle((s, 0), chb, 1, facecolor=fc, edgecolor="black", alpha=0.92))
        ax.text(s + chb / 2, 0.5, f"ch{ch}\n{s}..{e}", ha="center", va="center", fontsize=7, color="white")

    ax.set_xlim(0, ctot)
    ax.set_ylim(-0.1, 1.25)
    ax.set_yticks([])
    ax.set_xlabel("Byte Offset in One Column")
    ax.set_title(
        f"One Column Detailed Byte Map ({ctot}B = {chdr}B header + {ppc}x{chb}B channels)"
    )
    ax.grid(axis="x", alpha=0.2)
    plt.tight_layout()
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def fig_channel_table(layout: dict, out_png: Path) -> None:
    chdr = layout["column_header_bytes"]
    chb = layout["channel_block_bytes"]
    ppc = layout["pixels_per_column"]

    rows = []
    for ch in range(ppc):
        s = chdr + ch * chb
        e = s + chb - 1
        rows.append((ch, s, e, chb))

    fig_h = max(4.2, 0.32 * len(rows) + 1.5)
    fig, ax = plt.subplots(figsize=(7.8, fig_h))
    ax.axis("off")

    cols = ["channel", "start_B", "end_B", "size_B"]
    cell_text = [[str(x) for x in r] for r in rows]
    tbl = ax.table(cellText=cell_text, colLabels=cols, loc="center", cellLoc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1, 1.25)
    for (r, c), cell in tbl.get_celld().items():
        if r == 0:
            cell.set_facecolor("#2F4F4F")
            cell.get_text().set_color("white")

    ax.set_title("Column Channel Offset Table (Byte-level)", pad=10)
    plt.tight_layout()
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def fig_frag_timing(layout: dict, mtu: int, out_png: Path) -> dict:
    payload = layout["packet_payload_bytes"]
    frags = ip_fragment_payloads(payload, mtu=mtu)
    ser = [onwire_us_for_fragment(x, vlan=True) for x in frags]

    fig, ax = plt.subplots(figsize=(11, 3.4))
    labels = [f"frag{i+1}\nIP payload {x}B" for i, x in enumerate(frags)]
    bars = ax.bar(range(len(frags)), ser, color="#4c78a8", edgecolor="black")
    for i, b in enumerate(bars):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.05, f"{ser[i]:.3f}us", ha="center", va="bottom", fontsize=8)

    total = sum(ser)
    inter = layout["packet_interval_us"]
    ratio = (total / inter * 100.0) if inter > 0 else 0.0

    ax.set_xticks(range(len(frags)), labels)
    ax.set_ylabel("On-wire Serialization Time (us) @1GbE")
    ax.set_title("Fragment Serialization incl. IFG")
    ax.grid(axis="y", alpha=0.25)
    ax.text(0.99, 0.95, f"sum={total:.3f}us\ninterval={inter:.3f}us\nwire share={ratio:.2f}%", transform=ax.transAxes, ha="right", va="top", fontsize=9, bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "gray"})
    plt.tight_layout()
    fig.savefig(out_png, dpi=150)
    plt.close(fig)

    return {
        "mtu": mtu,
        "ip_payload_fragments_bytes": frags,
        "onwire_us_per_fragment": ser,
        "onwire_us_total": total,
        "packet_interval_us": inter,
        "wire_share_percent": ratio,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.6.11")
    ap.add_argument("--set-mode", default="", help="optional lidar_mode to apply before drawing")
    ap.add_argument("--mtu", type=int, default=1500)
    ap.add_argument("--outdir", default="/home/kim/lidar-tas260226/data")
    args = ap.parse_args()

    if args.set_mode:
        set_mode_if_requested(args.host, args.set_mode)

    cfg, md = query_sensor(args.host)
    layout = build_layout(cfg, md)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"packet_layout_detailed_{ts}"

    p_packet = outdir / f"{stem}_packet.png"
    p_col = outdir / f"{stem}_column.png"
    p_table = outdir / f"{stem}_channel_table.png"
    p_frag = outdir / f"{stem}_frag_timing.png"
    p_json = outdir / f"{stem}.json"
    p_md = outdir / f"{stem}.md"

    fig_packet_absolute(layout, p_packet)
    fig_column_detailed(layout, p_col)
    fig_channel_table(layout, p_table)
    frag = fig_frag_timing(layout, args.mtu, p_frag)

    out = {
        "timestamp": ts,
        "doc_reference": DOC_URL,
        "sensor_config": {
            "udp_profile_lidar": layout["profile"],
            "lidar_mode": layout["lidar_mode"],
            "timestamp_mode": layout["timestamp_mode"],
            "columns_per_packet": layout["columns_per_packet"],
            "pixels_per_column": layout["pixels_per_column"],
        },
        "layout": layout,
        "fragment_timing": frag,
        "figures": {
            "packet": str(p_packet),
            "column": str(p_col),
            "channel_table": str(p_table),
            "fragment_timing": str(p_frag),
        },
    }
    p_json.write_text(json.dumps(out, indent=2), encoding="ascii")

    lines = [
        "# Detailed Packet Layout",
        "",
        f"- docs: {DOC_URL}",
        f"- profile: `{layout['profile']}`",
        f"- lidar_mode: `{layout['lidar_mode']}`",
        f"- timestamp_mode: `{layout['timestamp_mode']}`",
        f"- packet_payload_bytes: `{layout['packet_payload_bytes']}`",
        f"- packet_interval_us: `{layout['packet_interval_us']:.3f}`",
        "",
        "## Byte Layout",
        f"- packet_header: `{layout['packet_header_bytes']}B`",
        f"- columns region: `{layout['columns_per_packet']} x {layout['column_total_bytes']}B`",
        f"- packet_footer: `{layout['packet_footer_bytes']}B`",
        f"- one column: `{layout['column_header_bytes']}B + {layout['pixels_per_column']} x {layout['channel_block_bytes']}B`",
        "",
        "## Fragment / IFG timing",
        f"- mtu: `{frag['mtu']}`",
        f"- ip_payload_fragments: `{frag['ip_payload_fragments_bytes']}`",
        f"- onwire_us_per_fragment: `{[round(x, 3) for x in frag['onwire_us_per_fragment']]}`",
        f"- onwire_us_total: `{frag['onwire_us_total']:.3f}`",
        f"- wire_share_percent_of_interval: `{frag['wire_share_percent']:.2f}%`",
        "",
        "## Figures",
        f"- `{p_packet.name}`",
        f"- `{p_col.name}`",
        f"- `{p_table.name}`",
        f"- `{p_frag.name}`",
        "",
    ]
    p_md.write_text("\n".join(lines), encoding="ascii")

    print(p_json)
    print(p_md)


if __name__ == "__main__":
    main()
