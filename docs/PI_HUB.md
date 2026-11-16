# Raspberry Pi 5 as the LOA hub

Current sensor: XIAO ESP32-S3 Sense. Arduino UNO Q support is deferred.

The Pi runs Mosquitto, FastAPI, SQLite, alerts, and the built React dashboard. The Mac and phone are clients; browser microphones use authenticated HTTP, and the ESP32 uses Wi-Fi/MQTT. A charger can power the ESP32 independently of the Mac. Internet access is still required for Supabase account verification.

## Prepare the Pi

Use Raspberry Pi OS (64-bit), connect the Pi to the same router (Ethernet recommended), and enable SSH. Reserve its address in the router. Obtain the username and hostname/IP, for example `hung@raspberrypi.local`. The installer uses sudo and Debian packages; it is intended for a dedicated LOA Pi. It refuses unrelated Mosquitto conf.d configurations rather than replacing them. Review custom main Mosquitto configuration before using it on an existing broker.

## Prepare and transfer from the Mac

Build the frontend for same-origin hosting (no separate backend URL):

```sh
cd frontend
VITE_BACKEND_URL='' npm run build
cd ..
.venv/bin/python scripts/prepare_pi.py
```

The bundle `.local/pi-migration.tar.gz` contains credentials and a consistent SQLite backup, including committed WAL data. It does not copy the Mac Python environment or regenerate sensor tokens. Treat it as a private backup. It preserves pairings and history at snapshot time; later Mac changes are not synchronized. For final cutover, stop Mac browser capture, stop the Mac backend, and regenerate the snapshot immediately before transfer. The Mac broker can remain up until the ESP32 is reconfigured.

Replace the example address with the real Pi login:

```sh
ssh hung@raspberrypi.local 'umask 077; mkdir -p ~/loa-migration; chmod 700 ~/loa-migration'
scp .local/pi-migration.tar.gz hung@raspberrypi.local:~/loa-migration/
ssh -t hung@raspberrypi.local
```

On the Pi:

```sh
cd ~/loa-migration
umask 077
tar -xzf pi-migration.tar.gz
sudo bash deploy/pi/install.sh
```

Verify the SSH host identity when connecting. Do not paste credentials into chat. A second installation preserves the Pi database, account settings, and broker credentials; it is not a database restore or credential rotation tool.

## What gets installed

- `/opt/loa`: application, prebuilt frontend, and Pi-native Python environment.
- `/etc/loa/loa.env`: private backend configuration, with Pi paths normalized.
- `/var/lib/loa/loa.sqlite3`: live database, owned by the unprivileged `loa` account.
- `/etc/mosquitto/loa`: broker password hashes and topic ACLs readable by Mosquitto.
- `/etc/mosquitto/conf.d/loa.conf`: authenticated LAN listener, TCP 1883.
- `loa.service` and `mosquitto.service`: enabled at boot; LOA restarts after failure.

This first stage is trusted-LAN HTTP/MQTT, not internet exposure. Do not port-forward these listeners. Keep existing SSH access when adjusting any firewall. If a host firewall is active, permit TCP 8000 and 1883 from the trusted LAN only.

## Switch the ESP32

The stored device token and pairing remain the same. Change only the firmware MQTT host to the Pi's reserved LAN IPv4. With the current helper, on the Mac:

```sh
.venv/bin/python scripts/configure_local.py --firmware-only \
  --wifi-from firmware/loa_sensor/secrets.h --host <PI-LAN-IP>
```

Compile and upload using docs/HARDWARE.md, then power the sensor from a charger if desired. `--firmware-only` preserves broker credentials and reads the existing Wi-Fi configuration. Do not provision the same device again or rotate its token for this migration.

Open `http://<PI-LAN-IP>:8000` in a browser and sign in. Add this origin to Supabase allowed redirects if using confirmation links. Verify your existing paired ESP32 is online and receives readings. The Pi receives ESP32 measurements without browser microphone permission.

Browser microphone capture from the Mac or phone to the Pi requires HTTPS: a Pi LAN HTTP address is not localhost on those clients. This includes the Mac's browser microphone after migration. HTTPS setup is a separate next step; the ESP32 and dashboard viewing can be validated first. Vercel is optional and is not required for this hub.

## Acceptance checks

```sh
systemctl is-active loa mosquitto
sudo journalctl -u loa -u mosquitto -n 50 --no-pager
hostname -I
```

Confirm all of the following before treating migration as complete:

1. Sign in through the Pi address; existing pairing and historical measurements remain.
2. ESP32 sends roughly one reading per second; MQTT shows connected.
3. Mac backend is stopped; readings still arrive on the Pi.
4. Trigger an alert, then return below tolerance and check the event closes.
5. Disconnect ESP32 power: offline within about 10 seconds. Reconnect: readings resume without pairing.
6. Reboot the Pi during a planned test: services start and the ESP32 reconnects.
7. Verify unauthenticated MQTT access fails; test cross-account and topic isolation.

Back up the live SQLite database using SQLite's backup facility, not just a copy of the main file while WAL writes are active. Keep `/etc/loa/loa.env` and broker credentials with the backup. The original Mac remains a rollback option: restore the ESP32 MQTT host and restart the old backend, noting that data collected separately on the Pi will not automatically merge.

## Installed hub (November 15, 2026)

- Pi login: `hunghoang@192.168.68.61` (`raspberrypi5hoang.local`).
- LAN dashboard: `http://192.168.68.61:8000`.
- Private HTTPS dashboard: `https://raspberrypi5hoang.taildd88d2.ts.net/` (clients must be connected to the authorized Tailscale network).
- ESP32 firmware MQTT destination is now `192.168.68.61:1883`.
- Broker and backend are installed and enabled at boot; the old Mac instances were stopped.
- Nine API tests passed on the Pi. LAN/HTTPS responses and live sensor ingestion into SQLite were verified. Anonymous MQTT access was rejected.
- The older broker config was backed up under `/etc/loa/backups/`; its old `/etc/mosquitto/passwd` file was left intact.
- Migrated password hashes were rejected by the Pi broker. Rehashing the unchanged credentials with the Pi's native `mosquitto_passwd` resolved authentication. New bundles include private `broker-users.json` input for native hashing; protect it like the other credentials.

Open the Pi address rather than localhost after cutover. Sign in again because browser sessions are specific to the origin. Browser microphones require the HTTPS address; LAN HTTP remains suitable for viewing the ESP32 dashboard. Confirmation-link redirects for this HTTPS origin may still need configuration in Supabase. Physical phone testing, a full Pi reboot test, alert acceptance tests, and network performance experiments remain separate checks.

A dedicated migration SSH key is stored in the ignored `.local/pi-ssh` on the Mac and authorized under the Pi account. Keep the private key private. You can revoke it later by removing the `loa-pi-migration` public-key line from the Pi's `~/.ssh/authorized_keys`.
