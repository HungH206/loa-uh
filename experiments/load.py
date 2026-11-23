"""Synthetic LOA sensor for networking experiments (MQTT vs HTTP, send rate, QoS).

Sends canonical LOA payloads at a fixed rate from this computer, using a provisioned
device's credentials, and records per-message delivery and round-trip times.

    python -m experiments.load --credentials .local/loa-001.json --transport mqtt --rate 5 --duration 60

Round-trip times need no clock synchronization: HTTP RTT is request to 201 response;
MQTT QoS 1 RTT is PUBLISH to PUBACK. MQTT QoS 0 has no acknowledgment, so its
"ack" column only means the bytes were handed to the socket.
Every run uses a fresh boot_id, so `python -m experiments.analyze --boot-id ...`
can isolate it in the backend database afterwards.
"""
import argparse
import csv
import json
import os
import random
import secrets
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field

SYNTHETIC_DBFS = -70.0  # Quiet enough not to trigger the default -40 dBFS alerts.


@dataclass
class Result:
    sequence: int
    sent_at: float
    acked_at: float | None = None
    ok: bool = False
    status: str = ''

    @property
    def rtt_ms(self):
        return None if self.acked_at is None else (self.acked_at - self.sent_at) * 1000


@dataclass
class Run:
    transport: str
    rate: float
    boot_id: str
    payload_bytes: int = 0
    results: list = field(default_factory=list)


def payload(device_id, boot_id, sequence, started):
    now = time.time()
    body = {'device_id': device_id, 'boot_id': boot_id, 'sequence': sequence,
            'dbfs': round(SYNTHETIC_DBFS + random.uniform(-1, 1), 2), 'rms': 10.37,
            'uptime_ms': int((now - started) * 1000), 'timestamp': round(now, 3)}
    return json.dumps(body, separators=(',', ':'))


def paced(rate, duration, sleep=time.sleep, clock=time.monotonic):
    """Yield sequence numbers on an absolute schedule so slow sends do not cause drift."""
    interval, start, count = 1 / rate, clock(), int(round(rate * duration))
    for sequence in range(count):
        delay = start + sequence * interval - clock()
        if delay > 0:
            sleep(delay)
        yield sequence


def run_http(client, device_id, token, rate, duration, boot_id, path='/api/readings', sleep=time.sleep):
    """client is an httpx.Client (or FastAPI TestClient) whose base_url is the backend."""
    run, started = Run('http', rate, boot_id), time.time()
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    for sequence in paced(rate, duration, sleep):
        body = payload(device_id, boot_id, sequence, started)
        run.payload_bytes += len(body)
        result = Result(sequence, time.time())
        try:
            response = client.post(path, content=body, headers=headers)
            result.acked_at, result.status = time.time(), str(response.status_code)
            result.ok = response.status_code == 201
        except Exception as error:  # Record failures; the experiment keeps sending.
            result.status = type(error).__name__
        run.results.append(result)
    return run


def run_mqtt(host, port, device_id, token, rate, duration, boot_id, qos, tls=False):
    import paho.mqtt.client as mqtt
    run, started = Run(f'mqtt-qos{qos}', rate, boot_id), time.time()
    pending, lock, connected = {}, threading.Lock(), threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'{device_id}-bench-{boot_id[:6]}')
    client.username_pw_set(device_id, token)
    if tls:
        client.tls_set()

    def on_connect(c, userdata, flags, reason_code, properties):
        if reason_code == 0:
            connected.set()
        else:
            print(f'MQTT connect refused: {reason_code}', file=sys.stderr)

    def on_publish(c, userdata, mid, reason_code, properties):
        # QoS 1: fires on PUBACK. QoS 0: fires once written to the socket.
        with lock:
            result = pending.pop(mid, None)
        if result:
            result.acked_at, result.ok, result.status = time.time(), True, 'puback' if qos else 'written'

    client.on_connect, client.on_publish = on_connect, on_publish
    client.connect(host, port, keepalive=15)
    client.loop_start()
    try:
        if not connected.wait(10):
            raise SystemExit('MQTT broker did not accept the connection within 10 s')
        topic = f'loa/{device_id}/noise'
        for sequence in paced(rate, duration):
            body = payload(device_id, boot_id, sequence, started)
            run.payload_bytes += len(body)
            result = Result(sequence, time.time())
            with lock:
                info = client.publish(topic, body, qos=qos)
                if info.rc == mqtt.MQTT_ERR_SUCCESS:
                    pending[info.mid] = result
                else:
                    result.status = f'rc{info.rc}'
            run.results.append(result)
        time.sleep(2)  # Allow outstanding PUBACKs to arrive.
    finally:
        client.disconnect()
        client.loop_stop()
    return run


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * p / 100
    low, high = int(index), min(int(index) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def summarize(run):
    results = run.results
    rtts = [r.rtt_ms for r in results if r.ok and r.rtt_ms is not None]
    span = results[-1].sent_at - results[0].sent_at if len(results) > 1 else 0
    return {
        'transport': run.transport, 'target_rate_hz': run.rate, 'boot_id': run.boot_id,
        'sent': len(results), 'acknowledged': sum(r.ok for r in results),
        'ack_ratio': round(sum(r.ok for r in results) / len(results), 4) if results else None,
        'achieved_rate_hz': round((len(results) - 1) / span, 3) if span else None,
        'payload_bytes_total': run.payload_bytes,
        'payload_bytes_per_message': round(run.payload_bytes / len(results), 1) if results else None,
        'rtt_ms_mean': round(statistics.fmean(rtts), 2) if rtts else None,
        'rtt_ms_p50': round(percentile(rtts, 50), 2) if rtts else None,
        'rtt_ms_p95': round(percentile(rtts, 95), 2) if rtts else None,
        'rtt_ms_max': round(max(rtts), 2) if rtts else None,
        'rtt_meaning': {'http': 'request to HTTP 201', 'mqtt-qos1': 'PUBLISH to PUBACK',
                        'mqtt-qos0': 'not an acknowledgment (socket write only)'}[run.transport],
    }


def write_csv(run, path):
    with open(path, 'w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['transport', 'boot_id', 'sequence', 'sent_at', 'acked_at', 'rtt_ms', 'ok', 'status'])
        for r in run.results:
            writer.writerow([run.transport, run.boot_id, r.sequence, f'{r.sent_at:.6f}',
                             '' if r.acked_at is None else f'{r.acked_at:.6f}',
                             '' if r.rtt_ms is None else f'{r.rtt_ms:.3f}', int(r.ok), r.status])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--credentials', required=True, help='JSON from scripts/provision_device.py')
    parser.add_argument('--transport', choices=['mqtt', 'http'], required=True)
    parser.add_argument('--rate', type=float, default=1, help='Messages per second (e.g. 1, 5, 10)')
    parser.add_argument('--duration', type=float, default=60, help='Seconds to send')
    parser.add_argument('--qos', type=int, choices=[0, 1], default=0, help='MQTT QoS (Experiment 4)')
    parser.add_argument('--mqtt-host', default=os.getenv('MQTT_HOST') or '127.0.0.1')
    parser.add_argument('--mqtt-port', type=int, default=int(os.getenv('MQTT_PORT', '1883')))
    parser.add_argument('--mqtt-tls', action='store_true')
    parser.add_argument('--http-url', default='http://127.0.0.1:8000', help='Backend base URL')
    parser.add_argument('--http-new-connection', action='store_true',
                        help='Open a new TCP connection per request instead of keep-alive')
    parser.add_argument('--output', help='Per-message CSV path (e.g. experiments/results/exp1-mqtt.csv)')
    args = parser.parse_args(argv)
    if not 0 < args.rate <= 100 or args.duration <= 0:
        parser.error('rate must be in (0, 100] and duration positive')
    with open(args.credentials) as handle:
        credentials = json.load(handle)
    device_id, token = credentials['device_id'], credentials['device_token']
    boot_id = 'exp' + secrets.token_hex(6)
    print(f'Run boot_id={boot_id}: {args.transport} at {args.rate:g} Hz for {args.duration:g} s', file=sys.stderr)
    if args.transport == 'http':
        import httpx
        limits = httpx.Limits(max_keepalive_connections=0) if args.http_new_connection else httpx.Limits()
        with httpx.Client(base_url=args.http_url, timeout=5, limits=limits) as client:
            run = run_http(client, device_id, token, args.rate, args.duration, boot_id)
    else:
        run = run_mqtt(args.mqtt_host, args.mqtt_port, device_id, token, args.rate, args.duration,
                       boot_id, args.qos, args.mqtt_tls)
    if args.output:
        os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
        write_csv(run, args.output)
    print(json.dumps(summarize(run), indent=2))


if __name__ == '__main__':
    main()
