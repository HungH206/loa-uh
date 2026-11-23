# Experiment results

Fill in from the commands in [EXPERIMENTS.md](EXPERIMENTS.md). Keep the raw JSON output and CSVs in `experiments/results/`.

## Setup

| Item | Value |
| --- | --- |
| Date | |
| Server | Raspberry Pi 5, OS / Python / Mosquitto versions |
| Sensor | XIAO ESP32-S3 Sense, firmware commit |
| Load generator host | |
| Wi-Fi | Band, access point, sensor RSSI (from `LINK wifi_up`) |
| Clock sync | NTP server; Pi `timedatectl` status; estimated offset |
| Capture point | Interface and tcpdump filter |

## Experiment 1 — MQTT vs. HTTP (1 Hz, ___ s)

| Metric | MQTT QoS 0 | HTTP keep-alive | HTTP new connection |
| --- | --- | --- | --- |
| Readings sent / delivered | | | |
| Delivery ratio | | | |
| Payload bytes per reading | | | |
| Wire (frame) bytes per reading | | | |
| Packets per reading | | | |
| Header overhead ratio | | | |
| New TCP connections | | | |
| RTT p50 / p95 (ms) | | | |
| One-way latency p50 / p95 (ms) | | | |

Observations:

## Experiment 2 — Rate and bandwidth (___, ___ s per run)

| Target rate | Achieved rate (Hz) | Delivery ratio | Inter-arrival stdev (ms) | Throughput (bit/s) | Bytes per reading | Latency p95 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 Hz | | | | | | |
| 5 Hz | | | | | | |
| 10 Hz | | | | | | |

Observations:

## Experiment 3 — Disconnection and recovery

| Trial | Cause | Outage length | Wi-Fi back → MQTT up (s) | Broker timeout logged after (s) | Dashboard offline after (s) | Readings lost | `recovery_s` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | AP off | | | | | | |
| 2 | Out of range | | | | | | |
| 3 | Broker stopped | | | | | | |

TCP observations from Wireshark (retransmissions, RST/FIN, new handshake):

## Experiment 4 — MQTT QoS 0 vs. QoS 1 (___ Hz, ___ s)

| Metric | QoS 0 | QoS 1 |
| --- | --- | --- |
| Delivery ratio | | |
| Packets per reading | | |
| Wire bytes per reading | | |
| PUBLISH → PUBACK p50 / p95 (ms) | n/a | |

Observations:

## Conclusions
