"""Add a new sensor to the Pi hub: database entry + one-time pairing code, broker login and topic ACL.

Run on the Pi (standard library only; needs mosquitto_passwd from the mosquitto package):

    sudo python3 add_device.py loa-002 --label "XIAO ESP32-S3 Sense"

Prints the device ID and pairing code, and saves {device_id, claim_code, device_token} to a private
JSON file in the calling user's home. Put DEVICE_ID and DEVICE_TOKEN in the ESP32's secrets.h;
enter the device ID and pairing code in the dashboard under "Pair a network sensor".
"""
import argparse
import hashlib
import json
import os
import pwd
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEVICE_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')

# Runs as the database owner so SQLite's -wal/-shm files are never created by root.
DB_STEP = r'''
import sqlite3, sys
db_path, action, device_id = sys.argv[1:4]
db = sqlite3.connect(db_path, timeout=10)
with db:
    if action == 'exists':
        sys.exit(3 if db.execute('SELECT 1 FROM devices WHERE device_id=?', (device_id,)).fetchone() else 0)
    if action == 'insert':
        label, claim_hash, token_hash, now = sys.argv[4:8]
        db.execute('INSERT INTO devices(device_id,label,claim_hash,token_hash,created_at) VALUES(?,?,?,?,?)',
                   (device_id, label, claim_hash, token_hash, float(now)))
    if action == 'delete':
        db.execute('DELETE FROM devices WHERE device_id=? AND owner_id IS NULL', (device_id,))
'''


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def db_step(db, db_user, *args):
    """Run DB_STEP as db_user (when root) and return its exit code."""
    kwargs = {'user': db_user, 'group': db_user} if os.geteuid() == 0 and db_user else {}
    return subprocess.run([sys.executable, '-c', DB_STEP, str(db), *args], **kwargs).returncode


def hashed_password_line(username, password, tool='mosquitto_passwd'):
    """Hash username:password with Mosquitto's own tool, without putting it on a command line."""
    with tempfile.TemporaryDirectory(prefix='loa-device-') as tmp:
        staging = Path(tmp) / 'passwords'
        staging.write_text(f'{username}:{password}\n')
        staging.chmod(0o600)
        subprocess.run([tool, '-U', str(staging)], check=True, capture_output=True)
        return staging.read_text().strip() + '\n'


def append_with_backup(path, text):
    path = Path(path)
    shutil.copy2(path, path.with_name(path.name + '.before-add-device'))
    with open(path, 'a') as handle:
        if path.stat().st_size and not path.read_text().endswith('\n'):
            handle.write('\n')
        handle.write(text)


def add_device(device_id, label, db, passwords, acl, db_user='loa', tool='mosquitto_passwd', now=None):
    if not DEVICE_ID.match(device_id) or device_id.startswith('browser-') or device_id == 'loa-backend':
        raise ValueError('Device ID must be 1–64 letters, digits, "_" or "-", and not a reserved name')
    for path in (db, passwords, acl):
        if not Path(path).is_file():
            raise ValueError(f'{path} not found. Is this the Pi hub?')
    if any(line.split(':', 1)[0] == device_id for line in Path(passwords).read_text().splitlines()):
        raise ValueError(f'{device_id} already has a broker login')
    if re.search(rf'^user {re.escape(device_id)}$', Path(acl).read_text(), re.MULTILINE):
        raise ValueError(f'{device_id} already has broker permissions')
    status = db_step(db, db_user, 'exists', device_id)
    if status == 3:
        raise ValueError(f'{device_id} already exists in the hub database')
    if status:
        raise RuntimeError('Could not read the hub database')

    claim_code, token = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    line = hashed_password_line(device_id, token, tool)
    if db_step(db, db_user, 'insert', device_id, label, digest(claim_code), digest(token), str(now or time.time())):
        raise RuntimeError('Could not add the device to the hub database')
    try:
        append_with_backup(passwords, line)
        append_with_backup(acl, f'\nuser {device_id}\ntopic write loa/{device_id}/noise\n')
    except Exception:
        db_step(db, db_user, 'delete', device_id)  # Undo so the ID can be added again.
        raise
    return {'device_id': device_id, 'claim_code': claim_code, 'device_token': token}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('device_id')
    parser.add_argument('--label', default='XIAO ESP32-S3 Sense')
    parser.add_argument('--db', default='/var/lib/loa/loa.sqlite3')
    parser.add_argument('--passwords', default='/etc/mosquitto/loa/passwords')
    parser.add_argument('--acl', default='/etc/mosquitto/loa/acl')
    args = parser.parse_args()
    if os.geteuid() != 0:
        sys.exit('Run with sudo: it updates the broker files and reloads Mosquitto.')
    try:
        result = add_device(args.device_id, args.label, args.db, args.passwords, args.acl)
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(f'Not added: {error}')
    subprocess.run(['systemctl', 'reload', 'mosquitto'], check=True)

    owner = pwd.getpwnam(os.environ.get('SUDO_USER') or 'root')
    output = Path(owner.pw_dir) / f'{args.device_id}.json'
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(result, handle, indent=2)
    os.chown(output, owner.pw_uid, owner.pw_gid)
    print(f'Added {args.device_id}. Broker login and topic loa/{args.device_id}/noise are active.')
    print(f'Pairing code (one-time, for the dashboard): {result["claim_code"]}')
    print(f'Credentials saved to {output} (private). The ESP32 needs DEVICE_ID and DEVICE_TOKEN from it.')


if __name__ == '__main__':
    main()
