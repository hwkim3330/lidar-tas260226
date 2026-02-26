# LAN9662 기반 Ouster LiDAR TAS 정렬 최적화: 781.25us 주기 실험 연구

## 초록
본 연구는 LAN9662 스위치와 Ouster LiDAR 단일 노드 환경에서 TSN TAS(Time-Aware Shaper) 파라미터를 실측 기반으로 최적화하고, 운영 안정 관점에서 의미 있는 설정을 도출하는 것을 목표로 한다. 핵심 질문은 세 가지다. 첫째, `open=28~30us` 수준의 극소 게이트가 실운영에서 가능한가. 둘째, LiDAR 시작 위상과 TAS base-time 위상을 실제로 얼마나 맞출 수 있는가. 셋째, 동일 open 폭에서 `close/open/close`의 앞뒤 close 분할을 ns 단위로 미세 조정하면 하위 퍼센타일 안정성이 개선되는가. 실험 결과, `open<=50us`는 최적 위상을 선택해도 all-open 동등 성능에 도달하지 못했다. 반면 `cycle=781250ns`, `open=150000ns` 조건에서 `close front/back` 및 phase를 동시 최적화하면 장시간(600s) 기준 all-open 대비 `fc_p01 +0.661%p`, `fc_mean +0.018%p` 개선을 확인했다. 최종 운영점은 `305625/150000/325625ns`, `phase=180000ns`, `phase_lock=false`로 정리된다.

## 1. 서론
LiDAR 트래픽은 고주기, 고대역폭, 지연 민감성을 동시에 갖는다. TSN TAS는 큐별 전송 허용 시점을 제어해 결정론적 전송을 목표로 하지만, 실제 운영에서는 단순 이론 폭 계산만으로 안정점이 결정되지 않는다. 이유는 다음과 같다.

1. LiDAR 송신 epoch를 사용자 측에서 절대 시각으로 완전 고정하기 어렵다.
2. MTU/fragmentation, 소프트웨어 스택, 장비 내부 파이프라인 등으로 이론 직렬화 시간과 실효 전송 윈도우가 어긋난다.
3. 평균 지표만 보면 좋아 보이나, 하위 퍼센타일 구간에서 급락이 발생할 수 있다.

본 연구는 "절대 동기 고정" 가정 대신, **상대 위상 정렬 + 하위 퍼센타일 강건성 최적화**를 채택했다.

## 2. 배경 및 관련 개념

### 2.1 TAS와 큐 적체 메커니즘
TAS에서 게이트가 닫히면 해당 트래픽 클래스 프레임은 egress 큐에 적체되고, 게이트가 열리면 누적 backlog가 방출된다. 이때 다음 현상이 발생한다.

1. drain burst로 인한 간격 분산 증가(`gap_stdev_us` 증가)
2. 프레임 유효열 손실 증가(`frame_completeness` 하락)
3. 주기적 위상 미스매치 시 하위 퍼센타일(`fc_p01`) 급락

큐 동작은 주기 단위로 다음과 같이 근사할 수 있다.

`B_{k+1} = max(0, B_k + A_k - S_k)`

- `B_k`: k번째 주기 시작 시 backlog
- `A_k`: close 구간 동안 유입 바이트
- `S_k`: open 구간 동안 서비스 가능한 바이트

`A_k > S_k`가 반복되면 backlog는 누적되고, 반대로 `A_k <= S_k`가 유지되면 queueing은 안정화된다.

### 2.2 본 연구의 해석 원칙
본 연구는 스위치 내부 큐 메모리(byte) 절대값을 직접 측정하지 않았다. 대신 `fc_mean`, `fc_p01`, `fps`, `gap_stdev_us`를 통해 queueing behavior를 간접 추정했다. 따라서 결론은 "큐 용량 산출"이 아니라 "운영 안정점을 찾는 실증 최적화"에 초점을 둔다.

## 3. 실험 환경

### 3.1 하드웨어/소프트웨어
1. 스위치: LAN9662 기반 보드
2. 센서: Ouster LiDAR (UDP stream)
3. 제어: `keti-tsn`로 TAS patch/fetch
4. 통계 수집: 로컬 웹 API(`/api/stats`)

### 3.2 고정 실험 조건
1. 기본 주기: `cycle = 781250ns (781.25us)`
2. 성능 지표:
   - 1차: `fc_p01` (하위 1% 안정성)
   - 2차: `fc_mean`
   - 3차: `fps_mean`, `fps_min`
3. 장시간 검증: 600s soak (all-open + candidate 비교)

### 3.3 데이터 및 재현
원본 결과는 `data/`에 저장되며, 논문 표는 아래로 재생성한다.

```bash
cd /home/kim/lidar-tas260226
python3 scripts/generate_paper_tables.py
```

생성 산출물:
1. `paper/results_tables.md`
2. 본문에서 인용한 Table A~D

## 4. 연구 방법

### 4.1 단계별 탐색 전략
1. phase sweep: `phase=0..cycle` 탐색
2. open 폭 탐색: wide->narrow 경계 확인
3. front/back ns 조정: `close_front_ns` 절대값 스윕
4. 단기 선별 후 장기(600s) 재검증

### 4.2 최적점 선택 규칙
후보 간 우선순위는 다음과 같다.

1. `fc_p01` 최대
2. 동률 시 `fc_mean` 최대
3. 동률 시 `fps_min` 최대

이 규칙은 "평균은 같아도 tail이 나쁜 설정"을 배제하기 위한 운영 중심 기준이다.

## 5. 결과

### 5.1 극소 open(30/40/50us) 검증
Table C 기준, 작은 open 구간은 phase 최적화 후에도 all-open 동등 성능을 만들지 못했다.

1. all-open: `fc_mean=99.926`, `fc_p01=98.084`, `fps_mean=9.870`
2. 30us best: `fc_mean=90.389`, `fps_mean=4.889`
3. 40us best: `fc_mean=93.843`, `fps_mean=5.773`
4. 50us best: `fc_mean=97.274`, `fps_mean=6.117`

해석: `open<=50us`는 이 환경에서 반복 가능한 운영 안정값이 아니다.

### 5.2 146~150us 경계
Table D 기준 장시간 반복에서 경계는 144~146us 사이로 확인됐다.

1. `open=146/148/150us`: pass-all 유지
2. `open=144us`: `comp_min=25.077%`로 붕괴

해석: 최소 안정 경계는 146us 부근이며, 운영 마진 고려 시 150us가 합리적이다.

### 5.3 front/back ns 미세조정 효과
동일 `open=150us`에서도 `close front/back` 분할에 따라 결과가 달라졌다(Table B).  
이는 "open 폭만 맞추면 충분"하다는 가설을 반박한다.

### 5.4 장시간 deep-opt 최종 비교
Table A(600s soak)에서 최종 best는 아래와 같다.

1. gate: `305625 / 150000 / 325625ns`
2. phase: `180000ns`
3. 비교 성능(대비 all-open):
   - `fc_p01: +0.661%p` (96.527 -> 97.187)
   - `fc_mean: +0.018%p`
   - `fps_mean`: 거의 동일

즉, 평균 성능을 유지하면서 tail 안정성을 개선했다.

## 6. 논의

### 6.1 "라이다 시작점 고정" 대신 "상대 정렬"
본 구성에서는 LiDAR 송신 시작 epoch를 사용자가 절대 0ns로 고정 제어하기 어렵다. 실무적으로는 다음이 유효했다.

1. switch time 기준 base-time 재설정
2. phase sweep으로 통과 구간 탐색
3. close front/back 미세 조정으로 tail 개선

### 6.2 큐 관점 해석
사용자 가설("게이트가 닫히면 버퍼 쌓였다가 다음 open에 나간다")은 본 데이터와 일치한다.  
본 실험에서 관찰된 `fps 저하 + completeness 하락 + jitter 증가` 조합은 backlog 누적/방출 패턴과 정합적이다.

### 6.3 운영 지표로서 fc_p01의 타당성
평균치가 비슷한 후보에서도 `fc_p01` 차이가 발생했다. 실제 운용에서는 순간 급락 구간이 서비스 품질에 직접 영향을 주므로, tail 중심 지표를 1차 최적화 기준으로 두는 것이 타당했다.

## 7. 결론
단일 LiDAR, `cycle=781250ns` 환경에서 다음 결론을 도출했다.

1. `open<=50us`는 최적 phase에서도 all-open 동등 성능 불가
2. 안정 경계는 146us 부근, 권장 운영값은 150us
3. `open=150us`에서 ns+phase 동시 최적화 시 tail 안정성 개선 가능
4. 최종 운영점: `305625 / 150000 / 325625ns`, `phase=180000ns`, `phase_lock=false`

## 8. 한계와 향후 연구
본 연구는 단일 실험 환경 중심이라 일반화 한계가 있다. 향후에는 아래를 수행한다.

1. 다일자 반복 실험 및 신뢰구간 제시
2. PTP on/off + 드리프트 추적(수시간)
3. 2~3대 LiDAR 동시 슬롯(TDMA) 검증
4. 통계 검정(부트스트랩 CI, 비모수 검정) 추가
5. 가능 시 스위치 큐 occupancy 텔레메트리와 상관 분석

## 9. 재현 절차(요약)
1. 표 재생성: `python3 scripts/generate_paper_tables.py`
2. best 재적용: `python3 scripts/apply_best_781p25_tas.py --disable-phase-lock`
3. fetch 검증: `admin/oper entry=305625/150000/325625`, `config-pending=false`

## 참고문헌(초안)
[1] IEEE Std 802.1Q, "Bridges and Bridged Networks."  
[2] IEEE 802.1 TSN Task Group documents on Time-Aware Shaping (Qbv).  
[3] Ouster Sensor Documentation (operational/network configuration).  
[4] 본 연구 데이터셋: `/home/kim/lidar-tas260226/data/*.json`.
