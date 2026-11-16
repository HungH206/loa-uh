import React, {useEffect, useState} from 'react';
import {createClient} from '@supabase/supabase-js';

export const backendOrigin = (import.meta.env.VITE_BACKEND_URL || '').replace(/\/$/, '');
export let supabase = null;
export async function accessToken() {
  if (!supabase) return null;
  const {data, error} = await supabase.auth.getSession();
  if (error) throw error;
  return data.session?.access_token;
}
export async function api(path, options = {}) {
  const token = await accessToken();
  const response = await fetch(`${backendOrigin}/api/${path}`, {...options, headers: {
    ...(options.body ? {'Content-Type':'application/json'} : {}),
    ...options.headers, ...(token ? {Authorization:`Bearer ${token}`} : {})
  }});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`);
  }
  return response.json();
}

export function AuthGate({children}) {
  const [config, setConfig] = useState(null), [session, setSession] = useState(null);
  const [ready, setReady] = useState(false), [error, setError] = useState('');
  useEffect(() => {
    let stopped = false, subscription;
    fetch(`${backendOrigin}/api/config`).then(r => {if (!r.ok) throw new Error('Cannot load server configuration'); return r.json();}).then(async c => {
      if (stopped) return;
      setConfig(c);
      if (c.auth_mode === 'demo') {setReady(true); return;}
      if (!c.supabase_url || !c.supabase_publishable_key) throw new Error('Configure Supabase on the backend to enable accounts. See README.md.');
      if (!supabase) supabase = createClient(c.supabase_url, c.supabase_publishable_key);
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
