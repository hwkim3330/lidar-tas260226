# USB NIC vs PCIe NIC: Why measured packet interval differs

## One-line answer
측정 지점이 wire가 아니라 host userspace timestamp이기 때문이다.

## What stays same
- LiDAR wire behavior (profile/pps) is same for same sensor config.
- IFG, fragmentation, serialization on the link are PHY/MAC-side and do not depend on Python code.

## What changes by NIC path
- USB NIC path (`r8152` class):
  - USB transfer scheduling/URB batching
  - driver/NAPI poll batching
  - interrupt moderation/coalescing effects
  - host receive timestamp can be delayed and clustered
- PCIe onboard NIC path:
  - generally lower transfer-layer batching latency
  - hardware timestamp support likely available (`ethtool -T`)
  - host timestamp tends to be closer to wire-arrival timing

## Practical implication
- Userspace `recvfrom()` timestamp jitter includes:
  - NIC/driver buffering
  - kernel scheduling
  - process wakeup latency
- Therefore interval histograms can differ even when wire pps is unchanged.

## IFG-inclusive wire model in this repo
- Current profile: `RNG19_RFL8_SIG16_NIR16`
- Payload: `3328B`
- MTU1500 IP fragments: `[1480, 1480, 376]`
- 1GbE on-wire time including IFG (VLAN model): `28.176us`

## Figure
- Concept figure: `../data/usb_vs_pcie_timing_20260227_172658.png`
- Note: conceptual explanation figure, not direct pcap plot.

## How to verify experimentally later
1. Capture same run on PCIe NIC and USB NIC with kernel timestamping (`SO_TIMESTAMPING`) and compare distributions.
2. Disable GRO/LRO and compare (`ethtool -K <if> gro off lro off`).
3. Check interrupt coalescing (`ethtool -c <if>`).
4. Compare pps/len stability first, then dt histogram shape.
