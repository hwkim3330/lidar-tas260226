# Repro Runbook

## 0) 기본
```bash
cd /home/kim/lidar-tas260226
```

## 1) 패킷 상세 그림 재생성
```bash
python3 scripts/generate_packet_layout_detailed.py --host 192.168.6.11 --mtu 1500
```

모드 지정:
```bash
python3 scripts/generate_packet_layout_detailed.py --host 192.168.6.11 --set-mode 2048x10 --mtu 1500
```

## 2) 모드 전수 패킷 매트릭스
```bash
python3 scripts/run_lidar_mode_packet_matrix.py \
  --host 192.168.6.11 \
  --duration-s 20 \
  --settle-s 4 \
  --restore-mode 1024x20
```

## 3) 단일 장시간 패킷 수집
```bash
python3 scripts/analyze_lidar_packet_timing.py --host 192.168.6.11 --duration-s 600
```

## 4) 24시간 수집
```bash
./scripts/run_24h_packet_soak.sh
```

## 5) 데이터 카탈로그 갱신
```bash
python3 scripts/build_data_catalog.py
```

## 6) 종료 전 체크
```bash
git status --short
ls -1 data | tail -n 30
```
