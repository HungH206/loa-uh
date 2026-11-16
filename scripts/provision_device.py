"""Provision a device on the backend host. Run from the repository root."""
import argparse
import json
import re
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()
from backend import main


def provision(device_id, label):
    if not re.fullmatch(main.DEVICE_ID_PATTERN, device_id):
        raise ValueError('Device ID must contain 1–64 letters, digits, underscores or hyphens')
    code, token = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    main.initialize()
    with main.connect() as db:
        if db.execute('SELECT 1 FROM devices WHERE device_id=?', (device_id,)).fetchone():
            raise ValueError('Device already exists; existing ownership and credentials were preserved')
        db.execute('''INSERT INTO devices(device_id,label,claim_hash,token_hash,created_at)
            VALUES(?,?,?,?,?)''', (device_id, label, main.digest(code), main.digest(token), time.time()))
    return {'device_id': device_id, 'claim_code': code, 'device_token': token}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('device_id')
    parser.add_argument('--label', default='LOA microphone')
    parser.add_argument('--output', required=True, help='Private JSON file for provisioning credentials')
    args = parser.parse_args()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the file first so an existing credential file cannot be overwritten.
    with open(target, 'x', opener=lambda path, flags: __import__('os').open(path, flags, 0o600)) as output:
        try:
            result = provision(args.device_id, args.label)
        except Exception:
            target.unlink()
            raise
        json.dump(result, output, indent=2)
    print(f'Provisioned {args.device_id}. Credentials saved to {target}; keep this file private.')
