# TSN TAS Optimization for Ouster LiDAR on LAN9662: An Experimental Study at 781.25 us Cycle

## Abstract
This paper presents an experimental optimization study of Time-Aware Shaper (TAS) parameters for a single Ouster LiDAR stream on a LAN9662-based TSN switch. We focus on three practical questions: (1) whether very narrow gate windows (`28-30 us`) are operationally stable, (2) how effectively LiDAR packet timing can be aligned with TAS base-time phase, and (3) whether nanosecond-level front/back close-split tuning improves tail robustness at a fixed open window. Results show that `open <= 50 us` cannot reach all-open-equivalent behavior, even after phase search. In contrast, at `cycle=781250 ns` and `open=150000 ns`, joint optimization of close-split and phase improves long-run (600 s) robustness over all-open by `+0.661 percentage points` in `fc_p01` and `+0.018 percentage points` in `fc_mean`, with near-identical FPS. The best operating point is `305625/150000/325625 ns` (close/open/close) with `phase=180000 ns` and `phase_lock=false`.

## 1. Introduction
LiDAR traffic is periodic, high-rate, and sensitive to timing mismatches. TAS can shape egress transmission deterministically, but real deployments rarely match ideal timing assumptions due to phase drift, packetization effects, and platform timing uncertainty. Therefore, we target robust operating points rather than single-point theoretical minima.

This study is motivated by the following practical issues:

1. LiDAR transmit epoch is difficult to pin to an absolute global zero in practice.
2. Effective transmission windows deviate from nominal serialization estimates.
3. Mean metrics may hide instability in lower-tail behavior.

Accordingly, we optimize with a tail-first criterion (`fc_p01`) and validate with long soak tests.

## 2. Queueing Perspective
When a TAS gate is closed, frames accumulate in the egress queue; when it opens, queued traffic drains in bursts. The per-cycle backlog can be approximated by:

`B_{k+1} = max(0, B_k + A_k - S_k)`

where `B_k` is cycle-start backlog, `A_k` is arrivals during closed periods, and `S_k` is service during open periods. Repeated `A_k > S_k` yields carry-over backlog, larger burstiness, and degraded frame completeness tails.

This work does not directly measure switch-internal queue memory in bytes; instead, queueing behavior is inferred from stream-level statistics (`frame_completeness`, `fps`, and gap jitter metrics).

## 3. Experimental Setup

### 3.1 Platform
1. Switch: LAN9662 board
2. Sensor: Ouster LiDAR (UDP stream)
3. TAS control: `keti-tsn` patch/fetch workflow
4. Metrics source: local web API (`/api/stats`)

### 3.2 Fixed Conditions
1. Cycle: `781250 ns` (`781.25 us`)
2. Primary metric: `fc_p01`
3. Secondary metrics: `fc_mean`, `fps_mean`, `fps_min`
4. Long-run validation: 600 s soak per candidate

### 3.3 Reproducibility Assets
1. Raw datasets: `data/*.json`
2. Table generator:
```bash
cd /home/kim/lidar-tas260226
python3 scripts/generate_paper_tables.py
```
3. Generated tables: `paper/results_tables.md`

## 4. Methodology

### 4.1 Search Procedure
1. Phase sweep across the cycle
2. Open-window sweep from wide to narrow
3. Absolute nanosecond tuning of `close_front_ns` and `close_back_ns`
4. Short-run screening followed by long-run soak comparison

### 4.2 Selection Rule
Candidates are ranked in this order:

1. maximize `fc_p01`
2. tie-break by `fc_mean`
3. then by `fps_min`

This favors operational robustness under occasional timing mismatch.

## 5. Results

### 5.1 Limitation of Very Narrow Open Windows
From Table C (`allopen_vs_smallopen_20260226_151542.json`), small open windows remain far below all-open behavior:

1. all-open: `fc_mean=99.926`, `fc_p01=98.084`, `fps_mean=9.870`
2. 30 us best: `fc_mean=90.389`, `fps_mean=4.889`
3. 40 us best: `fc_mean=93.843`, `fps_mean=5.773`
4. 50 us best: `fc_mean=97.274`, `fps_mean=6.117`

Hence, `open <= 50 us` is not a reproducible operating region in this setup.

### 5.2 Boundary Near 150 us
From long-run boundary data (`single_lidar_long_opt_20260226_110713.json`), stability transitions between 144 us and 146 us:

1. `146/148/150 us`: pass-all across repeats
2. `144 us`: severe collapse (`comp_min=25.077`)

Thus, practical operation should keep margin around `150 us`.

### 5.3 Benefit of Nanosecond Front/Back Split Tuning
At fixed `open=150 us`, front/back split changes tail outcomes (Table B), indicating that open width alone is insufficient.

### 5.4 Final 600 s Deep Optimization
From `deep_opt_150ns_20260226_170215.json` (Table A), the best profile is:

1. `close/open/close = 305625/150000/325625 ns`
2. `phase = 180000 ns`
3. versus all-open:
   - `fc_p01: +0.661 pp` (`96.527 -> 97.187`)
   - `fc_mean: +0.018 pp`
   - `fps_mean`: nearly unchanged

This improves lower-tail robustness without throughput penalty.

## 6. Discussion
The data support a practical interpretation: alignment quality is governed by relative phase robustness, not by forcing a perfect absolute LiDAR start epoch. Even with similar means, lower-tail differences (`fc_p01`) are significant for real operation. Therefore, joint `(phase, close-front, close-back)` tuning is more effective than ratio-only or open-width-only tuning.

## 7. Conclusion
For single-LiDAR operation at `781250 ns` cycle on this platform:

1. Extremely narrow opens (`<= 50 us`) are not operationally stable.
2. Stable boundary is around `146 us`, with recommended margin at `150 us`.
3. Nanosecond split + phase optimization yields measurable tail gains.
4. Recommended profile: `305625/150000/325625 ns`, `phase=180000 ns`, `phase_lock=false`.

## 8. Limitations and Future Work
This is a single-platform, single-date empirical study. Next steps:

1. multi-day repeats with confidence intervals
2. PTP on/off drift-tracking over multi-hour duration
3. multi-LiDAR slot scheduling validation
4. statistical significance testing (bootstrap or non-parametric tests)
5. correlation with direct queue telemetry when available

## References (Draft)
[1] IEEE Std 802.1Q, Bridges and Bridged Networks.  
[2] IEEE 802.1 TSN Task Group documents on Time-Aware Shaper (Qbv).  
[3] Ouster sensor networking/configuration documentation.  
[4] Dataset and scripts: `/home/kim/lidar-tas260226/data`, `/home/kim/lidar-tas260226/scripts`.
