# PTP Final Compare

- source: `ptp_final_compare_20260227_152849.json`

| case | plock | tas | phase | fc_mean | fc_p01 | fps_mean | udp_err_delta | rcvbuf_err_delta |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| ptp_allopen | 1 | allopen | 0 | 99.999 | 99.900 | 9.814 | 0 | 0 |
| ptp_plock_on_phase340k | 1 | profile | 340000 | 99.947 | 94.746 | 9.820 | 0 | 0 |
| ptp_plock_off_phase180k | 0 | profile | 180000 | 99.870 | 87.227 | 9.822 | 0 | 0 |

best: `ptp_allopen`
