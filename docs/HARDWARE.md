# ESP32-S3 integration

## Target and dependencies

XIAO ESP32-S3 **Sense**, with the Sense microphone board attached, using PDM clock GPIO 42 and data GPIO 41. A generic ESP32-S3 without a microphone is not equivalent. Install ESP32 Arduino core 3.x and PubSubClient. The sketch captures 16-bit mono audio at 16 kHz and calculates DC-corrected RMS over 16,000 samples for each report (one second). The `LOA_PUBLISH_HZ` and `LOA_TRANSPORT_HTTP` build flags change the report rate and transport for the networking experiments; see [EXPERIMENTS.md](EXPERIMENTS.md).

Each payload contains relative dBFS, RMS, boot ID, sequence, uptime, a local `alert` flag, and an NTP send `timestamp` once the clock is set. No audio leaves the device.

There are two alert layers:

- **Dashboard alerts (main):** the backend applies each user's own tolerance, duration and cooldown, so changing them never requires reflashing.
- **On-device alert (fallback):** a fixed rule, by default `-40 dBFS` for 3 seconds, set with `LOA_LOCAL_ALERT_DBFS` and `LOA_LOCAL_ALERT_SECONDS`. It lights the onboard LED and sets `"alert":true` in readings, and it keeps working with no network. The backend stores readings without this flag; it does not create dashboard events. The LED blinks once at boot. If it stays lit instead, build with `-DLOA_LED_ACTIVE_LOW=0`.

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

## Wi-Fi: home, phone hotspot and school (WPA2-Enterprise)

The ESP32-S3 joins **2.4 GHz** networks only. It supports WPA2-Personal (home routers, phone hotspots) and WPA2-Enterprise with **PEAP** or **TTLS** (typical school networks where you log in with a username and password). It does not support captive-portal guest networks, where you accept terms in a browser.

### Several networks with automatic switching

`secrets.h` can list several networks instead of one. See **Option B** in `firmware/loa_sensor/secrets.example.h`. The sensor tries them in order and moves to the next one:

- after **20 s with no Wi-Fi** (network missing, wrong password, or out of range);
- after **60 s with Wi-Fi but no way to reach the Pi** (no MQTT connection or HTTP reply). This is the usual symptom of campus Wi-Fi that blocks devices from talking to each other.

Each entry has its own `mqtt_host`, the Pi's address *as seen from that network*. Use the Pi's mDNS name, for example `raspberrypi5hoang.local` (`hostname` on the Pi plus `.local`), so the entry keeps working when the Pi gets a new IP. Use an IP where mDNS is blocked. With one network (Option A, what `scripts/configure_local.py` writes), nothing switches.

Serial output shows what happens:

```text
LOA: Connecting to Wi-Fi "SCHOOL_WIFI" (WPA2-Enterprise, 3 of 3), Pi at raspberrypi5hoang.local...
LINK wifi_up uptime_ms=8200 outage_ms=0 rssi=-61 ssid="SCHOOL_WIFI" ip=10.20.31.44
LINK wifi_switch reason=pi_unreachable from="SCHOOL_WIFI" to="HOME_WIFI"
```

The sensor also registers its device ID (for example `loa-001`) as its hostname, so it is easy to find in a router's or hotspot's device list.

### Most reliable for class demos: a phone hotspot

School Wi-Fi often isolates devices. Even when the ESP32 and the Pi are both connected, they cannot reach each other. A phone hotspot avoids that and still gives the Pi internet for sign-in (Supabase) and Tailscale.

1. On the phone, turn on the hotspot. On iPhone, also turn on **Maximize Compatibility**, which keeps it on 2.4 GHz for the ESP32.
2. Put the Pi on the hotspot. `--ask` prompts for the password, so it stays out of your shell history:
   ```sh
   sudo nmcli --ask device wifi connect "MY_PHONE_HOTSPOT"
   sudo nmcli connection modify "MY_PHONE_HOTSPOT" connection.autoconnect-priority 10
   ```
3. Add the hotspot to the sensor's `NETWORKS` list with `mqtt_host` set to the Pi's `.local` name, then flash.
4. Join the hotspot on your laptop as well, or use the Pi's Tailscale address, to open the dashboard.

On iPhone hotspots addresses are `172.20.10.x` and change between sessions, which is why the `.local` name matters.

### School WPA2-Enterprise

Use the settings from your school's Wi-Fi setup guide:

| Setting | Field in `NETWORKS` | Typical value |
| --- | --- | --- |
| EAP method | `eap_method` | `WPA2_AUTH_PEAP` (with MSCHAPv2 inside), sometimes `WPA2_AUTH_TTLS` |
| Outer / anonymous identity | `eap_identity` | `anonymous@school.edu`, or `nullptr` to send your login |
| Username | `eap_username` | your school login, often `name@school.edu` |
| Password | `password` | your school password |
| CA certificate | `ca_pem` | the certificate your school publishes for Wi-Fi setup |

Check these before relying on it:

- **Policy.** Many schools do not allow personal logins on IoT devices, and some offer a separate device or IoT network (often registered by MAC address, with a normal Wi-Fi password). Ask IT. If one exists, use it as a WPA2-Personal entry instead.
- **Credentials are stored in plain text** on the ESP32's flash. Anyone holding the board can read them. Use an app password or device password if your school offers one. Never commit `secrets.h`, which is git-ignored.
- **Set the CA certificate.** Without it, the ESP32 cannot tell your school's Wi-Fi from a fake hotspot with the same name, which could capture your password. The sensor prints a warning when `ca_pem` is missing.
- **Device isolation.** Test from your laptop on the school Wi-Fi: `ping raspberrypi5hoang.local` and `nc -vz raspberrypi5hoang.local 1883`. If they fail, the ESP32 will not reach the Pi either. Use the hotspot, and the sensor's 60 s rule will switch to it automatically.
- **Time server.** Campus networks may block `pool.ntp.org`. Readings are still sent, but without a timestamp (no latency figures). Build with `-DLOA_NTP_SERVER='"<school or Pi NTP server>"'` if needed.

To put the **Pi** itself on the school network (the same caveats apply):

```sh
sudo nmcli connection add type wifi con-name school ifname wlan0 ssid "SCHOOL_WIFI" \
  wifi-sec.key-mgmt wpa-eap 802-1x.eap peap 802-1x.phase2-auth mschapv2 \
  802-1x.identity "student@school.edu" 802-1x.anonymous-identity "anonymous@school.edu" \
  802-1x.ca-cert /etc/ssl/certs/school-wifi-ca.pem
sudo nmcli --ask connection up school
```

`--ask` prompts for the password. To store it for automatic reconnects, use `sudo nmtui` and edit the `school` connection.

### Alternative: the Pi as its own access point

If the Pi has Ethernet for internet, it can host a private Wi-Fi network just for the sensor. The address is then fixed and no other network can interfere:

```sh
sudo nmcli device wifi hotspot ifname wlan0 ssid LOA-Hub password "choose-a-long-password"
```

Use `mqtt_host = "10.42.0.1"` (the Pi's address on its own hotspot) for that entry. Without Ethernet the Pi would have no internet, so dashboard sign-in would fail; prefer the phone hotspot in that case.

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
