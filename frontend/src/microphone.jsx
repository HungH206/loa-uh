import React, {useEffect, useRef, useState} from 'react';
import {api} from './auth';

export function BrowserMicrophone({onConnected, onError}) {
  const [inputs, setInputs] = useState([]), [selected, setSelected] = useState('');
  const [active, setActive] = useState(false), [busy, setBusy] = useState(false);
  const resources = useRef(null), generation = useRef(0);
  function stop() {
    generation.current++;
    const r = resources.current;
    resources.current = null;
    if (r) {clearTimeout(r.timer); r.stream.getTracks().forEach(t => t.stop()); r.context?.close().catch(() => {});}
    setActive(false);
  }
  useEffect(() => {
    const hidden = () => {if (document.hidden) stop();};
    document.addEventListener('visibilitychange', hidden);
    return () => {document.removeEventListener('visibilitychange', hidden); stop();};
  }, []);
  async function start() {
    stop(); const attempt = generation.current; setBusy(true);
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error('Microphone access needs HTTPS, or localhost on this computer. A phone cannot use a plain HTTP LAN address.');
      const stream = await navigator.mediaDevices.getUserMedia({audio:{
        ...(selected ? {deviceId:{exact:selected}} : {}),
        echoCancellation:false, noiseSuppression:false, autoGainControl:false
      }});
      if (attempt !== generation.current) {stream.getTracks().forEach(t => t.stop()); return;}
      const r = {stream}; resources.current = r;
      const Audio = window.AudioContext || window.webkitAudioContext;
      r.context = new Audio(); await r.context.resume();
      const source = r.context.createMediaStreamSource(stream), analyser = r.context.createAnalyser();
      analyser.fftSize = 2048; source.connect(analyser);
      const devices = await navigator.mediaDevices.enumerateDevices();
      if (resources.current !== r) return;
      setInputs(devices.filter(d => d.kind === 'audioinput'));
      const track = stream.getAudioTracks()[0];
      setSelected(track.getSettings().deviceId || '');
      const registered = await api('devices/browser', {method:'POST',body:JSON.stringify({label:track.label || 'Browser microphone'})});
      if (resources.current !== r) return;
      const boot = crypto.randomUUID().replaceAll('-',''); let sequence = 0;
      const samples = new Float32Array(analyser.fftSize);
      setActive(true); onConnected(registered.device_id);
      track.onended = () => {if (resources.current === r) {stop(); onError('Microphone disconnected. Select an input and start again.');}};
      async function report() {
        if (resources.current !== r) return;
        if (r.context.state !== 'running') {stop(); onError('Microphone paused by the browser. Start again to resume.'); return;}
        analyser.getFloatTimeDomainData(samples);
        const mean = samples.reduce((s,v) => s+v,0)/samples.length;
        const rms = Math.sqrt(samples.reduce((s,v) => s+(v-mean)**2,0)/samples.length);
        try {
          await api('browser/readings',{method:'POST',body:JSON.stringify({device_id:registered.device_id,dbfs:Math.max(-120,Math.min(0,20*Math.log10(Math.max(rms,1e-6)))),boot_id:boot,sequence:sequence++})});
          if (resources.current === r) r.timer = setTimeout(report,1000);
        } catch (e) {if (resources.current === r) {stop(); onError(e.message);}}
      }
      r.timer = setTimeout(report,1000);
    } catch (e) {if (attempt === generation.current) {stop(); onError(e.name === 'NotAllowedError' ? 'Microphone permission was denied. Allow access in your browser’s site settings, then try again.' : e.message);}}
    finally {setBusy(false);}
  }
  return <section className="panel pairing"><h2>Use this device’s microphone</h2><p>Allow microphone access to use your phone, laptop, or a connected headset. Only sound-level numbers are sent; raw audio stays on this device.</p>
    {inputs.length > 0 && <label>Audio input <select value={selected} disabled={active || busy} onChange={e => setSelected(e.target.value)}>{inputs.map((d,i) => <option key={d.deviceId || i} value={d.deviceId}>{d.label || `Microphone ${i+1}`}</option>)}</select></label>}
    <button className="save" disabled={busy} onClick={active ? stop : start}>{busy ? 'Requesting microphone…' : active ? 'Stop microphone' : 'Allow microphone & start'}</button>
    <p role="status">{active ? 'Microphone active. Keep this page visible. Stop before switching inputs.' : 'Microphone off. Available inputs appear after permission is granted.'}</p><p>Headsets appear only when your operating system and browser expose their microphone. This measures ambient input, not headphone playback or listening volume. Monitoring stops when the page is hidden.</p></section>;
}
