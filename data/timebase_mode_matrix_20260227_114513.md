# Timebase/PhaseLock Matrix

- source: `timebase_mode_matrix_20260227_114513.json`
- entries(ns): `305625/150000/325625`

| rank | case | ts_mode | plock | offset | tas_phase | fc_mean | fc_p01 | fps_mean | jit_mean |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | ptp_plock_on_off0_phase300k | TIME_FROM_PTP_1588 | 1 | 0 | 300000 | 99.314 | 94.797 | 9.808 | 2689.7 |
| 2 | ptp_plock_off_phase180k | TIME_FROM_PTP_1588 | 0 | 0 | 180000 | 99.161 | 90.210 | 9.815 | 4138.4 |
| 3 | ptp_plock_on_off90k_phase300k | TIME_FROM_PTP_1588 | 1 | 90000 | 300000 | 99.062 | 89.219 | 9.819 | 4704.6 |
| 4 | syncin_plock_off_phase180k | TIME_FROM_SYNC_PULSE_IN | 0 | 0 | 180000 | 99.275 | 86.101 | 9.822 | 6908.4 |
| 5 | syncin_plock_on_off90k_phase300k | TIME_FROM_SYNC_PULSE_IN | 1 | 90000 | 300000 | 54.132 | 17.000 | 6.441 | 56410.5 |

best: ptp_plock_on_off0_phase300k (fc_p01=94.797, fc_mean=99.314, fps_mean=9.808)
