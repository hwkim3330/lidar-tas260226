# LAN9662 기반 Ouster LiDAR TSN TAS 정렬 최적화: 781.25us 주기 실험 보고서 (초안)

## 초록
본 문서는 LAN9662 스위치와 Ouster LiDAR 단일 노드 환경에서, TAS(Time-Aware Shaper) 게이트 파라미터를 실측 기반으로 최적화한 결과를 정리한다. 핵심 질문은 (1) 매우 좁은 open window(예: 28~30us)의 운영 가능성, (2) LiDAR 시작 위상과 TAS 시작 위상 정렬의 실효성, (3) `close/open/close` 앞뒤 close 구간 미세 조정의 효과이다. 실험 결과, `open<=50us` 영역은 최적 위상을 선택해도 all-open 동등 성능에 도달하지 못했다. 반면 `cycle=781250ns`, `open=150000ns` 조건에서 `close front/back`와 phase를 ns 단위로 정렬하면 하위 퍼센타일(frame completeness p01)이 개선되었다. 최종 장시간(600s) 비교에서 `305625/150000/325625ns`, `phase=180000ns` 설정은 all-open 대비 `fc_p01 +0.661%p`, `fc_mean +0.018%p`, fps 동등 수준을 기록했다.

## 1. 서론
실시간 LiDAR 스트림은 주기적 고속 트래픽 특성으로 인해 큐 적체, burst, 위상 미스매치에 민감하다. TSN TAS는 전송 게이트를 주기적으로 제어해 지터와 혼잡을 줄일 수 있으나, 실제 장비 환경에서는 다음 문제가 존재한다.

1. LiDAR 송신 위상을 사용자가 절대 시간 0ns에 고정하기 어려움.
2. MTU/프래그먼트 등 링크 계층 외 요인으로 이론 open 폭과 실측 안정 폭이 다름.
3. 단일 평균 지표보다 하위 퍼센타일(p01/p05)이 운영 안정성을 더 잘 반영함.

본 실험은 위 조건을 전제로, "완전 동기 고정"이 아니라 "상대 위상 정렬 + 강건 구간 확보" 접근을 채택하였다.

## 2. 실험 환경 및 방법

### 2.1 시스템
1. 스위치: LAN9662
2. 센서: Ouster LiDAR
3. 기본 주기: `781.25us (781250ns)`
4. 평가 지표: `frame_completeness`, `fps` (보조: `fc_p01`, `fc_p05`, `fc_min`)

### 2.2 방법론
1. 스위치 `current-time` fetch 후 `admin-base-time = now + offset + phase`로 TAS 시작점 설정.
2. phase sweep으로 위상 민감도를 확인.
3. open 폭 및 `close front/back`를 ns 단위로 변경.
4. 단기(120s)와 장기(600s) soak를 분리해 성능 비교.
5. 최종 의사결정은 `fc_p01` 우선, 이후 `fc_mean`, `fps_min` 순으로 선택.

### 2.3 데이터 및 재현
실험 원본은 `data/`에 저장되며, 논문용 표는 아래 스크립트로 재생성 가능하다.

```bash
cd /home/kim/lidar-tas260226
python3 scripts/generate_paper_tables.py
```

생성 파일: `paper/results_tables.md`

## 3. 결과

### 3.1 좁은 open window의 한계
`open=30/40/50us` 구간은 최적 phase를 선택해도 all-open 대비 큰 손실이 남았다(Table C).

1. `30us`: `fc_mean 90.389`, `fps_mean 4.889`
2. `40us`: `fc_mean 93.843`, `fps_mean 5.773`
3. `50us`: `fc_mean 97.274`, `fps_mean 6.117`

해석: 단일 LiDAR에서도 open 폭이 지나치게 좁으면 큐 비적체 가정이 성립하지 않으며, 실운영 동등 성능 확보가 어렵다.

### 3.2 146~150us 경계 안정성
장시간 경계 실험에서 `open=146us`부터 pass-all이 유지되었고, `144us`는 급락했다(Table D).

1. `146us`: `comp_min 99.997`, pass-all
2. `150us`: `comp_min 99.996`, pass-all
3. `144us`: `comp_min 25.077`, fail

해석: 본 구성의 안정 경계는 144~146us 사이이며, 운영 마진 포함 150us가 타당하다.

### 3.3 ns 미세정렬 효과
`open=150us`를 유지한 채 `close front/back`를 조정하면 하위 퍼센타일이 달라졌다(Table B). 이후 장시간 deep-opt(Table A)에서 다음 설정이 최적이었다.

1. `close/open/close = 305625 / 150000 / 325625ns`
2. `phase = 180000ns`

장시간(600s) 비교 결과:

1. all-open: `fc_p01=96.527`, `fc_mean=99.713`, `fps_mean=10.002`
2. best: `fc_p01=97.187`, `fc_mean=99.731`, `fps_mean=10.002`

즉, all-open 대비 `fc_p01 +0.661%p` 개선을 확인했다.

## 4. 논의
핵심은 "LiDAR 시작점을 절대 고정"하는 것이 아니라 "drift/위상 오차를 견디는 운영점"을 찾는 것이다. 본 결과는 다음을 시사한다.

1. 평균값(`fc_mean`)만으로는 최적점 구분이 약하고, 하위 퍼센타일(`fc_p01`)이 더 민감한 선택 기준이다.
2. 같은 `open=150us`라도 `close front/back` 분할이 달라지면 안정성이 달라진다.
3. 따라서 단순 비율 튜닝보다 절대 ns + phase 동시 최적화가 유효하다.

## 5. 결론
본 환경에서 단일 LiDAR 운영 최적점은 다음으로 정리된다.

1. 주기: `781250ns`
2. 게이트: `305625 / 150000 / 325625ns`
3. phase: `180000ns`
4. 센서 phase lock: `false`

운영 관점에서 28~30us 수준의 극소 open은 재현 가능한 안정값이 아니며, 최소 안정 경계(146us 부근)와 마진(150us)을 함께 고려해야 한다.

## 6. 한계 및 향후 실험
현재 초안은 단일 날짜/환경 실험 중심이라 일반화에 한계가 있다. 논문화 단계에서는 아래가 추가되어야 한다.

1. 반복 실험 N회(다일자) 및 신뢰구간 제시
2. PTP on/off, 위상 drift 장시간(수시간) 비교
3. 다중 LiDAR 동시 슬롯 구성(2~3대)에서 충돌 회피 성능
4. 통계 검정(예: 부트스트랩 CI 또는 비모수 검정)으로 유의성 제시

## 부록 A. 주요 표
정량 표는 `paper/results_tables.md`를 참조한다.
