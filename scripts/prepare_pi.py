"""Create a private Pi migration bundle, including a consistent SQLite snapshot."""
import os
import json
import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def main():
    required = ['.env', '.local/mqtt-passwords', '.local/mqtt-acl', 'frontend/dist/index.html']
    for name in required:
        if not (ROOT / name).is_file():
            raise SystemExit(f'Missing {name}. Configure the existing hub and build the frontend first.')
    env = dotenv_values(ROOT / '.env')
    database = Path(env.get('LOA_DB') or 'loa.sqlite3')
    if not database.is_absolute():
        database = ROOT / database
    if not database.is_file():
        raise SystemExit('Existing database is missing; refusing to create an empty migration.')
    if not env.get('SUPABASE_URL') or not env.get('SUPABASE_PUBLISHABLE_KEY'):
        raise SystemExit('Supabase configuration is required for the Pi hub.')
    output = ROOT / '.local/pi-migration.tar.gz'
    # Restrict temporary files as well as the final archive.
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='loa-pi-') as tmp:
        stage = Path(tmp)
        for name in ['backend', 'frontend/dist', 'deploy/pi']:
            shutil.copytree(ROOT / name, stage / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copy2(ROOT / 'requirements.txt', stage / 'requirements.txt')
        shutil.copy2(ROOT / '.env', stage / 'hub.env')
        for name in ['mqtt-passwords', 'mqtt-acl']:
            shutil.copy2(ROOT / '.local' / name, stage / name)
        users = {env.get('MQTT_USERNAME') or 'loa-backend': env['MQTT_PASSWORD']}
        for config in (ROOT / '.local').glob('*.json'):
            data = json.loads(config.read_text())
            if isinstance(data, dict) and data.get('device_id') and data.get('device_token'):
                users[data['device_id']] = data['device_token']
        names = {line.split(':', 1)[0] for line in (ROOT / '.local/mqtt-passwords').read_text().splitlines()}
        if names != set(users):
            raise SystemExit('Broker accounts differ from available provisioning credentials; review before migration.')
        (stage / 'broker-users.json').write_text(json.dumps(users))
        # backup() captures committed WAL data without stopping the current hub.
        with sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True) as source:
            with sqlite3.connect(stage / 'loa.sqlite3') as target:
                source.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise SystemExit('SQLite integrity check failed.')
        with tarfile.open(output, 'w:gz') as archive:
            for path in stage.iterdir():
                archive.add(path, arcname=path.name)
    output.chmod(0o600)
    print(f'Private migration bundle: {output}')
    print('Contains credentials and account data. Transfer via SSH; do not commit or upload publicly.')
    print('Snapshot preserves existing pairings and history up to bundle creation time.')


if __name__ == '__main__':
    main()
