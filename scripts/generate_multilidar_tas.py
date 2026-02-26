#!/usr/bin/env python3
"""Generate 781us TDMA TAS YAML for 2/3 LiDAR (TC0/TC1/TC2 separated)."""
import argparse
from pathlib import Path


CYCLE_US_DEFAULT = 781
NON_LIDAR_MASK = 0xF8  # TC3..TC7 open
TC_MASKS = {
    0: NON_LIDAR_MASK | 0x01,  # TC0 + TC3..TC7
    1: NON_LIDAR_MASK | 0x02,  # TC1 + TC3..TC7
    2: NON_LIDAR_MASK | 0x04,  # TC2 + TC3..TC7
}


def build_yaml(cycle_us, slots_us, base_sec, base_nsec):
    if len(slots_us) not in (2, 3):
        raise ValueError("slots_us length must be 2 or 3")
    if any(s <= 0 for s in slots_us):
        raise ValueError("all slot values must be > 0")

    used = sum(slots_us)
    guard_us = cycle_us - used
    if guard_us < 0:
        raise ValueError("sum(slots_us) must be <= cycle_us")

    entries = []
    idx = 0
    for tc, slot_us in enumerate(slots_us):
        entries.append(
            f"""        - index: {idx}
          operation-name: set-gate-states
          gate-states-value: {TC_MASKS[tc]}
          time-interval-value: {slot_us * 1000}"""
        )
        idx += 1

    if guard_us > 0:
        entries.append(
            f"""        - index: {idx}
          operation-name: set-gate-states
          gate-states-value: {NON_LIDAR_MASK}
          time-interval-value: {guard_us * 1000}"""
        )

    entries_txt = "\n".join(entries)
    return f"""- ? "/ietf-interfaces:interfaces/interface[name='1']/ieee802-dot1q-bridge:bridge-port/ieee802-dot1q-sched-bridge:gate-parameter-table"
  : gate-enabled: true
    admin-gate-states: 255
    admin-cycle-time:
      numerator: {cycle_us * 1000}
      denominator: 1000000000
    admin-base-time:
      seconds: {base_sec}
      nanoseconds: {base_nsec}
    admin-control-list:
      gate-control-entry:
{entries_txt}
    config-change: true
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle-us", type=int, default=CYCLE_US_DEFAULT)
    ap.add_argument(
        "--slots-us",
        required=True,
        help="comma-separated slot widths in us; ex: 280,280 or 200,200,200",
    )
    ap.add_argument("--base-sec", type=int, default=0)
    ap.add_argument("--base-nsec", type=int, default=0)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    slots = [int(x.strip()) for x in args.slots_us.split(",") if x.strip()]
    yml = build_yaml(args.cycle_us, slots, args.base_sec, args.base_nsec)
    out = Path(args.output)
    out.write_text(yml, encoding="ascii")
    print(f"written: {out}")
    print(f"cycle_us={args.cycle_us} slots_us={slots} guard_us={args.cycle_us - sum(slots)}")


if __name__ == "__main__":
    main()
