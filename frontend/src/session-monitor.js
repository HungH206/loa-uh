import {defaults} from './cloud-api.js';

// Browser readings stay in memory for this signed-in session. ESP32 data stays on the hub.
export class SessionMonitor {
  constructor(notify=()=>{}, now=()=>Date.now()/1000) {this.notify=notify;this.now=now;this.settings={...defaults};this.devices=[];this.rows=[];this.alerts=[];this.states=new Map();this.counter=0;}
  preferences(value) {for (const d of this.devices) this.stop(d.device_id);this.settings={...value};}
  register(label) {const device={device_id:'browser-'+crypto.randomUUID(),label,last_seen:null,last_source:'browser',local:true};this.devices.push(device);this.notify({type:'devices'});return device;}
  stop(id) {const s=this.states.get(id);if(s?.event && !s.event.ended_at){s.event.ended_at=s.last;s.event.end_reason='microphone_stopped';}this.states.delete(id);const d=this.devices.find(v=>v.device_id===id);if(d)d.last_seen=null;this.notify({type:'devices'});}
  record(data) {
    const d=this.devices.find(v=>v.device_id===data.device_id);if(!d)throw new Error('Start this microphone again.');
    const now=this.now();d.last_seen=now;
    const row={...data,id:'local-'+(++this.counter),source:'browser',received_at:now};this.rows.push(row);this.rows=this.rows.slice(-10000);
    let s=this.states.get(d.device_id);
    if(s && (now-s.last>2.5 || s.boot!==data.boot_id)){if(s.event&&!s.event.ended_at){s.event.ended_at=s.last;s.event.end_reason='sensor_gap';}s=null;}
    if(!s){s={boot:data.boot_id,last:now,above:null,lastAlert:null,event:null};this.states.set(d.device_id,s);}
    const p=this.settings;
    if(data.dbfs>=p.threshold){
      if(s.above===null)s.above=now;
      if(s.event&&!s.event.ended_at)s.event.peak_dbfs=Math.max(s.event.peak_dbfs,data.dbfs);
      else if(now-s.above>=p.duration_seconds && (s.lastAlert===null || now-s.lastAlert>=p.cooldown_seconds)){
        s.event={id:'local-alert-'+this.counter,device_id:d.device_id,started_at:s.above,triggered_at:now,peak_dbfs:data.dbfs,threshold:p.threshold,duration_seconds:p.duration_seconds,ended_at:null};s.lastAlert=now;this.alerts.unshift(s.event);this.alerts=this.alerts.slice(0,50);
      }
    }else{if(s.event&&!s.event.ended_at){s.event.ended_at=now;s.event.end_reason='below_threshold';}s.above=null;s.event=null;}
    s.last=now;this.notify({type:'reading',data:row});return row;
  }
}
