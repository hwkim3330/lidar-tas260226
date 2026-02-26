# Paper Result Tables

## Table A. 600s Soak Comparison (All-open vs Deep-optimized)
- source: `data/deep_opt_150ns_20260226_170215.json`

| config | front(ns) | phase(ns) | fc_mean(%) | fc_p01(%) | fps_mean |
|---|---:|---:|---:|---:|---:|
| all_open | - | - | 99.713 | 96.527 | 10.002 |
| cand1_f305625_p220000 | 305625 | 220000 | 99.692 | 96.719 | 10.003 |
| cand2_f305625_p200000 | 305625 | 200000 | 99.706 | 96.812 | 10.003 |
| cand3_f305625_p180000 | 305625 | 180000 | 99.731 | 97.187 | 10.002 |

best-all_open delta: fc_mean=+0.018, fc_p01=+0.661, fps_mean=-0.000

## Table B. 120s Front/Back Split Sensitivity (open=150us, phase=0)
- source: `data/fine_front_compare_20260226_161307.json`

| config | entries(ns) | fc_mean(%) | fc_p01(%) | fps_mean |
|---|---|---:|---:|---:|
| all_open | `255/1000000` | 99.750 | 97.077 | 9.860 |
| front_305625 | `[[254, 305625], [255, 150000], [254, 325625]]` | 99.784 | 97.598 | 10.002 |
| front_315625 | `[[254, 315625], [255, 150000], [254, 315625]]` | 99.715 | 95.818 | 10.001 |
| front_325625 | `[[254, 325625], [255, 150000], [254, 305625]]` | 99.726 | 96.466 | 10.003 |


## Table C. Small-open Limitation (All-open vs 30/40/50us)
- source: `data/allopen_vs_smallopen_20260226_151542.json`

| config | open(us) | phase(ns) | fc_mean(%) | fc_p01(%) | fps_mean | delta_fc_mean |
|---|---:|---:|---:|---:|---:|---:|
| all_open | 781.25 | 0 | 99.926 | 98.084 | 9.870 | +0.000 |
| coc_open_30us | 30 | 0 | 90.389 | 90.025 | 4.889 | -9.537 |
| coc_open_40us | 40 | 651040 | 93.843 | 91.953 | 5.773 | -6.083 |
| coc_open_50us | 50 | 390624 | 97.274 | 96.817 | 6.117 | -2.652 |


## Table D. Long-run Boundary Near 150us (phase=0)
- source: `data/single_lidar_long_opt_20260226_110713.json`

| open(us) | repeats | comp_min(%) | comp_mean(%) | pass_all |
|---:|---:|---:|---:|---:|
| 168 | 3 | 100.000 | 100.000 | True |
| 164 | 3 | 100.000 | 100.000 | True |
| 160 | 3 | 100.000 | 100.000 | True |
| 156 | 3 | 100.000 | 100.000 | True |
| 152 | 3 | 99.997 | 99.999 | True |
| 150 | 3 | 99.996 | 99.997 | True |
| 148 | 3 | 99.997 | 99.998 | True |
| 146 | 3 | 99.997 | 99.998 | True |
| 144 | 3 | 25.077 | 50.725 | False |


