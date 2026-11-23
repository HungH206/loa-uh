import React, {useEffect, useRef, useState} from 'react';
import {Radio, Router, Server, Monitor, Activity, Timer, ShieldCheck, Gauge} from 'lucide-react';
import {ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid} from 'recharts';
import {api} from './auth';

const clock = t => new Date(t*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});
const fixed = (n, digits=1) => n == null ? '—' : Number(n).toFixed(digits);
const EVENT_LABELS = {
  sensor_recovered:'Sensor reconnected', sensor_silent:'Sensor silent', sensor_restarted:'Sensor restarted',
  transport_changed:'Transport changed', broker_connected:'Backend ↔ broker connected',
  broker_disconnected:'Backend ↔ broker disconnected', broker_refused:'Broker refused backend login',
};
const BAD_EVENTS = new Set(['sensor_silent','broker_disconnected','broker_refused']);
const TRANSPORT = {mqtt:'Wi-Fi · MQTT over TCP', http:'Wi-Fi · HTTP POST', browser:'Browser · HTTPS', simulator:'Simulated'};

function Node({icon:Icon, title, subtitle, state, lines}) {
  return <div className={`net-node ${state}`}>
    <div className="net-node-head"><span className="net-icon"><Icon size={18}/></span><i className="net-dot"/></div>
    <strong>{title}</strong><small>{subtitle}</small>
    {lines.map(line => <p key={line}>{line}</p>)}
  </div>;
}

function Link({label, state, detail}) {
  return <div className={`net-link ${state}`}><span>{label}</span><div className="net-wire"><i/></div><small>{detail}</small></div>;
}

function Metric({icon:Icon, label, value, unit, note}) {
  return <section className="stat net-metric">
    <span className="card-label">{label} <Icon size={16}/></span>
    <div className="number">{value}<span>{unit}</span></div>
    <p>{note}</p>
  </section>;
}

export function NetworkPanel({selected, wsConnected, ping}) {
  const [data, setData] = useState(null), [error, setError] = useState(''), [rtts, setRtts] = useState([]);
  const [fetchMs, setFetchMs] = useState(null);
  const pingRef = useRef(ping);
  pingRef.current = ping;
  useEffect(() => {
    let stopped = false;
    const load = async () => {
      const started = performance.now();
      try {
        const result = await api('network');
        if (stopped) return;
        setFetchMs(performance.now()-started); setData(result); setError('');
      } catch (e) {if (!stopped) setError(e.message);}
      try {
        const rtt = await pingRef.current?.();
        if (!stopped && rtt != null) setRtts(previous => [...previous.slice(-29), rtt]);
      } catch {/* Shown as a disconnected WebSocket link. */}
    };
    load();
    const timer = setInterval(load, 2000);
    return () => {stopped = true; clearInterval(timer);};
  }, []);

  const device = data?.devices.find(d => d.device_id === selected) || data?.devices[0];
  const backend = data?.backend, broker = data?.broker;
  const viaBroker = !device || device.last_source === 'mqtt' || device.last_source == null;
  const rtt = rtts.at(-1), sortedRtts = [...rtts].sort((a,b) => a-b), rttMedian = sortedRtts[Math.floor(sortedRtts.length/2)];
  // Amber once readings pause for more than 3 s (the outage rule); red after the 10 s offline rule.
  const sensorState = !device ? 'idle' : !device.online ? 'bad' : device.seconds_since_last > 3 ? 'warn' : 'ok';
  const brokerState = !backend?.mqtt_configured ? 'idle' : backend.mqtt_connected ? 'ok' : 'bad';
  const events = [...(device?.events || []), ...(data?.link_events || [])].sort((a,b) => b.time-a.time).slice(0,20);
  const delivery = device?.delivery;

  if (!data) return <section className="panel">{error ? <p role="alert">Network data unavailable: {error}</p> : <p className="empty">Measuring the connection path…</p>}</section>;
  return <div className="network">
    {error && <div role="alert" className="error">Last refresh failed: {error}</div>}
    <section className="panel">
      <div className="panel-heading"><div><h2>Connection path <span className="live-dot"/></h2><p>Every hop a reading takes, live. Refreshes every 2 seconds.</p></div><span className="net-legend"><i className="ok"/>working <i className="warn"/>silent <i className="bad"/>down <i className="idle"/>not used</span></div>
      <div className="net-path">
        <Node icon={Radio} title="Sensor" subtitle={device ? device.device_id : 'No sensor paired'} state={sensorState}
          lines={device ? [device.online ? `${fixed(device.rate_hz,2)} readings/s` : 'Offline', `Last reading ${device.seconds_since_last == null ? 'never' : `${fixed(device.seconds_since_last)} s ago`}`] : ['Pair a sensor first']}/>
        <Link label={device ? TRANSPORT[device.last_source] || device.last_source || 'Wi-Fi · MQTT' : 'Wi-Fi'} state={sensorState}
          detail={viaBroker ? 'publish loa/<id>/noise' : 'sent straight to the backend'}/>
        <Node icon={Router} title="MQTT broker" subtitle={`Mosquitto on the Pi${backend?.mqtt_endpoint ? ` · ${backend.mqtt_endpoint}` : ''}`} state={viaBroker ? brokerState : 'idle'}
          lines={!viaBroker ? ['Not used by this sensor'] : broker?.available ? [`${fixed(broker.clients_connected,0)} clients connected`, `${fixed(broker.messages_in_per_min)} msgs/min in`] : ['Broker statistics unavailable']}/>
        <Link label="MQTT subscribe" state={viaBroker ? brokerState : 'idle'} detail={backend?.mqtt_connected ? 'QoS 1 · loa/+/noise' : backend?.mqtt_configured ? 'reconnecting' : 'not configured'}/>
        <Node icon={Server} title="Backend" subtitle="FastAPI + SQLite on the Pi" state={error ? 'bad' : 'ok'}
          lines={[backend?.mqtt_connected ? `Broker link up since ${clock(backend.connected_since)}` : 'Broker link down', `API reply ${fixed(fetchMs,0)} ms`]}/>
        <Link label="WebSocket" state={wsConnected ? 'ok' : 'bad'} detail={wsConnected ? 'live readings pushed' : 'reconnecting'}/>
        <Node icon={Monitor} title="This browser" subtitle="Dashboard" state={wsConnected ? 'ok' : 'bad'}
          lines={[rtt != null ? `Round trip ${fixed(rtt)} ms` : 'Measuring round trip…', rttMedian != null ? `Median ${fixed(rttMedian)} ms (last ${rtts.length})` : '']}/>
      </div>
    </section>

    <div className="stats net-stats">
      <Metric icon={Activity} label="READING RATE" value={fixed(device?.rate_hz,2)} unit="per s" note="Readings received in the last 10 seconds"/>
      <Metric icon={ShieldCheck} label="DELIVERY · 5 MIN" value={delivery?.ratio == null ? '—' : fixed(delivery.ratio*100)} unit="%"
        note={delivery?.expected ? `${delivery.received} of ${delivery.expected} delivered, ${delivery.lost} lost (sequence gaps)` : 'Needs readings with sequence numbers'}/>
      <Metric icon={Timer} label="ONE-WAY LATENCY" value={fixed(device?.latency_ms?.p50)} unit="ms p50"
        note={device?.latency_ms ? `p95 ${fixed(device.latency_ms.p95)} ms · ${device.latency_ms.clock_warning ? 'clocks out of sync' : 'sensor → backend, NTP time'}` : 'Needs NTP send times from the sensor firmware'}/>
      <Metric icon={Gauge} label="BROKER LOAD" value={broker?.available ? fixed(broker.messages_in_per_min) : '—'} unit="msgs/min in"
        note={broker?.available ? `${fixed(broker.messages_out_per_min)} out · ${fixed(broker.bytes_in_per_min,0)} B/min in · ${fixed(broker.clients_connected,0)} clients` : 'Allow `topic read $SYS/#` for the backend in the broker ACL'}/>
    </div>

    <section className="panel">
      <div className="panel-heading"><div><h2>Readings per second</h2><p>Last 2 minutes for {device?.device_id || 'your sensor'}. A drop to zero is an outage.</p></div></div>
      <div className="net-chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={device?.series || []} margin={{top:10,right:10,left:-20,bottom:0}}>
        <CartesianGrid vertical={false} stroke="#edf0ed"/>
        <XAxis dataKey="t" tickFormatter={clock} minTickGap={60} tick={{fontSize:11,fill:'#89918b'}} axisLine={false} tickLine={false}/>
        <YAxis allowDecimals={false} tick={{fontSize:11,fill:'#89918b'}} axisLine={false} tickLine={false}/>
        <Tooltip labelFormatter={clock} formatter={v => [v,'Readings']}/>
        <Bar dataKey="count" fill="#518c6d" isAnimationActive={false}/>
      </BarChart></ResponsiveContainer></div>
    </section>

    <section className="panel">
      <div className="panel-heading"><div><h2>Connection events</h2><p>Outages, reconnects and restarts in the last 15 minutes, newest first.</p></div></div>
      {events.length ? events.map((e,i) => <div className={`net-event ${BAD_EVENTS.has(e.kind) ? 'bad' : ''}`} key={`${e.time}-${e.kind}-${i}`}>
        <span>{clock(e.time)}</span><strong>{EVENT_LABELS[e.kind] || e.kind}</strong><p>{e.detail}</p>
      </div>) : <p className="empty">No outages in the last 15 minutes. Switch off the Wi-Fi access point to see one appear.</p>}
    </section>

    <p className="unit-note">Delivery counts missing sequence numbers within each sensor boot. One-way latency is backend receive time minus the sensor's NTP send time, so it is only as accurate as the two clocks. Round trip is a WebSocket ping from this browser to the Pi and back. Broker figures are Mosquitto's own 1-minute averages.</p>
  </div>;
}
