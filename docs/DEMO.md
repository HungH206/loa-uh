# Demo runbook

Setup and live demo of LOA. Pi login: `hunghoang@raspberrypi5hoang.local` (`hunghoang@192.168.68.61` on the home network). Each command says where it runs: **Mac** (in the `loa-uh` folder), **Pi** (SSH or Raspberry Pi Connect's terminal), or **ESP32**.

## A. One-time setup (the day before)

### A1. Mac: build and copy the update to the Pi

```sh
cd frontend && VITE_SUPABASE_URL= VITE_SUPABASE_PUBLISHABLE_KEY= VITE_BACKEND_URL= npm run build && cd ..
.venv/bin/python scripts/prepare_pi.py
ssh hunghoang@raspberrypi5hoang.local 'mkdir -p ~/loa-migration'
scp .local/pi-migration.tar.gz experiments/monitor.py scripts/reset_pairing.py hunghoang@raspberrypi5hoang.local:~/loa-migration/
```

The blank `VITE_…` values make the Pi serve a dashboard that loads its sign-in settings from the Pi and shows live data. For Vercel, build with your normal `.env.local` instead.

### A2. Pi: install and configure

```sh
cd ~/loa-migration && tar -xzf pi-migration.tar.gz && sudo bash deploy/pi/install.sh
hostname                         # must print raspberrypi5hoang (used in the ESP32 secrets.h)
timedatectl                      # "System clock synchronized: yes" (needed for latency)
sudo grep -q 'topic read \$SYS/#' /etc/mosquitto/loa/acl || sudo sed -i '/^topic read loa\/+\/noise$/a topic read $SYS/#' /etc/mosquitto/loa/acl
sudo systemctl reload mosquitto
sudo apt install -y tcpdump
```

The installer keeps the Pi's database, settings and broker passwords. The `$SYS` line lets the Network tab and the monitor show broker statistics.

If you use the Vercel site, add its address to `LOA_WEB_ORIGINS=` in `/etc/loa/loa.env` (`sudo nano /etc/loa/loa.env`), then `sudo systemctl restart loa`.

### A3. Pi: join the phone hotspot

On the iPhone: **Settings › Personal Hotspot**, turn on **Allow Others to Join** and **Maximize Compatibility** (2.4 GHz for the ESP32).

```sh
sudo nmcli --ask device wifi connect "Harry's IPhone"
sudo nmcli connection modify "Harry's IPhone" connection.autoconnect-priority 10
```

The hotspot name must match exactly, including the apostrophe and capitals. Copy it from the Mac's Wi-Fi menu.

### A4. ESP32: upload the firmware

`firmware/loa_sensor/secrets.h` lists home Wi-Fi first, then the hotspot, with the Pi at `raspberrypi5hoang.local` on the hotspot. In Arduino IDE open **`loa-uh/firmware/loa_sensor/loa_sensor.ino`** (not a copy in Downloads), choose **XIAO_ESP32S3** and the port, then **Upload**. Or on the Mac:

```sh
CLI="/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli"
"$CLI" --config-file ~/.arduinoIDE/arduino-cli.yaml compile --fqbn esp32:esp32:XIAO_ESP32S3 --libraries .local/arduino-libraries --build-path "$PWD/.local/arduino-build" firmware/loa_sensor
"$CLI" --config-file ~/.arduinoIDE/arduino-cli.yaml upload --fqbn esp32:esp32:XIAO_ESP32S3 --port /dev/cu.usbmodemXXXX --input-dir "$PWD/.local/arduino-build" firmware/loa_sensor
```

`ls /dev/cu.usbmodem*` shows the port. In the Serial Monitor (115200 baud) expect:

```text
LOA Wearable Starting                    (and the LED blinks once)
LINK wifi_up ... ssid="Harry's IPhone" ip=172.20.10.x
LOA: MQTT connected!
LOA Reading: {... "timestamp":17...}     (timestamp appears once NTP time is set)
MQTT: Published
```

Write down the ESP32's `ip=` for packet captures.

### A5. Dashboard: sign in and pair

Open `http://raspberrypi5hoang.local:8000` with the laptop on the hotspot, or `https://raspberrypi5hoang.taildd88d2.ts.net/` through Tailscale from any network. Sign in.

- If **loa-001** is under **Your microphone**, it is already paired to this account. Skip the next step.
- Otherwise, get a new one-time code on the **Pi** and enter it under **Pair a network sensor**:
  ```sh
  sudo install -m 644 ~/loa-migration/reset_pairing.py /tmp/reset_pairing.py
  sudo -u loa python3 /tmp/reset_pairing.py loa-001 --db /var/lib/loa/loa.sqlite3 --release
  ```

### A6. Prepare fallback material

- The experiment charts (`experiments/results/*.png`) and one saved capture (`experiments/captures/*.pcap`) open in Wireshark.
- A 1–2 minute screen recording of steps B2–B4 working.
- A full rehearsal in the classroom if possible. The room's Wi-Fi is the biggest unknown.

## B. Live demo (about 10 minutes)

**Before you start (5 min):** hotspot on; Pi powered and on the hotspot; ESP32 on a power bank; then run on the **Pi**:

```sh
sudo systemctl status loa mosquitto --no-pager           # both active (running)
sudo /opt/loa/.venv/bin/python ~/loa-migration/monitor.py --env-file /etc/loa/loa.env --log ~/monitor-events.csv
```

In the browser, the **Pi hub** button should be green and the **Network** tab green end to end.

| # | Time | Show | Say |
| --- | --- | --- | --- |
| B1 | 0:30 | **Network** tab: the connection path | The ESP32 publishes over Wi-Fi with MQTT over TCP to Mosquitto on the Pi; FastAPI stores readings in SQLite and pushes them to this browser over a WebSocket. Point out readings/s, delivery %, latency and the browser↔Pi round trip. |
| B2 | 1:00 | **Overview**: talk or clap near the sensor | The level updates within about a second with no refresh (WebSocket push). dBFS is a relative digital level, not calibrated dBA. |
| B3 | 1:30 | **Settings**: tolerance just above the room level, 3 s; make a sustained noise; then **Alerts** | One alert with its peak; a short clap does not trigger (duration rule). The ESP32's LED lights too: the on-device fallback works even offline. |
| B4 | 2:30 | Wrap the ESP32 in foil or a metal tin, wait about 15 s, unwrap | Network tab: sensor amber, then red, chart drops to zero; the monitor shows SILENT. After unwrapping: "Sensor reconnected · silent N s, M readings lost", counted from sequence-number gaps. |
| B5 | 1:00 | Pi: `sudo systemctl stop mosquitto`, then `sudo systemctl start mosquitto` | Backend↔broker turns red, then the ESP32 reconnects within about 5 s and the backend re-subscribes. Server-side failure compared with the wireless failure in B4. |
| B6 | 1:00 | Wireshark on the saved capture: filter `mqtt`, then `http.request.method == "POST"` | TCP handshake, MQTT CONNECT and PUBLISH; an HTTP reading carries far more header bytes. |
| B7 | 2:00 | Experiment charts | MQTT vs HTTP overhead and RTT; 1/5/10 Hz throughput; outage recovery; QoS 0 vs 1. |
| B8 | 0:30 | Wrap-up | Client-server, MQTT/HTTP/WebSocket, TCP reliability, Wi-Fi recovery, measured performance. Limits: dBFS not dBA, latency depends on clock sync, ESP32-S3 used instead of the UNO Q. |

Optional live capture for B6, on the **Pi** (Ctrl+C to stop; replace the IP):

```sh
sudo tcpdump -i wlan0 -w ~/demo.pcap 'host 172.20.10.x and (tcp port 1883 or tcp port 8000)'
```

## C. If something goes wrong

| Problem | What to do |
| --- | --- |
| ESP32 never shows `wifi_up` | Hotspot on with Maximize Compatibility; SSID spelled exactly as in `secrets.h` (curly vs straight apostrophe). |
| `wifi_up` but no `MQTT connected!` | Pi not on the same hotspot, or `hostname` is not `raspberrypi5hoang`. Check with `nc -vz raspberrypi5hoang.local 1883` from the Mac on the hotspot. |
| Dashboard: sensor list empty or pairing refused | Use the account that paired it, or reset the code (A5). |
| Pi hub button red on the Vercel site | Laptop signed in to Tailscale; Vercel origin in `LOA_WEB_ORIGINS`; **Retry now**. |
| Network tab broker card says unavailable | The `$SYS` ACL line (A2) is missing. |
| Latency card empty | ESP32 has no NTP time yet (wait a minute; hotspot needs internet) or old firmware. |
| Nothing works | Mac-only demo with simulated data (after the A1 build): `LOA_AUTH_MODE=demo LOA_SIMULATOR=true LOA_DB=demo.sqlite3 MQTT_HOST='' .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`, open `http://127.0.0.1:8000`; then play the recording and show the charts and capture. |

## D. Afterwards (Mac)

```sh
scp hunghoang@raspberrypi5hoang.local:~/demo.pcap hunghoang@raspberrypi5hoang.local:~/monitor-events.csv experiments/captures/
```

Captures contain the device token in plain text. Keep them in `experiments/captures/` (git-ignored) and do not share them.
