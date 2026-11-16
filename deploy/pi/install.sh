#!/usr/bin/env bash
# Run on Raspberry Pi OS/Debian from the extracted private migration bundle.
set -euo pipefail
umask 077
if [[ "$EUID" -ne 0 ]]; then
  echo 'Run with sudo bash deploy/pi/install.sh' >&2
  exit 1
fi
cd "$(dirname "$0")/../.."
for item in hub.env broker-users.json mqtt-passwords mqtt-acl loa.sqlite3 frontend/dist/index.html backend/main.py; do
  [[ -f "$item" ]] || { echo "Missing bundle file: $item" >&2; exit 1; }
done
# Do not replace an unrelated broker's configuration.
if [[ -d /etc/mosquitto/conf.d ]]; then
  for config in /etc/mosquitto/conf.d/*.conf; do
    [[ ! -e "$config" || "$config" == /etc/mosquitto/conf.d/loa.conf ]] || {
      echo "Existing broker configuration: $config. Review before installing LOA." >&2; exit 1;
    }
  done
fi
apt-get update
apt-get install -y python3-venv mosquitto
id loa >/dev/null 2>&1 || useradd --system --home-dir /var/lib/loa --shell /usr/sbin/nologin loa
install -d -m 755 /opt/loa /opt/loa/backend /opt/loa/frontend /opt/loa/frontend/dist
install -d -o loa -g loa -m 700 /var/lib/loa
install -d -m 700 /etc/loa
install -d -o root -g mosquitto -m 750 /etc/mosquitto/loa
if systemctl is-active --quiet loa; then systemctl stop loa; fi
cp -R backend/. /opt/loa/backend/
cp -R frontend/dist/. /opt/loa/frontend/dist/
chmod -R a+rX /opt/loa/backend /opt/loa/frontend/dist
install -m 644 requirements.txt /opt/loa/requirements.txt
python3 -m venv /opt/loa/.venv
/opt/loa/.venv/bin/python -m pip install -r /opt/loa/requirements.txt
chmod -R a+rX /opt/loa/.venv
if [[ ! -e /etc/loa/loa.env ]]; then
  install -m 600 hub.env /etc/loa/loa.env
fi
if [[ ! -e /var/lib/loa/loa.sqlite3 ]]; then
  install -o loa -g loa -m 600 loa.sqlite3 /var/lib/loa/loa.sqlite3
else
  echo 'Preserving the existing Pi database; migration snapshot was not restored.'
fi
# Existing Pi installations retain credentials and pairing state.
if [[ ! -e /etc/mosquitto/loa/passwords ]]; then
  /opt/loa/.venv/bin/python deploy/pi/install_passwords.py
  install -o root -g mosquitto -m 640 mqtt-acl /etc/mosquitto/loa/acl
fi
if [[ -f /etc/mosquitto/conf.d/loa.conf ]]; then
  install -d -m 700 /etc/loa/backups
  cp -p /etc/mosquitto/conf.d/loa.conf "/etc/loa/backups/mosquitto-$(date +%Y%m%d-%H%M%S).conf"
fi
cat > /etc/mosquitto/conf.d/loa.conf <<'CONFIG'
listener 1883 0.0.0.0
allow_anonymous false
password_file /etc/mosquitto/loa/passwords
acl_file /etc/mosquitto/loa/acl
CONFIG
chmod 644 /etc/mosquitto/conf.d/loa.conf
cat > /etc/systemd/system/loa.service <<'UNIT'
[Unit]
Description=LOA noise monitoring hub
Wants=network-online.target mosquitto.service
After=network-online.target mosquitto.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=loa
Group=loa
WorkingDirectory=/opt/loa
EnvironmentFile=/etc/loa/loa.env
# Pi-local paths and broker override values migrated from the Mac.
ExecStart=/opt/loa/.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/loa

[Install]
WantedBy=multi-user.target
UNIT
# EnvironmentFile values override Environment=, so normalize the file itself.
/opt/loa/.venv/bin/python - <<'PY'
from dotenv import set_key
for key, value in {'LOA_DB':'/var/lib/loa/loa.sqlite3','MQTT_HOST':'127.0.0.1',
                   'MQTT_PORT':'1883','MQTT_TLS':'false','LOA_AUTH_MODE':'supabase',
                   'LOA_SIMULATOR':'false'}.items():
    set_key('/etc/loa/loa.env', key, value)
PY
systemctl daemon-reload
systemctl enable mosquitto loa
systemctl restart mosquitto
systemctl start loa
sleep 2
systemctl is-active --quiet mosquitto
systemctl is-active --quiet loa
/opt/loa/.venv/bin/python - <<'PY'
import httpx
r = httpx.get('http://127.0.0.1:8000/api/config', timeout=10)
r.raise_for_status()
assert r.json()['auth_mode'] == 'supabase'
print('LOA backend responding; Supabase mode enabled.')
PY
echo 'Pi hub installed. Update the ESP32 MQTT host to this Pi before completing cutover.'
echo 'Logs: sudo journalctl -u loa -u mosquitto -f'
