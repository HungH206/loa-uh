import React, {useEffect, useRef, useState} from 'react';
import {api} from './auth';

const feelings = ['Tolerable', 'Need to Cope', 'Sensitive'];
const when = t => new Date(t*1000).toLocaleString();

export function DisturbanceReports({onHome, storage}) {
  const [incident, setIncident] = useState(() => ({time:Date.now()/1000, id:crypto.randomUUID()}));
  const [stage, setStage] = useState('confirm'), [reports, setReports] = useState([]);
  const [draft, setDraft] = useState({location:'',description:'',feeling:'',details:''});
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [loading, setLoading] = useState(true);
  const submitting = useRef(false), heading = useRef(null);
  useEffect(() => {
    let stopped = false;
    api('reports').then(data => {if (!stopped) setReports(previous => [...new Map([...data,...previous].map(r => [r.id,r])).values()].sort((a,b) => b.occurred_at-a.occurred_at || String(b.id).localeCompare(String(a.id))).slice(0,50));}).catch(e => {if (!stopped) setError(e.message);}).finally(() => {if (!stopped) setLoading(false);});
    return () => {stopped = true;};
  }, []);
  useEffect(() => {heading.current?.focus();}, [stage]);
  async function submit(e) {
    e.preventDefault(); if (submitting.current) return;
    submitting.current = true; setBusy(true); setError('');
    try {
      const saved = await api('reports',{method:'POST',body:JSON.stringify({...draft,occurred_at:incident.time,request_id:incident.id})});
      setReports(previous => [saved,...previous.filter(r => r.id !== saved.id)].sort((a,b) => b.occurred_at-a.occurred_at || String(b.id).localeCompare(String(a.id))).slice(0,50));
      setStage('saved');
    } catch (e) {setError(e.message);} finally {submitting.current = false; setBusy(false);}
  }
  function again() {
    setIncident({time:Date.now()/1000,id:crypto.randomUUID()});
    setDraft({location:'',description:'',feeling:'',details:''}); setError(''); setStage('confirm');
  }
  const time = new Date(incident.time*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',hour12:false});
  return <div className="reports-page">
    <section className="panel report-panel">
      <div className="eyebrow">YOUR EXPERIENCE MATTERS</div>
      <h2 ref={heading} tabIndex={-1}>{stage === 'confirm' ? `Is there a noise disturbance at ${time}? Would you like to report it?` : stage === 'saved' ? 'Your report is saved.' : 'Report a noise disturbance'}</h2>
      <p>{when(incident.time)} · Your local time</p>
      {error && <div className="error" role="alert">{error}</div>}
      {stage === 'confirm' && <><p>Record what you heard and how it affected you. No microphone or sensor is required.</p><div className="report-actions"><button className="primary" onClick={() => setStage('form')}>Yes, take me to a form.</button><button className="export" onClick={onHome}>No, back to overview</button></div></>}
      {stage === 'form' && <form className="report-form" onSubmit={submit}>
        <label>Location <input autoFocus required maxLength={200} value={draft.location} onChange={e => setDraft({...draft,location:e.target.value})} placeholder="Where are you?"/></label>
        <label>How does it sound? <textarea required maxLength={2000} rows={4} value={draft.description} onChange={e => setDraft({...draft,description:e.target.value})} placeholder="Describe the noise you heard"/></label>
        <fieldset><legend>How do you feel?</legend><div className="feeling-options">{feelings.map((feeling,i) => <label key={feeling} className={draft.feeling === feeling ? 'chosen' : ''}><input type="radio" name="feeling" required value={feeling} checked={draft.feeling === feeling} onChange={() => setDraft({...draft,feeling})}/><span>{String.fromCharCode(65+i)} · {feeling}</span></label>)}</div></fieldset>
        <label>Other information <span className="optional">Optional</span><textarea maxLength={4000} rows={4} value={draft.details} onChange={e => setDraft({...draft,details:e.target.value})} placeholder="Any more details you’d like to record"/></label>
        <p>{storage === 'supabase' ? 'Your report will be saved privately to your cloud account. An internet connection is required.' : 'Your report will be saved privately in LOA.'} It is not sent to a landlord, authority, or other person.</p>
        <div className="report-actions"><button className="primary" disabled={busy}>{busy ? 'Saving…' : 'Save report'}</button><button type="button" className="export" disabled={busy} onClick={onHome}>Cancel</button></div>
      </form>}
      {stage === 'saved' && <div role="status"><p>{storage === 'supabase' ? 'Saved to your private cloud history. ' : ''}You can find it in your recent reports below.</p><div className="report-actions"><button className="primary" onClick={onHome}>Back to overview</button><button className="export" onClick={again}>Report another incident</button></div></div>}
    </section>
    <section className="panel report-history"><h2>Your recent reports</h2><p>Latest 50 incidents, newest first.</p>{loading ? <p className="empty">Loading reports…</p> : !reports.length ? <p className="empty">Your saved reports will appear here.</p> : reports.map(r => <article key={r.id}><div className="panel-heading"><h3>{r.location}</h3><span className="report-feeling">{r.feeling}</span></div><time dateTime={new Date(r.occurred_at*1000).toISOString()}>{when(r.occurred_at)}</time><p>{r.description}</p>{r.details && <p><strong>Other information: </strong>{r.details}</p>}</article>)}</section>
  </div>;
}
