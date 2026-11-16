export const defaults = {threshold:-40,duration_seconds:3,cooldown_seconds:30};
const reportFields = 'id,occurred_at,created_at,location,description,feeling,details';
export const reportFromCloud = row => ({...row,occurred_at:Date.parse(row.occurred_at)/1000,created_at:Date.parse(row.created_at)/1000});

export async function cloudRequest({url,key,token,user,fetcher=fetch}, path, options={}) {
  const [resource] = path.split('?');
  const method = options.method || 'GET';
  const body = options.body ? JSON.parse(options.body) : null;
  let table, params, payload, prefer;
  if (resource === 'reports') {
    table = 'incident_reports';
    if (method === 'POST') {
      if (!body.location?.trim() || !body.description?.trim() || !['Tolerable','Need to Cope','Sensitive'].includes(body.feeling)) throw new Error('Fill in location, sound description, and how you feel.');
      if (!Number.isFinite(body.occurred_at) || body.occurred_at > Date.now()/1000+60) throw new Error('Check the incident time and your device clock.');
      payload = {user_id:user,request_id:body.request_id,occurred_at:new Date(body.occurred_at*1000).toISOString(),location:body.location.trim(),description:body.description.trim(),feeling:body.feeling,details:(body.details || '').trim()};
      params = {on_conflict:'user_id,request_id'}; prefer = 'resolution=ignore-duplicates,return=minimal';
    } else params = {select:reportFields,user_id:`eq.${user}`,order:'occurred_at.desc,id.desc',limit:'50'};
  } else if (resource === 'settings') {
    table = 'user_preferences';
    if (method === 'PUT') {
      if (!Number.isFinite(body.threshold) || body.threshold < -90 || body.threshold > -1 || !Number.isInteger(body.duration_seconds) || body.duration_seconds < 1 || body.duration_seconds > 60 || !Number.isInteger(body.cooldown_seconds) || body.cooldown_seconds < 0 || body.cooldown_seconds > 3600) throw new Error('Enter valid tolerance, duration, and alert interval values.');
      payload = {user_id:user,threshold:body.threshold,duration_seconds:body.duration_seconds,cooldown_seconds:body.cooldown_seconds};
      params = {on_conflict:'user_id'};prefer = 'resolution=merge-duplicates,return=representation';
    } else params = {select:'threshold,duration_seconds,cooldown_seconds',user_id:`eq.${user}`,limit:'1'};
  } else throw new Error('Unsupported cloud operation');
  async function request(verb, query, data, preference) {
    let response;
    try {response = await fetcher(`${url}/rest/v1/${table}?${new URLSearchParams(query)}`,{method:verb,headers:{apikey:key,Authorization:`Bearer ${token}`,...(data ? {'Content-Type':'application/json'} : {}),...(preference ? {Prefer:preference} : {})},...(data ? {body:JSON.stringify(data)} : {}),signal:AbortSignal.timeout(12000)});}
    catch {throw new Error('Cloud connection unavailable. Your changes were not confirmed saved; please retry.');}
    if (!response.ok) throw new Error(response.status === 401 ? 'Sign in again to continue.' : 'Cloud data is unavailable. Check the database setup and access policies.');
    return response.status === 204 || preference?.includes('return=minimal') ? null : response.json();
  }
  const rows = await request(method === 'PUT' ? 'POST' : method,params,payload,prefer);
  if (resource === 'reports') {
    if (method === 'GET') return rows.map(reportFromCloud);
    const saved = await request('GET',{select:reportFields,user_id:`eq.${user}`,request_id:`eq.${body.request_id}`,limit:'1'});
    if (!saved.length) throw new Error('The report could not be confirmed saved. Please retry.');
    return reportFromCloud(saved[0]);
  }
  return rows[0] ? {threshold:rows[0].threshold,duration_seconds:rows[0].duration_seconds,cooldown_seconds:rows[0].cooldown_seconds} : {...defaults};
}
