# lidar-tas260226

LAN9662 + Ouster LiDAR에서 `cycle=781us` TAS를 실측한 레포.

## 결론 (핵심)

- 질문: `TAS open 28us`를 딱 맞출 수 있나?
- 답: **이론적으로는 가능해 보이지만, 현재 구성에서는 안정 운용 불가**.

이유:
- 1GbE에서 3328B LiDAR UDP 1개 직렬화 시간은 대략 `26.9us`.
- 하지만 현재 경로(MTU 1500)에서는 이 UDP가 **IP fragment 3개**로 전송됨.
- 따라서 실제로는 약 `26.9us x 3 = 80.8us` + 가드가 필요.
- 또, LiDAR 송신 시작 위상과 TAS gate 시작 위상이 계속 어긋나면 결과가 급변.

즉, `28us`는 "딱 맞춰 쓰는 운영값"으로는 현실적으로 매우 불안정함.

## 시작위상(라이다 시작점) 관련 결론

- 질문: "라이다 시작지점을 못 정하는 거 아님?"
- 답: **맞음. 현재 구성에선 완전 고정하기 어려움.**

정확히는:
- PTP 없으면: 시간축이 달라서 위상 유지 거의 불가.
- PTP 있어도: LiDAR 패킷 배출 위상을 사용자가 0ns로 완전히 고정 제어하기 어렵고, 드리프트/재초기화 영향이 남음.
- 그래서 실무는 "정렬(phase sweep)로 최적 위상 찾기 + open 여유 확보" 방식으로 운영.

## 이번 실험 결과 (2026-02-26)

기준:
- `cycle = 781us`
- `base-time = switch-future` (스위치 current-time 기준)
- phase sweep 후 best phase로 open sweep 수행

요약 결과:
- best phase: `0 ns`
- best phase 성능(open=120us): `96.19%` (100% 아님)
- open sweep에서 `>=99.9%` 최소 open: `148us`
- `144us`: `90.37%`
- `140us`: `0%`
- `28us`: `0%`
- `0us`(완전 차단): `0%`

원본:
- `data/alignment_summary_20260226_103136.md`
- `data/phase_sweep_20260226_103136.json`
- `data/open_sweep_best_phase_20260226_103136.json`

## 권장 운영값

- 안전 운용은 `open >= 160us`부터 시작 권장.
- 최소화 실험은 `160 -> 152 -> 148 -> 144`처럼 경계 근처를 촘촘히 검증.
- `28us`는 실험값으로는 의미가 있어도 운영 목표값으로는 비권장.

## 실행 방법

1. LiDAR 설정 (UDP 목적지 + timestamp mode)
```bash
cd /home/kim/lidar-tas260226
./scripts/lidar_sensor_config.sh 192.168.6.11 192.168.6.1 TIME_FROM_PTP_1588
```

2. 전체 정렬 테스트 (phase + open)
```bash
cd /home/kim/lidar-tas260226
python3 scripts/run_full_alignment_suite.py
```

3. 단일 스윕
```bash
cd /home/kim/lidar-tas260226
python3 scripts/tas_781_wide_to_narrow.py \
  --keti-dir /home/kim/keti-tsn-cli-new \
  --duration 5 \
  --start-open-us 200 \
  --min-open-us 0 \
  --step-us 4 \
  --base-time-mode switch-future \
  --base-time-offset-sec 2
```

## PTP 메모

- `enxc84d4420405b`(USB r8152): HW timestamp 미지원 (`ptp4l` 불가)
- `enp4s0`: HW timestamp 지원
- 현재 배선에서는 스위치(9662) 기준 시간축으로 맞추는 구성 권장

## 테스트 후 복구

```bash
cd /home/kim/keti-tsn-cli-new
./keti-tsn patch /home/kim/lidar-tas260226/configs/tas_disable_all_open.yaml
```
