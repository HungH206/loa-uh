"""Rehash existing credentials with the Pi's native Mosquitto password tool."""
import grp
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

os.umask(0o077)
users = json.loads(Path('broker-users.json').read_text())
if not users or any(not u or ':' in u or '\n' in u or '\n' in p for u, p in users.items()):
    raise SystemExit('Invalid broker credential input')
destination = Path('/etc/mosquitto/loa/passwords')
with tempfile.TemporaryDirectory(prefix='loa-mqtt-') as tmp:
    staging = Path(tmp) / 'passwords'
    staging.write_text(''.join(f'{u}:{p}\n' for u,p in users.items()))
    subprocess.run(['mosquitto_passwd', '-U', str(staging)], check=True, capture_output=True)
    if destination.exists():
        shutil.copy2(destination, destination.with_suffix('.previous'))
    shutil.copyfile(staging, destination)
    os.chown(destination, 0, grp.getgrnam('mosquitto').gr_gid)
    destination.chmod(0o640)
print('Broker credentials rehashed locally; passwords and sensor tokens unchanged.')
