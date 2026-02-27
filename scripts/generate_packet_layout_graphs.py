#!/usr/bin/env python3
"""Generate byte-level packet layout figures for current Ouster LiDAR profile.

Outputs:
- packet_layout_*.png         : full UDP payload layout (bytes)
- column_layout_*.png         : one-column internal layout (bytes)
- fragment_timing_*.png       : MTU fragmentation + 1GbE serialization timing
- packet_layout_*.md/.json    : numeric summary
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import requests

DOC_URL = (
    "https://static.ouster.dev/sensor-docs/image_route1/image_route2/"
    "sensor_data/sensor-data.html#lidar-data-packet-format"
)


def channel_block_bytes(profile: str) -> int:
    p = profile.strip().upper()
    if p == "RNG19_RFL8_SIG16_NIR16":
        return 12
    if p == "RNG15_RFL8_NIR8":
        return 4
    if p == "RNG19_RFL8_SIG16_NIR16_DUAL":
        return 16
    return 12


def load_sensor(host: str) -> tuple[dict, dict]:
    cfg = requests.get(f"http://{host}/api/v1/sensor/config", timeout=3).json()
    md = requests.get(f"http://{host}/api/v1/sensor/metadata", timeout=3).json()
    return cfg, md


def load_from_packet_json(path: Path) -> tuple[dict, dict]:
    d = json.loads(path.read_text(encoding="ascii"))
    cfg = {
        "udp_profile_lidar": d["sensor_config"]["udp_profile_lidar"],
        "columns_per_packet": d["sensor_config"]["columns_per_packet"],
        "lidar_mode": d["sensor_config"]["lidar_mode"],
        "timestamp_mode": d["sensor_config"]["timestamp_mode"],
    }
    md = {
        "lidar_data_format": {
            "pixels_per_column": d["metadata_format"]["pixels_per_column"],
            "columns_per_frame": d["metadata_format"]["columns_per_frame"],
            "udp_profile_lidar": d["metadata_format"]["udp_profile_lidar"],
            "columns_per_packet": d["metadata_format"]["columns_per_packet"],
        }
    }
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


def make_layout(cfg: dict, md: dict) -> dict:
    profile = str(cfg.get("udp_profile_lidar", "RNG19_RFL8_SIG16_NIR16"))
    cpp = int(cfg.get("columns_per_packet", 16))
    ppc = int(md["lidar_data_format"]["pixels_per_column"])
    cb = channel_block_bytes(profile)

    packet_header = 32
    col_header = 12
    packet_footer = 32
    channel_total = ppc * cb
    col_total = col_header + channel_total
    payload_total = packet_header + cpp * col_total + packet_footer

    pps = expected_pps(cfg, md)
    dt_us = 1e6 / pps if pps > 0 else 0.0

    return {
        "udp_profile_lidar": profile,
        "columns_per_packet": cpp,
        "pixels_per_column": ppc,
        "channel_block_bytes": cb,
        "packet_header_bytes": packet_header,
        "column_header_bytes": col_header,
        "column_total_bytes": col_total,
        "packet_footer_bytes": packet_footer,
        "packet_payload_bytes": payload_total,
        "packet_rate_hz": pps,
        "inter_packet_us": dt_us,
    }


def ip_fragments_udp_payload(payload_bytes: int, mtu: int = 1500) -> list[int]:
    # Fragment payload is IP payload bytes. First fragment includes UDP header (8B).
    udp_len = payload_bytes + 8
    max_ip_payload = mtu - 20  # IPv4 header 20B
    out = []
    rem = udp_len
    while rem > 0:
        x = min(max_ip_payload, rem)
        out.append(x)
        rem -= x
    return out


def serialization_us_1g(frag_ip_payload: int, vlan: bool = True) -> float:
    # On-wire bytes/frame: preamble+SFD(8) + L2(14 or 18) + IP packet + FCS(4) + IFG(12)
    l2 = 18 if vlan else 14
    wire_bytes = 8 + l2 + 20 + frag_ip_payload + 4 + 12
    return (wire_bytes * 8) / 1e3  # at 1Gbps => 1 bit/ns, bytes*8 ns => /1000 us


def plot_packet_layout(layout: dict, out_png: Path) -> None:
    cpp = layout["columns_per_packet"]
    hdr = layout["packet_header_bytes"]
    col = layout["column_total_bytes"]
    ftr = layout["packet_footer_bytes"]
    total = layout["packet_payload_bytes"]

    fig, ax = plt.subplots(figsize=(14, 2.8))

    colors = ["#2E86AB", "#66A61E", "#F6A821"]
    x = 0
    ax.barh([0], [hdr], left=[x], color=colors[0], edgecolor="black")
    ax.text(x + hdr / 2, 0, f"Packet Header\n{hdr}B", ha="center", va="center", fontsize=8)
    x += hdr

    ax.barh([0], [cpp * col], left=[x], color=colors[1], edgecolor="black")
    ax.text(
        x + (cpp * col) / 2,
        0,
        f"{cpp} Columns x {col}B = {cpp*col}B",
        ha="center",
        va="center",
        fontsize=8,
    )
    x += cpp * col

    ax.barh([0], [ftr], left=[x], color=colors[2], edgecolor="black")
    ax.text(x + ftr / 2, 0, f"Packet Footer\n{ftr}B", ha="center", va="center", fontsize=8)

    ax.set_xlim(0, total)
    ax.set_yticks([])
    ax.set_xlabel("Byte Offset in UDP Payload")
    ax.set_title(
        f"LiDAR UDP Payload Layout ({layout['udp_profile_lidar']}) - total {total}B"
    )
    ax.grid(axis="x", alpha=0.2)
    plt.tight_layout()
    fig.savefig(out_png, dpi=140)
    plt.close(fig)


def plot_column_layout(layout: dict, out_png: Path) -> None:
    col_hdr = layout["column_header_bytes"]
    ppc = layout["pixels_per_column"]
    cb = layout["channel_block_bytes"]
    total = layout["column_total_bytes"]

    fig, ax = plt.subplots(figsize=(12, 2.8))

    x = 0
    ax.barh([0], [col_hdr], left=[x], color="#2E86AB", edgecolor="black")
    ax.text(x + col_hdr / 2, 0, f"Column Header\n{col_hdr}B", ha="center", va="center", fontsize=8)
    x += col_hdr

    ax.barh([0], [ppc * cb], left=[x], color="#9D5C63", edgecolor="black")
    ax.text(
        x + (ppc * cb) / 2,
        0,
        f"Channel Data: {ppc} x {cb}B = {ppc*cb}B",
        ha="center",
        va="center",
        fontsize=8,
    )

    # Draw channel boundaries for B-level understanding.
    for i in range(1, ppc):
        xx = col_hdr + i * cb
        ax.axvline(xx, color="white", linewidth=0.6, alpha=0.7)

    ax.set_xlim(0, total)
    ax.set_yticks([])
    ax.set_xlabel("Byte Offset in One Column")
    ax.set_title(f"One Column Layout - total {total}B")
    ax.grid(axis="x", alpha=0.2)
    plt.tight_layout()
    fig.savefig(out_png, dpi=140)
    plt.close(fig)


def plot_fragment_timing(layout: dict, mtu: int, out_png: Path) -> dict:
    payload = layout["packet_payload_bytes"]
    frags = ip_fragments_udp_payload(payload, mtu=mtu)
    times = [serialization_us_1g(x, vlan=True) for x in frags]

    fig, ax = plt.subplots(figsize=(10, 3.2))
    labels = [f"frag{i+1}\nIP payload={x}B" for i, x in enumerate(frags)]
    ax.bar(range(len(frags)), times, color="#4F6D7A", edgecolor="black")
    for i, t in enumerate(times):
        ax.text(i, t + 0.08, f"{t:.3f}us", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(range(len(frags)), labels)
    ax.set_ylabel("Serialization Time per Fragment @1GbE (us)")
    ax.set_title(
        "MTU Fragmentation Timing (includes preamble+SFD, VLAN L2 hdr, IP hdr, FCS, IFG)"
    )
    ax.grid(axis="y", alpha=0.2)
    plt.tight_layout()
    fig.savefig(out_png, dpi=140)
    plt.close(fig)

    return {
        "mtu": mtu,
        "ip_payload_fragments_bytes": frags,
        "serialization_us_per_fragment": times,
        "serialization_us_total": sum(times),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="192.168.6.11")
    p.add_argument("--packet-json", default="")
    p.add_argument("--outdir", default="/home/kim/lidar-tas260226/data")
    p.add_argument("--mtu", type=int, default=1500)
    args = p.parse_args()

    if args.packet_json:
        cfg, md = load_from_packet_json(Path(args.packet_json))
        source = f"packet_json:{args.packet_json}"
    else:
        cfg, md = load_sensor(args.host)
        source = f"sensor:{args.host}"

    layout = make_layout(cfg, md)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"packet_layout_{ts}"

    p1 = outdir / f"{stem}_payload.png"
    p2 = outdir / f"{stem}_column.png"
    p3 = outdir / f"{stem}_fragments.png"
    js = outdir / f"{stem}.json"
    mdp = outdir / f"{stem}.md"

    plot_packet_layout(layout, p1)
    plot_column_layout(layout, p2)
    frag = plot_fragment_timing(layout, args.mtu, p3)

    out = {
        "timestamp": ts,
        "source": source,
        "doc_reference": DOC_URL,
        "layout": layout,
        "fragmentation": frag,
    }
    js.write_text(json.dumps(out, indent=2), encoding="ascii")

    lines = [
        "# Packet Layout (Byte-level)",
        "",
        f"- source: `{source}`",
        f"- docs: {DOC_URL}",
        "",
        "## Layout",
        f"- profile: `{layout['udp_profile_lidar']}`",
        f"- columns_per_packet: `{layout['columns_per_packet']}`",
        f"- pixels_per_column: `{layout['pixels_per_column']}`",
        f"- channel_block_bytes: `{layout['channel_block_bytes']}`",
        f"- packet_payload_bytes: `{layout['packet_payload_bytes']}`",
        f"- packet_rate_hz: `{layout['packet_rate_hz']:.3f}`",
        f"- inter_packet_us: `{layout['inter_packet_us']:.3f}`",
        "",
        "## MTU Fragmentation / IFG timing (1GbE)",
        f"- mtu: `{frag['mtu']}`",
        f"- ip_payload_fragments_bytes: `{frag['ip_payload_fragments_bytes']}`",
        f"- serialization_us_per_fragment: `{[round(x,3) for x in frag['serialization_us_per_fragment']]}`",
        f"- serialization_us_total: `{frag['serialization_us_total']:.3f}`",
        "",
        "## Figures",
        f"- payload layout: `{p1.name}`",
        f"- column layout: `{p2.name}`",
        f"- fragment timing: `{p3.name}`",
        "",
    ]
    mdp.write_text("\n".join(lines), encoding="ascii")

    print(js)
    print(mdp)


if __name__ == "__main__":
    main()
