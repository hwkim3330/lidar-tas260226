# Why Results Differ (기존 결과와 지금 결과가 다른 이유)

## 결론
기준 모델이 달라서 다르게 보인다.

- 과거 기준: `LEGACY 3392B` 해석(212B block 중심)
- 현재 실센서 기준: `RNG19_RFL8_SIG16_NIR16`, `3328B`, `columns_per_packet=16`

같은 센서라도 **활성 UDP profile / packet format 기준**이 다르면,
패킷 구조 해석과 기대 수치가 달라진다.

## 현재 실측 고정값
- `udp_profile_lidar=RNG19_RFL8_SIG16_NIR16`
- `columns_per_packet=16`
- `packet_payload=3328B`
- MTU1500 분할(IP payload): `[1480, 1480, 376]`
- IFG 포함 1GbE on-wire 합: `28.176us`

## 모드 바꿔도 패킷 크기가 같은 이유
모드(`512x10`, `1024x20`, `2048x10`)는 주로 프레임 컬럼 수/프레임레이트를 바꾼다.

패킷 크기는 아래가 같으면 그대로다:
- profile의 channel block bytes
- `columns_per_packet`
- `pixels_per_column`

현재 조건에서는 위 항목이 동일해 `3328B` 유지.

## 모드가 실제로 바꾸는 것
- `pps` (packets per second)
- `inter-packet dt`

예:
- 1280pps 모드(`1024x20`, `2048x10`) -> `~781.25us`
- 640pps 모드(`512x20`, `1024x10`) -> `~1562.5us`
- 320pps 모드(`512x10`) -> `~3125us`

## 실무 해석
- `28us`는 "한 패킷이 링크를 점유하는 시간(분할+IFG 합)"
- `781us`는 "패킷과 패킷 사이 도착 간격"
- 둘은 다른 물리량이라 숫자를 직접 비교하면 혼동이 생긴다.

## 근거 파일
- `../data/mode_packet_matrix_20260227_164427.md`
- `../data/packet_layout_detailed_20260227_164122.md`
- `../data/packet_timing_20260227_170633.md`
