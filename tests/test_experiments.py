import json
import struct

import pytest

from experiments import analyze, load, monitor, pcap_summary, plot
from tests.test_api import pair, setup  # noqa: F401  (fixture reuse)


def row(received, sequence, boot='b1', timestamp=None, source='mqtt'):
    return {'received_at': received, 'sequence': sequence, 'boot_id': boot,
            'timestamp': timestamp, 'source': source, 'dbfs': -50}


def test_delivery_latency_and_outages():
    rows = [row(100.0, 0, timestamp=99.99), row(101.0, 1, timestamp=100.98),
            row(102.0, 2, timestamp=101.97),
            # Outage: sequences 3–7 lost, recovery after 6 s.
            row(108.0, 8, timestamp=107.99), row(109.0, 9, timestamp=108.99),
            row(200.0, 0, boot='b2')]
    first, second = analyze.analyze(rows, gap_seconds=2.0)
    assert first['received'] == 5 and first['expected'] == 10 and first['lost'] == 5
    assert first['delivery_ratio'] == 0.5
    assert first['outages'] == [{'last_before': 102.0, 'first_after': 108.0, 'recovery_s': 6.0, 'readings_lost': 5}]
    assert first['one_way_latency_ms']['min'] == pytest.approx(10.0)
    assert first['one_way_latency_ms']['max'] == pytest.approx(30.0)
    assert 'latency_warning' not in first
    assert second['boot_id'] == 'b2' and second['one_way_latency_ms'] is None and second['outages'] == []
    skewed = analyze.analyze([row(100.0, 0, timestamp=100.5), row(101.0, 1, timestamp=101.5)])[0]
    assert 'latency_warning' in skewed


def test_pacing_uses_absolute_schedule():
    clock = {'now': 0.0}
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds)
        clock['now'] += seconds
    sequences = []
    for sequence in load.paced(5, 1, sleep=sleep, clock=lambda: clock['now']):
        sequences.append(sequence)
        clock['now'] += 0.05  # Each send takes 50 ms; schedule must not drift.
    assert sequences == [0, 1, 2, 3, 4]
    assert sleeps == pytest.approx([0.15, 0.15, 0.15, 0.15])


def test_http_load_reaches_backend_and_is_analyzable(setup, tmp_path):  # noqa: F811
    main, client = setup
    assert pair(client).status_code == 200
    run = load.run_http(client, 'sensor-a', 'device-secret', rate=50, duration=0.2, boot_id='expabc')
    summary = load.summarize(run)
    assert summary['sent'] == 10 and summary['acknowledged'] == 10 and summary['ack_ratio'] == 1
    assert summary['payload_bytes_per_message'] > 50 and summary['rtt_ms_p50'] is not None
    bad = load.run_http(client, 'sensor-a', 'wrong', rate=50, duration=0.04, boot_id='expbad')
    assert [r.status for r in bad.results] == ['401', '401']
    rows = analyze.load_rows(main.DB_PATH, 'sensor-a', boot_id='expabc')
    result = analyze.analyze(rows)[0]
    assert result['received'] == 10 and result['delivery_ratio'] == 1 and result['transport'] == ['http']
    assert result['one_way_latency_ms']['min'] >= 0
    analyze.write_csv(rows, tmp_path / 'latency.csv')
    assert len((tmp_path / 'latency.csv').read_text().splitlines()) == 11


def tcp_frame(sport, dport, seq, flags, payload=b''):
    tcp = struct.pack('!HHIIBBHHH', sport, dport, seq, 0, 5 << 4, flags, 65535, 0, 0) + payload
    ip = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 20 + len(tcp), 0, 0, 64, 6, 0,
                     bytes([192, 168, 1, 20]), bytes([192, 168, 1, 10])) + tcp
    return b'\xaa' * 6 + b'\xbb' * 6 + b'\x08\x00' + ip


def pcap(frames):
    data = struct.pack('<IHHiIII', 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    for i, frame in enumerate(frames):
        data += struct.pack('<IIII', 1000 + i, 0, len(frame), len(frame)) + frame
    return data


def test_pcap_summary_counts_handshakes_payload_and_retransmissions():
    frames = [tcp_frame(50000, 1883, 1, 0x02),            # SYN
              tcp_frame(1883, 50000, 9, 0x12),            # SYN-ACK
              tcp_frame(50000, 1883, 2, 0x10),            # ACK
              tcp_frame(50000, 1883, 2, 0x18, b'x' * 100),  # PUBLISH
              tcp_frame(50000, 1883, 2, 0x18, b'x' * 100),  # Retransmission
              tcp_frame(50001, 8000, 1, 0x02),            # Other port
              tcp_frame(50002, 22, 1, 0x02)]              # Ignored (SSH)
    report = pcap_summary.summarize(pcap(frames), messages=1)
    mqtt = report['ports'][1883]
    assert mqtt['packets'] == 5 and mqtt['new_connections'] == 1 and mqtt['syn'] == 2
    assert mqtt['tcp_payload_bytes'] == 200 and mqtt['retransmissions'] == 1 and mqtt['pure_acks'] == 1
    assert mqtt['to_server_packets'] == 4 and mqtt['from_server_packets'] == 1
    assert mqtt['frame_bytes'] == 5 * 54 + 200 and mqtt['frame_bytes_per_message'] == 470
    assert report['ports'][8000]['packets'] == 1 and 22 not in report['ports']
    assert report['duration_s'] == 5
    with pytest.raises(ValueError, match='pcapng'):
        pcap_summary.summarize(b'\x0a\x0d\x0d\x0a' + b'\0' * 30)


def reading_payload(sequence, boot='b1', dbfs=-50.0):
    return json.dumps({'device_id': 'loa-001', 'boot_id': boot, 'sequence': sequence, 'dbfs': dbfs}).encode()


def test_monitor_tracks_rate_loss_silence_and_reboot():
    m = monitor.Monitor(gap_seconds=3)
    topic = 'loa/loa-001/noise'
    assert m.reading(topic, reading_payload(0), 0.0)[0][0] == 'new_device'
    assert m.reading(topic, reading_payload(1), 1.0) == []
    assert m.reading(topic, reading_payload(4), 2.0) == [('lost', 'loa-001', '2 reading(s) missing before sequence 4')]
    assert m.tick(4.0) == []
    assert m.tick(5.5)[0][0] == 'silent' and m.tick(6.0) == []  # Reported once.
    events = m.reading(topic, reading_payload(0, boot='b2'), 9.0)
    assert [e[0] for e in events] == ['recovered', 'reboot']
    assert m.reading(topic, reading_payload(1, boot='b2'), 10.0) == []  # No false loss after reboot.
    assert m.reading('loa/loa-001/status', b'x', 10.0) == []
    assert m.reading(topic, b'not json', 10.0)[0][0] == 'invalid_payload'
    m.sys('$SYS/broker/clients/connected', b'3')
    m.connected = True
    m.window_started = 0.0
    view = m.render(10.0)
    assert 'clients 3' in view and 'loa-001' in view and 'live' in view
    device = m.devices['loa-001']
    assert device.received == 5 and device.lost == 2


def test_monitor_env_file(tmp_path):
    path = tmp_path / 'loa.env'
    path.write_text('# comment\nMQTT_HOST=10.0.0.5\nexport MQTT_PASSWORD="p=a ss"\nEMPTY=\n')
    assert monitor.read_env_file(path) == {'MQTT_HOST': '10.0.0.5', 'MQTT_PASSWORD': 'p=a ss', 'EMPTY': ''}


def test_charts_render(tmp_path):
    pytest.importorskip('matplotlib')
    frames = [tcp_frame(50000, 1883, i, 0x18, b'x' * 60) for i in range(4)]
    capture = tmp_path / 'cap.pcap'
    capture.write_bytes(pcap(frames))
    series = pcap_summary.traffic_series(capture.read_bytes(), bin_seconds=2)
    assert series == {1883: [(0.0, 228), (2.0, 228)]}
    plot.traffic(str(capture), str(tmp_path / 'traffic.png'), (1883,), 1.0)
    load_csv = tmp_path / 'mqtt.csv'
    load_csv.write_text('transport,boot_id,sequence,sent_at,acked_at,rtt_ms,ok,status\n'
                        'mqtt-qos1,x,0,100.0,100.001,1.0,1,puback\nmqtt-qos1,x,1,100.2,,,0,rc4\n')
    analyze_csv = tmp_path / 'latency.csv'
    analyze_csv.write_text('received_at,boot_id,sequence,source,timestamp,latency_ms,dbfs\n100.0,x,0,mqtt,99.99,10.0,-50\n')
    plot.timeline([str(load_csv), str(analyze_csv)], str(tmp_path / 'timeline.png'))
    pcap_json, load_json, analyze_json = tmp_path / 'p.json', tmp_path / 'l.json', tmp_path / 'a.json'
    pcap_json.write_text(json.dumps(pcap_summary.summarize(capture.read_bytes(), messages=4)))
    load_json.write_text(json.dumps({'rtt_ms_p50': 1.0, 'rtt_ms_p95': 2.0, 'ack_ratio': 1.0, 'payload_bytes_per_message': 130}))
    analyze_json.write_text(json.dumps(analyze.analyze([row(1.0, 0, timestamp=0.99), row(2.0, 1, timestamp=1.99)])))
    values = plot.compare([('pcap', pcap_json), ('load', load_json), ('db', analyze_json)], str(tmp_path / 'compare.png'))
    assert values['pcap']['frame_bytes_per_message'] == 114 and values['load']['rtt_ms_p50'] == 1.0
    assert values['db']['delivery_ratio'] == 1.0
    for name in ('traffic', 'timeline', 'compare'):
        assert (tmp_path / f'{name}.png').stat().st_size > 5000
    with pytest.raises(ValueError):
        plot.flatten({'unexpected': 1})
