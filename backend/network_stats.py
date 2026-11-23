"""Connectivity statistics for the dashboard's Network tab.

Per-sensor figures are computed from stored readings, so they survive backend restarts and
only ever cover the signed-in user's own devices. Broker figures come from Mosquitto's
$SYS/broker topics and backend<->broker link events are kept in memory (single worker).
"""
import collections
import time

ONLINE_SECONDS = 10      # Same rule as /api/status.
RATE_SECONDS = 10        # Readings per second is averaged over this window.
DELIVERY_SECONDS = 300   # Delivery ratio and latency use the last 5 minutes.
SERIES_SECONDS = 120     # Readings-per-second chart length.
EVENT_SECONDS = 900      # Outages and restarts from the last 15 minutes.
GAP_SECONDS = 3.0        # A longer silence between readings counts as an outage.

SYS_TOPICS = {
    '$SYS/broker/clients/connected': 'clients_connected',
    '$SYS/broker/load/messages/received/1min': 'messages_in_per_min',
    '$SYS/broker/load/messages/sent/1min': 'messages_out_per_min',
    '$SYS/broker/load/bytes/received/1min': 'bytes_in_per_min',
    '$SYS/broker/load/bytes/sent/1min': 'bytes_out_per_min',
    '$SYS/broker/uptime': 'uptime',
    '$SYS/broker/version': 'version',
}


class BrokerLink:
    """What the backend knows about its MQTT broker. Callbacks run on paho's thread."""

    def __init__(self):
        self.stats, self.stats_at, self.connected_since = {}, None, None
        self.events = collections.deque(maxlen=30)

    def event(self, kind, detail='', now=None):
        self.events.appendleft({'time': now or time.time(), 'kind': kind, 'detail': detail})

    def connected(self, now=None):
        self.connected_since = now or time.time()
        self.event('broker_connected', 'Backend subscribed to sensor topics', now)

    def disconnected(self, reason, now=None):
        if self.connected_since is not None:
            self.event('broker_disconnected', str(reason), now)
        self.connected_since = None

    def sys_message(self, topic, payload, now=None):
        field = SYS_TOPICS.get(topic)
        if not field:
            return
        text = payload.decode(errors='replace') if isinstance(payload, bytes) else str(payload)
        try:
            value = float(text.split()[0]) if field not in ('uptime', 'version') else text
        except (ValueError, IndexError):
            value = text
        self.stats[field] = value
        self.stats_at = now or time.time()


def percentile(values, p):
    ordered = sorted(values)
    index = (len(ordered) - 1) * p / 100
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def device_summary(device, rows, now):
    """rows: this device's readings from the last EVENT_SECONDS, ordered by received_at."""
    last_seen = device['last_seen']
    summary = {'device_id': device['device_id'], 'label': device['label'], 'last_seen': last_seen,
               'last_source': device['last_source'],
               'online': last_seen is not None and now - last_seen < ONLINE_SECONDS,
               'seconds_since_last': round(now - last_seen, 1) if last_seen is not None else None}
    summary['rate_hz'] = round(sum(r['received_at'] >= now - RATE_SECONDS for r in rows) / RATE_SECONDS, 2)

    counts = [0] * SERIES_SECONDS
    start = int(now) - SERIES_SECONDS + 1
    for r in rows:
        slot = int(r['received_at']) - start
        if 0 <= slot < SERIES_SECONDS:
            counts[slot] += 1
    summary['series'] = [{'t': start + i, 'count': c} for i, c in enumerate(counts)]

    recent = [r for r in rows if r['received_at'] >= now - DELIVERY_SECONDS]
    boots = {}
    for r in recent:
        if r['sequence'] is not None:
            boots.setdefault(r['boot_id'], set()).add(r['sequence'])
    expected = sum(max(s) - min(s) + 1 for s in boots.values())
    received = sum(len(s) for s in boots.values())
    summary['delivery'] = {'window_seconds': DELIVERY_SECONDS, 'received': received, 'expected': expected,
                           'lost': expected - received,
                           'ratio': round(received / expected, 4) if expected else None}
    summary['packets'] = packet_summary(recent)

    latencies = [(r['received_at'] - r['timestamp']) * 1000 for r in recent if r['timestamp'] is not None]
    summary['latency_ms'] = None if not latencies else {
        'n': len(latencies), 'p50': round(percentile(latencies, 50), 1),
        'p95': round(percentile(latencies, 95), 1),
        # Millisecond rounding of send times gives tiny negatives; only real clock skew should warn.
        'clock_warning': min(latencies) < -5}

    events = []
    for previous, current in zip(rows, rows[1:]):
        gap = current['received_at'] - previous['received_at']
        if current['boot_id'] != previous['boot_id']:
            events.append({'time': current['received_at'], 'kind': 'sensor_restarted',
                           'detail': f"New boot {current['boot_id'] or 'unknown'} after {gap:.1f} s"})
        elif gap > GAP_SECONDS:
            lost = None
            if previous['sequence'] is not None and current['sequence'] is not None:
                lost = max(0, current['sequence'] - previous['sequence'] - 1)
            detail = f'Silent for {gap:.1f} s' + ('' if lost is None else f', {lost} reading(s) lost')
            events.append({'time': current['received_at'], 'kind': 'sensor_recovered', 'detail': detail,
                           'outage_seconds': round(gap, 1), 'lost': lost})
        if current['source'] != previous['source']:
            events.append({'time': current['received_at'], 'kind': 'transport_changed',
                           'detail': f"{previous['source']} → {current['source']}"})
    if rows and now - rows[-1]['received_at'] > GAP_SECONDS:
        events.append({'time': rows[-1]['received_at'], 'kind': 'sensor_silent',
                       'detail': f"No readings for {now - rows[-1]['received_at']:.0f} s (ongoing)"})
    summary['events'] = sorted(events, key=lambda e: e['time'], reverse=True)[:20]
    if rows:
        summary['boot_id'], summary['last_sequence'] = rows[-1]['boot_id'], rows[-1]['sequence']
    return summary


def packet_summary(rows):
    """Application message gaps, separated by boot; late arrivals fill earlier gaps.

    Only MQTT/HTTP sensor messages with a boot ID and sequence are eligible.
    Missing prefixes and trailing silence cannot be inferred from this sample.
    """
    eligible = [r for r in rows if r['source'] in ('mqtt', 'http')
                and r['boot_id'] and r['sequence'] is not None]
    boots = {}
    for r in eligible:
        boots.setdefault(r['boot_id'], set()).add(r['sequence'])
    gaps = []
    for boot, sequences in boots.items():
        ordered = sorted(sequences)
        for left, right in zip(ordered, ordered[1:]):
            if right > left + 1:
                gaps.append({'boot_id': boot, 'first': left + 1,
                             'last': right - 1, 'count': right - left - 1})
    received = sum(len(s) for s in boots.values())
    missing = sum(g['count'] for g in gaps)
    expected = received + missing
    return {'window_seconds': DELIVERY_SECONDS, 'received': received, 'missing': missing,
            'expected': expected, 'loss_percent': round(missing / expected * 100, 2) if expected else None,
            'gaps': gaps[-100:], 'gap_count': len(gaps),
            'recent': [{'received_at': r['received_at'], 'boot_id': r['boot_id'],
                        'sequence': r['sequence'], 'source': r['source']} for r in eligible[-100:]][::-1]}


def network_summary(db, user, link, mqtt_configured, mqtt_connected, mqtt_endpoint, now=None):
    now = now or time.time()
    devices = db.execute('''SELECT device_id,label,last_seen,last_source,claimed_at FROM devices
        WHERE owner_id=? ORDER BY device_id''', (user,)).fetchall()
    result = []
    for device in devices:
        rows = db.execute('''SELECT received_at,sequence,boot_id,timestamp,source FROM sound_readings
            WHERE device_id=? AND received_at>=? AND received_at>=? ORDER BY received_at,id''',
            (device['device_id'], now - EVENT_SECONDS, device['claimed_at'] or 0)).fetchall()
        result.append(device_summary(device, rows, now))
    fresh = link.stats_at is not None and now - link.stats_at < 30
    return {
        'server_time': now,
        'backend': {'mqtt_configured': mqtt_configured, 'mqtt_connected': mqtt_connected,
                    'mqtt_endpoint': mqtt_endpoint, 'connected_since': link.connected_since},
        'broker': {'available': fresh, 'updated_at': link.stats_at, **(link.stats if fresh else {})},
        'link_events': list(link.events)[:20],
        'devices': result,
    }
