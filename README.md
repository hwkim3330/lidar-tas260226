# lidar-tas260226

LAN9662(9662 보드) + Ouster LiDAR 환경에서 `cycle=781us` 고정 TAS 실험을 위한 정리 레포.

목표:
- 처음에는 gate open을 넓게 시작
- open window를 점점 줄이면서 drop/jitter 임계점 찾기
- 필요 시 base-time 정렬(PTP 유무에 따라 방법 구분)

## 빠른 시작

1. 의존
```bash
python3 --version
```

2. 스윕 실행 (open 넓게 시작)
```bash
cd /home/kim/lidar-tas260226
python3 scripts/tas_781_wide_to_narrow.py \
  --keti-dir /home/kim/keti-tsn-cli-new \
  --duration 20 \
  --start-open-us 200 \
  --min-open-us 36 \
  --step-us 4
```

LiDAR 송신 대상/타임스탬프 모드 설정:
```bash
cd /home/kim/lidar-tas260226
./scripts/lidar_sensor_config.sh 192.168.6.11 192.168.6.1 TIME_FROM_PTP_1588
```

정렬 전체 테스트(phase 탐색 + 최소 open 탐색):
```bash
cd /home/kim/lidar-tas260226
python3 scripts/run_full_alignment_suite.py
```

기본값:
- `cycle=781us`
- open: `200, 196, 192, ...` 식으로 감소
- close: `781 - open`
- UDP 포트 `7502`

## 시작시간(base-time) 정렬

### 1) PTP 없이
- `admin-base-time = 0`(즉시 적용)으로 실험 가능
- 단, LiDAR/보드 클럭이 따로 놀아서 장시간 위상 고정은 어려움

### 2) PTP 사용
- LiDAR와 보드가 같은 시간축(PTP/TAI 기준)에 붙으면 시작위상 유지가 쉬움
- 스크립트에서 `--base-time-mode tai-future` 사용 시 현재 시각+여유시간으로 base-time 설정

예:
```bash
python3 scripts/tas_781_wide_to_narrow.py \
  --base-time-mode tai-future \
  --base-time-offset-sec 2
```

## PTP 실행 (권장: 스위치/하드웨어 NIC)

현재 PC 기준 확인 결과:
- `enxc84d4420405b` (USB r8152): PTP HW timestamp 미지원 (`ptp4l` 불가)
- `enp4s0`: PTP HW timestamp 지원

실행:
```bash
cd /home/kim/lidar-tas260226
./scripts/ptp_start.sh enp4s0 slave 24
./scripts/ptp_status.sh enp4s0
```

중지:
```bash
./scripts/ptp_stop.sh enp4s0
```

참고:
- LiDAR가 USB NIC 경로에만 연결된 현재 배선에서는, PTP 기준시계는 스위치(9662) 쪽에서 잡는 구성이 더 현실적
- 즉, 스위치와 LiDAR를 PTP 동기시키고 TAS base-time을 그 시간축으로 넣는 방식 권장

## 결과 파일
- `data/sweep_781_wide_to_narrow_<timestamp>.json`

주요 지표:
- `completeness_pct` (1280pps 기준)
- `gap_stdev_us`
- `gap_p99_us`
- `burst_pct` (50us 미만 간격 비율)

## 주의
- 테스트 종료 후에는 `configs/tas_disable_all_open.yaml`로 all-open 복귀 권장
- `gate-enabled: false` 대신 all-open 단일 GCL로 복귀하는 편이 안전함

복귀 예:
```bash
cd /home/kim/keti-tsn-cli-new
./keti-tsn patch /home/kim/lidar-tas260226/configs/tas_disable_all_open.yaml
```
