import React, {useEffect, useState} from 'react';
import {api} from './auth';

const stamp = t => new Date(t * 1000).toLocaleTimeString();

export function PacketTracker() {
  const [data, setData] = useState(null), [error, setError] = useState(''), [selected, setSelected] = useState('');
  useEffect(() => {
    let stopped = false, busy = false;
    async function load() {
      if (busy) return;
      busy = true;
      try {
        const result = await api('network');
        if (!stopped) {setData(result); setError('');}
      } catch (e) {if (!stopped) setError(e.message);}
      finally {busy = false;}
    }
    load(); const timer = setInterval(load, 2000);
    return () => {stopped = true; clearInterval(timer);};
  }, []);
  const devices = (data?.devices || []).filter(d => !d.device_id.startsWith('browser-'));
  const device = devices.find(d => d.device_id === selected) || devices[0];
  const packets = device?.packets;
  function download() {
    const csv = 'received_at,device_id,boot_id,sequence,transport\n' + packets.recent.map(r =>
      [new Date(r.received_at * 1000).toISOString(), device.device_id, r.boot_id, r.sequence, r.source].join(',')).join('\n');
    const url = URL.createObjectURL(new Blob([csv], {type:'text/csv'}));
    const a = document.createElement('a'); a.href = url; a.download = 'loa-packet-receipts.csv'; a.click(); URL.revokeObjectURL(url);
  }
  return <div className="packet-tracker">
    <section className="panel">
      <div className="panel-heading"><h2>Wi-Fi packet tracker</h2><button className="export" onClick={download} disabled={!packets?.recent.length}>Export receipts</button></div>
      <p>Tracks sensor messages received by the Pi over MQTT or HTTP. Refreshes every 2 seconds; measurements cover the last 5 minutes.</p>
      <label className="esp32-label">Sensor<select value={device?.device_id || ''} disabled={!devices.length} onChange={e => setSelected(e.target.value)}>{!devices.length && <option value="">No sensor available</option>}{devices.map(d => <option key={d.device_id} value={d.device_id}>{d.label} · {d.device_id}</option>)}</select></label>
      {error && <p role="alert" className="hub-error">Refresh failed: {error}. Displayed measurements may be stale.</p>}
      <p role="status">{!data ? error ? 'Cannot reach the Pi. Check the Pi hub connection.' : 'Loading measurements…' : !device ? 'Connect the Pi and pair an ESP32 to begin tracking.' : !packets ? 'Update the Pi backend to enable packet tracking.' : device.online ? `${device.device_id} is online.` : `${device.device_id} is offline. Loss during ongoing silence is unknown until more messages arrive.`}</p>
      {data && <p>Last updated: {stamp(data.server_time)}</p>}
    </section>
    <div className="stats net-stats">{[
      ['RECEIVED', packets ? packets.received : '—', 'Unique sequenced sensor messages'],
      ['MISSING', packets?.expected ? packets.missing : '—', 'Sequence numbers missing between received messages'],
      ['MESSAGE LOSS', packets?.loss_percent == null ? '—' : `${packets.loss_percent}%`, 'Missing ÷ (received + missing)'],
      ['READING RATE', device ? `${device.rate_hz}/s` : '—', 'Messages received in the last 10 seconds'],
    ].map(([label,value,note]) => <section className="stat net-metric" key={label}><span className="card-label">{label}</span><div className="number">{value}</div><p>{note}</p></section>)}</div>
    <section className="panel"><h2>Missing sequence ranges</h2><p>Boots are measured separately. Late arrivals fill gaps on the next refresh.</p>
      {packets?.gaps.length ? <div className="packet-table"><table><thead><tr><th>Boot</th><th>First missing</th><th>Last missing</th><th>Count</th></tr></thead><tbody>{packets.gaps.map(g => <tr key={`${g.boot_id}-${g.first}`}><td>{g.boot_id}</td><td>{g.first}</td><td>{g.last}</td><td>{g.count}</td></tr>)}</tbody></table></div> : <p className="empty">{packets?.expected ? 'No sequence gaps observed in this window.' : 'Not enough sequenced sensor data to measure loss.'}</p>}
      {packets?.gap_count > 100 && <p>Showing the last 100 of {packets.gap_count} ranges.</p>}
    </section>
    <section className="panel"><h2>Recent received messages</h2><p>Up to 100 receipts, newest first.</p>{packets?.recent.length ? <div className="packet-table"><table><thead><tr><th>Received</th><th>Boot</th><th>Sequence</th><th>Transport</th></tr></thead><tbody>{packets.recent.map((r,i) => <tr key={`${r.boot_id}-${r.sequence}-${i}`}><td>{stamp(r.received_at)}</td><td>{r.boot_id}</td><td>{r.sequence}</td><td>{r.source.toUpperCase()}</td></tr>)}</tbody></table></div> : <p className="empty">Waiting for sensor messages.</p>}</section>
    <p className="packet-note">These are application message gaps, not Wi-Fi frame loss or TCP retransmissions. The first and last missing messages outside the observed sequence range cannot be counted. No readings means loss is unknown, not 100%. Radio-level measurements require a separate Wi-Fi capture.</p>
  </div>;
}
