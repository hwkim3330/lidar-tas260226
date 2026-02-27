#!/usr/bin/env python3
"""Create conceptual timing figure: wire(IFG) vs host timestamp (USB vs PCIe NIC)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


def draw_lane(ax, title, packet_start_us, wire_dur_us, sample_points_us, color):
    y0 = 0.0
    for i, s in enumerate(packet_start_us):
        ax.add_patch(Rectangle((s, y0 + 0.2), wire_dur_us, 0.25, facecolor=color, edgecolor="black", alpha=0.85))
        if i == 0:
            ax.text(s + wire_dur_us / 2, y0 + 0.51, "wire packet\n(incl. IFG in duration model)", ha="center", va="bottom", fontsize=8)
    for i, t in enumerate(sample_points_us):
        ax.axvline(t, ymin=0.02, ymax=0.75, color="#d62728", linewidth=1.2)
        ax.plot([t], [y0 + 0.75], marker="o", color="#d62728", markersize=4)
        if i == 0:
            ax.text(t, y0 + 0.79, "host timestamp", ha="left", va="bottom", fontsize=8, color="#d62728")
    ax.set_ylim(0, 1.0)
    ax.set_yticks([])
    ax.set_title(title)
    ax.grid(axis="x", alpha=0.2)


def main() -> None:
    outdir = Path("/home/kim/lidar-tas260226/data")
    outdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_png = outdir / f"usb_vs_pcie_timing_{ts}.png"
    out_md = outdir / f"usb_vs_pcie_timing_{ts}.md"

    # Conceptual values from repo findings:
    # 1280pps -> 781.25us wire interval, wire occupation ~28.176us (fragment+IFG model).
    interval = 781.25
    wire_dur = 28.176
    starts = [200 + i * interval for i in range(8)]

    # PCIe NIC: timestamps close to packet arrival boundaries.
    pcie_ts = [s + 5 for s in starts]

    # USB NIC: batching/URB/NAPI can delay and cluster delivery.
    usb_ts = [
        starts[0] + 180,
        starts[1] + 200,
        starts[2] + 220,
        starts[3] + 210,
        starts[4] + 450,  # delayed burst delivery
        starts[5] + 80,
        starts[6] + 95,
        starts[7] + 110,
    ]

    fig, axes = plt.subplots(2, 1, figsize=(14, 6), sharex=True)
    draw_lane(
        axes[0],
        "PCIe NIC (onboard): host timestamp ~ close to wire arrival",
        starts,
        wire_dur,
        pcie_ts,
        color="#1f77b4",
    )
    draw_lane(
        axes[1],
        "USB NIC (r8152 class): USB/driver batching can shift and cluster host timestamps",
        starts,
        wire_dur,
        usb_ts,
        color="#2ca02c",
    )

    axes[1].set_xlabel("time (us)")
    fig.suptitle("Why measured inter-packet interval differs by NIC path\n(wire timing vs host receive timestamp)")
    plt.tight_layout()
    fig.savefig(out_png, dpi=150)
    plt.close(fig)

    lines = [
        "# USB vs PCIe Timing Concept",
        "",
        "- This figure is conceptual (not a direct pcap plot).",
        "- Wire interval example: 781.25us, wire-occupancy model: 28.176us.",
        "- Key point: host timestamp includes software/driver/USB scheduling effects.",
        "",
        f"- figure: `{out_png.name}`",
    ]
    out_md.write_text("\n".join(lines), encoding="ascii")

    print(out_png)
    print(out_md)


if __name__ == "__main__":
    main()
