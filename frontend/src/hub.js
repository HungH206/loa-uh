// Choosing and checking which Raspberry Pi hub a cloud-hosted dashboard talks to.
// Pure helpers (no React, no Vite env) so they can be tested with node --test.

export const HUB_STORAGE_KEY = 'loa-hub-origin';
export const PI_CONNECT_URL = 'https://connect.raspberrypi.com/';

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);

/** Returns a clean origin such as https://pi.tailnet.ts.net, or throws an Error a person can act on. */
export function normalizeHubUrl(input, pageProtocol = 'https:') {
  let text = String(input || '').trim();
  if (!text) throw new Error('Enter your hub address.');
  if (!/^[a-z]+:\/\//i.test(text)) text = `${pageProtocol === 'http:' ? 'http' : 'https'}://${text}`;
  let url;
  try {url = new URL(text);} catch {throw new Error('That is not a valid address.');}
  if (url.protocol !== 'https:' && url.protocol !== 'http:') throw new Error('Use an http:// or https:// address.');
  if (url.username || url.password) throw new Error('Leave user names and passwords out of the address.');
  if (url.pathname !== '/' || url.search || url.hash) throw new Error('Use only the hub address, without a path (for example https://raspberrypi.your-tailnet.ts.net).');
  // Browsers block http:// requests from an https:// page (mixed content), except to this computer.
  if (pageProtocol === 'https:' && url.protocol === 'http:' && !LOCAL_HOSTS.has(url.hostname)) {
    throw new Error('This page uses HTTPS, so the hub needs an https:// address, such as its Tailscale address. A LAN http:// address only works when the dashboard is opened over http.');
  }
  return url.origin;
}

/**
 * Checks that origin is an LOA hub using the same Supabase project, then that this account can use it.
 * Returns {devices, mqtt_connected}. Throws an Error with the most likely cause.
 */
export async function checkHub(origin, {expectedSupabaseUrl, token, fetcher = fetch, timeoutMs = 6000}) {
  const get = async (path, headers = {}) => {
    try {
      return await fetcher(`${origin}/api/${path}`, {headers, signal: AbortSignal.timeout(timeoutMs)});
    } catch (error) {
      if (error?.name === 'TimeoutError') throw new Error('The hub did not answer. Check that it is on, and that this device is on the same network or signed in to Tailscale.');
      throw new Error('The hub could not be reached. Check the address and your network (Tailscale). If it is reachable, add this site to LOA_WEB_ORIGINS on the hub and restart it.');
    }
  };
  const configResponse = await get('config');
  if (!configResponse.ok) throw new Error(`That address answered, but not as an LOA hub (HTTP ${configResponse.status}).`);
  const config = await configResponse.json().catch(() => null);
  if (!config || config.unit !== 'dBFS' || !('auth_mode' in config)) throw new Error('That address is not an LOA hub.');
  if (config.auth_mode !== 'supabase') throw new Error('That hub runs in local demo mode, without accounts, so it cannot be used from this site.');
  const strip = value => String(value || '').replace(/\/$/, '');
  if (expectedSupabaseUrl && strip(config.supabase_url) !== strip(expectedSupabaseUrl)) {
    throw new Error('That hub uses a different account system (Supabase project), so your sign-in would not work there.');
  }
  const statusResponse = await get('status', token ? {Authorization: `Bearer ${token}`} : {});
  if (statusResponse.status === 401) throw new Error('The hub did not accept your sign-in. Sign out and in again, then retry.');
  if (!statusResponse.ok) throw new Error(`The hub reported a problem (HTTP ${statusResponse.status}).`);
  const status = await statusResponse.json();
  return {devices: status.devices?.length ?? 0, mqtt_connected: Boolean(status.mqtt_connected)};
}

export function loadHubOverride(storage) {
  try {return storage?.getItem(HUB_STORAGE_KEY) || '';} catch {return '';}
}

export function saveHubOverride(storage, origin) {
  try {
    if (origin) storage?.setItem(HUB_STORAGE_KEY, origin);
    else storage?.removeItem(HUB_STORAGE_KEY);
    return true;
  } catch {return false;}
}
