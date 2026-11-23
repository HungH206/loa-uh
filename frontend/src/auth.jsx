import React, {useEffect, useState} from 'react';
import {createClient} from '@supabase/supabase-js';
import {cloudRequest} from './cloud-api';
import {SessionMonitor} from './session-monitor';
import {loadHubOverride, normalizeHubUrl} from './hub.js';

const env = import.meta.env;
export const cloudMode = Boolean(env.VITE_SUPABASE_URL && env.VITE_SUPABASE_PUBLISHABLE_KEY);
export const supabaseUrl = env.VITE_SUPABASE_URL || '';
// The hub address built into the site, and an optional one chosen in this browser (cloud mode only;
// a dashboard served by the Pi itself always talks to its own origin).
export const configuredHubOrigin = (env.VITE_BACKEND_URL || '').replace(/\/$/, '');
function storedHub() {
  if (!cloudMode || typeof localStorage === 'undefined') return '';
  try {return normalizeHubUrl(loadHubOverride(localStorage), location.protocol);} catch {return '';}
}
const chosenHub = storedHub();
export const hubOverridden = Boolean(chosenHub) && chosenHub !== configuredHubOrigin;
export const backendOrigin = chosenHub || configuredHubOrigin;
export let supabase = null;
let monitor, owner, hubCache = [], nextHubAttempt = 0;
function localMonitor(user) {
  if (owner !== user) {owner=user;hubCache=[];nextHubAttempt=0;monitor=new SessionMonitor(event => window.dispatchEvent(new CustomEvent('loa-local',{detail:event})));}
  return monitor;
}
export async function accessToken() {
  if (!supabase) return null;
  const {data,error} = await supabase.auth.getSession();
  if (error) throw error;
  return data.session?.access_token;
}
// Lets the hub button retry immediately instead of waiting out the 30-second back-off.
export function retryHubNow() {nextHubAttempt = 0;}
export async function hubApi(path,options={}) {
  const token = await accessToken();
  const response = await fetch(`${backendOrigin}/api/${path}`,{...options,signal:AbortSignal.timeout(5000),headers:{...(options.body ? {'Content-Type':'application/json'} : {}),...options.headers,...(token ? {Authorization:`Bearer ${token}`} : {})}});
  if (!response.ok) {const b=await response.json().catch(()=>({}));const e=new Error(typeof b.detail === 'string' ? b.detail : `Request failed (${response.status})`);e.status=response.status;throw e;}
  return response.json();
}
export async function api(path, options={}) {
  if (!cloudMode) return hubApi(path,options);
  const {data,error}=await supabase.auth.getSession();
  if(error || !data.session) throw new Error('Sign in again to continue.');
  const user=data.session.user.id, m=localMonitor(user), resource=path.split('?')[0];
  if(resource==='reports' || resource==='settings') {
    const result=await cloudRequest({url:env.VITE_SUPABASE_URL.replace(/\/$/,''),key:env.VITE_SUPABASE_PUBLISHABLE_KEY,token:data.session.access_token,user},path,options);
    if(resource==='settings') m.preferences(result);
    return result;
  }
  if(resource==='devices/browser') return m.register(JSON.parse(options.body).label);
  if(resource==='browser/readings') return m.record(JSON.parse(options.body));
  if(resource==='browser/stop'){m.stop(JSON.parse(options.body).device_id);return {};}
  if(resource==='devices/claim') {
    try{return await hubApi(path,options);}
    catch(e){
      // The hub answered (e.g. wrong or already-used code): show its reason, not "unavailable".
      if(e.status) throw new Error(e.status===403 ? `${e.message}. Pairing codes work once; a sensor that is already paired appears under Your microphone for the account that paired it.` : e.message);
      throw new Error(`Cannot pair while the hub is unavailable: ${e.message}`);
    }
  }
  if(['status','alerts','readings'].includes(resource)) {
    let remote=null;
    if(backendOrigin && Date.now()>=nextHubAttempt) {
      try{remote=await hubApi(path,options);}catch{nextHubAttempt=Date.now()+30000;}
    }
    if(resource==='status') {
      if(remote)hubCache=remote.devices;
      return {...remote,hub_online:Boolean(remote),mqtt_connected:remote?.mqtt_connected || false,mqtt_configured:Boolean(backendOrigin),devices:[...m.devices,...hubCache.map(d=>remote ? d : {...d,last_seen:null})]};
    }
    if(resource==='alerts')return [...m.alerts,...(remote || [])].sort((a,b)=>b.triggered_at-a.triggered_at).slice(0,100);
    const cutoff=Date.now()/1000-Number(new URLSearchParams(path.split('?')[1]).get('minutes') || 60)*60;
    return [...(remote || []),...m.rows.filter(r=>r.received_at>=cutoff)];
  }
  return hubApi(path,options);
}

export function AuthGate({children}) {
  const [config, setConfig] = useState(null), [session, setSession] = useState(null);
  const [ready, setReady] = useState(false), [error, setError] = useState('');
  useEffect(() => {
    let stopped = false, subscription;
    (cloudMode ? Promise.resolve({auth_mode:'supabase',supabase_url:env.VITE_SUPABASE_URL,supabase_publishable_key:env.VITE_SUPABASE_PUBLISHABLE_KEY,report_store:'supabase'}) : fetch(`${backendOrigin}/api/config`,{signal:AbortSignal.timeout(8000)}).then(r => {if (!r.ok) throw new Error('Cannot load server configuration'); return r.json();})).then(async c => {
      if (stopped) return;
      setConfig(c);
      if (c.auth_mode === 'demo') {setReady(true); return;}
      if (!c.supabase_url || !c.supabase_publishable_key) throw new Error('Configure Supabase on the backend to enable accounts. See README.md.');
      if (!supabase) supabase = createClient(c.supabase_url, c.supabase_publishable_key, {global:{fetch:(url,options={}) => fetch(url,{...options,signal:options.signal || AbortSignal.timeout(12000)})}});
      const {data} = supabase.auth.onAuthStateChange((_event, s) => {if (!stopped) {setSession(s); setReady(true);}});
      subscription = data.subscription;
      const {data: initial, error: authError} = await supabase.auth.getSession();
      if (authError) throw authError;
      if (!stopped) {setSession(initial.session); setReady(true);}
    }).catch(e => {if (!stopped) setError(e.message);});
    return () => {stopped = true; subscription?.unsubscribe();};
  }, []);
  if (error) return <div className="auth-page"><section className="auth-card"><h1>Connect your workspace</h1><p role="alert">{error}</p><button onClick={() => location.reload()}>Try again</button></section></div>;
  if (!ready) return <div className="auth-page"><p>Opening LOA…</p></div>;
  if (config.auth_mode === 'demo') return children({demo:true, email:'Local demo', reportStore:config.report_store});
  if (session) return children({demo:false, email:session.user.email, id:session.user.id, reportStore:config.report_store});
  return <SignIn/>;
}

function SignIn() {
  const [mode, setMode] = useState('signin'), [email, setEmail] = useState(''), [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [error, setError] = useState('');
  async function submit(e) {
    e.preventDefault(); setBusy(true); setError(''); setMessage('');
    try {
      const result = mode === 'signup' ? await supabase.auth.signUp({email, password}) : await supabase.auth.signInWithPassword({email, password});
      if (result.error) throw result.error;
      if (mode === 'signup' && !result.data.session) setMessage('Check your email to confirm your account, then return here to sign in.');
    } catch (e) {setError(e.message);} finally {setBusy(false);}
  }
  return <div className="auth-page"><section className="auth-card"><a className="auth-brand" href="/">loa.</a><div className="eyebrow">LISTEN. OBSERVE. ALERT.</div><h1>Your quiet, your way.</h1><p>Sign in to pair your microphone and choose your personal sound tolerance.</p><form onSubmit={submit}><label>Email<input type="email" autoComplete="email" required value={email} onChange={e => setEmail(e.target.value)}/></label><label>Password<input type="password" minLength={8} autoComplete={mode === 'signup' ? 'new-password' : 'current-password'} required value={password} onChange={e => setPassword(e.target.value)}/></label>{error && <p className="error" role="alert">{error}</p>}{message && <p role="status">{message}</p>}<button className="primary" disabled={busy}>{busy ? 'Please wait…' : mode === 'signup' ? 'Create account' : 'Sign in'}</button></form><button className="text-button" onClick={() => {setMode(mode === 'signup' ? 'signin' : 'signup'); setError(''); setMessage('');}}>{mode === 'signup' ? 'Already have an account? Sign in' : 'New to LOA? Create an account'}</button><p className="privacy-note">Choose a paired sensor or explicitly allow this device’s microphone after signing in.</p></section></div>;
}
