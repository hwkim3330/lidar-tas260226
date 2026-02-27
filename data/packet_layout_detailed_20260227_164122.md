# Detailed Packet Layout

- docs: https://static.ouster.dev/sensor-docs/image_route1/image_route2/sensor_data/sensor-data.html#lidar-data-packet-format
- profile: `RNG19_RFL8_SIG16_NIR16`
- lidar_mode: `1024x20`
- timestamp_mode: `TIME_FROM_PTP_1588`
- packet_payload_bytes: `3328`
- packet_interval_us: `781.250`

## Byte Layout
- packet_header: `32B`
- columns region: `16 x 204B`
- packet_footer: `32B`
- one column: `12B + 16 x 12B`

## Fragment / IFG timing
- mtu: `1500`
- ip_payload_fragments: `[1480, 1480, 376]`
- onwire_us_per_fragment: `[12.336, 12.336, 3.504]`
- onwire_us_total: `28.176`
- wire_share_percent_of_interval: `3.61%`

## Figures
- `packet_layout_detailed_20260227_164122_packet.png`
- `packet_layout_detailed_20260227_164122_column.png`
- `packet_layout_detailed_20260227_164122_channel_table.png`
- `packet_layout_detailed_20260227_164122_frag_timing.png`
