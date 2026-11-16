# ESP32-S3 integration

## Target and dependencies

XIAO ESP32-S3 **Sense**, with the Sense microphone board attached, using PDM clock GPIO 42 and data GPIO 41. A generic ESP32-S3 without a microphone is not equivalent. Install ESP32 Arduino core 3.x and PubSubClient. The sketch captures 16-bit mono audio at 16 kHz and calculates DC-corrected RMS over 16,000 samples for each report.

Each payload contains relative dBFS, RMS, boot ID, sequence, and uptime. No audio leaves the device. The backend evaluates the user's preferences, so changing tolerance does not require reflashing. The sketch removes the old fixed local `-40 dBFS` alert rule.

## Provision and configure

From the repository root with Python dependencies installed and `.env` configured:

```sh
.venv/bin/python scripts/provision_device.py loa-001 \
  --label 'XIAO ESP32-S3 Sense' --output .local/loa-001.json
```

The private JSON contains `device_id`, `claim_code`, and `device_token`. Enter the pairing code in the signed-in dashboard, not in the firmware. The device token is used for MQTT login and HTTP experiments. Both the JSON and `firmware/**/secrets.h` are git-ignored. The server stores only hashes. Do not commit credentials.

Install Mosquitto (macOS: `brew install mosquitto`; Raspberry Pi: install the distribution's Mosquitto package).

```sh
.venv/bin/python scripts/configure_local.py \
  --wifi-from /absolute/path/to/your/original/secrets.h
```

This reuses only `WIFI_SSID` and `WIFI_PASSWORD`, generates authenticated broker configuration in `.local/`, updates backend MQTT variables in `.env`, and creates the firmware's ignored `secrets.h`. It detects the laptop's outbound LAN address; use `--host <LAN-IP>` if multiple interfaces/VPNs make that incorrect. It supports string constants or `#define` Wi-Fi credentials. Use a 2.4 GHz network accessible by both the board and laptop.

To refresh the firmware's IP/Wi-Fi configuration without replacing broker credentials:

```sh
.venv/bin/python scripts/configure_local.py --firmware-only \
  --wifi-from /absolute/path/to/your/original/secrets.h --host <LAN-IP>
```

For manual setup, copy `firmware/loa_sensor/secrets.example.h` to `secrets.h` and fill in the fields. MQTT username must equal device ID. The broker must enforce that this username can publish **only** to `loa/<device_id>/noise`; only the backend account should read `loa/+/noise`. The backend checks topic/payload agreement but relies on the broker for publisher authentication.

The local helper initially configures one device. Adding devices requires provisioning each ID, adding a hashed password to the existing broker password file, and extending its ACL; it deliberately refuses to overwrite an existing password file. Back up the database and configuration before credential rotation. If the database is removed, provisioning must be repeated and firmware credentials updated.

## Start broker and backend

```sh
# Terminal 1 (macOS Homebrew; use your installed path elsewhere)
/opt/homebrew/sbin/mosquitto -c .local/mosquitto.conf

# Terminal 2
.venv/bin/python -m uvicorn backend.main:app --env-file .env --host 0.0.0.0 --port 8000
```

Do not start a second broker if port 1883 is already in use. The helper does not enable a system service. Firewall/router client isolation can block board-to-laptop traffic; allow TCP 1883 and 8000 only on the development LAN. After changing `.env`, restart FastAPI.

## Compile and flash

In Arduino IDE, open `firmware/loa_sensor/loa_sensor.ino`, choose **XIAO_ESP32S3**, choose the connected USB port, and upload. Enable USB CDC on boot if serial output is needed; leave the board's other defaults unless your hardware requires otherwise.

With Arduino CLI:

```sh
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 \
  --build-path "$PWD/.local/arduino-build" firmware/loa_sensor
arduino-cli upload --fqbn esp32:esp32:XIAO_ESP32S3 --port /dev/cu.usbmodemXXXX \
  --input-dir "$PWD/.local/arduino-build" firmware/loa_sensor
```

USB port names can change after flashing. The CLI bundled with Arduino IDE on macOS is `/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli`; pass `--config-file ~/.arduinoIDE/arduino-cli.yaml` if needed to use IDE-installed cores/libraries. The build needs adequate free disk space, typically allow at least 2 GB. Serial baud is 115200.

## Acceptance checks

1. Sign in, pair `loa-001` with the one-time code, and see **Sensor online** plus **MQTT connected**.
2. Observe that louder sounds move dBFS closer to zero. A flat minimum suggests microphone/cabling configuration problems. These are not dBA readings.
3. Set tolerance near the current reading and duration to 3 seconds. Sustain a louder sound for several seconds: one event appears with a peak. Return to quieter sound: event ends.
4. Verify brief spikes do not produce a sustained event. Set a cooldown and verify a second short noisy episode cannot immediately retrigger.
5. Stop the broker or disconnect the sensor's Wi-Fi. The device becomes offline after 10 seconds; event continuity breaks after 2.5 seconds. Restore connectivity and observe recovery.
6. Sign into a different account: it must not see the first account's microphone, readings, alerts, or preferences, and cannot claim an already-paired device.
7. Open the dashboard on an iPhone on the same Wi-Fi. Confirm live readings, settings, and alerts. No phone microphone access is requested.

Network reconnect attempts can briefly delay sampling. There is no offline queue; sequence gaps are expected. The backend uses receive-time continuity, so delayed/batched historic measurements are not suitable for live event evaluation. This is a demonstration monitor, not a calibrated sound level instrument.
