"""Delivery, throughput, latency and outage analysis of readings stored by the backend.

    python -m experiments.analyze --device loa-001 --minutes 10
    python -m experiments.analyze --device loa-001 --boot-id exp1a2b3c4d5e6

Delivery uses sequence numbers: within one boot_id the sensor numbers every reading,
including ones it could not transmit, so missing numbers are lost readings.
Latency is received_at (backend clock) minus the sensor's timestamp (sensor clock); it is
only meaningful when both clocks are NTP-synchronized, or the sender runs on the backend host.
"""
import argparse
import csv
import json
import os
import sqlite3
import statistics
import sys
import time

from experiments.load import percentile


def load_rows(db_path, device_id, boot_id=None, since=None, until=None):
    db = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    query = 'SELECT * FROM sound_readings WHERE device_id=?'
    params = [device_id]
    for clause, value in (('boot_id=?', boot_id), ('received_at>=?', since), ('received_at<=?', until)):
        if value is not None:
            query += f' AND {clause}'
            params.append(value)
    rows = [dict(r) for r in db.execute(query + ' ORDER BY received_at, id', params)]
    db.close()
    return rows


def stats(values, digits=2):
    if not values:
        return None
    return {'n': len(values), 'mean': round(statistics.fmean(values), digits),
            'stdev': round(statistics.stdev(values), digits) if len(values) > 1 else 0.0,
            'min': round(min(values), digits), 'p50': round(percentile(values, 50), digits),
            'p95': round(percentile(values, 95), digits), 'max': round(max(values), digits)}


def analyze_boot(rows, gap_seconds):
    """rows: readings of one device and one boot_id, ordered by received_at."""
    arrivals = [r['received_at'] for r in rows]
    sequences = [r['sequence'] for r in rows if r['sequence'] is not None]
    span = arrivals[-1] - arrivals[0] if len(arrivals) > 1 else 0
    result = {'boot_id': rows[0]['boot_id'], 'transport': sorted({r['source'] for r in rows}),
              'received': len(rows), 'first_received_at': arrivals[0], 'last_received_at': arrivals[-1],
              'duration_s': round(span, 3), 'arrival_rate_hz': round((len(rows) - 1) / span, 3) if span else None}
    if sequences:
        expected = max(sequences) - min(sequences) + 1
        result.update({'sequence_first': min(sequences), 'sequence_last': max(sequences),
                       'expected': expected, 'lost': expected - len(set(sequences)),
                       'delivery_ratio': round(len(set(sequences)) / expected, 4),
                       'out_of_order': sum(b < a for a, b in zip(sequences, sequences[1:]))})
    gaps_ms = [(b - a) * 1000 for a, b in zip(arrivals, arrivals[1:])]
    result['inter_arrival_ms'] = stats(gaps_ms)
    latencies = [(r['received_at'] - r['timestamp']) * 1000 for r in rows if r['timestamp'] is not None]
    result['one_way_latency_ms'] = stats(latencies)
    if latencies and min(latencies) < 0:
        result['latency_warning'] = 'Negative latency: sensor and backend clocks are not synchronized.'
    # Outages: arrival gaps longer than gap_seconds (Experiment 3).
    outages = []
    for previous, current in zip(rows, rows[1:]):
        gap = current['received_at'] - previous['received_at']
        if gap > gap_seconds:
            lost = None
            if previous['sequence'] is not None and current['sequence'] is not None:
                lost = current['sequence'] - previous['sequence'] - 1
            outages.append({'last_before': round(previous['received_at'], 3),
                            'first_after': round(current['received_at'], 3),
                            'recovery_s': round(gap, 3), 'readings_lost': lost})
    result['outages'] = outages
    return result


def analyze(rows, gap_seconds=2.0):
    boots = {}  # Insertion-ordered: one entry per sensor boot / load-generator run.
    for row in rows:
        boots.setdefault(row['boot_id'], []).append(row)
    return [analyze_boot(group, gap_seconds) for group in boots.values()]


def write_csv(rows, path):
    with open(path, 'w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['received_at', 'boot_id', 'sequence', 'source', 'timestamp', 'latency_ms', 'dbfs'])
        for r in rows:
            latency = '' if r['timestamp'] is None else f"{(r['received_at'] - r['timestamp']) * 1000:.3f}"
            writer.writerow([f"{r['received_at']:.6f}", r['boot_id'], r['sequence'], r['source'],
                             r['timestamp'] or '', latency, r['dbfs']])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--db', default=os.getenv('LOA_DB', 'loa.sqlite3'))
    parser.add_argument('--device', required=True)
    parser.add_argument('--boot-id', help='Restrict to one run (load generator prints it)')
    parser.add_argument('--minutes', type=float, help='Only readings received in the last N minutes')
    parser.add_argument('--since', type=float, help='Unix seconds (inclusive)')
    parser.add_argument('--until', type=float, help='Unix seconds (inclusive)')
    parser.add_argument('--gap', type=float, default=2.0, help='Arrival gap in seconds counted as an outage')
    parser.add_argument('--csv', help='Write per-reading latency CSV here')
    args = parser.parse_args(argv)
    since = time.time() - args.minutes * 60 if args.minutes else args.since
    rows = load_rows(args.db, args.device, args.boot_id, since, args.until)
    if not rows:
        sys.exit('No matching readings. Check --db, --device and the time range.')
    if args.csv:
        os.makedirs(os.path.dirname(args.csv) or '.', exist_ok=True)
        write_csv(rows, args.csv)
    print(json.dumps(analyze(rows, args.gap), indent=2))


if __name__ == '__main__':
    main()
