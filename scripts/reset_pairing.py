"""Give an existing sensor a new one-time pairing code, releasing it from its current account.

The device token (MQTT/HTTP login) is unchanged, so the sensor firmware needs no update.
Its old account loses access to the sensor and its history; the new account sees readings
from the moment it pairs. Self-contained (standard library only) so it can be copied to the Pi:

    scp scripts/reset_pairing.py hunghoang@raspberrypi5hoang.local:~/
    # On the Pi: run as the service user so database files keep the right owner.
    sudo install -m 644 ~/reset_pairing.py /tmp/reset_pairing.py
    sudo -u loa python3 /tmp/reset_pairing.py loa-001 --db /var/lib/loa/loa.sqlite3 --release

Run while the hub is running; SQLite handles the concurrent write. The new code is printed once.
"""
import argparse
import hashlib
import re
import secrets
import sqlite3
import sys


def reset(db_path, device_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', device_id):
        raise ValueError('Device ID must contain 1–64 letters, digits, underscores or hyphens')
    code = secrets.token_urlsafe(24)
    db = sqlite3.connect(db_path, timeout=10)
    try:
        with db:
            row = db.execute('SELECT owner_id FROM devices WHERE device_id=?', (device_id,)).fetchone()
            if row is None:
                raise ValueError(f'No device {device_id!r} in {db_path}. Provision it first.')
            if device_id.startswith('browser-'):
                raise ValueError('Browser microphones are not paired with codes.')
            db.execute('UPDATE devices SET owner_id=NULL, claimed_at=NULL, claim_hash=? WHERE device_id=?',
                       (hashlib.sha256(code.encode()).hexdigest(), device_id))
            # Close the previous owner's alert tracking for this sensor.
            db.execute("UPDATE noise_events SET ended_at=COALESCE(ended_at, strftime('%s','now')), "
                       "end_reason=COALESCE(end_reason, 'unpaired') WHERE device_id=? AND ended_at IS NULL", (device_id,))
            db.execute('DELETE FROM alert_state WHERE device_id=?', (device_id,))
    finally:
        db.close()
    return code, row[0] is not None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('device_id')
    parser.add_argument('--db', required=True, help='Hub database, /var/lib/loa/loa.sqlite3 on the Pi')
    parser.add_argument('--release', action='store_true', help='Confirm: remove the sensor from its current account')
    args = parser.parse_args()
    if not args.release:
        sys.exit('This removes the sensor from its current account. Re-run with --release to confirm.')
    try:
        code, was_paired = reset(args.db, args.device_id)
    except (ValueError, sqlite3.Error) as error:
        sys.exit(str(error))
    print(f"{args.device_id} {'released from its previous account' if was_paired else 'was not paired'}.")
    print(f'New one-time pairing code: {code}')
    print('Enter the device ID and this code under "Pair a network sensor" while signed in to the account you want.')
