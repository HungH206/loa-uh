import asyncio
import base64
import hashlib
import hmac
import json
import logging
import math
import os
import random
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator
from backend.report_store import SupabaseReports, iso_time
from backend.network_stats import BrokerLink, network_summary

DB_PATH = os.getenv('LOA_DB', 'loa.sqlite3')
AUTH_MODE = os.getenv('LOA_AUTH_MODE', 'supabase')
SUPABASE_URL = os.getenv('SUPABASE_URL', '').rstrip('/')
SUPABASE_KEY = os.getenv('SUPABASE_PUBLISHABLE_KEY', '')
MQTT_HOST = os.getenv('MQTT_HOST', '')
REPORT_STORE = os.getenv('LOA_REPORT_STORE', 'sqlite')
SIMULATOR = os.getenv('LOA_SIMULATOR', 'false').lower() == 'true'
DEVICE_ID_PATTERN = r'^[A-Za-z0-9_-]{1,64}$'
DEMO_USER = 'local-demo'
clients: dict[WebSocket, str] = {}
mqtt_connected = False
broker_link = BrokerLink()
log = logging.getLogger('loa')


def connect():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def initialize():
    # Only the public/publishable key may be exposed through /api/config.
    if SUPABASE_KEY.startswith('sb_secret_'):
        raise RuntimeError('Use a Supabase publishable key, never a secret key')
    if SUPABASE_KEY.count('.') == 2:
        try:
            claims = json.loads(base64.urlsafe_b64decode(SUPABASE_KEY.split('.')[1] + '==='))
        except (ValueError, UnicodeDecodeError):
            raise RuntimeError('Invalid Supabase public key')
        if claims.get('role') != 'anon':
            raise RuntimeError('Use the Supabase anon key, never the service-role key')
    if AUTH_MODE not in ('supabase', 'demo'):
        raise RuntimeError('LOA_AUTH_MODE must be supabase or demo')
    if SIMULATOR and AUTH_MODE != 'demo':
        raise RuntimeError('The simulator is available only in explicit demo mode')
    if REPORT_STORE not in ('sqlite', 'supabase'):
        raise RuntimeError('LOA_REPORT_STORE must be sqlite or supabase')
    if REPORT_STORE == 'supabase' and AUTH_MODE != 'supabase':
        raise RuntimeError('Cloud reports require Supabase authentication')
    with connect() as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS devices (
          device_id TEXT PRIMARY KEY, label TEXT NOT NULL,
          claim_hash TEXT NOT NULL, token_hash TEXT NOT NULL,
          owner_id TEXT, claimed_at REAL, created_at REAL NOT NULL,
          last_seen REAL, last_source TEXT);
        CREATE TABLE IF NOT EXISTS preferences (
          user_id TEXT PRIMARY KEY, threshold REAL NOT NULL DEFAULT -40,
          duration_seconds INTEGER NOT NULL DEFAULT 3,
          cooldown_seconds INTEGER NOT NULL DEFAULT 30);
        CREATE TABLE IF NOT EXISTS sound_readings (
          id INTEGER PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(device_id),
          dbfs REAL NOT NULL, rms REAL, timestamp REAL, received_at REAL NOT NULL,
          source TEXT NOT NULL, sequence INTEGER, boot_id TEXT, uptime_ms INTEGER,
          UNIQUE(device_id, boot_id, sequence));
        CREATE INDEX IF NOT EXISTS sound_time ON sound_readings(received_at);
        CREATE INDEX IF NOT EXISTS sound_device_time ON sound_readings(device_id, received_at);
        CREATE TABLE IF NOT EXISTS noise_events (
          id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, device_id TEXT NOT NULL,
          started_at REAL NOT NULL, triggered_at REAL NOT NULL, ended_at REAL,
          peak_dbfs REAL NOT NULL, threshold REAL NOT NULL,
          duration_seconds INTEGER NOT NULL, end_reason TEXT);
        CREATE INDEX IF NOT EXISTS events_owner ON noise_events(user_id, triggered_at);
        CREATE TABLE IF NOT EXISTS disturbance_reports (
          id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, request_id TEXT NOT NULL,
          occurred_at REAL NOT NULL, created_at REAL NOT NULL,
          location TEXT NOT NULL, description TEXT NOT NULL,
          feeling TEXT NOT NULL, details TEXT NOT NULL,
          UNIQUE(user_id, request_id));
        CREATE TABLE IF NOT EXISTS report_cloud_sync (
          project TEXT NOT NULL, user_id TEXT NOT NULL, request_id TEXT NOT NULL,
          PRIMARY KEY(project, user_id, request_id));
        CREATE INDEX IF NOT EXISTS reports_owner ON disturbance_reports(user_id, occurred_at);
        CREATE TABLE IF NOT EXISTS alert_state (
          user_id TEXT NOT NULL, device_id TEXT NOT NULL,
          above_since REAL, last_sample REAL, last_alert REAL,
          active_event INTEGER, boot_id TEXT,
          PRIMARY KEY(user_id, device_id));
        ''')
        if AUTH_MODE == 'demo':
            db.execute('INSERT OR IGNORE INTO preferences(user_id) VALUES(?)', (DEMO_USER,))
            if SIMULATOR:
                db.execute('''INSERT OR IGNORE INTO devices
                    (device_id,label,claim_hash,token_hash,owner_id,created_at)
                    VALUES('demo-sensor','Simulated microphone','','',?,?)''', (DEMO_USER, time.time()))


class Reading(BaseModel):
    # dbfs is a relative digital measurement, never a calibrated SPL/dBA value.
    model_config = ConfigDict(extra='ignore')
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    dbfs: float = Field(ge=-120, le=0, allow_inf_nan=False)
    rms: float | None = Field(default=None, ge=0, le=32768, allow_inf_nan=False)
    timestamp: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    sequence: int | None = Field(default=None, ge=0, le=4294967295)
    boot_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,64}$')
    uptime_ms: int | None = Field(default=None, ge=0)


class Settings(BaseModel):
    threshold: float = Field(default=-40, ge=-90, le=-1, allow_inf_nan=False)
    duration_seconds: int = Field(default=3, ge=1, le=60)
    cooldown_seconds: int = Field(default=30, ge=0, le=3600)


class Claim(BaseModel):
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    claim_code: str = Field(min_length=16, max_length=256)


async def verify_token(token: str):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(503, 'Configure SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY on the server')
    if not token:
        raise HTTPException(401, 'Sign in to continue')
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(f'{SUPABASE_URL}/auth/v1/user', headers={
                'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {token}'})
    except httpx.RequestError:
        raise HTTPException(503, 'Authentication service is unavailable')
    if response.status_code in (401, 403):
        raise HTTPException(401, 'Your session has expired. Sign in again.')
    if response.status_code != 200:
        raise HTTPException(503, 'Authentication service is unavailable')
    try:
        user_id = response.json()['id']
        if not isinstance(user_id, str) or not user_id or user_id == DEMO_USER:
            raise ValueError()
    except (KeyError, ValueError):
        raise HTTPException(401, 'Invalid authentication response')
    return user_id


async def current_user(authorization: Annotated[str | None, Header()] = None):
    if AUTH_MODE == 'demo':
        return DEMO_USER
    token = authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
    return await verify_token(token)


User = Annotated[str, Depends(current_user)]


def preferences(db, user_id):
    db.execute('INSERT OR IGNORE INTO preferences(user_id) VALUES(?)', (user_id,))
    return dict(db.execute('SELECT threshold,duration_seconds,cooldown_seconds FROM preferences WHERE user_id=?', (user_id,)).fetchone())


async def broadcast(user_id, event):
    async def send(ws):
        try:
            await asyncio.wait_for(ws.send_json(event), 2)
        except Exception:
            clients.pop(ws, None)
    await asyncio.gather(*(send(ws) for ws, owner in tuple(clients.items()) if owner == user_id))


def close_event(db, event_id, now, reason):
    if event_id:
        db.execute('UPDATE noise_events SET ended_at=?,end_reason=? WHERE id=? AND ended_at IS NULL', (now, reason, event_id))


def evaluate(db, owner, reading, now):
    pref = preferences(db, owner)
    state = db.execute('SELECT * FROM alert_state WHERE user_id=? AND device_id=?', (owner, reading.device_id)).fetchone()
    above, last_alert, active = None, None, None
    if state:
        above, last_alert, active = state['above_since'], state['last_alert'], state['active_event']
        # Missing samples or sensor reboot are not evidence of continuous loudness.
        if now - state['last_sample'] > 2.5 or state['boot_id'] != reading.boot_id:
            close_event(db, active, state['last_sample'], 'sensor_gap')
            above, active = None, None
    if reading.dbfs >= pref['threshold']:
        above = now if above is None else above
        if active:
            db.execute('UPDATE noise_events SET peak_dbfs=MAX(peak_dbfs,?) WHERE id=?', (reading.dbfs, active))
        elif now - above >= pref['duration_seconds'] and (last_alert is None or now - last_alert >= pref['cooldown_seconds']):
            peak = db.execute('SELECT MAX(dbfs) FROM sound_readings WHERE device_id=? AND received_at>=?', (reading.device_id, above)).fetchone()[0]
            cur = db.execute('''INSERT INTO noise_events(user_id,device_id,started_at,triggered_at,peak_dbfs,threshold,duration_seconds)
                VALUES(?,?,?,?,?,?,?)''', (owner, reading.device_id, above, now, peak, pref['threshold'], pref['duration_seconds']))
            active, last_alert = cur.lastrowid, now
    else:
        close_event(db, active, now, 'below_threshold')
        above, active = None, None
    db.execute('''INSERT OR REPLACE INTO alert_state
        (user_id,device_id,above_since,last_sample,last_alert,active_event,boot_id)
        VALUES(?,?,?,?,?,?,?)''', (owner, reading.device_id, above, now, last_alert, active, reading.boot_id))


def expire_states(db, now):
    stale = db.execute('SELECT * FROM alert_state WHERE active_event IS NOT NULL AND last_sample<?', (now-2.5,)).fetchall()
    for state in stale:
        close_event(db, state['active_event'], state['last_sample'], 'sensor_gap')
        db.execute('UPDATE alert_state SET above_since=NULL,active_event=NULL WHERE user_id=? AND device_id=?', (state['user_id'], state['device_id']))


async def ingest(reading: Reading, source: str):
    now = time.time()
    with connect() as db:
        device = db.execute('SELECT * FROM devices WHERE device_id=?', (reading.device_id,)).fetchone()
        if device is None:
            raise HTTPException(403, 'Device must be provisioned before sending readings')
        cur = db.execute('''INSERT OR IGNORE INTO sound_readings
            (device_id,dbfs,rms,timestamp,received_at,source,sequence,boot_id,uptime_ms)
            VALUES(?,?,?,?,?,?,?,?,?)''', (reading.device_id, reading.dbfs, reading.rms, reading.timestamp,
                                          now, source, reading.sequence, reading.boot_id, reading.uptime_ms))
        if not cur.rowcount:
            return {'duplicate': True}
        row = dict(db.execute('SELECT * FROM sound_readings WHERE id=?', (cur.lastrowid,)).fetchone())
        db.execute('UPDATE devices SET last_seen=?,last_source=? WHERE device_id=?', (now, source, reading.device_id))
        expire_states(db, now)
        owner = device['owner_id']
        if owner:
            evaluate(db, owner, reading, now)
    if owner:
        await broadcast(owner, {'type': 'reading', 'data': row})
    return row


async def simulate():
    sequence = 0
    while True:
        value = round(max(-85, min(-5, -47 + 14 * math.sin(time.time()/8) + random.gauss(0, 2))), 1)
        await ingest(Reading(device_id='demo-sensor', dbfs=value, sequence=sequence, boot_id='demo'), 'simulator')
        sequence += 1
        await asyncio.sleep(1)


async def monitor():
    while True:
        with connect() as db:
            expire_states(db, time.time())
        await asyncio.sleep(1)


def mqtt_message(topic, payload):
    reading = Reading.model_validate_json(payload)
    if topic != f'loa/{reading.device_id}/noise':
        raise ValueError('MQTT topic must match payload device_id')
    return reading


@asynccontextmanager
async def lifespan(app):
    global mqtt_connected
    initialize()
    tasks = [asyncio.create_task(monitor())]
    if SIMULATOR:
        tasks.append(asyncio.create_task(simulate()))
    mqtt = None
    if MQTT_HOST:
        import paho.mqtt.client as mqtt_lib
        loop = asyncio.get_running_loop()
        mqtt = mqtt_lib.Client(mqtt_lib.CallbackAPIVersion.VERSION2, client_id='loa-backend')
        if os.getenv('MQTT_USERNAME'):
            mqtt.username_pw_set(os.environ['MQTT_USERNAME'], os.getenv('MQTT_PASSWORD'))
        if os.getenv('MQTT_TLS', 'false').lower() == 'true':
            mqtt.tls_set(ca_certs=os.getenv('MQTT_CA_FILE') or None)
        def on_connect(client, userdata, flags, reason_code, properties):
            global mqtt_connected
            mqtt_connected = reason_code == 0
            if mqtt_connected:
                client.subscribe('loa/+/noise', qos=1)
                # Broker statistics for the Network tab; needs `topic read $SYS/#` in the ACL.
                client.subscribe('$SYS/broker/#', qos=0)
                broker_link.connected()
            else:
                broker_link.event('broker_refused', str(reason_code))
        def on_disconnect(client, userdata, flags, reason_code, properties):
            global mqtt_connected
            mqtt_connected = False
            broker_link.disconnected(reason_code)
        def report_result(future):
            try:
                future.result()
            except Exception:
                log.exception('MQTT reading ingestion failed')
        def on_message(client, userdata, message):
            if message.topic.startswith('$SYS/'):
                broker_link.sys_message(message.topic, message.payload)
                return
            try:
                reading = mqtt_message(message.topic, message.payload)
                future = asyncio.run_coroutine_threadsafe(ingest(reading, 'mqtt'), loop)
                future.add_done_callback(report_result)
            except (ValueError, UnicodeDecodeError):
                log.warning('Rejected invalid MQTT reading')
        mqtt.on_connect, mqtt.on_disconnect, mqtt.on_message = on_connect, on_disconnect, on_message
        mqtt.connect_async(MQTT_HOST, int(os.getenv('MQTT_PORT', '1883')))
        mqtt.loop_start()
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if mqtt:
            mqtt.disconnect()
            mqtt.loop_stop()
        mqtt_connected = False


app = FastAPI(title='LOA · Listen, Observe, Alert', lifespan=lifespan)
app.add_middleware(CORSMiddleware,
    allow_origins=[s.strip() for s in os.getenv('LOA_WEB_ORIGINS', '').split(',') if s.strip()],
    allow_methods=['GET', 'POST', 'PUT'], allow_headers=['Authorization', 'Content-Type'])


class DisturbanceReport(BaseModel):
    request_id: uuid.UUID
    occurred_at: float = Field(gt=0, allow_inf_nan=False)
    location: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    feeling: Literal['Tolerable', 'Need to Cope', 'Sensitive']
    details: str = Field(default='', max_length=4000)

    @field_validator('location', 'description', 'details', mode='before')
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value


def cloud_reports(authorization, user):
    token = authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
    return SupabaseReports(SUPABASE_URL, SUPABASE_KEY, token, user)


async def migrate_own_reports(store, user):
    # Only upload this signed-in user's legacy reports. Originals remain for backup.
    while True:
        with connect() as db:
            rows = [dict(r) for r in db.execute("""SELECT r.* FROM disturbance_reports r
                WHERE r.user_id=? AND NOT EXISTS (
                    SELECT 1 FROM report_cloud_sync s WHERE s.project=? AND s.user_id=r.user_id AND s.request_id=r.request_id)
                ORDER BY r.id LIMIT 100""", (user, SUPABASE_URL))]
        if not rows:
            return
        await store.insert([{**{k: r[k] for k in ('request_id','location','description','feeling','details')},
                             'occurred_at':iso_time(r['occurred_at']), 'created_at':iso_time(r['created_at'])} for r in rows])
        with connect() as db:
            db.executemany('INSERT OR IGNORE INTO report_cloud_sync VALUES(?,?,?)',
                           [(SUPABASE_URL, user, r['request_id']) for r in rows])


@app.get('/api/reports')
async def get_reports(user: User, authorization: Annotated[str | None, Header()] = None):
    if REPORT_STORE == 'supabase':
        store = cloud_reports(authorization, user)
        await migrate_own_reports(store, user)
        return await store.list()
    with connect() as db:
        return [dict(row) for row in db.execute('''SELECT id,occurred_at,created_at,location,description,feeling,details
            FROM disturbance_reports WHERE user_id=? ORDER BY occurred_at DESC,id DESC LIMIT 50''', (user,))]


@app.post('/api/reports', status_code=201)
async def create_report(report: DisturbanceReport, user: User, authorization: Annotated[str | None, Header()] = None):
    now = time.time()
    if report.occurred_at > now + 60:
        raise HTTPException(422, 'Incident time cannot be in the future. Check your device clock.')
    if REPORT_STORE == 'supabase':
        store = cloud_reports(authorization, user)
        await store.insert([{**report.model_dump(mode='json'), 'occurred_at':iso_time(report.occurred_at)}])
        saved = await store.list(str(report.request_id))
        if not saved:
            raise HTTPException(503, 'Cloud report could not be confirmed. Please retry.')
        return saved[0]
    with connect() as db:
        db.execute('''INSERT OR IGNORE INTO disturbance_reports
            (user_id,request_id,occurred_at,created_at,location,description,feeling,details)
            VALUES(?,?,?,?,?,?,?,?)''', (user, str(report.request_id), report.occurred_at, now,
                                      report.location, report.description, report.feeling, report.details))
        return dict(db.execute('''SELECT id,occurred_at,created_at,location,description,feeling,details
            FROM disturbance_reports WHERE user_id=? AND request_id=?''', (user, str(report.request_id))).fetchone())


class BrowserDevice(BaseModel):
    label: str = Field(min_length=1, max_length=100)


@app.post('/api/devices/browser', status_code=201)
async def browser_device(body: BrowserDevice, user: User):
    device_id = 'browser-' + uuid.uuid4().hex
    now = time.time()
    with connect() as db:
        db.execute('''INSERT INTO devices
            (device_id,label,claim_hash,token_hash,owner_id,claimed_at,created_at)
            VALUES(?,?,'','',?,?,?)''', (device_id, body.label, user, now, now))
    await broadcast(user, {'type': 'devices'})
    return {'device_id': device_id}


@app.post('/api/browser/readings', status_code=201)
async def browser_reading(reading: Reading, user: User):
    with connect() as db:
        device = db.execute('SELECT owner_id,token_hash FROM devices WHERE device_id=?', (reading.device_id,)).fetchone()
    if not device or device['owner_id'] != user or device['token_hash'] or not reading.device_id.startswith('browser-'):
        raise HTTPException(403, 'This browser microphone does not belong to your account')
    return await ingest(reading, 'browser')


@app.get('/api/config')
def config():
    return {'auth_mode': AUTH_MODE, 'supabase_url': SUPABASE_URL,
            'supabase_publishable_key': SUPABASE_KEY, 'unit': 'dBFS', 'report_store': REPORT_STORE}


@app.get('/api/status')
def status(user: User):
    now = time.time()
    with connect() as db:
        devices = [dict(r) for r in db.execute('SELECT device_id,label,last_seen,last_source FROM devices WHERE owner_id=? ORDER BY device_id', (user,))]
    for device in devices:
        device['online'] = device['last_seen'] is not None and now-device['last_seen'] < 10
    return {'simulator': SIMULATOR, 'auth_mode': AUTH_MODE, 'mqtt_configured': bool(MQTT_HOST),
            'mqtt_connected': mqtt_connected, 'devices': devices}


@app.post('/api/devices/claim')
async def claim_device(claim: Claim, user: User):
    with connect() as db:
        device = db.execute('SELECT * FROM devices WHERE device_id=?', (claim.device_id,)).fetchone()
        if not device or not device['claim_hash'] or not hmac.compare_digest(device['claim_hash'], digest(claim.claim_code)):
            raise HTTPException(403, 'Invalid device ID or pairing code')
        cur = db.execute('UPDATE devices SET owner_id=?,claimed_at=?,claim_hash=? WHERE device_id=? AND owner_id IS NULL',
                         (user, time.time(), '', claim.device_id))
        if not cur.rowcount:
            raise HTTPException(409, 'Device is already paired')
    await broadcast(user, {'type': 'devices'})
    return {'device_id': claim.device_id, 'paired': True}


@app.get('/api/readings')
def readings(user: User, limit: int = 300, minutes: int = 60):
    if not 1 <= limit <= 10000 or not 1 <= minutes <= 10080:
        raise HTTPException(422, 'Limit must be 1–10000 and minutes 1–10080')
    with connect() as db:
        rows = db.execute('''SELECT r.* FROM sound_readings r JOIN devices d USING(device_id)
            WHERE d.owner_id=? AND r.received_at>=? AND r.received_at>=COALESCE(d.claimed_at,0)
            ORDER BY r.id DESC LIMIT ?''', (user, time.time()-minutes*60, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


@app.post('/api/readings', status_code=201)
async def post_reading(reading: Reading, authorization: Annotated[str | None, Header()] = None):
    token = authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
    with connect() as db:
        device = db.execute('SELECT token_hash FROM devices WHERE device_id=?', (reading.device_id,)).fetchone()
    if not token or not device or not device['token_hash'] or not hmac.compare_digest(device['token_hash'], digest(token)):
        raise HTTPException(401, 'Invalid device credential')
    return await ingest(reading, 'http')


@app.get('/api/settings')
def get_settings(user: User):
    with connect() as db:
        return preferences(db, user)


@app.put('/api/settings')
async def set_settings(settings: Settings, user: User):
    with connect() as db:
        preferences(db, user)
        db.execute('UPDATE preferences SET threshold=?,duration_seconds=?,cooldown_seconds=? WHERE user_id=?',
                   (settings.threshold, settings.duration_seconds, settings.cooldown_seconds, user))
        for state in db.execute('SELECT * FROM alert_state WHERE user_id=?', (user,)).fetchall():
            close_event(db, state['active_event'], time.time(), 'settings_changed')
        db.execute('DELETE FROM alert_state WHERE user_id=?', (user,))
    await broadcast(user, {'type': 'settings', 'data': settings.model_dump()})
    return settings


@app.get('/api/alerts')
def alerts(user: User):
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM noise_events WHERE user_id=? ORDER BY id DESC LIMIT 50', (user,))]


@app.get('/api/network')
def network(user: User):
    with connect() as db:
        return network_summary(db, user, broker_link, bool(MQTT_HOST), mqtt_connected,
                               f"{MQTT_HOST}:{os.getenv('MQTT_PORT', '1883')}" if MQTT_HOST else None)


@app.websocket('/ws')
async def websocket(ws: WebSocket):
    await ws.accept()
    try:
        token = ''
        if AUTH_MODE == 'demo':
            user = DEMO_USER
        else:
            message = await asyncio.wait_for(ws.receive_json(), 10)
            if not isinstance(message, dict) or message.get('type') != 'auth' or not isinstance(message.get('token'), str):
                await ws.close(code=4401)
                return
            token = message['token']
            user = await verify_token(token)
        clients[ws] = user
        await ws.send_json({'type': 'ready'})
        next_check = time.monotonic() + 60
        while True:
            try:
                remaining = max(0.01, next_check - time.monotonic())
                message = await asyncio.wait_for(ws.receive_json(), remaining)
                if isinstance(message, dict) and message.get('type') == 'ping':
                    # Lets the browser measure its WebSocket round-trip time to this server.
                    ping_id = message.get('id')
                    await ws.send_json({'type': 'pong', 'id': ping_id if isinstance(ping_id, int) else None})
                # No `continue`: frequent pings must not skip the periodic session re-check below.
                if isinstance(message, dict) and message.get('type') == 'auth' and AUTH_MODE != 'demo':
                    candidate = message.get('token')
                    if not isinstance(candidate, str) or await verify_token(candidate) != user:
                        await ws.close(code=4401)
                        return
                    token = candidate
                if time.monotonic() >= next_check:
                    if AUTH_MODE != 'demo':
                        await verify_token(token)
                    next_check = time.monotonic() + 60
            except asyncio.TimeoutError:
                if AUTH_MODE != 'demo' and await verify_token(token) != user:
                    await ws.close(code=4401)
                    return
                next_check = time.monotonic() + 60
    except (HTTPException, ValueError, asyncio.TimeoutError):
        await ws.close(code=4401)
    except WebSocketDisconnect:
        pass
    finally:
        clients.pop(ws, None)


dist = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
if dist.exists():
    app.mount('/', StaticFiles(directory=dist, html=True), name='dashboard')
