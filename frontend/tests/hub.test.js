import {test} from 'node:test';
import assert from 'node:assert/strict';
import {checkHub, normalizeHubUrl, loadHubOverride, saveHubOverride, HUB_STORAGE_KEY} from '../src/hub.js';

test('hub addresses are normalized and unsafe ones explained', () => {
  assert.equal(normalizeHubUrl(' raspberrypi.tail1.ts.net/ ', 'https:'), 'https://raspberrypi.tail1.ts.net');
  assert.equal(normalizeHubUrl('http://192.168.68.61:8000', 'http:'), 'http://192.168.68.61:8000');
  assert.equal(normalizeHubUrl('http://localhost:8000', 'https:'), 'http://localhost:8000');
  assert.throws(() => normalizeHubUrl('http://192.168.68.61:8000', 'https:'), /needs an https/);
  assert.throws(() => normalizeHubUrl('https://pi.ts.net/api', 'https:'), /without a path/);
  assert.throws(() => normalizeHubUrl('https://user:pw@pi.ts.net', 'https:'), /passwords/);
  assert.throws(() => normalizeHubUrl('ftp://pi', 'https:'), /http/);
  assert.throws(() => normalizeHubUrl('', 'https:'), /Enter/);
});

const reply = (status, body) => new Response(JSON.stringify(body), {status});
const config = {auth_mode: 'supabase', unit: 'dBFS', supabase_url: 'https://proj.supabase.co'};

test('a hub is accepted only if it is LOA, same Supabase project, and accepts the sign-in', async () => {
  const calls = [];
  const fetcher = async (url, options) => {
    calls.push({url, auth: options.headers.Authorization});
    return url.endsWith('/config') ? reply(200, config) : reply(200, {devices: [{}, {}], mqtt_connected: true});
  };
  assert.deepEqual(await checkHub('https://pi', {expectedSupabaseUrl: 'https://proj.supabase.co/', token: 't', fetcher}), {devices: 2, mqtt_connected: true});
  assert.equal(calls[0].auth, undefined);  // No sign-in sent until the hub proves it is LOA on the same project.
  assert.equal(calls[1].auth, 'Bearer t');
  const sent = [];
  const otherProject = async url => {sent.push(url); return reply(200, {...config, supabase_url: 'https://evil.supabase.co'});};
  await assert.rejects(checkHub('https://pi', {expectedSupabaseUrl: 'https://proj.supabase.co', token: 't', fetcher: otherProject}), /different account system/);
  assert.equal(sent.length, 1);
  await assert.rejects(checkHub('https://pi', {fetcher: async () => reply(200, {hello: 1})}), /not an LOA hub/);
  await assert.rejects(checkHub('https://pi', {fetcher: async () => reply(200, {...config, auth_mode: 'demo'})}), /demo mode/);
  await assert.rejects(checkHub('https://pi', {fetcher: async () => {throw new TypeError('Failed to fetch');}}), /LOA_WEB_ORIGINS/);
  await assert.rejects(checkHub('https://pi', {fetcher: async () => {throw Object.assign(new Error('t'), {name: 'TimeoutError'});}}), /did not answer/);
  await assert.rejects(checkHub('https://pi', {token: 'x', fetcher: async url => url.endsWith('/config') ? reply(200, config) : reply(401, {})}), /did not accept your sign-in/);
});

test('hub choice storage tolerates blocked storage', () => {
  const data = {};
  const storage = {getItem: k => data[k] ?? null, setItem: (k, v) => {data[k] = v;}, removeItem: k => {delete data[k];}};
  assert.ok(saveHubOverride(storage, 'https://pi')); assert.equal(data[HUB_STORAGE_KEY], 'https://pi');
  assert.equal(loadHubOverride(storage), 'https://pi');
  assert.ok(saveHubOverride(storage, '')); assert.equal(loadHubOverride(storage), '');
  const blocked = {getItem() {throw new Error('blocked');}, setItem() {throw new Error('blocked');}};
  assert.equal(loadHubOverride(blocked), ''); assert.equal(saveHubOverride(blocked, 'x'), false);
});
