"""Summarize a tcpdump capture by TCP port: packets, bytes, handshakes, retransmissions.

    sudo tcpdump -i wlan0 -w experiments/captures/exp1-mqtt.pcap 'tcp port 1883 or tcp port 8000'
    python -m experiments.pcap_summary experiments/captures/exp1-mqtt.pcap --messages 600

Reads classic pcap (tcpdump's default), not pcapng; in Wireshark use File > Save As > pcap.
Link types: Ethernet/Wi-Fi as Ethernet (1), macOS loopback (0), raw IP (101), Linux any (113, 276).
Standard library only. Pass --messages (readings delivered, from experiments.analyze or
experiments.load) to get wire bytes per reading, the protocol-overhead figure for Experiment 1.
"""
import argparse
import json
import struct
import sys
from collections import defaultdict

DEFAULT_PORTS = (1883, 8883, 8000)


def read_packets(data):
    """Yield (timestamp, linktype, frame_bytes, captured_frame) from classic pcap bytes."""
    if len(data) < 24:
        raise ValueError('File too short to be a pcap capture')
    magic = data[:4]
    formats = {b'\xd4\xc3\xb2\xa1': ('<', 1e-6), b'\xa1\xb2\xc3\xd4': ('>', 1e-6),
               b'\x4d\x3c\xb2\xa1': ('<', 1e-9), b'\xa1\xb2\x3c\x4d': ('>', 1e-9)}
    if magic == b'\x0a\x0d\x0d\x0a':
        raise ValueError('pcapng is not supported; save the capture as classic pcap')
    if magic not in formats:
        raise ValueError('Not a pcap file')
    endian, fraction = formats[magic]
    linktype = struct.unpack(endian + 'I', data[20:24])[0] & 0x0FFFFFFF
    offset = 24
    while offset + 16 <= len(data):
        seconds, sub, captured, original = struct.unpack(endian + 'IIII', data[offset:offset + 16])
        offset += 16
        yield seconds + sub * fraction, linktype, original, data[offset:offset + captured]
        offset += captured


def ip_payload(linktype, frame):
    """Return (ip_version, ip_packet) or None for non-IP frames."""
    if linktype == 1:  # Ethernet, possibly with 802.1Q VLAN tags.
        offset, ethertype = 14, struct.unpack('!H', frame[12:14])[0] if len(frame) >= 14 else 0
        while ethertype in (0x8100, 0x88A8) and len(frame) >= offset + 4:
            ethertype, offset = struct.unpack('!H', frame[offset + 2:offset + 4])[0], offset + 4
        version = {0x0800: 4, 0x86DD: 6}.get(ethertype)
        return (version, frame[offset:]) if version else None
    if linktype == 0:  # BSD loopback: 4-byte address family in host order.
        packet = frame[4:]
    elif linktype == 101:
        packet = frame
    elif linktype == 113:  # Linux cooked v1: 16-byte header.
        packet = frame[16:]
    elif linktype == 276:  # Linux cooked v2: 20-byte header.
        packet = frame[20:]
    else:
        raise ValueError(f'Unsupported link type {linktype}')
    version = packet[0] >> 4 if packet else 0
    return (version, packet) if version in (4, 6) else None


def tcp_segment(version, packet):
    """Return (src, dst, sport, dport, seq, flags, payload_len, ip_len) for TCP, else None."""
    if version == 4:
        if len(packet) < 20 or packet[9] != 6:
            return None
        header, total = (packet[0] & 0x0F) * 4, struct.unpack('!H', packet[2:4])[0]
        src, dst, tcp = packet[12:16], packet[16:20], packet[header:total]
    else:
        if len(packet) < 40 or packet[6] != 6:  # Extension headers are not followed.
            return None
        total = 40 + struct.unpack('!H', packet[4:6])[0]
        src, dst, tcp = packet[8:24], packet[24:40], packet[40:total]
    if len(tcp) < 20:
        return None
    sport, dport, seq = struct.unpack('!HHI', tcp[:8])
    offset, flags = (tcp[12] >> 4) * 4, tcp[13]
    return src, dst, sport, dport, seq, flags, max(0, len(tcp) - offset), total


def tcp_packets(data, ports=DEFAULT_PORTS):
    """Yield (timestamp, server_port, frame_bytes, segment) for TCP packets to or from ports."""
    for timestamp, linktype, frame_bytes, frame in read_packets(data):
        ip = ip_payload(linktype, frame)
        segment = tcp_segment(*ip) if ip else None
        if not segment:
            continue
        sport, dport = segment[2], segment[3]
        port = dport if dport in ports else sport if sport in ports else None
        if port is not None:
            yield timestamp, port, frame_bytes, segment


def traffic_series(data, ports=DEFAULT_PORTS, bin_seconds=1.0):
    """Bytes per bin per port, like Wireshark's I/O graph: {port: [(seconds_from_start, bytes)]}."""
    packets = list(tcp_packets(data, ports))
    if not packets:
        return {}
    start = packets[0][0]
    bins = int((packets[-1][0] - start) // bin_seconds) + 1
    series = {}
    for timestamp, port, frame_bytes, _ in packets:
        counts = series.setdefault(port, [0] * bins)
        counts[int((timestamp - start) // bin_seconds)] += frame_bytes
    return {port: [(i * bin_seconds, b) for i, b in enumerate(counts)] for port, counts in series.items()}


def summarize(data, ports=DEFAULT_PORTS, messages=None):
    totals = defaultdict(lambda: defaultdict(float))
    seen = defaultdict(set)
    first = last = None
    for timestamp, port, frame_bytes, segment in tcp_packets(data, ports):
        src, dst, sport, dport, seq, flags, payload, ip_len = segment
        first, last = first if first is not None else timestamp, timestamp
        t = totals[port]
        direction = 'to_server' if dport == port else 'from_server'
        t['packets'] += 1
        t['frame_bytes'] += frame_bytes
        t['ip_bytes'] += ip_len
        t['tcp_payload_bytes'] += payload
        t[f'{direction}_packets'] += 1
        t[f'{direction}_payload_bytes'] += payload
        syn, fin, rst, ack = flags & 0x02, flags & 0x01, flags & 0x04, flags & 0x10
        t['syn'] += bool(syn)
        t['new_connections'] += bool(syn and not ack)
        t['fin'] += bool(fin)
        t['rst'] += bool(rst)
        t['pure_acks'] += bool(ack and not payload and not (syn or fin or rst))
        if payload:  # Same flow and sequence number with data again: a retransmission.
            key = (src, dst, sport, dport, seq)
            t['retransmissions'] += key in seen[port]
            seen[port].add(key)
    duration = (last - first) if first is not None else 0
    report = {'duration_s': round(duration, 3), 'ports': {}}
    for port, t in sorted(totals.items()):
        entry = {k: int(v) for k, v in sorted(t.items())}
        entry['header_overhead_ratio'] = round(1 - t['tcp_payload_bytes'] / t['frame_bytes'], 4) if t['frame_bytes'] else None
        if duration:
            entry['throughput_bps'] = round(t['frame_bytes'] * 8 / duration, 1)
        if messages:
            entry['frame_bytes_per_message'] = round(t['frame_bytes'] / messages, 1)
            entry['packets_per_message'] = round(t['packets'] / messages, 2)
        report['ports'][port] = entry
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('capture')
    parser.add_argument('--ports', default=','.join(map(str, DEFAULT_PORTS)), help='Comma-separated server ports')
    parser.add_argument('--messages', type=int, help='Readings delivered during the capture')
    args = parser.parse_args(argv)
    with open(args.capture, 'rb') as handle:
        data = handle.read()
    try:
        report = summarize(data, tuple(int(p) for p in args.ports.split(',')), args.messages)
    except ValueError as error:
        sys.exit(str(error))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
