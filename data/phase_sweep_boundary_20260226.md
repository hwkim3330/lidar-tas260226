# Phase Boundary Sweep (2026-02-26)

`cycle=781us`, `duration=3s`, `phase step=20us`, `base-time=switch-future`.

| open_us | min completeness | mean completeness | max completeness | nonzero phases | phases >=99.9% |
|---:|---:|---:|---:|---:|---:|
| 144 | 0.00 | 11.51 | 97.96 | 5/40 | 0/40 |
| 146 | 99.95 | 99.98 | 100.00 | 40/40 | 40/40 |
| 148 | 99.97 | 99.98 | 100.00 | 40/40 | 40/40 |
| 150 | 99.96 | 99.99 | 100.00 | 40/40 | 40/40 |

Interpretation:
- Boundary is between 144us and 146us.
- 144us is phase-sensitive and unstable.
- 146us and above are phase-robust for this setup.
- Operational margin recommendation: 150us.
