import React, {useCallback, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Activity, Radio, Bell, ArrowUpRight, Volume2, SlidersHorizontal, Download, Waves, LogOut, MessageSquarePlus} from 'lucide-react';
import {ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, ReferenceLine, CartesianGrid} from 'recharts';
import {AuthGate, api, accessToken, supabase, backendOrigin, cloudMode, hubApi} from './auth';
import './style.css';
import {BrowserMicrophone} from './microphone';
import {DisturbanceReports} from './reports';

const clock = t => new Date(t*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});
const dateTime = t => new Date(t*1000).toLocaleString();
const defaults = {threshold:-40, duration_seconds:3, cooldown_seconds:30};
const byTime = (a,b) => a.received_at-b.received_at || String(a.id).localeCompare(String(b.id));
const number = n => n == null ? '—' : n.toFixed(1);

function App({account}) {
  const [rows,setRows] = useState([]), [alerts,setAlerts] = useState([]), [status,setStatus] = useState(null);
  const [settings,setSettings] = useState(defaults), [draft,setDraft] = useState(defaults), [range,setRange] = useState(60);
  const [connected,setConnected] = useState(false), [error,setError] = useState(''), [notice,setNotice] = useState('');
  const [tab,setTab] = useState('Overview'), [saving,setSaving] = useState(false), [selected,setSelected] = useState('');
  const [pairing,setPairing] = useState(false), [deviceId,setDeviceId] = useState(''), [claimCode,setClaimCode] = useState('');
  const [now,setNow] = useState(Date.now()/1000), [dataReady,setDataReady] = useState(false);
  const rangeRef = useRef(range), generation = useRef(0);
  rangeRef.current = range;
  const refresh = useCallback(async () => {
    const [a,s] = await Promise.all([api('alerts'),api('status')]);
    setAlerts(a); setStatus(s);
    setSelected(previous => s.devices.some(d => d.device_id === previous) ? previous : s.devices[0]?.device_id || '');
  }, []);
  const loadHistory = useCallback(async () => {
    const request = ++generation.current;
    const data = await api(`readings?minutes=${rangeRef.current}&limit=10000`);
    if (request !== generation.current) return;
    setRows(previous => [...new Map([...data, ...previous.filter(r => r.received_at > (data.at(-1)?.received_at ?? 0))].map(r => [r.id,r])).values()].sort(byTime).slice(-10000));
  }, []);
  useEffect(() => {loadHistory().catch(e => setError(e.message));}, [range,loadHistory]);
  useEffect(() => {
    let stopped = false, socket, retry, timer, socketTimeout;
    api('settings').then(s => {if (!stopped) {setSettings(s); setDraft(s); setDataReady(true);}}).catch(e => setError(e.message));
    const poll = () => refresh().catch(e => {if (!stopped) setError(e.message);});
    poll();
    const open = () => {
      if (stopped || (cloudMode && !backendOrigin)) return;
      socket = new WebSocket(`${(backendOrigin || location.origin).replace(/^http/, 'ws')}/ws`);
      socketTimeout = setTimeout(() => socket?.close(), 5000);
      socket.onopen = async () => {
        clearTimeout(socketTimeout);
        try {
          if (!account.demo) {
            const token = await accessToken();
            if (!token) throw new Error('Sign in again to reconnect');
            if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type:'auth',token}));
          }
        } catch (e) {setError(e.message); socket.close();}
      };
      socket.onmessage = e => {
        if (stopped) return;
        const event = JSON.parse(e.data);
        if (event.type === 'ready') {setConnected(true); loadHistory().catch(e => setError(e.message));}
        if (event.type === 'reading') setRows(r => [...r.filter(v => v.id !== event.data.id),event.data].sort(byTime).slice(-10000));
        if (event.type === 'settings' && !cloudMode) {setSettings(event.data); setDraft(event.data);}
        if (event.type === 'devices') poll();
      };
      socket.onerror = () => {if (!stopped) setConnected(false);};
      socket.onclose = e => {
        if (stopped) return;
        setConnected(false);
        clearTimeout(socketTimeout);
        if (e.code === 4401 && !cloudMode) setError('Session expired or authentication unavailable. Reconnecting…');
        retry = setTimeout(open, cloudMode ? 30000 : 3000);
      };
    };
    const localEvent = e => {
      if (e.detail.type === 'reading') setRows(r => [...r.filter(v => v.id !== e.detail.data.id),e.detail.data].sort(byTime).slice(-10000));
      if (e.detail.type === 'devices') poll();
    };
    window.addEventListener('loa-local', localEvent);
    open(); timer = setInterval(poll, 3000);
    const tick = setInterval(() => setNow(Date.now()/1000), 1000);
    const {data} = supabase?.auth.onAuthStateChange((_event, session) => {
      if (socket?.readyState === WebSocket.OPEN && session?.access_token) socket.send(JSON.stringify({type:'auth',token:session.access_token}));
    }) || {};
    return () => {stopped = true; clearInterval(timer); clearInterval(tick); clearTimeout(retry); clearTimeout(socketTimeout); window.removeEventListener('loa-local',localEvent); socket?.close(); data?.subscription.unsubscribe();};
  }, [account.demo,refresh,loadHistory]);
  const visible = rows.filter(r => r.device_id === selected && r.received_at > now-range*60);
  const latest = rows.filter(r => r.device_id === selected).at(-1);
  const device = status?.devices.find(d => d.device_id === selected);
  const online = device?.last_seen != null && now-device.last_seen < 10;
  const value = online ? latest?.dbfs : null;
  const avg = visible.length ? visible.reduce((s,r) => s+r.dbfs,0)/visible.length : null;
  const peak = visible.length ? Math.max(...visible.map(r => r.dbfs)) : null;
  const loud = value != null && value >= settings.threshold;
  const deviceAlerts = alerts.filter(a => a.device_id === selected);
  async function save(e) {
    e.preventDefault(); setSaving(true); setError(''); setNotice('');
    try {const s = await api('settings',{method:'PUT',body:JSON.stringify(draft)}); setSettings(s); setDraft(s); setNotice(cloudMode ? 'Your cloud preferences are saved. Apply them to the hub separately when connected.' : 'Your tolerance settings are saved.'); await refresh();}
    catch(e) {setError(e.message);} finally {setSaving(false);}
  }
  async function pair(e) {
    e.preventDefault(); setPairing(true); setError(''); setNotice('');
    try {await api('devices/claim',{method:'POST',body:JSON.stringify({device_id:deviceId.trim(),claim_code:claimCode.trim()})}); setClaimCode(''); await refresh(); setSelected(deviceId.trim()); setDeviceId(''); setNotice('Microphone paired. Waiting for live measurements.'); await loadHistory();}
    catch(e) {setError(e.message);} finally {setPairing(false);}
  }
  function download() {
    const csv = 'received_at,device_id,source,dbfs,sequence,boot_id\n'+visible.map(r => [new Date(r.received_at*1000).toISOString(),r.device_id,r.source,r.dbfs,r.sequence ?? '',r.boot_id ?? ''].join(',')).join('\n');
    const url = URL.createObjectURL(new Blob([csv],{type:'text/csv'})); const a = document.createElement('a'); a.href = url; a.download = 'loa-readings.csv'; a.click(); URL.revokeObjectURL(url);
  }
  return <div className="shell">
    <aside><a className="brand" href="/"><span className="brand-icon"><Waves/></span>loa<span className="brand-dot">.</span></a><div className="workspace">YOUR WORKSPACE <span>01</span></div><nav>{[['Overview',Activity],['Noise history',Waves],['Alerts',Bell],['Settings',SlidersHorizontal],['Report disturbance',MessageSquarePlus]].map(([name,Icon]) => <button key={name} className={tab === name ? 'active' : ''} onClick={() => setTab(name)}><Icon size={18}/>{name}{name === 'Alerts' && <small>{deviceAlerts.length}</small>}</button>)}</nav><div className="aside-bottom"><Radio size={20}/><strong>Small device.<br/>A quieter perspective.</strong><p>Listen. Observe. Alert.</p><span>LOA / NETWORKING PROJECT</span></div></aside>
    <main><header><span>{account.email}</span><div className="account-actions"><div className="live"><i className={connected ? '' : 'off'}/>{cloudMode ? status?.hub_online ? 'Hub connected' : 'Hub offline · cloud available' : connected ? 'Live connection' : 'Reconnecting…'}</div>{!account.demo && <button className="text-button" onClick={async () => {const {error} = await supabase.auth.signOut(); if (error) setError(error.message);}}><LogOut size={14}/> Sign out</button>}</div></header>
    <section className="title"><div><div className="eyebrow">YOUR ENVIRONMENT, IN FOCUS</div><h1>{tab === 'Report disturbance' ? 'Tell us what you heard.' : tab === 'Overview' ? 'A little more aware.' : tab === 'Noise history' ? 'Every moment, measured.' : tab === 'Alerts' ? 'Know when it gets loud.' : 'Make quiet your own.'}</h1><p>Understand the sound around you. Make room for quiet.</p></div><div className="title-actions"><button className="export report-trigger" onClick={() => setTab('Report disturbance')}><MessageSquarePlus size={16}/> Report disturbance</button><button className="export" onClick={download} disabled={!visible.length}><Download size={16}/> Export data</button></div></section>
    {error && <div role="alert" className="error">{error}<button className="text-button" onClick={() => setError('')}>Dismiss</button></div>}
    {notice && <div role="status" className="demo">{notice}</div>}
    {account.demo && <div className="demo"><span>LOCAL DEMO · NO ACCOUNT PROTECTION</span>{status?.simulator ? 'Readings are simulated. ' : ''}Use Supabase mode for private accounts.</div>}
    {tab === 'Report disturbance' && <DisturbanceReports storage={account.reportStore} onHome={() => setTab('Overview')}/>}
    {cloudMode && <div className="demo" role="status">Login and reports work without a hub. Browser monitoring stays in this page session; ESP32 history requires the hub.</div>}
    <div hidden={tab === 'Report disturbance'}>
    <div className="sensor-picker"><label htmlFor="sensor">Your microphone</label><select id="sensor" value={selected} onChange={e => setSelected(e.target.value)} disabled={!status?.devices.length}>{!status?.devices.length && <option value="">No microphone paired</option>}{status?.devices.map(d => <option key={d.device_id} value={d.device_id}>{d.label} · {d.device_id}</option>)}</select><span>{selected ? online ? 'Microphone online' : device?.local ? 'Microphone stopped' : 'Sensor offline' : 'Use this device’s microphone or connect a hub'}</span></div>
    {(tab === 'Overview' || tab === 'Noise history') && <><div className="stats"><section className={'current '+(loud ? 'loud' : '')}><div className="card-label"><span><Volume2 size={16}/> CURRENT SOUND LEVEL</span><span className="badge">{latest?.source === 'simulator' ? 'DEMO' : 'SENSOR'}</span></div><div className="reading">{number(value)}<span>dBFS · relative input</span></div><p>{value == null ? 'Waiting for sound' : loud ? 'Above your chosen tolerance' : 'Within your chosen tolerance'}</p><div className="current-footer"><span><i/>{!online ? 'Waiting for live readings' : loud ? 'Above your tolerance level' : 'Within your tolerance level'}</span><Waves size={48}/></div></section><section className="stat"><span className="card-label">AVERAGE LEVEL <Activity size={16}/></span><div className="number">{number(avg)}<span>dBFS</span></div><p>Mean of reported dBFS levels in this range</p><p className="metric-note">Relative microphone level</p></section><section className="stat"><span className="card-label">PEAK LEVEL <ArrowUpRight size={17}/></span><div className="number">{number(peak)}<span>dBFS</span></div><p>Highest recorded in this range</p><div className="peak-label">{peak == null ? 'Waiting for readings' : peak >= settings.threshold ? 'Tolerance exceeded' : 'Within tolerance'} <span>↗</span></div></section></div><section className="chart-panel"><div className="panel-heading"><div><h2>Sound over time <span className="live-dot"/></h2><p>Readings from your selected microphone.</p></div><select aria-label="History time range" value={range} onChange={e => setRange(Number(e.target.value))}><option value={5}>Last 5 minutes</option><option value={60}>Last hour</option><option value={1440}>Last 24 hours</option><option value={10080}>Last 7 days</option></select></div><div className="legend"><span><i/> Relative sound level</span><span><i/> Tolerance · {settings.threshold} dBFS</span></div><div className="chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={visible} margin={{top:10,right:10,left:-10,bottom:0}}><defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#89b39c" stopOpacity={.4}/><stop offset="100%" stopColor="#89b39c" stopOpacity={0}/></linearGradient></defs><CartesianGrid vertical={false} stroke="#edf0ed"/><XAxis dataKey="received_at" type="number" domain={['dataMin','dataMax']} tickFormatter={clock} minTickGap={65} tick={{fontSize:11,fill:'#89918b'}} axisLine={false} tickLine={false}/><YAxis domain={[-90,0]} tick={{fontSize:11,fill:'#89918b'}} axisLine={false} tickLine={false}/><Tooltip labelFormatter={clock} formatter={v => [`${v} dBFS`,'Sound level']}/><ReferenceLine y={settings.threshold} stroke="#c6985e" strokeDasharray="5 5"/><Area type="linear" dataKey="dbfs" baseValue={-90} stroke="#518c6d" strokeWidth={2} fill="url(#fill)" isAnimationActive={false}/></AreaChart></ResponsiveContainer></div>{!visible.length && <p className="empty">Waiting for measurements in this range.</p>}<p className="history-note">History and export show up to 10,000 readings in the selected range.</p></section></>}
    <div className="bottom-grid">{tab !== 'Settings' && <section className="panel"><div className="panel-heading"><h2>Recent exceedances</h2><Bell size={17}/></div>{deviceAlerts.length ? deviceAlerts.slice(0,tab === 'Alerts' ? 50 : 3).map(a => <div className="alert-row" key={a.id}><span className="alert-icon"><Volume2 size={17}/></span><div><strong>Personal tolerance exceeded</strong><p>{a.device_id} · peak {a.peak_dbfs.toFixed(1)} dBFS / limit {a.threshold}</p><p>{dateTime(a.triggered_at)} · {a.ended_at ? `Ended: ${a.end_reason.replaceAll('_',' ')}` : 'Ongoing'}</p></div></div>) : <p className="empty">Alerts appear after sound stays above your tolerance for {settings.duration_seconds} seconds.</p>}</section>}{tab !== 'Alerts' && <section className="panel threshold"><div className="panel-heading"><h2>Your quiet zone</h2><SlidersHorizontal size={17}/></div><p>{cloudMode ? 'Cloud preferences apply to this browser. Your hub keeps its own settings until you apply these to it.' : 'Applies to all microphones paired to your account.'}</p>{cloudMode && <button className="save" disabled={!status?.hub_online || saving} onClick={async () => {setSaving(true); try {await hubApi('settings',{method:'PUT',body:JSON.stringify(settings)}); setNotice('Cloud preferences applied to this hub.');} catch(e) {setError(e.message);} finally {setSaving(false);}}}>Apply saved preferences to connected hub</button>}<form onSubmit={save}><label htmlFor="threshold">Relative tolerance <strong>{draft.threshold} <small>dBFS</small></strong></label><input id="threshold" type="range" min="-90" max="-1" value={draft.threshold} onChange={e => setDraft({...draft,threshold:Number(e.target.value)})}/><div className="range-label"><span>−90 · More sensitive</span><span>−1 · Less sensitive</span></div><div className="rule-fields"><label htmlFor="duration">Time above tolerance (seconds)<input id="duration" type="number" min="1" max="60" required value={draft.duration_seconds} onChange={e => setDraft({...draft,duration_seconds:e.target.value === '' ? '' : Number(e.target.value)})}/></label><label htmlFor="cooldown">Minimum between alerts (seconds)<input id="cooldown" type="number" min="0" max="3600" required value={draft.cooldown_seconds} onChange={e => setDraft({...draft,cooldown_seconds:e.target.value === '' ? '' : Number(e.target.value)})}/></label></div><button className="save" disabled={saving || !dataReady}>{saving ? 'Saving…' : 'Save my tolerance'}</button></form><p className="unit-note">dBFS measures relative microphone input, not environmental dBA. Choose a level using your own microphone in a comfortable setting. Louder sounds are closer to 0.</p></section>}</div>
    {(tab === 'Settings' || tab === 'Overview') && <><section className="device-strip"><Radio size={19}/><div><strong>Device connections</strong><p>{status?.devices.length ? status.devices.map(d => `${d.device_id}: ${d.last_seen != null && now-d.last_seen < 10 ? 'online' : 'offline'}`).join(' · ') : 'Waiting for a sensor connection'}</p></div><span>{cloudMode && !status?.hub_online ? 'Hub offline — optional' : status?.mqtt_connected ? 'MQTT connected' : status?.mqtt_configured ? 'MQTT reconnecting' : 'MQTT not configured'}</span></section><section className="panel pairing"><h2>Pair a network sensor</h2><p>The selector above lists microphones already added to your account. For an ESP32, enter its device ID and the 32-character claim_code from device setup, not its device_token.</p><form onSubmit={pair}><label>Device ID<input required maxLength={64} pattern="[A-Za-z0-9_-]+" autoCapitalize="none" autoCorrect="off" value={deviceId} onChange={e => setDeviceId(e.target.value)}/></label><label>Pairing code<input type="password" required minLength={16} autoComplete="off" value={claimCode} onChange={e => setClaimCode(e.target.value)}/></label><button className="save" disabled={pairing}>{pairing ? 'Pairing…' : 'Pair microphone'}</button></form></section></>}
    <BrowserMicrophone onError={setError} onConnected={id => {setSelected(id); refresh().then(() => setSelected(id)).catch(e => setError(e.message));}}/>
    </div>
    <footer><span><b>loa.</b> Listen. Observe. Alert.</span><span>Relative sound monitoring · No raw audio stored · No construction classification</span></footer>
    </main></div>;
}
createRoot(document.getElementById('root')).render(<AuthGate>{account => <App key={account.id || 'demo'} account={account}/>}</AuthGate>);
