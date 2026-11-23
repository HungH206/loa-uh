import React, {useEffect, useRef, useState} from 'react';
import {Router, ExternalLink, RefreshCw, X} from 'lucide-react';
import {accessToken, backendOrigin, cloudMode, configuredHubOrigin, hubOverridden, retryHubNow, supabaseUrl} from './auth';
import {PI_CONNECT_URL, checkHub, normalizeHubUrl, saveHubOverride} from './hub.js';

const hostOf = origin => {try {return new URL(origin).host;} catch {return origin;}};

/** Header button + panel: see the hub connection, connect to a hub address, or open Raspberry Pi Connect. */
export function HubControl({hubOnline, liveConnected, onRetry}) {
  const [open, setOpen] = useState(false), [address, setAddress] = useState(backendOrigin);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('');
  const wrapper = useRef(null), input = useRef(null);
  const online = cloudMode ? hubOnline : liveConnected;
  const label = cloudMode && !backendOrigin ? 'Pi hub: not set up' : online ? 'Pi hub: connected' : 'Pi hub: offline';

  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    const close = e => {if (e.key === 'Escape' || (e.type === 'mousedown' && !wrapper.current?.contains(e.target))) setOpen(false);};
    document.addEventListener('keydown', close); document.addEventListener('mousedown', close);
    return () => {document.removeEventListener('keydown', close); document.removeEventListener('mousedown', close);};
  }, [open]);

  async function connect(e) {
    e.preventDefault(); setBusy(true); setError(''); setMessage('');
    try {
      const origin = normalizeHubUrl(address, location.protocol);
      setMessage(`Checking ${hostOf(origin)}…`);
      const result = await checkHub(origin, {expectedSupabaseUrl: supabaseUrl, token: await accessToken()});
      if (origin === backendOrigin) {
        retryHubNow(); await onRetry();
        setMessage(`Connected to ${hostOf(origin)} · ${result.devices} sensor(s) · MQTT ${result.mqtt_connected ? 'connected' : 'not connected'}.`);
      } else {
        if (!saveHubOverride(localStorage, origin === configuredHubOrigin ? '' : origin)) throw new Error('This browser blocked saving the address (private mode or blocked site data).');
        setMessage('Hub reachable. Reloading to connect…');
        location.reload();  // Reopens the live WebSocket and data requests against the new hub.
      }
    } catch (err) {setMessage(''); setError(err.message);} finally {setBusy(false);}
  }

  async function retry() {
    setError(''); setMessage('Retrying…'); retryHubNow();
    try {await onRetry(); setMessage('');} catch (err) {setMessage(''); setError(err.message);}
  }

  function useBuiltIn() {
    saveHubOverride(localStorage, '');
    location.reload();
  }

  return <div className="hub-control" ref={wrapper}>
    <button className={`hub-button ${online ? 'ok' : 'off'}`} aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen(!open)}>
      <Router size={14}/>{label}
    </button>
    {open && <section className="hub-panel" role="dialog" aria-label="Raspberry Pi hub connection">
      <div className="hub-panel-head"><h2>Raspberry Pi hub</h2><button className="icon-button" aria-label="Close" onClick={() => setOpen(false)}><X size={16}/></button></div>
      <p className="hub-status"><i className={online ? 'ok' : 'off'}/>{cloudMode
        ? backendOrigin ? `${online ? 'Connected to' : 'Cannot reach'} ${hostOf(backendOrigin)}${hubOverridden ? ' (chosen in this browser)' : ''}` : 'No hub address set for this site.'
        : `This dashboard talks to the hub at ${backendOrigin ? hostOf(backendOrigin) : location.host}. ${online ? 'Live connection is up.' : 'Reconnecting…'}`}</p>

      {cloudMode && <form onSubmit={connect}>
        <label htmlFor="hub-address">Hub address</label>
        <input id="hub-address" ref={input} inputMode="url" autoCapitalize="none" autoCorrect="off" spellCheck={false}
          placeholder="https://raspberrypi.your-tailnet.ts.net" value={address} onChange={e => setAddress(e.target.value)}/>
        <div className="hub-actions">
          <button className="save" disabled={busy}>{busy ? 'Checking…' : 'Connect'}</button>
          {backendOrigin && <button type="button" className="text-button" onClick={retry} disabled={busy}><RefreshCw size={13}/> Retry now</button>}
          {hubOverridden && <button type="button" className="text-button" onClick={useBuiltIn} disabled={busy}>Use the site's built-in address</button>}
        </div>
        <p className="hub-help">Use the Pi's HTTPS address (its Tailscale name) and keep this device signed in to Tailscale. The hub must list this site in <code>LOA_WEB_ORIGINS</code>. Only enter your own hub: your sign-in is sent to it.</p>
      </form>}
      {error && <p className="hub-error" role="alert">{error}</p>}
      {message && <p className="hub-message" role="status">{message}</p>}

      <div className="hub-connect">
        <h3>Raspberry Pi Connect</h3>
        <p>Opens the Pi's desktop or a terminal in your browser, from anywhere, for maintenance: restart the service, read logs, or open <code>http://localhost:8000</code> in the Pi's own browser. It cannot carry this dashboard's data connection.</p>
        <a className="hub-link" href={PI_CONNECT_URL} target="_blank" rel="noopener noreferrer"><ExternalLink size={14}/> Open Raspberry Pi Connect</a>
      </div>
    </section>}
  </div>;
}
