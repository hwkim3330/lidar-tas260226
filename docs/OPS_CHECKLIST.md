# Ops Checklist

## 실험 전
- [ ] LiDAR 전원/링크 확인
- [ ] UDP 7502 포트 점유 확인 (`ss -lunp | rg ':7502 '`) 
- [ ] 기존 웹서버가 7502 점유 중이면 중지
- [ ] 센서 config 확인 (`lidar_mode`, `udp_profile_lidar`, `columns_per_packet`)

## 실험 중
- [ ] mode 변경 시 반드시 `reinitialize` 후 settle 대기
- [ ] 각 실험의 duration/settle 값을 로그에 기록
- [ ] 결과 파일명 timestamp 확인

## 실험 후
- [ ] `python3 scripts/build_data_catalog.py` 실행
- [ ] 핵심 md/json/png 생성 확인
- [ ] `README.md`에 필요한 링크/요약 반영
- [ ] `git add/commit/push`

## 해석 주의
- [ ] `packet_size`와 `inter-packet dt`는 다른 개념
- [ ] 28us(직렬화)와 781us(도착간격)를 혼동하지 않기
- [ ] legacy(3392B) 설명과 current profile(3328B) 설명을 분리해 기록
