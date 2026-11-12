import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Activity, Radio, Bell, ArrowUpRight, Volume2, SlidersHorizontal, Download, Waves} from 'lucide-react';
import {ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, ReferenceLine, CartesianGrid} from 'recharts';
import './style.css';

async function api(path, options) {
  const res = await fetch('/api/'+path, options);
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
  return res.json();
}
const clock = t => new Date(t*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});

function App(){
  const [rows,setRows]=useState([]), [alerts,setAlerts]=useState([]), [status,setStatus]=useState(null);
  const [threshold,setThreshold]=useState(70),[draft,setDraft]=useState(70),[range,setRange]=useState(60);
  const [connected,setConnected]=useState(false),[error,setError]=useState(''),[tab,setTab]=useState('Overview'),[saving,setSaving]=useState(false);
  useEffect(()=>{
    let disposed=false;
    api(`readings?minutes=${range}&limit=10000`).then(data=>{if(!disposed)setRows(data)}).catch(e=>setError(e.message));
    return()=>{disposed=true};
  },[range]);
  useEffect(()=>{
    let stopped=false, socket, retry;
    const refresh=()=>Promise.all([api('alerts'),api('status')]).then(([a,s])=>{if(!stopped){setAlerts(a);setStatus(s)}}).catch(e=>{if(!stopped)setError(e.message)});
    api('settings').then(s=>{setThreshold(s.threshold);setDraft(s.threshold)}).catch(e=>setError(e.message));
    refresh();
    const open=()=>{
      socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws`);
      socket.onopen=()=>{setConnected(true);setError('')};
      socket.onmessage=e=>{const event=JSON.parse(e.data);if(event.type==='reading')setRows(r=>[...r,event.data].slice(-10000));if(event.type==='settings'){setThreshold(event.data.threshold);setDraft(event.data.threshold)}};
      socket.onclose=()=>{setConnected(false);if(!stopped)retry=setTimeout(()=>{api(`readings?minutes=10080&limit=10000`).then(setRows).catch(()=>{});open()},2000)};
    };
    open();const timer=setInterval(refresh,5000);
    return()=>{stopped=true;clearInterval(timer);clearTimeout(retry);socket?.close()};
  },[]);
  const visible=rows.filter(r=>r.received_at>Date.now()/1000-range*60), latest=rows.at(-1), value=latest?.value;
  const avg=visible.length?visible.reduce((s,r)=>s+r.value,0)/visible.length:0;
  const peak=visible.length?Math.max(...visible.map(r=>r.value)):0;
  const loud=value>threshold;
  async function save(e){e.preventDefault();setSaving(true);try{const s=await api('settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({threshold:Number(draft)})});setThreshold(s.threshold);setError('')}catch(e){setError(e.message)}finally{setSaving(false)}}
  function download(){const csv='received_at,device_id,source,value,sequence\n'+visible.map(r=>[new Date(r.received_at*1000).toISOString(),`"${r.device_id.replaceAll('"','""')}"`,r.source,r.value,r.sequence??''].join(',')).join('\n');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));const a=document.createElement('a');a.href=url;a.download='loa-readings.csv';a.click();URL.revokeObjectURL(url)}
  return <div className="shell">
    <aside><a className="brand" href="/"><span className="brand-icon"><Waves/></span>loa<span className="brand-dot">.</span></a><div className="workspace">YOUR WORKSPACE <span>01</span></div><nav>{[['Overview',Activity],['Noise history',Waves],['Alerts',Bell],['Settings',SlidersHorizontal]].map(([name,Icon])=><button key={name} className={tab===name?'active':''} onClick={()=>setTab(name)}><Icon size={18}/>{name}{name==='Alerts'&&<small>{alerts.length}</small>}</button>)}</nav><div className="aside-bottom"><Radio size={20}/><strong>Small device.<br/>A quieter perspective.</strong><p>Listen. Observe. Alert.</p><span>LOA / NETWORKING PROJECT</span></div></aside>
    <main><header><span>WORKSPACE <b>/</b> {tab}</span><div className="live"><i className={connected?'':'off'}/>{connected?'Live connection':'Reconnecting…'}</div></header>
    <section className="title"><div><div className="eyebrow">YOUR ENVIRONMENT, IN FOCUS</div><h1>{tab==='Overview'?'A little more aware.':tab==='Noise history'?'Every moment, measured.':tab==='Alerts'?'Know when it gets loud.':'Make quiet your own.'}</h1><p>Understand the sound around you. Make room for quiet.</p></div><button className="export" onClick={download}><Download size={16}/> Export data</button></section>
    {error&&<div role="alert" className="error">{error}</div>}
    {status?.simulator&&<div className="demo"><span>SIMULATION MODE</span> Readings are generated for development. Hardware is not required.</div>}
    {(tab==='Overview'||tab==='Noise history')&&<><div className="stats"><section className={'current '+(loud?'loud':'')}><div className="card-label"><span><Volume2 size={16}/> CURRENT SOUND LEVEL</span><span className="badge">{latest?.source==='simulator'?'DEMO':'SENSOR'}</span></div><div className="reading">{value?.toFixed(1)??'—'}<span>dB*</span></div><div className="current-footer"><span><i/>{value==null?'Waiting for readings':loud?'Above your threshold':'Within your threshold'}</span><Waves size={48}/></div></section><section className="stat"><span className="card-label">AVERAGE LEVEL <Activity size={16}/></span><div className="number">{visible.length?avg.toFixed(1):'—'}<span>dB*</span></div><p>Across the selected time range</p><div className="mini-bars">{Array.from({length:24},(_,i)=><i key={i} style={{height:12+(i*17%35)}}/>)}</div></section><section className="stat"><span className="card-label">PEAK LEVEL <ArrowUpRight size={17}/></span><div className="number">{visible.length?peak.toFixed(1):'—'}<span>dB*</span></div><p>Highest recorded in this range</p><div className="peak-label">{peak>threshold?'Threshold exceeded':'Under threshold'} <span>↗</span></div></section></div><section className="chart-panel"><div className="panel-heading"><div><h2>Sound over time <span className="live-dot"/></h2><p>A live picture of your acoustic environment.</p></div><select aria-label="History time range" value={range} onChange={e=>setRange(Number(e.target.value))}><option value={5}>Last 5 minutes</option><option value={60}>Last hour</option><option value={1440}>Last 24 hours</option><option value={10080}>Last 7 days</option></select></div><div className="legend"><span><i/> Sound level</span><span><i/> Threshold · {threshold} dB*</span></div><div className="chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={visible} margin={{top:10,right:10,left:-20,bottom:0}}><defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#89b39c" stopOpacity={.4}/><stop offset="100%" stopColor="#89b39c" stopOpacity={0}/></linearGradient></defs><CartesianGrid vertical={false} stroke="#edf0ed"/><XAxis dataKey="received_at" tickFormatter={clock} minTickGap={65} tick={{fontSize:11,fill:'#89918b'}} axisLine={false} tickLine={false}/><YAxis domain={[0,150]} tick={{fontSize:11,fill:'#89918b'}} axisLine={false} tickLine={false}/><Tooltip labelFormatter={clock} formatter={v=>[`${v} dB*`,'Sound level']}/><ReferenceLine y={threshold} stroke="#c6985e" strokeDasharray="5 5"/><Area type="monotone" dataKey="value" stroke="#518c6d" strokeWidth={2} fill="url(#fill)" isAnimationActive={false}/></AreaChart></ResponsiveContainer></div>{!visible.length&&<p className="empty">Waiting for measurements in this range.</p>}</section></>}
    <div className="bottom-grid">{tab!=='Settings'&&<section className="panel"><div className="panel-heading"><h2>Recent alerts</h2><Bell size={17}/></div>{alerts.length?alerts.slice(0,tab==='Alerts'?50:3).map(a=><div className="alert-row" key={a.id}><span className="alert-icon"><Volume2 size={17}/></span><div><strong>Noise threshold exceeded</strong><p>{a.device_id} · {a.value.toFixed(1)} dB* / limit {a.threshold}</p></div><time>{clock(a.timestamp)}</time></div>):<p className="empty">All quiet so far. Alerts appear when a reading crosses your threshold.</p>}</section>}{tab!=='Alerts'&&<section className="panel threshold"><div className="panel-heading"><h2>Your quiet zone</h2><SlidersHorizontal size={17}/></div><p>Get an alert when sound crosses this level.</p><form onSubmit={save}><label htmlFor="threshold">Noise threshold <strong>{draft} <small>dB*</small></strong></label><input id="threshold" type="range" min="20" max="140" value={draft} onChange={e=>setDraft(e.target.value)}/><div className="range-label"><span>20 · Quiet</span><span>140 · Loud</span></div><button className="save" disabled={saving}>{saving?'Saving…':'Save threshold'}</button></form></section>}</div>
    {(tab==='Settings'||tab==='Overview')&&<section className="device-strip"><Radio size={19}/><div><strong>Device connections</strong><p>{status?.devices.length?status.devices.map(d=>`${d.id}: ${d.online?'online':'offline'}`).join(' · '):'Waiting for a sensor connection'}</p></div><span>{status?.mqtt_configured?'MQTT configured':'MQTT not configured'}</span></section>}
    <footer><span><b>loa.</b> Listen. Observe. Alert.</span><span>* Uncalibrated sound estimate. Demo values are simulated.</span></footer>
    </main></div>
}
createRoot(document.getElementById('root')).render(<App/>);
