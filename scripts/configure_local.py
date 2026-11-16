"""Configure the local LAN broker and firmware without printing credentials."""
import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
from pathlib import Path

from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]


def private_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='loa-001')
    parser.add_argument('--wifi-from', type=Path, help='Read WIFI_SSID and WIFI_PASSWORD from an existing Arduino secrets.h')
    parser.add_argument('--firmware-only', action='store_true', help='Reuse an already configured broker')
    parser.add_argument('--host', help='LAN IPv4 of the computer hosting MQTT')
    args = parser.parse_args()
    config = json.loads((ROOT/'.local'/f'{args.device}.json').read_text())
    env = dotenv_values(ROOT/'.env')
    broker_password = env.get('MQTT_PASSWORD') or secrets.token_urlsafe(32)
    binary = shutil.which('mosquitto_passwd') or '/opt/homebrew/bin/mosquitto_passwd'
    if not Path(binary).exists():
        raise SystemExit('Install Mosquitto first (macOS: brew install mosquitto).')
    password_path = ROOT/'.local/mqtt-passwords'
    if not args.firmware_only:
        if password_path.exists():
            raise SystemExit('Broker credentials already exist. Refusing to overwrite them; see docs/HARDWARE.md.')
        private_write(password_path, f'loa-backend:{broker_password}\n{args.device}:{config["device_token"]}\n')
        try:
            subprocess.run([binary,'-U',str(password_path)],check=True,capture_output=True)
        except Exception:
            password_path.unlink(missing_ok=True)
            raise
        acl = ROOT/'.local/mqtt-acl'
        private_write(acl, f'user loa-backend\ntopic read loa/+/noise\n\nuser {args.device}\ntopic write loa/{args.device}/noise\n')
        broker = ROOT/'.local/mosquitto.conf'
        private_write(broker, f'listener 1883 0.0.0.0\nallow_anonymous false\npassword_file {password_path}\nacl_file {acl}\npersistence false\nlog_type error\nlog_type warning\nlog_type notice\n')
        for key, value in {'MQTT_HOST':'127.0.0.1','MQTT_PORT':'1883','MQTT_USERNAME':'loa-backend','MQTT_PASSWORD':broker_password}.items():
            set_key(str(ROOT/'.env'),key,value)
    if args.wifi_from:
        original = args.wifi_from.read_text()
        wifi = {}
        for name in ('WIFI_SSID','WIFI_PASSWORD'):
            match = re.search(r'\b'+name+r'\s*=\s*("(?:\\.|[^"\\])*")\s*;', original)
            if not match:
                match = re.search(r'^\s*#define\s+'+name+r'\s+("(?:\\.|[^"\\])*")', original, re.MULTILINE)
            if not match:
                raise SystemExit(f'Cannot read {name} from header; configure secrets.h manually.')
            wifi[name] = match[1]
        host = args.host
        if not host:
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                sock.connect(('1.1.1.1',80))
                host = sock.getsockname()[0]
        header = '#pragma once\n'
        for name in ('WIFI_SSID','WIFI_PASSWORD'):
            header += f'const char* {name} = {wifi[name]};\n'
        for name,value in {'MQTT_HOST':host,'DEVICE_ID':args.device,'DEVICE_TOKEN':config['device_token']}.items():
            header += f'const char* {name} = {json.dumps(value)};\n'
        header += 'constexpr unsigned short MQTT_PORT = 1883;\n'
        private_write(ROOT/'firmware/loa_sensor/secrets.h',header)
        print(f'Firmware configured for broker {host}:1883; Wi-Fi credentials reused privately.')
    print('Authenticated broker configuration saved in .local/mosquitto.conf. No system service was started.')


if __name__ == '__main__':
    main()
