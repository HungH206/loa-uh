"""Live MQTT monitor for the LOA broker: sensor message rates, losses, outages, broker load.

On the Pi (uses the hub's existing virtualenv and broker account; only paho-mqtt is needed):

    sudo /opt/loa/.venv/bin/python monitor.py --env-file /etc/loa/loa.env

On the laptop, from the repository root:

    .venv/bin/python -m experiments.monitor --env-file .env --log experiments/results/monitor.csv

It subscribes read-only to loa/+/noise and the broker's $SYS/broker/# statistics with its own
client ID, so it never replaces the backend's MQTT session. $SYS statistics need
`topic read $SYS/#` for the account in the broker ACL (see docs/EXPERIMENTS.md); without it the
sensor section still works. Mosquitto updates $SYS every 10 seconds by default.
This file is self-contained so it can be copied to the Pi on its own.
"""
import argparse
import csv
import json
import os
import secrets
import sys
import threading
import time

SYS_FIELDS = {
    '$SYS/broker/clients/connected': 'clients_connected',
    '$SYS/broker/load/messages/received/1min': 'msgs_in_per_min',
    '$SYS/broker/load/messages/sent/1min': 'msgs_out_per_min',
    '$SYS/broker/load/bytes/received/1min': 'bytes_in_per_min',
    '$SYS/broker/load/bytes/sent/1min': 'bytes_out_per_min',
    '$SYS/broker/uptime': 'uptime',
    '$SYS/broker/version': 'version',
}


def read_env_file(path):
    """Minimal KEY=VALUE reader (comments, optional quotes) so python-dotenv is not required."""
    values = {}
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
                value = value[1:-1]
            values[key.strip().removeprefix('export ')] = value
    return values


class Device:
    def __init__(self, now):
        self.first_seen = self.last_seen = now
        self.received = self.lost = self.bytes = self.window_count = 0
        self.boot_id = self.sequence = self.dbfs = None
        self.silent = False


class Monitor:
    """Pure state machine: feed messages and clock ticks, get events and a text view."""

    def __init__(self, gap_seconds=3.0):
        self.gap_seconds = gap_seconds
        self.devices = {}
        self.broker = {}
        self.connected = False
        self.window_started = None

    def reading(self, topic, payload, now):
        events = []
        parts = topic.split('/')
        if len(parts) != 3 or parts[0] != 'loa' or parts[2] != 'noise':
            return events
        try:
            data = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return [('invalid_payload', parts[1], 'payload is not JSON')]
        device_id = parts[1]
        device = self.devices.get(device_id)
        if device is None:
            device = self.devices[device_id] = Device(now)
            events.append(('new_device', device_id, f"boot {data.get('boot_id')}"))
        elif device.silent:
            events.append(('recovered', device_id, f'silent for {now - device.last_seen:.1f} s'))
        device.silent = False
        boot, sequence = data.get('boot_id'), data.get('sequence')
        rebooted = device.boot_id is not None and boot != device.boot_id
        if rebooted:
            events.append(('reboot', device_id, f'boot {device.boot_id} -> {boot}'))
        elif isinstance(sequence, int) and isinstance(device.sequence, int):
            missing = sequence - device.sequence - 1
            if missing > 0:
                device.lost += missing
                events.append(('lost', device_id, f'{missing} reading(s) missing before sequence {sequence}'))
            elif missing < 0:
                events.append(('out_of_order', device_id, f'sequence {sequence} after {device.sequence}'))
        device.boot_id = boot
        if isinstance(sequence, int) and (rebooted or device.sequence is None or sequence > device.sequence):
            device.sequence = sequence
        device.dbfs = data.get('dbfs')
        device.received += 1
        device.window_count += 1
        device.bytes += len(payload)
        device.last_seen = now
        return events

    def sys(self, topic, payload):
        field = SYS_FIELDS.get(topic)
        if field:
            self.broker[field] = payload.decode(errors='replace') if isinstance(payload, bytes) else payload

    def tick(self, now):
        events = []
        for device_id, device in self.devices.items():
            if not device.silent and now - device.last_seen > self.gap_seconds:
                device.silent = True
                events.append(('silent', device_id, f'no reading for {now - device.last_seen:.1f} s'))
        return events

    def render(self, now, wall=None):
        elapsed = (now - self.window_started) if self.window_started is not None else 0
        self.window_started = now
        lines = [f"LOA MQTT monitor  {time.strftime('%H:%M:%S', time.localtime(wall or time.time()))}  "
                 f"broker {'connected' if self.connected else 'DISCONNECTED'}"]
        if self.broker:
            b = self.broker
            lines.append(f"Broker: clients {b.get('clients_connected', '?')}  "
                         f"msgs in/out per min {b.get('msgs_in_per_min', '?')}/{b.get('msgs_out_per_min', '?')}  "
                         f"bytes in/out per min {b.get('bytes_in_per_min', '?')}/{b.get('bytes_out_per_min', '?')}  "
                         f"uptime {b.get('uptime', '?')}")
        else:
            lines.append('Broker: no $SYS statistics (ACL needs `topic read $SYS/#`, or wait 10 s)')
        lines.append(f"{'device':<20}{'state':<9}{'rate/s':>8}{'received':>10}{'lost':>7}{'deliv%':>8}"
                     f"{'last seen':>11}{'dBFS':>8}{'B/msg':>7}")
        for device_id, d in sorted(self.devices.items()):
            rate = d.window_count / elapsed if elapsed else 0
            d.window_count = 0
            delivered = 100 * d.received / (d.received + d.lost) if d.received + d.lost else 100
            dbfs = f'{d.dbfs:.1f}' if isinstance(d.dbfs, (int, float)) else '-'
            lines.append(f"{device_id[:19]:<20}{'SILENT' if d.silent else 'live':<9}{rate:>8.2f}{d.received:>10}"
                         f"{d.lost:>7}{delivered:>8.1f}{now - d.last_seen:>10.1f}s{dbfs:>8}{d.bytes / d.received:>7.0f}")
        if not self.devices:
            lines.append('(no sensor messages yet)')
        return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--env-file', help='Reads MQTT_HOST/PORT/USERNAME/PASSWORD/TLS from this file')
    parser.add_argument('--host')
    parser.add_argument('--port', type=int)
    parser.add_argument('--interval', type=float, default=2, help='Seconds between status refreshes')
    parser.add_argument('--gap', type=float, default=3, help='Seconds without readings before a device is SILENT')
    parser.add_argument('--log', help='Append events to this CSV')
    parser.add_argument('--no-clear', action='store_true', help='Scroll instead of redrawing the screen')
    args = parser.parse_args(argv)
    env = dict(os.environ)
    if args.env_file:
        env.update(read_env_file(args.env_file))
    host = args.host or env.get('MQTT_HOST') or '127.0.0.1'
    port = args.port or int(env.get('MQTT_PORT') or 1883)

    import paho.mqtt.client as mqtt
    monitor, lock = Monitor(args.gap), threading.Lock()
    log_file = open(args.log, 'a', newline='') if args.log else None
    log = csv.writer(log_file) if log_file else None
    if log_file and log_file.tell() == 0:
        log.writerow(['time', 'event', 'device', 'detail'])
    clear = sys.stdout.isatty() and not args.no_clear
    recent = []

    def emit(events):
        for kind, device, detail in events:
            stamp = time.strftime('%H:%M:%S')
            recent.append(f'{stamp} {kind:<13} {device}: {detail}')
            del recent[:-10]
            if not clear:
                print(recent[-1], flush=True)
            if log:
                log.writerow([f'{time.time():.3f}', kind, device, detail])
                log_file.flush()

    # A unique client ID: reusing 'loa-backend' would disconnect the backend.
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'loa-monitor-{secrets.token_hex(3)}')
    if env.get('MQTT_USERNAME'):
        client.username_pw_set(env['MQTT_USERNAME'], env.get('MQTT_PASSWORD'))
    if (env.get('MQTT_TLS') or 'false').lower() == 'true':
        client.tls_set(ca_certs=env.get('MQTT_CA_FILE') or None)

    def on_connect(c, userdata, flags, reason_code, properties):
        with lock:
            monitor.connected = reason_code == 0
            emit([('broker_up' if reason_code == 0 else 'broker_refused', host, str(reason_code))])
        if reason_code == 0:
            c.subscribe([('loa/+/noise', 0), ('$SYS/broker/#', 0)])

    def on_disconnect(c, userdata, flags, reason_code, properties):
        with lock:
            monitor.connected = False
            emit([('broker_down', host, str(reason_code))])

    def on_message(c, userdata, message):
        with lock:
            if message.topic.startswith('$SYS/'):
                monitor.sys(message.topic, message.payload)
            else:
                emit(monitor.reading(message.topic, message.payload, time.monotonic()))

    client.on_connect, client.on_disconnect, client.on_message = on_connect, on_disconnect, on_message
    client.reconnect_delay_set(1, 10)
    client.connect_async(host, port, keepalive=15)
    client.loop_start()
    try:
        while True:
            time.sleep(args.interval)
            with lock:
                now = time.monotonic()
                emit(monitor.tick(now))
                view = monitor.render(now)
            if clear:
                print('\033[2J\033[H' + view + '\n\nRecent events:\n' + ('\n'.join(recent) or '(none)'), flush=True)
            else:
                print(view + '\n', flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
        client.loop_stop()
        if log_file:
            log_file.close()


if __name__ == '__main__':
    main()
