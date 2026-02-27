# PTP Master Soak Compare

- source: `ptp_master_soak_compare_20260227_141403.json`

| case | ts_mode | plock | offset | phase | fc_mean | fc_p01 | fps_mean | jit_mean |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| ptp_plock_on_off60k_phase520k | TIME_FROM_PTP_1588 | 1 | 60000 | 520000 | 96.913 | 90.723 | 9.864 | 2524.6 |
| ptp_plock_off_phase180k | TIME_FROM_PTP_1588 | 0 | 0 | 180000 | 96.496 | 90.599 | 9.859 | 3714.8 |
| syncin_plock_off_phase180k | TIME_FROM_SYNC_PULSE_IN | 0 | 0 | 180000 | 96.771 | 91.061 | 9.862 | 2714.3 |

best: `syncin_plock_off_phase180k`
