# Extra Experiments (2026-02-26)

- This matrix was measured while 3D web server stayed running and stats were read from `/api/stats`.
- Absolute pps can be biased by server smoothing, but relative robustness trends are still visible.

### Sensor mode: 2048x10

| phase_lock | open_us | comp_min | comp_mean | comp_max | pass>=99.9 |
|:---:|---:|---:|---:|---:|---:|
| off | 144 | 77.79 | 91.94 | 100.00 | 11/20 |
| off | 146 | 100.00 | 100.00 | 100.00 | 20/20 |
| off | 150 | 100.00 | 100.00 | 100.00 | 20/20 |
| on | 144 | 81.79 | 88.52 | 100.00 | 3/20 |
| on | 146 | 100.00 | 100.00 | 100.00 | 20/20 |
| on | 150 | 100.00 | 100.00 | 100.00 | 20/20 |

### Sensor mode: 1024x10

| phase_lock | open_us | comp_min | comp_mean | comp_max | pass>=99.9 |
|:---:|---:|---:|---:|---:|---:|
| off | 144 | 53.55 | 77.69 | 100.00 | 5/10 |
| off | 146 | 54.12 | 56.49 | 58.68 | 0/10 |
| on | 144 | 52.91 | 54.56 | 55.96 | 0/10 |
| on | 146 | 54.40 | 56.61 | 60.00 | 0/10 |

## Interpretation

- 2048x10: open 146/150 stayed robust across sampled phases; 144 remained phase-sensitive.
- phase_lock_enable=true did not improve the 144us boundary in this setup (no external sync pulse lock).
- 1024x10 did not make 144/146 robust in server-based matrix; mode change alone is not a fix.
