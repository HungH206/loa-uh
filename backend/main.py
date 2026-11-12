import asyncio
import json
import math
import os
import random
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

DB_PATH = os.getenv('LOA_DB', 'loa.sqlite3')
MQTT_HOST = os.getenv('MQTT_HOST', '')
clients: set[WebSocket] = set()
last_seen: dict[str, float] = {}


def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def initialize():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS readings (
          id INTEGER PRIMARY KEY, device_id TEXT, value REAL, timestamp REAL,
          received_at REAL, source TEXT, sequence INTEGER);
        CREATE INDEX IF NOT EXISTS readings_time ON readings(received_at);
        CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, threshold REAL);
        INSERT OR IGNORE INTO settings VALUES (1, 70);
        CREATE TABLE IF NOT EXISTS alerts (
          id INTEGER PRIMARY KEY, reading_id INTEGER, value REAL, threshold REAL,
          timestamp REAL, device_id TEXT);
        ''')


class Reading(BaseModel):
    device_id: str = Field(default='arduino-01', min_length=1, max_length=64)
    value: float = Field(ge=0, le=150, allow_inf_nan=False)
    timestamp: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    sequence: int | None = Field(default=None, ge=0)


class Settings(BaseModel):
    threshold: float = Field(ge=20, le=140, allow_inf_nan=False)


def threshold():
    with connect() as db:
        return db.execute('SELECT threshold FROM settings WHERE id=1').fetchone()[0]


async def broadcast(event):
    for client in tuple(clients):
        try:
            await asyncio.wait_for(client.send_json(event), timeout=2)
        except Exception:
            clients.discard(client)


async def ingest(reading: Reading, source: str):
    now = time.time()
    limit = threshold()
    with connect() as db:
        cur = db.execute('INSERT INTO readings(device_id,value,timestamp,received_at,source,sequence) VALUES(?,?,?,?,?,?)',
                         (reading.device_id, reading.value, reading.timestamp or now, now, source, reading.sequence))
        row = dict(db.execute('SELECT * FROM readings WHERE id=?', (cur.lastrowid,)).fetchone())
        # Alert on crossing the threshold, not every sample in a sustained noisy period.
        previous = db.execute('SELECT value FROM readings WHERE device_id=? AND id<? ORDER BY id DESC LIMIT 1', (reading.device_id, cur.lastrowid)).fetchone()
        if reading.value > limit and (previous is None or previous[0] <= limit):
            db.execute('INSERT INTO alerts(reading_id,value,threshold,timestamp,device_id) VALUES(?,?,?,?,?)',
                       (cur.lastrowid, reading.value, limit, now, reading.device_id))
    last_seen[reading.device_id] = now
    await broadcast({'type': 'reading', 'data': row})
    return row


async def simulate():
    sequence = 0
    while True:
        value = round(max(25, min(100, 48 + 10 * math.sin(time.time() / 12) + random.gauss(0, 3) + (24 if random.random() < .08 else 0))), 1)
        await ingest(Reading(device_id='demo-sensor', value=value, sequence=sequence), 'simulator')
        sequence += 1
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app):
    initialize()
    task = asyncio.create_task(simulate()) if os.getenv('LOA_SIMULATOR', 'true').lower() == 'true' else None
    mqtt = None
    if MQTT_HOST:
        import paho.mqtt.client as mqtt_lib
        loop = asyncio.get_running_loop()
        mqtt = mqtt_lib.Client(mqtt_lib.CallbackAPIVersion.VERSION2)
        if os.getenv('MQTT_USERNAME'):
            mqtt.username_pw_set(os.environ['MQTT_USERNAME'], os.getenv('MQTT_PASSWORD'))
        def on_connect(client, userdata, flags, reason_code, properties):
            if reason_code == 0:
                client.subscribe('loa/+/noise', qos=1)
        def on_message(client, userdata, message):
            try:
                reading = Reading.model_validate_json(message.payload)
                asyncio.run_coroutine_threadsafe(ingest(reading, 'mqtt'), loop)
            except (ValueError, UnicodeDecodeError):
                import logging
                logging.getLogger('loa').warning('Rejected invalid MQTT reading on %s', message.topic)
        mqtt.on_connect = on_connect
        mqtt.on_message = on_message
        mqtt.connect_async(MQTT_HOST, int(os.getenv('MQTT_PORT', '1883')))
        mqtt.loop_start()
    yield
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    if mqtt:
        mqtt.disconnect()
        mqtt.loop_stop()


app = FastAPI(title='LOA · Listen, Observe, Alert', lifespan=lifespan)


@app.get('/api/status')
def status():
    now = time.time()
    return {'simulator': os.getenv('LOA_SIMULATOR', 'true').lower() == 'true',
            'mqtt_configured': bool(MQTT_HOST),
            'devices': [{'id': k, 'last_seen': v, 'online': now-v < 10} for k,v in last_seen.items()]}


@app.get('/api/readings')
def readings(limit: int = 300, minutes: int = 60):
    if not 1 <= limit <= 10000 or not 1 <= minutes <= 10080:
        raise HTTPException(422, 'Limit must be 1–10000 and minutes 1–10080')
    with connect() as db:
        rows = db.execute('SELECT * FROM readings WHERE received_at>=? ORDER BY id DESC LIMIT ?', (time.time()-minutes*60, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


@app.post('/api/readings', status_code=201)
async def post_reading(reading: Reading):
    return await ingest(reading, 'http')


@app.get('/api/settings')
def get_settings():
    return {'threshold': threshold()}


@app.put('/api/settings')
async def set_settings(settings: Settings):
    with connect() as db:
        db.execute('UPDATE settings SET threshold=? WHERE id=1', (settings.threshold,))
    await broadcast({'type': 'settings', 'data': settings.model_dump()})
    return settings


@app.get('/api/alerts')
def alerts():
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM alerts ORDER BY id DESC LIMIT 50')]


@app.websocket('/ws')
async def websocket(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


dist = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
if dist.exists():
    app.mount('/', StaticFiles(directory=dist, html=True), name='dashboard')
