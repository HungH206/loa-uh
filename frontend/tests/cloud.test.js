import {test} from 'node:test';
import assert from 'node:assert/strict';
import {cloudRequest} from '../src/cloud-api.js';
import {SessionMonitor} from '../src/session-monitor.js';

const identity={url:'https://cloud.example',key:'public-test',token:'user-token',user:'alice'};
test('reports go directly to cloud with session identity and deduplication',async()=>{
 const calls=[];
 const row={id:'row',occurred_at:'2026-11-15T20:00:00Z',created_at:'2026-11-15T20:00:01Z',location:'Home',description:'Drilling',feeling:'Sensitive',details:''};
 const fetcher=async(url,o)=>{calls.push({url,o});return new Response(o.method==='POST' ? null : JSON.stringify([row]),{status:o.method==='POST'?201:200});};
 const saved=await cloudRequest({...identity,fetcher},'reports',{method:'POST',body:JSON.stringify({request_id:'request',occurred_at:1794772800,location:' Home ',description:'Drilling',feeling:'Sensitive',user_id:'bob'})});
 assert.equal(saved.id,'row');assert.equal(typeof saved.occurred_at,'number');
 assert.equal(JSON.parse(calls[0].o.body).user_id,'alice');assert.match(calls[0].o.headers.Prefer,/ignore-duplicates/);
 assert.equal(calls[0].o.headers.Authorization,'Bearer user-token');assert.match(calls[1].url,/user_id=eq.alice/);
});
test('cloud failure never reports successful local save',async()=>{
 await assert.rejects(()=>cloudRequest({...identity,fetcher:async()=>{throw Error('offline');}},'reports'),/not confirmed saved/);
 await assert.rejects(()=>cloudRequest({...identity,fetcher:async()=>new Response('{}',{status:403})},'reports'),/access policies/);
});
test('cloud preferences use account ownership and validate duration',async()=>{
 let sent;
 const fetcher=async(url,o)=>{sent=JSON.parse(o.body);return new Response(JSON.stringify([sent]));};
 const body={threshold:-30,duration_seconds:3,cooldown_seconds:30};
 assert.deepEqual(await cloudRequest({...identity,fetcher},'settings',{method:'PUT',body:JSON.stringify(body)}),body);
 assert.equal(sent.user_id,'alice');
 await assert.rejects(()=>cloudRequest({...identity,fetcher},'settings',{method:'PUT',body:JSON.stringify({...body,duration_seconds:0})}),/valid tolerance/);
});
test('browser monitoring and duration alerts work without network',()=>{
 let now=1000;const events=[];const m=new SessionMonitor(e=>events.push(e),()=>now);
 const d=m.register('Mac microphone');
 for(let i=0;i<4;i++){now=1000+i;m.record({device_id:d.device_id,dbfs:-30,boot_id:'one'});}
 assert.equal(m.rows.length,4);assert.equal(m.alerts.length,1);assert.equal(m.alerts[0].triggered_at,1003);
 now=1004;m.record({device_id:d.device_id,dbfs:-60,boot_id:'one'});
 assert.equal(m.alerts[0].end_reason,'below_threshold');
 m.stop(d.device_id);assert.equal(d.last_seen,null);
 assert.equal(events.filter(e=>e.type==='reading').length,5);
});
test('reporting gaps cannot satisfy duration and separate sessions share no data',()=>{
 let now=1000;const m=new SessionMonitor(()=>{},()=>now);const d=m.register('Input');
 m.record({device_id:d.device_id,dbfs:-20,boot_id:'one'});now=1010;m.record({device_id:d.device_id,dbfs:-20,boot_id:'one'});
 assert.equal(m.alerts.length,0);assert.equal(new SessionMonitor().rows.length,0);
});
