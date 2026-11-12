# LOA — Listen, Observe, Alert

Raspberry Pi–ready noise monitoring MVP. FastAPI + SQLite, React + Vite, WebSockets, optional MQTT subscriber. The simulator runs by default; no hardware is required. Sound values are **uncalibrated estimates**, not validated sound pressure measurements.

## Run locally (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
cd frontend
npm install
npm run build
cd ..
.venv\Scripts\python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000. On your phone, use `http://<computer-LAN-IP>:8000` on the same Wi-Fi (allow the port through your firewall). API docs: http://localhost:8000/docs. Build the frontend **before** starting the backend. For frontend development run `npm run dev` in `frontend` alongside the backend; Vite proxies API and WebSocket requests.

On Raspberry Pi/Linux use `python3 -m venv .venv`, `.venv/bin/python` and the same npm commands. Use one Uvicorn worker: device status and WebSocket clients are process-local.

## Connect the Arduino later

Install/run Mosquitto on the Raspberry Pi separately; the application is a broker client. Configure broker authentication and a LAN listener as needed. Set environment variables before starting Uvicorn:

```powershell
$env:LOA_SIMULATOR = 'false'
$env:MQTT_HOST = '127.0.0.1'
$env:MQTT_PORT = '1883'
# Optional: MQTT_USERNAME and MQTT_PASSWORD
```

Publish JSON on `loa/arduino-01/noise` (subscription: `loa/+/noise`, QoS 1):

```json
{"device_id":"arduino-01","value":58.2,"timestamp":1794500000.25,"sequence":42}
```

`timestamp` is optional Unix seconds; `sequence` is optional. The server records its own receive time and transport (`mqtt`, `http`, `simulator`). Send the same JSON to `POST /api/readings` for the HTTP experiment. Synchronize clocks before estimating one-way latency. QoS 1 duplicates are retained, so use device and sequence to identify duplicates in analysis. Hardware firmware, calibration, packet capture analysis, and round-trip probes are future work.

## Behavior and API

- `GET /api/readings?minutes=60&limit=300`: time-filtered history, oldest first, capped at 10,000 rows.
- `GET/PUT /api/settings`: persisted threshold (20–140).
- `GET /api/alerts`: latest 50 threshold crossings, newest first.
- `GET /api/status`: simulator flag and devices seen this process; offline after 10 seconds.
- `/ws`: new readings and threshold changes. Dashboard reconnects automatically.
- CSV export exports readings in the selected range, subject to the 10,000-row history cap.

Alerts trigger on a crossing, not each consecutive high sample. Alerts and readings persist in `loa.sqlite3` (override with `LOA_DB`). No retention policy yet. History and metrics include all sources, so disable simulation before real experiments. Threshold edits apply to subsequent readings; existing alerts remain historical. The UI polls device status and alerts every five seconds.

This MVP has no authentication or TLS. Run on your trusted development LAN. It is not configured for public internet hosting. MQTT broker reachability and browser rendering need validation on the target network.

## Verify

```powershell
.venv\Scripts\python -m pytest -q
cd frontend
npm run build
```
