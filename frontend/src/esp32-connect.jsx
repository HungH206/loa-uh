import React, {useState} from 'react';
import {Radio, X} from 'lucide-react';

export function Esp32Connect({devices = [], selected, now, hubOnline, onCheck, onSelect, onPair}) {
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const sensors = devices.filter(d => !d.local && !d.device_id.startsWith('browser-'));
  const sensor = sensors.find(d => d.device_id === selected) || sensors[0];
  const online = hubOnline && sensor?.last_seen != null && now - sensor.last_seen < 10;
  async function check() {
    setBusy(true); setError('');
    try {await onCheck(); if (sensor) onSelect(sensor.device_id);}
    catch (e) {setError(e.message);}
    finally {setBusy(false);}
  }
  return <div className="esp32-connect">
    <button className="export" aria-expanded={open} aria-controls="esp32-setup" onClick={() => setOpen(!open)}><Radio size={16}/> Connect ESP32</button>
    {open && <section className="panel esp32-panel" id="esp32-setup" aria-labelledby="esp32-title">
      <div className="panel-heading"><h2 id="esp32-title">Connect your ESP32</h2><button className="icon-button" aria-label="Close ESP32 setup" onClick={() => setOpen(false)}><X size={18}/></button></div>
      <p>Plug your ESP32 into USB power or a charger. Its saved firmware starts sensing and connects to Wi-Fi and your Pi automatically.</p>
      <ol><li>Turn on the Wi-Fi network or hotspot saved on the sensor. Keep the ESP32 nearby.</li><li>Connect the Pi to the same network. For an iPhone hotspot, enable Maximize Compatibility.</li><li>Wait about 30 seconds, then check for readings below. An already paired sensor does not need a new pairing code.</li></ol>
      <p role="status">{!hubOnline ? 'Connect your Pi using the Pi hub button above to check the ESP32.' : !sensor ? 'No ESP32 paired with this account yet.' : online ? `${sensor.device_id} is online. Live readings are arriving.` : `${sensor.device_id} is offline. Waiting for readings from the Pi.`}</p>
      {sensors.length > 1 && <label className="esp32-label">ESP32 sensor<select value={sensor.device_id} onChange={e => onSelect(e.target.value)}>{sensors.map(d => <option key={d.device_id} value={d.device_id}>{d.label} · {d.device_id}</option>)}</select></label>}
      {error && <p role="alert" className="hub-error">{error}</p>}
      <div className="hub-actions"><button className="save" disabled={busy} onClick={check}>{busy ? 'Checking…' : online ? 'Show live readings' : 'Check connection'}</button><button className="text-button" onClick={onPair}>Pair a new ESP32</button></div>
      <p>The button checks the connection through your Pi. Initial firmware and Wi-Fi setup require USB; it cannot turn on an unpowered sensor or change its saved Wi-Fi.</p>
    </section>}
  </div>;
}
