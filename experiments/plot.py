"""PNG charts of experiment results for the report. Needs matplotlib (requirements-experiments.txt).

    # Traffic over time from a capture, like Wireshark's I/O graph
    python -m experiments.plot traffic experiments/captures/exp2-10hz.pcap --out experiments/results/exp2-traffic.png

    # Per-message RTT or one-way latency over time (CSVs from experiments.load --output / analyze --csv)
    python -m experiments.plot timeline experiments/results/exp1-mqtt.csv experiments/results/exp1-http.csv \
        --out experiments/results/exp1-rtt.png

    # Bar charts across runs; each JSON is saved output of load, analyze or pcap_summary
    python -m experiments.plot compare MQTT=results/exp1-mqtt-pcap.json HTTP=results/exp1-http-pcap.json \
        --out experiments/results/exp1-overhead.png
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

PORT_NAMES = {1883: 'MQTT (1883)', 8883: 'MQTT TLS (8883)', 8000: 'HTTP (8000)'}

# (key, title, unit) for each kind of saved JSON.
PCAP_METRICS = [('frame_bytes_per_message', 'Wire bytes per reading', 'bytes'),
                ('packets_per_message', 'Packets per reading', 'packets'),
                ('throughput_bps', 'Average throughput', 'bit/s'),
                ('header_overhead_ratio', 'Header overhead (share of wire bytes)', 'ratio')]
ANALYZE_METRICS = [('delivery_ratio', 'Delivery ratio', 'ratio'),
                   ('arrival_rate_hz', 'Achieved rate', 'readings/s'),
                   ('jitter_ms', 'Inter-arrival stdev (jitter)', 'ms'),
                   ('latency_p50', 'One-way latency p50', 'ms'),
                   ('latency_p95', 'One-way latency p95', 'ms')]
LOAD_METRICS = [('ack_ratio', 'Acknowledged ratio', 'ratio'),
                ('rtt_ms_p50', 'Round-trip time p50', 'ms'),
                ('rtt_ms_p95', 'Round-trip time p95', 'ms'),
                ('payload_bytes_per_message', 'Payload bytes per reading', 'bytes')]


def pyplot():
    try:
        import matplotlib
    except ImportError:
        sys.exit('matplotlib is missing: .venv/bin/python -m pip install -r requirements-experiments.txt')
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    return plt


def save(fig, out):
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    print(f'Wrote {out}')


def flatten(document, port=None):
    """Turn a saved load / analyze / pcap_summary JSON into {metric: value}."""
    if isinstance(document, dict) and 'ports' in document:
        ports = document['ports']
        if not ports:
            return {}
        key = str(port) if port is not None else max(ports, key=lambda p: ports[p].get('frame_bytes', 0))
        if key not in ports:
            raise ValueError(f'Port {port} is not in this capture summary')
        return {k: ports[key].get(k) for k, _, _ in PCAP_METRICS}
    if isinstance(document, list):  # analyze: one entry per boot_id; use the largest run.
        if not document:
            return {}
        run = max(document, key=lambda r: r.get('received', 0))
        latency, gaps = run.get('one_way_latency_ms') or {}, run.get('inter_arrival_ms') or {}
        return {'delivery_ratio': run.get('delivery_ratio'), 'arrival_rate_hz': run.get('arrival_rate_hz'),
                'jitter_ms': gaps.get('stdev'), 'latency_p50': latency.get('p50'), 'latency_p95': latency.get('p95')}
    if isinstance(document, dict) and 'rtt_ms_p50' in document:
        return {k: document.get(k) for k, _, _ in LOAD_METRICS}
    raise ValueError('Unrecognized JSON: expected output of experiments.load, analyze or pcap_summary')


def compare(runs, out, port=None):
    """runs: [(label, path)]. One bar chart per metric that any run reports."""
    plt = pyplot()
    values = {}
    for label, path in runs:
        with open(path) as handle:
            values[label] = flatten(json.load(handle), port)
    metrics = [m for m in PCAP_METRICS + ANALYZE_METRICS + LOAD_METRICS
               if any(v.get(m[0]) is not None for v in values.values())]
    if not metrics:
        sys.exit('None of the files contain plottable metrics.')
    columns = min(3, len(metrics))
    rows = (len(metrics) + columns - 1) // columns
    fig, axes = plt.subplots(rows, columns, figsize=(4.2 * columns, 3.2 * rows), squeeze=False)
    for ax, (key, title, unit) in zip(axes.flat, metrics):
        labels = [label for label in values if values[label].get(key) is not None]
        heights = [values[label][key] for label in labels]
        bars = ax.bar(labels, heights, color='#3b6ea8')
        ax.bar_label(bars, labels=[f'{h:.3g}' for h in heights], fontsize=8)
        ax.set_title(title, fontsize=10)
        ax.set_ylabel(unit)
        if unit == 'ratio':
            ax.set_ylim(0, 1.1)
        ax.grid(axis='y', alpha=0.3)
    for ax in list(axes.flat)[len(metrics):]:
        ax.axis('off')
    save(fig, out)
    return values


def read_timeline(path):
    """Return (label, kind, [(seconds, ms)], [failed seconds]) from a load or analyze CSV."""
    with open(path, newline='') as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f'{path} is empty')
    if 'rtt_ms' in rows[0]:
        start = float(rows[0]['sent_at'])
        points = [(float(r['sent_at']) - start, float(r['rtt_ms'])) for r in rows if r['rtt_ms'] and r['ok'] == '1']
        failed = [float(r['sent_at']) - start for r in rows if r['ok'] != '1']
        return f"{Path(path).stem} ({rows[0]['transport']})", 'Round-trip time', points, failed
    if 'latency_ms' in rows[0]:
        start = float(rows[0]['received_at'])
        points = [(float(r['received_at']) - start, float(r['latency_ms'])) for r in rows if r['latency_ms']]
        return Path(path).stem, 'One-way latency', points, []
    raise ValueError(f'{path}: expected a CSV from experiments.load --output or experiments.analyze --csv')


def timeline(paths, out):
    plt = pyplot()
    fig, ax = plt.subplots(figsize=(10, 4.5))
    kinds = set()
    for path in paths:
        label, kind, points, failed = read_timeline(path)
        kinds.add(kind)
        if points:
            ax.plot([p[0] for p in points], [p[1] for p in points], marker='.', markersize=3, linewidth=0.8, label=label)
        if failed:
            ax.scatter(failed, [0] * len(failed), marker='x', color='#c0392b', label=f'{label}: not acknowledged', zorder=3)
    ax.set_xlabel('Seconds from start of run')
    ax.set_ylabel(' / '.join(sorted(kinds)) + ' (ms)')
    ax.set_title('Per-message delivery time')
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    save(fig, out)


def traffic(capture, out, ports, bin_seconds):
    from experiments.pcap_summary import traffic_series
    plt = pyplot()
    with open(capture, 'rb') as handle:
        series = traffic_series(handle.read(), ports, bin_seconds)
    if not series:
        sys.exit('No TCP traffic on those ports in the capture.')
    fig, ax = plt.subplots(figsize=(10, 4.5))
    for port, points in sorted(series.items()):
        ax.step([p[0] for p in points], [p[1] * 8 / bin_seconds for p in points], where='post',
                label=PORT_NAMES.get(port, f'port {port}'))
    ax.set_xlabel('Seconds from start of capture')
    ax.set_ylabel('bit/s (all frame bytes, both directions)')
    ax.set_title(f'Traffic over time — {Path(capture).name}')
    ax.grid(alpha=0.3)
    ax.legend()
    save(fig, out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('traffic', help='Bit/s over time per port from a pcap')
    p.add_argument('capture')
    p.add_argument('--ports', default='1883,8883,8000')
    p.add_argument('--bin', type=float, default=1.0, help='Seconds per bar')
    p.add_argument('--out', required=True)
    p = sub.add_parser('timeline', help='Per-message RTT or latency over time from CSVs')
    p.add_argument('csv', nargs='+')
    p.add_argument('--out', required=True)
    p = sub.add_parser('compare', help='Bar charts across saved JSON results')
    p.add_argument('runs', nargs='+', metavar='LABEL=PATH')
    p.add_argument('--port', type=int, help='Capture port to use when a pcap summary has several')
    p.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'traffic':
            traffic(args.capture, args.out, tuple(int(x) for x in args.ports.split(',')), args.bin)
        elif args.command == 'timeline':
            timeline(args.csv, args.out)
        else:
            runs = []
            for item in args.runs:
                if '=' not in item:
                    parser.error(f'{item}: use LABEL=PATH')
                runs.append(tuple(item.split('=', 1)))
            compare(runs, args.out, args.port)
    except (ValueError, OSError) as error:
        sys.exit(str(error))


if __name__ == '__main__':
    main()
