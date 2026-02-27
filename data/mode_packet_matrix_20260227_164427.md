# LiDAR Mode Packet Matrix

- docs: https://static.ouster.dev/sensor-docs/image_route1/image_route2/sensor_data/sensor-data.html#lidar-data-packet-format
- duration per mode: `20.0s`
- settle after mode switch: `4.0s`
- restored mode: `1024x20`

| mode | active | pps_exp | pps_meas | pps_err(%) | pkt_size_exp | pkt_size_min/max | dt_exp(us) | dt_mean(us) | dt_p95(us) | dt_p99(us) |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| 512x10 | 512x10 | 320.000 | 319.954 | -0.014 | 3328 | 3328/3328 | 3125.000 | 3125.447 | 3463.011 | 3668.536 |
| 512x20 | 512x20 | 640.000 | 639.951 | -0.008 | 3328 | 3328/3328 | 1562.500 | 1562.620 | 1853.651 | 2078.182 |
| 1024x10 | 1024x10 | 640.000 | 639.959 | -0.006 | 3328 | 3328/3328 | 1562.500 | 1562.600 | 1847.469 | 2069.363 |
| 1024x20 | 1024x20 | 1280.000 | 1279.895 | -0.008 | 3328 | 3328/3328 | 781.250 | 781.314 | 942.282 | 1127.481 |
| 2048x10 | 2048x10 | 1280.000 | 1279.965 | -0.003 | 3328 | 3328/3328 | 781.250 | 781.272 | 939.689 | 1126.183 |

## Histograms
- mode_packet_matrix_20260227_164427_512x10_dt_hist.png
- mode_packet_matrix_20260227_164427_512x20_dt_hist.png
- mode_packet_matrix_20260227_164427_1024x10_dt_hist.png
- mode_packet_matrix_20260227_164427_1024x20_dt_hist.png
- mode_packet_matrix_20260227_164427_2048x10_dt_hist.png