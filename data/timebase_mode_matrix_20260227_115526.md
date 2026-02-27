# Timebase/PhaseLock Matrix

- source: `timebase_mode_matrix_20260227_115526.json`
- entries(ns): `305625/150000/325625`

| rank | case | ts_mode | plock | offset | tas_phase | fc_mean | fc_p01 | fps_mean | jit_mean |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | ptp_plock_on_off0_phase300k | TIME_FROM_PTP_1588 | 1 | 0 | 300000 | 99.298 | 96.274 | 9.725 | 3061.2 |
| 2 | ptp_plock_off_phase180k | TIME_FROM_PTP_1588 | 0 | 0 | 180000 | 99.531 | 95.159 | 9.724 | 3720.4 |
| 3 | ptp_plock_on_off90k_phase300k | TIME_FROM_PTP_1588 | 1 | 90000 | 300000 | 98.936 | 89.422 | 9.719 | 5407.3 |
| 4 | syncin_plock_off_phase180k | TIME_FROM_SYNC_PULSE_IN | 0 | 0 | 180000 | 99.319 | 85.429 | 9.738 | 15246.7 |
| 5 | syncin_plock_on_off90k_phase300k | TIME_FROM_SYNC_PULSE_IN | 1 | 90000 | 300000 | 77.267 | 50.666 | 9.326 | 63037.9 |

best: ptp_plock_on_off0_phase300k (fc_p01=96.274, fc_mean=99.298, fps_mean=9.725)
