# LOA — Listen, Observe, Alert

Personalized IoT noise monitoring for a XIAO ESP32-S3 Sense, a laptop/Raspberry Pi, and a phone browser. FastAPI + SQLite + React, Supabase email/password accounts, MQTT sensor ingestion, and private WebSocket updates.

> **Hardware note:** The project proposal names an Arduino UNO Q as the sensor node. This implementation uses a Seeed XIAO ESP32-S3 Sense instead, programmed with the Arduino framework (Arduino IDE / `arduino-cli` with the esp32 board package). It fills the same role, a Wi-Fi MQTT data producer for the Raspberry Pi 5. Its onboard microphone and Wi-Fi in one module avoid the UNO Q's external microphone wiring and its split between a Linux processor and a microcontroller. That keeps the project focused on the networking experiments, and the networking concepts and experiments are unchanged.

```text
PDM microphone → ESP32-S3 → Wi-Fi/MQTT → Mosquitto → FastAPI/SQLite → phone dashboard
                                                      ↑
                                             Supabase Auth identity
```

**All sound values are relative dBFS, not calibrated SPL or dBA.** Louder signals are closer to 0. There is no construction-sound classifier or background push notification service. Browser microphone capture is available after explicit permission. An open dashboard shows recorded exceedances. No raw audio is transmitted or stored.

## Run locally (macOS/Linux)

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cd frontend
npm install
npm run build
cd ..
cp .env.example .env  # Only for a new checkout; preserve an existing .env.
```

Set `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` in `.env`. Use a publishable/anon key, **never** a secret/service-role key. Enable email/password authentication in Supabase. Set the Auth Site URL and allowed redirect URLs for your development origin (for example `http://localhost:8000` and your phone-accessible LAN origin) so confirmation links return to the dashboard. Accounts require email confirmation if enabled in your Supabase project.

```sh
.venv/bin/python -m uvicorn backend.main:app --env-file .env --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`, create an account, confirm the email, and sign in. On iPhone, use `http://<computer-LAN-IP>:8000` on the same Wi-Fi. Keep the computer awake. A paired ESP32 needs no browser microphone permission. To use the phone itself as a sensor, open the dashboard over HTTPS and grant microphone permission. Use one Uvicorn worker; socket clients and broker status are process-local.

For frontend development, run `npm run dev` in `frontend` with the backend running. Vite proxies `/api` and `/ws`. This is an existing local IoT app, not a Sites-hosted deployment. Internet access is needed for Supabase login/session verification; the data stays in SQLite on this backend host.

## Connect the hardware

See [docs/HARDWARE.md](docs/HARDWARE.md) for provisioning, broker configuration, firmware compilation/flashing, and end-to-end checks.

1. Provision the device on the backend host, generating a one-time pairing code and device token.
2. Configure Mosquitto authentication and an ACL restricting each sensor to `loa/<device_id>/noise`.
3. Configure and flash [firmware/loa_sensor/loa_sensor.ino](firmware/loa_sensor/loa_sensor.ino).
4. Start Mosquitto and FastAPI with `LOA_SIMULATOR=false`.
5. Sign in and enter the device ID + pairing code under **Pair your microphone**.
6. Save your tolerance, duration, and minimum alert interval.

Pairing consumes a high-entropy, one-time code; knowing a device ID alone cannot claim it. A device has one owner. Readings before pairing are hidden from the account. Each user's preferences apply to all their paired microphones. The server checks Supabase identity for API calls and WebSocket sessions; HTTP sensor ingestion uses a separate device credential.

## Browser microphones and Vercel

Click **Allow microphone & start** after signing in. Grant permission, then use the input selector to choose a browser-exposed built-in, USB, or headset microphone (stop before switching). Browser inputs are registered directly to the signed-in account; ESP32 sensors still use one-time pairing. This captures ambient microphone input, not headphone playback or system volume. Only numeric levels leave the browser. Stop releases the microphone; hiding the page also stops monitoring. Background/locked-screen monitoring is not supported. Actual microphone routing depends on the browser and operating system and requires physical testing on iOS, Android, Windows, and macOS.

Negative dBFS is normal: 0 is the maximum digital signal. Values closer to 0 are louder. The dashboard describes readings relative to your chosen tolerance. Environmental dB SPL needs a reference measurement and calibration for each microphone; dBA additionally requires frequency weighting. No arbitrary offset or uncalibrated environmental dB is displayed. Browser levels use short Web Audio snapshots about once per second and can differ from the ESP32's full-second RMS; compare each microphone against its own tolerance.

For a Vercel frontend, import this repository with **Root Directory = frontend**, Vite preset, `npm run build`, and output `dist`. Set `VITE_BACKEND_URL=https://your-backend.example` before building. Keep FastAPI, persistent SQLite storage, and MQTT on a persistent backend host with HTTPS/WSS. Set `LOA_WEB_ORIGINS=https://your-project.vercel.app` on that backend and restart it. Add the Vercel URL to Supabase's Site URL/allowed redirect configuration. A deployed frontend cannot reach `localhost` on your laptop; the backend must be reachable from the client. This repository has been prepared for that split but has not been deployed.

Browser capture works on HTTPS or localhost; plain HTTP LAN URLs on a phone do not qualify. Do not put broker credentials or device tokens into Vercel frontend variables.

## Alert behavior

Default: threshold `-40 dBFS`, duration `3` seconds, cooldown `30` seconds.

- A reading at or above the threshold starts a possible exceedance.
- An event is recorded once subsequent measurements establish the configured duration above threshold. At 1 Hz, a 3-second rule needs samples spanning 3 seconds, usually four reports.
- Sustained loudness updates the event's peak rather than generating repeated alerts.
- A below-threshold sample ends the event. A later event must also satisfy the cooldown since the last trigger.
- A reporting gap over 2.5 seconds or a changed boot ID breaks continuity. A gap ends the event at its last observed sample; the app never assumes the gap was loud or quiet.
- Saving preferences closes current events and restarts their duration checks.
- Devices show offline after 10 seconds. The current value clears when offline; historical readings remain.

Server receive time drives alert duration; device uptime is diagnostic, not Unix time. QoS/retry duplicates with the same device, boot ID, and sequence are ignored. Readings without boot IDs/sequences cannot be deduplicated reliably. There is no offline replay buffer; only live measurements should be ingested for duration evaluation.

## API

Except `/api/config` and device ingestion, APIs require `Authorization: Bearer <Supabase access token>`. Demo mode is the explicitly unprotected exception.

| Endpoint | Behavior |
| --- | --- |
| `GET /api/config` | Public auth configuration and measurement unit; only the public Supabase key |
| `GET /api/status` | Own devices, online status, actual broker connection status |
| `POST /api/devices/claim` | Pair an unclaimed device using its one-time code |
| `GET /api/readings?minutes=60&limit=300` | Own paired devices' history, oldest first; maximum 10,000 rows |
| `POST /api/readings` | Device-token-authenticated numeric measurement ingestion |
| `GET/PUT /api/settings` | Own threshold, duration, and cooldown |
| `GET /api/alerts` | Own latest 50 events, newest first |
| `GET /api/network` | Network tab: own sensors' rate, delivery, latency, outages; backend↔broker link; Mosquitto `$SYS` load |
| `/ws` | Send `{"type":"auth","token":"<access token>"}` as first frame; then private readings/settings updates. `{"type":"ping","id":n}` is answered with `{"type":"pong","id":n}` |

Canonical sensor payload, MQTT topic `loa/loa-001/noise`:

```json
{"device_id":"loa-001","boot_id":"ac65e84013ad8270","sequence":42,"dbfs":-38.7,"rms":380.0,"uptime_ms":42000}
```

`timestamp` is optional Unix seconds from a synchronized clock. The firmware sends it once NTP has set its clock; it is stored for latency measurement only and never drives alert timing. MQTT topic and payload device ID must match; the broker ACL enforces publisher identity. Firmware publishes QoS 0 using PubSubClient; the backend subscribes at QoS 1, which does not upgrade publisher QoS. No retained sensor messages.

CSV export and charts cover the selected microphone/time range, subject to the 10,000-reading cap. The average card is the arithmetic mean of reported dBFS values, not an acoustic Leq measurement.

## Isolated simulator

```sh
LOA_AUTH_MODE=demo LOA_SIMULATOR=true LOA_DB=demo.sqlite3 MQTT_HOST='' \
  .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

This is explicitly a shared local demo with no login protection. Real mode defaults to `supabase`, with simulation disabled; missing configuration fails closed. Simulation is not allowed in Supabase mode.

## Verify

```sh
.venv/bin/python -m pytest -q
cd frontend
npm run build
```

Tests cover account isolation, one-time pairing, device credentials, private WebSockets, validation, duration/cooldown/peak tracking, duplicates, sensor gaps/reboots, persistence, and mocked Supabase token verification. Physical Wi-Fi/microphone behavior, real email delivery/login, and iPhone rendering require device checks.

## Pi hub button and Raspberry Pi Connect

The **Pi hub** button in the dashboard header shows whether the site can reach your Raspberry Pi hub.

- **Cloud site (Vercel):** the button opens a panel where you can enter the hub address. Before saving, the site checks that the address is an LOA hub on the same Supabase project and that it accepts your sign-in. The address is saved in this browser only and replaces the site's built-in `VITE_BACKEND_URL` until you choose **Use the site's built-in address**.
  - Use the Pi's **HTTPS Tailscale address** (for example `https://raspberrypi.your-tailnet.ts.net`), and keep the phone or laptop signed in to Tailscale. Browsers block `http://` LAN addresses from an HTTPS page.
  - Add the site's origin to `LOA_WEB_ORIGINS` on the hub and restart it.
  - **Retry now** skips the 30-second wait between connection attempts.
- **Dashboard served by the Pi:** the panel shows the live connection status only.

**Raspberry Pi Connect** (linked from the panel) gives remote screen sharing and a remote shell for the Pi from any network. It is useful for maintenance: restarting `loa`, reading `journalctl`, or opening `http://localhost:8000` in the Pi's own browser over screen sharing. It does not forward the Pi's web ports, so the dashboard's data connection cannot run through it. Use Tailscale (or the LAN) for that.

## Network tab: showing connectivity

The dashboard's **Network** tab shows the whole path a reading takes, refreshed every 2 seconds: **sensor → (Wi-Fi, MQTT) → Mosquitto broker → backend → (WebSocket) → this browser**. Each hop is green (working), amber (sensor silent for more than 3 s), red (down) or grey (not used; for example, the broker is bypassed when a sensor sends over HTTP).

- **Sensor:** readings per second over the last 10 s, and seconds since the last reading.
- **Delivery over the last 5 minutes:** received vs. expected, from gaps in sequence numbers.
- **One-way latency (p50/p95):** needs the sensor's NTP send time, so the updated firmware.
- **Broker:** connected clients, messages and bytes per minute from Mosquitto's `$SYS` statistics. This needs `topic read $SYS/#` for `loa-backend` in the broker ACL; see [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md).
- **Backend ↔ broker link:** status and connect time.
- **Browser ↔ Pi:** WebSocket round-trip time from a ping every 2 s.
- **Charts and events:** a readings-per-second chart for the last 2 minutes, and an event list of outages (with readings lost), sensor restarts, transport changes and broker disconnects from the last 15 minutes.

Per-sensor figures are computed from stored readings, so they cover only the signed-in account's own sensors and survive backend restarts. Broker and link figures are kept in memory by the single backend worker.

**Live demo of connectivity:** open the Network tab, then switch off the Wi-Fi access point (or carry the sensor out of range). The sensor turns amber, then red; the chart drops to zero. Restore Wi-Fi and a "Sensor reconnected" event appears with the outage length and how many readings were lost.

## Networking experiments

[docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) covers the proposal's experiments: MQTT vs. HTTP, 1/5/10 Hz send rates, Wi-Fi disconnection and recovery, and MQTT QoS 0 vs. 1. The firmware takes `LOA_PUBLISH_HZ` and `LOA_TRANSPORT_HTTP` build flags. `experiments/` contains a synthetic sensor (`python -m experiments.load`), a delivery/latency/outage analyzer for the SQLite data (`python -m experiments.analyze`), a standard-library tcpdump summarizer (`python -m experiments.pcap_summary`), a live broker monitor that also runs on the Pi (`python -m experiments.monitor`), and PNG charts (`python -m experiments.plot`, needs `requirements-experiments.txt`). Record results in [docs/EXPERIMENT_RESULTS.md](docs/EXPERIMENT_RESULTS.md). Captures contain device tokens and are git-ignored under `experiments/captures/`.

## Data and deployment notes

SQLite is the application data store; Supabase provides authentication and, when `LOA_REPORT_STORE=supabase`, Postgres storage for incident reports. Back up `LOA_DB`; there is no retention policy or cloud preference synchronization across separate backend installations. Existing legacy `readings/settings/alerts` tables are preserved but not displayed, because their positive, uncalibrated values cannot be converted truthfully to dBFS. New data uses `sound_readings`, `preferences`, `devices`, `noise_events`, and `alert_state`.

The supplied LAN setup uses HTTP and unencrypted MQTT with authentication. Keep it on your trusted development network. Internet deployment needs HTTPS, MQTT TLS, and an appropriate backend host; a static frontend deployment alone cannot run the SQLite/MQTT backend. MQTT TLS is supported on the backend with `MQTT_TLS=true` and optional `MQTT_CA_FILE`; the provided firmware is configured for local plaintext MQTT.

## Raspberry Pi main hub

The sensor node is the ESP32-S3 (see the hardware note at the top); the Arduino UNO Q is not used. See [docs/PI_HUB.md](docs/PI_HUB.md) for the private migration bundle, Pi installation, automatic startup, firmware cutover, and acceptance checks. Prepare the bundle with `.venv/bin/python scripts/prepare_pi.py` after building the frontend for same-origin hosting. Migration has not completed until the Pi is installed and receiving live sensor readings.

## Noise disturbance reports

Use the **Report disturbance** button or navigation tab. LOA freezes the incident time when the confirmation opens and asks whether you want to report it. **No** returns to Overview; **Yes** opens a form for location, sound description, one feeling (Tolerable / Need to Cope / Sensitive), and optional additional information. Location, description, and feeling are required. The incident time is shown in the browser's local time and stored as a Unix timestamp.

Reports do not require a microphone or an automatic threshold exceedance. They are saved to the signed-in account in Supabase Postgres when cloud reports are enabled (otherwise in the Pi's SQLite database) and displayed as the latest 50 incidents on the Reports screen. `GET /api/reports` and `POST /api/reports` require account authentication; retrying a submission with the same request ID does not create another report. Reports are personal records and are not forwarded to a landlord, authority, or other recipient. Explicit local demo mode shares its reports like its other demo data.

See [docs/SUPABASE_REPORTS.md](docs/SUPABASE_REPORTS.md) for cloud report setup, migration of existing incidents, privacy policies, and the developer Table Editor/SQL view.

## Cloud-first frontend and optional Pi

With `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` set, the frontend starts and uses reports/preferences directly through Supabase without the Pi. Browser microphone monitoring runs in the page session. The Pi remains optional for cloud features and required for ESP32 pairing, live data, and stored sensor history. See [docs/CLOUD_FIRST.md](docs/CLOUD_FIRST.md) for configuration, preferences policies, and verification limits.
