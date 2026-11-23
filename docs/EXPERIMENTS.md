# Networking experiments

These are the experiments from the proposal (section 8), plus a fourth on MQTT QoS. Each one says what to run, what to capture, and what to record in [EXPERIMENT_RESULTS.md](EXPERIMENT_RESULTS.md).

| Tool | Purpose |
| --- | --- |
| Firmware build flags | Real sensor at 1/5/10 Hz over MQTT or HTTP, with NTP send timestamps and `LINK` serial logs |
| `python -m experiments.load` | Synthetic sensor from a laptop: MQTT QoS 0/1 or HTTP, any rate, per-message round-trip times |
| `python -m experiments.analyze` | Delivery ratio, arrival rate, jitter, one-way latency, outages from the backend's SQLite |
| `python -m experiments.pcap_summary` | Packets, bytes, handshakes, retransmissions and bytes per reading from a tcpdump capture |
| `python -m experiments.monitor` | Live view of the broker: per-sensor rate, lost readings, silent/recovered sensors, broker load from `$SYS` |
| `python -m experiments.plot` | PNG charts: traffic over time, per-message RTT/latency, bar comparisons across runs |

Run all commands from the repository root with `.venv/bin/python`. Every tool has `--help`. Charts need matplotlib: `.venv/bin/python -m pip install -r requirements-experiments.txt` (on the laptop; the Pi does not need it).

Save each tool's JSON output to a file so it can be charted later, for example `... experiments.load ... > experiments/results/exp1-mqtt-load.json` (the run's boot ID goes to stderr, so it still shows in the terminal).

## Live MQTT monitor on the Pi

`experiments/monitor.py` is a single file that only needs paho-mqtt, which the Pi hub's virtualenv already has. It subscribes read-only to `loa/+/noise` and the broker's `$SYS/broker/#` statistics using the backend's broker account, but with its own client ID, so the backend stays connected.

```sh
scp experiments/monitor.py <pi-user>@<PI-IP>:~/
ssh <pi-user>@<PI-IP>
sudo /opt/loa/.venv/bin/python ~/monitor.py --env-file /etc/loa/loa.env --log ~/monitor-events.csv
```

`sudo` is only needed to read `/etc/loa/loa.env`. Every 2 seconds the screen shows the broker (connected clients, messages and bytes in/out per minute) and each sensor (live/SILENT, readings per second, received, lost, delivery %, seconds since last reading, latest dBFS, bytes per message). Events such as `new_device`, `lost`, `silent`, `recovered`, `reboot` and `broker_down` appear under the table and are appended to the `--log` CSV with timestamps, which gives a record for Experiment 3. Use `--no-clear` to scroll instead of redrawing, and `--gap` to change how many seconds without readings count as silent (default 3). The monitor only sees MQTT; readings sent over HTTP never pass through the broker.

The dashboard's **Network** tab shows the same broker statistics and per-sensor figures in the browser (see the README). Use the terminal monitor on the Pi when you want a log file or no browser.

**Allow `$SYS` statistics.** The Network tab's broker card needs this too. Without this the sensor table still works, but the broker line says no statistics are available. New broker configurations from `scripts/configure_local.py` include it. For an existing install, add `topic read $SYS/#` under `user loa-backend` in the ACL, then reload Mosquitto:

```sh
# Pi
sudo nano /etc/mosquitto/loa/acl
sudo systemctl reload mosquitto
```

On the laptop the ACL is `.local/mqtt-acl`; restart your Mosquitto process after editing it. The block should read:

```text
user loa-backend
topic read loa/+/noise
topic read $SYS/#
```

Mosquitto publishes `$SYS` every 10 seconds by default (`sys_interval`). Other ways to watch the broker on the Pi: `sudo journalctl -u mosquitto -f` (connects, disconnects, keepalive timeouts) and `mosquitto_sub -h localhost -u loa-backend -P '<password>' -t 'loa/+/noise' -v` (raw messages).

## Charts

```sh
# Traffic over time per port (the same idea as Wireshark's I/O graph)
.venv/bin/python -m experiments.plot traffic experiments/captures/exp2-10hz.pcap --out experiments/results/exp2-10hz-traffic.png

# Per-message RTT (from experiments.load --output) or one-way latency (from experiments.analyze --csv)
.venv/bin/python -m experiments.plot timeline experiments/results/exp1-mqtt.csv experiments/results/exp1-http.csv \
  --out experiments/results/exp1-rtt.png

# Bar charts across runs. Each file is saved JSON from load, analyze or pcap_summary; mix freely
.venv/bin/python -m experiments.plot compare 1Hz=experiments/results/exp2-1hz-pcap.json \
  5Hz=experiments/results/exp2-5hz-pcap.json 10Hz=experiments/results/exp2-10hz-pcap.json \
  --out experiments/results/exp2-bandwidth.png
```

`compare` draws one bar chart per metric found in the files: wire bytes and packets per reading, throughput and header overhead (capture summaries); delivery ratio, achieved rate, jitter and latency (analyze); acknowledged ratio, RTT and payload size (load). If a capture summary has several ports, it uses the busiest unless you pass `--port`.

**HTTPS traffic.** The Pi's HTTPS dashboard address goes through Tailscale, so its traffic is encrypted (TLS inside WireGuard). Captures and charts can show its packet sizes and timing, but not the requests. The experiments use plain HTTP on port 8000, which Wireshark can decode in full.

## Before you start

**Captures contain secrets.** MQTT CONNECT carries the device token in plain text and HTTP readings carry it in the `Authorization` header. Save captures under `experiments/captures/` (git-ignored) and do not share them. Rotate the device token after the experiments if you share any capture.

**Where to capture.** Capture on the Pi (the server) so every reading's traffic passes the capture point:

```sh
sudo tcpdump -i wlan0 -w experiments/captures/<name>.pcap 'host <SENSOR-IP> and (tcp port 1883 or tcp port 8000)'
```

The `host` filter matters. Without it, an MQTT capture on the broker host also counts the broker→backend delivery (the same reading a second time). For a capture on a Mac over loopback, use `-i lo0` (no `sudo` needed if your user has BPF access, which the Wireshark installer grants). Wireshark is good for inspecting captures; `experiments.pcap_summary` needs classic pcap, so in Wireshark use **File › Save As › pcap**.

Useful Wireshark display filters: `mqtt`, `mqtt.msgtype == 3` (PUBLISH), `mqtt.msgtype == 4` (PUBACK), `http.request.method == "POST"`, `tcp.analysis.retransmission`, `tcp.flags.syn == 1`, `tcp.analysis.ack_rtt`.

**Clocks, for one-way latency.** `analyze` reports latency as backend receive time minus the sensor's send timestamp. That is only accurate when both clocks are synchronized:

- The firmware syncs with NTP (`pool.ntp.org` by default, or `-DLOA_NTP_SERVER='"192.168.x.x"'` for a LAN server).
- On the Pi, check `timedatectl` reports `System clock synchronized: yes`.
- Residual clock offset is typically a few milliseconds on a LAN; report it as uncertainty. A negative latency means the clocks disagree and `analyze` warns.
- Round-trip times from `experiments.load` (HTTP request → 201, MQTT PUBLISH → PUBACK) need no clock sync. Prefer them when comparing protocols.

**Isolate runs.** Every sensor boot and every load-generator run has its own `boot_id`. The load generator prints it; `analyze --boot-id <id>` reports only that run. Turn the ESP32 off while the load generator uses the same device credentials: two senders with the same device ID but different boot IDs make the alert logic reset continuity on every reading.

**Building firmware variants.** Defaults are 1 Hz over MQTT. With Arduino CLI:

```sh
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 \
  --build-property "build.extra_flags=-DLOA_PUBLISH_HZ=10 -DLOA_TRANSPORT_HTTP=1" \
  --build-path "$PWD/.local/arduino-build-exp" firmware/loa_sensor
```

In the Arduino IDE, change the `#define` defaults near the top of `loa_sensor.ino` instead. HTTP mode posts to `MQTT_HOST` on port 8000 (`LOA_HTTP_PORT`) using the same device token. It uses HTTP/1.1 keep-alive, so normally one TCP connection carries all the requests. Serial printing of each payload is turned off above 1 Hz to keep the loop fast.

## Experiment 1 — MQTT vs. HTTP

**Question.** For the same sensor payload, how do bytes on the wire, packets per reading, and delivery time differ between MQTT and HTTP?

1. Start a capture on the Pi (see above), named `exp1-mqtt.pcap`.
2. Run the sensor at 1 Hz over MQTT for 5 minutes, or use the load generator from your laptop:
   ```sh
   .venv/bin/python -m experiments.load --credentials .local/loa-001.json --transport mqtt \
     --rate 1 --duration 300 --mqtt-host <PI-IP> --output experiments/results/exp1-mqtt.csv
   ```
3. Stop the capture. Run `analyze --boot-id <id>` to get the number of readings delivered, then:
   ```sh
   .venv/bin/python -m experiments.pcap_summary experiments/captures/exp1-mqtt.pcap --messages <delivered>
   ```
4. Repeat with HTTP: `--transport http --http-url http://<PI-IP>:8000`, or the firmware built with `LOA_TRANSPORT_HTTP=1`. Optionally repeat HTTP with `--http-new-connection` to show the cost of a TCP handshake per reading.

**Record.** `frame_bytes_per_message`, `packets_per_message`, `header_overhead_ratio`, `new_connections`, RTT p50/p95, and delivery ratio for each transport.

**What to expect.** MQTT keeps one TCP connection open and adds only a few bytes of fixed header per PUBLISH. HTTP repeats a request line and headers (including the token) for every reading, and gets a full response back. A new connection per request adds SYN/SYN-ACK/ACK and FIN exchanges for every reading.

## Experiment 2 — Transmission frequency and bandwidth

**Question.** How do traffic volume and delivery hold up at 1, 5 and 10 readings per second?

1. For each rate, capture for 2 minutes while sending at that rate: firmware with `-DLOA_PUBLISH_HZ=<rate>`, or `experiments.load --rate <rate> --duration 120`.
2. For each run, record `analyze` (arrival rate, inter-arrival stdev = jitter, delivery ratio) and `pcap_summary` (throughput_bps, frame_bytes_per_message).

**What to expect.** Bytes per reading stay roughly constant, so throughput grows linearly with rate. Watch whether `arrival_rate_hz` matches the target. On the ESP32 at 10 Hz each window is 100 ms of audio, so any blocking network call (especially HTTP) can make the achieved rate fall below target. `pcap_summary`'s throughput is averaged over the whole capture, so start and stop the capture close to the run.

## Experiment 3 — Wireless disconnection and recovery

**Question.** How does each layer detect a failure, and how long does recovery take?

1. Run the ESP32 at 1 Hz over MQTT with the serial monitor open (115200 baud), a capture on the Pi, and the dashboard open.
2. Note the time, then interrupt the link for about 30 seconds: switch the Wi-Fi access point off or move the sensor out of range. Repeat with a broker stop (`sudo systemctl stop mosquitto`, then start) to compare a Wi-Fi outage with a server-side outage.
3. After recovery, run `analyze --device loa-001 --minutes 10 --gap 2`.

**Record, per outage.**

| Layer | Where to read it |
| --- | --- |
| Sensor Wi-Fi down/up, outage length, RSSI | Serial: `LINK wifi_down`, `LINK wifi_up outage_ms=… rssi=…` |
| Sensor MQTT reconnect | Serial: `LINK mqtt_down`, `LINK mqtt_up outage_ms=…` |
| Broker detects the dead client | Mosquitto log: `Client loa-001 has exceeded timeout` (keepalive 15 s × 1.5) |
| Backend marks the device offline | Dashboard after 10 s without readings; the **Network** tab turns the sensor amber after 3 s and red after 10 s |
| Outage summary on the website | **Network** tab → Connection events: "Sensor reconnected · Silent for N s, M reading(s) lost" |
| Readings lost, recovery time | `analyze` → `outages[].readings_lost`, `recovery_s` |
| TCP behaviour | Wireshark: retransmissions before the drop, new SYN on reconnect |

**What to expect.** The firmware keeps sampling and numbering readings while offline but has no replay buffer, so every reading during the outage is lost (QoS 0) and shows up as a sequence gap. Retry intervals are 5 seconds, so recovery takes up to about 5 s after Wi-Fi returns, plus the MQTT CONNECT.

## Experiment 4 — MQTT QoS 0 vs. QoS 1

The proposal mentions four experiments but describes three. This fourth covers acknowledgments at the application layer, on top of TCP's own.

The ESP32's PubSubClient can only publish at QoS 0, so this uses the load generator:

```sh
.venv/bin/python -m experiments.load --credentials .local/loa-001.json --transport mqtt --qos 0 --rate 5 --duration 120 --mqtt-host <PI-IP>
.venv/bin/python -m experiments.load --credentials .local/loa-001.json --transport mqtt --qos 1 --rate 5 --duration 120 --mqtt-host <PI-IP>
```

**Record.** Packets per reading (QoS 1 adds a PUBACK per PUBLISH), PUBLISH→PUBACK RTT for QoS 1, and delivery ratio. For QoS 0 the load generator's "RTT" is only the socket write time, not a delivery confirmation. Optionally repeat both while the laptop is on weak Wi-Fi to see whether QoS 1 recovers anything TCP alone would not (it only matters across reconnects, because TCP already retransmits within a connection).

## Limitations to state in the report

- dBFS readings are relative and not calibrated. The experiments measure the network, not sound.
- A load generator run from a laptop measures the laptop's network stack, not the ESP32's. Use the firmware variants for the device-side results and the load generator for controlled comparisons and QoS 1.
- One-way latency is limited by NTP accuracy. RTT values are clock-independent.
- Alert duration uses server receive time. At 5–10 Hz alerts still work, but each reading also writes to SQLite, so record the Pi's CPU (`top`) if the achieved rate drops.
