import asyncio
import importlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('LOA_DB', str(tmp_path / 'test.sqlite3'))
    monkeypatch.setenv('LOA_SIMULATOR', 'false')
    monkeypatch.setenv('LOA_AUTH_MODE', 'supabase')
    monkeypatch.delenv('MQTT_HOST', raising=False)
    monkeypatch.setenv('SUPABASE_URL', 'https://example.supabase.co')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'sb_publishable_test')
    from backend import main
    importlib.reload(main)
    async def verify(token):
        if token not in ('alice', 'bob'):
            raise main.HTTPException(401, 'Invalid token')
        return token
    monkeypatch.setattr(main, 'verify_token', verify)
    with TestClient(main.app) as client:
        with main.connect() as db:
            for device_id in ('sensor-a', 'sensor-b'):
                db.execute('''INSERT INTO devices(device_id,label,claim_hash,token_hash,created_at)
                    VALUES(?,?,?,?,?)''', (device_id, device_id, main.digest('pairing-code-123456'), main.digest('device-secret'), 1))
        yield main, client


def auth(user='alice'):
    return {'Authorization': f'Bearer {user}'}


def pair(client, device='sensor-a', user='alice'):
    return client.post('/api/devices/claim', headers=auth(user), json={'device_id': device, 'claim_code':'pairing-code-123456'})


def send(client, value=-30, sequence=1, device='sensor-a', boot='boot-one'):
    return client.post('/api/readings', headers=auth('device-secret'), json={
        'device_id':device, 'dbfs':value, 'sequence':sequence, 'boot_id':boot})


def test_accounts_pairing_and_private_history(setup):
    main, client = setup
    for endpoint in ('readings','settings','status','alerts'):
        assert client.get('/api/'+endpoint).status_code == 401
    assert pair(client).status_code == 200
    assert pair(client, user='bob').status_code == 403
    assert pair(client, device='sensor-b', user='bob').status_code == 200
    assert send(client).status_code == 201
    assert send(client, device='sensor-b').status_code == 201
    assert [r['device_id'] for r in client.get('/api/readings', headers=auth()).json()] == ['sensor-a']
    assert [d['device_id'] for d in client.get('/api/status', headers=auth('bob')).json()['devices']] == ['sensor-b']
    assert client.put('/api/settings', headers=auth(), json={'threshold':-50,'duration_seconds':2,'cooldown_seconds':10}).status_code == 200
    assert client.get('/api/settings', headers=auth('bob')).json()['threshold'] == -40
    assert client.post('/api/readings', headers=auth('alice'), json={'device_id':'sensor-a','dbfs':-30}).status_code == 401
    assert client.post('/api/readings', json={'device_id':'sensor-a','dbfs':-30}).status_code == 401
    with TestClient(main.app) as reopened:
        assert reopened.get('/api/settings', headers=auth()).json()['threshold'] == -50
        assert len(reopened.get('/api/readings', headers=auth()).json()) == 1


def test_duration_peak_cooldown_and_deduplication(setup, monkeypatch):
    main, client = setup
    assert pair(client).status_code == 200
    now = [1000.0]
    monkeypatch.setattr(main.time, 'time', lambda: now[0])
    # Pairing timestamp should precede these synthetic samples.
    with main.connect() as db:
        db.execute('UPDATE devices SET claimed_at=0')
    client.put('/api/settings', headers=auth(), json={'threshold':-40,'duration_seconds':3,'cooldown_seconds':10})
    def sample(t, value, seq):
        now[0] = t
        assert send(client, value, seq).status_code == 201
    for t, value in [(1000,-30),(1001,-20),(1002,-25)]:
        sample(t,value,int(t))
    assert client.get('/api/alerts',headers=auth()).json() == []
    sample(1003,-35,1003)
    sample(1004,-15,1004)
    events = client.get('/api/alerts',headers=auth()).json()
    assert len(events) == 1 and events[0]['peak_dbfs'] == -15
    assert events[0]['started_at'] == 1000 and events[0]['ended_at'] is None
    # QoS/retry duplicates must not extend or retrigger events.
    assert send(client,-1,1004).json() == {'duplicate':True}
    sample(1005,-60,1005)
    assert client.get('/api/alerts',headers=auth()).json()[0]['end_reason'] == 'below_threshold'
    for t in range(1006,1014): sample(t,-30,t)
    assert len(client.get('/api/alerts',headers=auth()).json()) == 2
    assert client.get('/api/alerts',headers=auth('bob')).json() == []
    assert len(client.get('/api/readings',headers=auth()).json()) == 14


def test_gaps_reboots_and_setting_changes_reset_duration(setup, monkeypatch):
    main, client = setup
    pair(client)
    now = [1000.0]
    monkeypatch.setattr(main.time, 'time', lambda: now[0])
    for t in [1000,1001,1010,1011]:
        now[0] = t
        send(client,-30,int(t))
    assert client.get('/api/alerts',headers=auth()).json() == []
    # Reboot restarts duration and sequence zero is not confused with earlier boots.
    now[0] = 1012
    send(client,-30,0,boot='boot-two')
    for t in [1013,1014,1015]:
        now[0] = t
        send(client,-30,int(t),boot='boot-two')
    assert len(client.get('/api/alerts',headers=auth()).json()) == 1
    now[0] = 1020
    with main.connect() as db: main.expire_states(db,now[0])
    event = client.get('/api/alerts',headers=auth()).json()[0]
    assert event['end_reason'] == 'sensor_gap' and event['ended_at'] == 1015
    send(client,-30,1020,boot='boot-two')
    client.put('/api/settings',headers=auth(),json={'threshold':-20,'duration_seconds':1,'cooldown_seconds':0})
    now[0] = 1021
    send(client,-30,1021,boot='boot-two')
    assert len(client.get('/api/alerts',headers=auth()).json()) == 1


def test_validation_and_mqtt_topic_binding(setup):
    main, client = setup
    pair(client)
    for value in [1,-121]: assert send(client,value).status_code == 422
    assert client.post('/api/readings',headers=auth('device-secret'),json={'device_id':'sensor-a','value':58.2}).status_code == 422
    assert client.put('/api/settings',headers=auth(),json={'threshold':70}).status_code == 422
    assert client.put('/api/settings',headers=auth(),json={'threshold':-40,'duration_seconds':0}).status_code == 422
    assert client.get('/api/readings?limit=10001',headers=auth()).status_code == 422
    payload = json.dumps({'device_id':'sensor-a','dbfs':-30})
    assert main.mqtt_message('loa/sensor-a/noise',payload).dbfs == -30
    with pytest.raises(ValueError): main.mqtt_message('loa/sensor-b/noise',payload)
    with pytest.raises(main.HTTPException): asyncio.run(main.ingest(main.Reading(device_id='unknown',dbfs=-20),'mqtt'))


def test_websocket_ownership_and_authentication(setup):
    _, client = setup
    pair(client)
    pair(client,'sensor-b','bob')
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'type':'auth','token':'alice'})
        assert ws.receive_json()['type'] == 'ready'
        send(client,-30,1,device='sensor-b')
        send(client,-30,1)
        message = ws.receive_json()
        assert message['type'] == 'reading' and message['data']['device_id'] == 'sensor-a'
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'type':'auth','token':'bad-token'})
        assert ws.receive()['code'] == 4401


def test_preclaim_history_hidden(setup):
    main, client = setup
    send(client)
    assert pair(client).status_code == 200
    assert client.get('/api/readings',headers=auth()).json() == []


def test_demo_is_explicit_and_uses_dbfs(tmp_path, monkeypatch):
    monkeypatch.setenv('LOA_DB',str(tmp_path/'demo.sqlite3'))
    monkeypatch.setenv('LOA_AUTH_MODE','demo')
    monkeypatch.setenv('LOA_SIMULATOR','true')
    monkeypatch.delenv('MQTT_HOST',raising=False)
    from backend import main
    importlib.reload(main)
    with TestClient(main.app) as client:
        readings = client.get('/api/readings').json()
        assert readings and -90 <= readings[0]['dbfs'] <= 0
        assert readings[0]['source'] == 'simulator'
        assert client.get('/api/config').json()['auth_mode'] == 'demo'
        if main.dist.exists(): assert 'Noise Observatory' in client.get('/').text


def test_real_supabase_verifier_rejects_untrusted_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('LOA_DB',str(tmp_path/'auth.sqlite3'))
    monkeypatch.setenv('LOA_AUTH_MODE','supabase')
    monkeypatch.setenv('LOA_SIMULATOR','false')
    monkeypatch.setenv('SUPABASE_URL','https://example.supabase.co')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY','sb_publishable_test')
    from backend import main
    importlib.reload(main)
    real_client = httpx.AsyncClient
    def handler(request):
        assert str(request.url) == 'https://example.supabase.co/auth/v1/user'
        assert request.headers['apikey'] == 'sb_publishable_test'
        if request.headers['authorization'] == 'Bearer valid': return httpx.Response(200,json={'id':'trusted-user'})
        return httpx.Response(401,json={'error':'bad token'})
    monkeypatch.setattr(main.httpx,'AsyncClient',lambda **kwargs: real_client(transport=httpx.MockTransport(handler),**kwargs))
    assert asyncio.run(main.verify_token('valid')) == 'trusted-user'
    with pytest.raises(main.HTTPException) as error: asyncio.run(main.verify_token('forged'))
    assert error.value.status_code == 401


def test_browser_microphone_ownership(setup):
    main, client = setup
    assert client.post('/api/devices/browser', json={'label':'Laptop'}).status_code == 401
    created = client.post('/api/devices/browser', headers=auth(), json={'label':'Laptop'})
    assert created.status_code == 201
    device = created.json()['device_id']
    sample = {'device_id':device,'dbfs':-35,'boot_id':'session','sequence':1}
    assert client.post('/api/browser/readings', headers=auth('bob'), json=sample).status_code == 403
    assert client.post('/api/browser/readings', headers=auth(), json=sample).status_code == 201
    assert client.get('/api/readings', headers=auth()).json()[0]['source'] == 'browser'
    assert client.get('/api/readings', headers=auth('bob')).json() == []
    assert pair(client).status_code == 200
    assert client.post('/api/browser/readings', headers=auth(), json={**sample,'device_id':'sensor-a'}).status_code == 403
    assert client.post('/api/browser/readings', headers=auth(), json={**sample,'dbfs':12}).status_code == 422


def test_disturbance_reports_private_persistent_and_retry_safe(setup):
    import time
    import uuid
    main, client = setup
    payload = {'request_id':str(uuid.uuid4()),'occurred_at':time.time(),
               'location':' Library ', 'description':' Repeated drilling ',
               'feeling':'Need to Cope', 'details':' Trying to study '}
    assert client.get('/api/reports').status_code == 401
    assert client.post('/api/reports', json=payload).status_code == 401
    saved = client.post('/api/reports', headers=auth(), json=payload)
    assert saved.status_code == 201
    data = saved.json()
    assert data['location'] == 'Library'
    assert data['description'] == 'Repeated drilling'
    assert data['details'] == 'Trying to study'
    assert abs(data['occurred_at'] - payload['occurred_at']) < .001
    assert 'user_id' not in data
    assert client.post('/api/reports', headers=auth(), json=payload).json()['id'] == data['id']
    assert len(client.get('/api/reports', headers=auth()).json()) == 1
    assert client.get('/api/reports', headers=auth('bob')).json() == []
    # No microphone/device is required; optional details default to an empty string.
    second = {**payload,'request_id':str(uuid.uuid4()),'feeling':'Tolerable'}
    second.pop('details')
    assert client.post('/api/reports', headers=auth('bob'), json=second).json()['details'] == ''
    with TestClient(main.app) as reopened:
        assert reopened.get('/api/reports', headers=auth()).json() == [data]


def test_disturbance_report_validation(setup):
    import time
    import uuid
    _, client = setup
    payload = {'request_id':str(uuid.uuid4()),'occurred_at':time.time(),
               'location':'Home','description':'Loud music','feeling':'Sensitive'}
    for field, value in [('location','   '),('description','\n'),('feeling','Unknown'),
                         ('location','x'*201),('description','x'*2001),('details','x'*4001),
                         ('occurred_at',time.time()+3600),('request_id','invalid')]:
        assert client.post('/api/reports', headers=auth(), json={**payload,field:value}).status_code == 422


def test_cloud_reports_token_forwarding_legacy_sync_and_retry(setup, monkeypatch):
    import time
    import uuid
    from backend import report_store
    main, client = setup
    # Create one legacy report for each account before switching stores.
    body = {'request_id':str(uuid.uuid4()),'occurred_at':time.time(),
            'location':'Library','description':'Drilling','feeling':'Need to Cope'}
    assert client.post('/api/reports', headers=auth(), json=body).status_code == 201
    assert client.post('/api/reports', headers=auth('bob'), json={**body,'request_id':str(uuid.uuid4())}).status_code == 201
    monkeypatch.setattr(main, 'REPORT_STORE', 'supabase')
    saved = {}
    posts = []
    real_client = httpx.AsyncClient
    def handler(request):
        assert str(request.url).startswith('https://example.supabase.co/rest/v1/incident_reports')
        assert request.headers['apikey'] == 'sb_publishable_test'
        assert request.headers['authorization'] == 'Bearer alice'
        if request.method == 'POST':
            assert request.url.params['on_conflict'] == 'user_id,request_id'
            assert 'ignore-duplicates' in request.headers['prefer']
            rows = json.loads(request.content)
            for row in rows:
                assert row['user_id'] == 'alice'
                saved.setdefault(row['request_id'], {'id':str(uuid.uuid4()),'created_at':'2026-11-15T20:00:00Z',**row})
            posts.append(rows)
            return httpx.Response(201)
        assert request.url.params['user_id'] == 'eq.alice'
        assert request.url.params['limit'] == '50'
        rows = list(saved.values())
        if 'request_id' in request.url.params:
            rows = [r for r in rows if 'eq.'+r['request_id'] == request.url.params['request_id']]
        return httpx.Response(200, json=rows)
    monkeypatch.setattr(report_store.httpx,'AsyncClient',lambda **kw: real_client(transport=httpx.MockTransport(handler),**kw))
    history = client.get('/api/reports',headers=auth())
    assert history.status_code == 200
    assert len(history.json()) == 1 and isinstance(history.json()[0]['occurred_at'],float)
    assert len(posts) == 1
    assert client.get('/api/reports',headers=auth()).status_code == 200
    assert len(posts) == 1  # Legacy upload is marked only after success.
    fresh = {**body,'request_id':str(uuid.uuid4()),'location':'Home'}
    first = client.post('/api/reports',headers=auth(),json=fresh)
    retry = client.post('/api/reports',headers=auth(),json=fresh)
    assert first.status_code == retry.status_code == 201
    assert first.json()['id'] == retry.json()['id']
    with main.connect() as db:
        assert db.execute('select count(*) from disturbance_reports').fetchone()[0] == 2
        assert db.execute('select count(*) from report_cloud_sync').fetchone()[0] == 1


def test_cloud_report_failures_preserve_legacy_and_do_not_fallback(setup, monkeypatch):
    import time
    import uuid
    from backend import report_store
    main, client = setup
    body = {'request_id':str(uuid.uuid4()),'occurred_at':time.time(),
            'location':'Home','description':'Music','feeling':'Sensitive'}
    client.post('/api/reports',headers=auth(),json=body)
    monkeypatch.setattr(main, 'REPORT_STORE', 'supabase')
    real_client = httpx.AsyncClient
    failure = [503]
    def handler(request):
        if failure[0] == 0: raise httpx.ConnectError('offline',request=request)
        return httpx.Response(failure[0],json={'message':'internal details should not leak'})
    monkeypatch.setattr(report_store.httpx,'AsyncClient',lambda **kw: real_client(transport=httpx.MockTransport(handler),**kw))
    for status, expected in [(503,503),(404,503),(401,401),(403,403),(0,503)]:
        failure[0] = status
        assert client.get('/api/reports',headers=auth()).status_code == expected
        response = client.post('/api/reports',headers=auth(),json={**body,'request_id':str(uuid.uuid4())})
        assert response.status_code == expected
        assert 'internal details' not in response.text
    with main.connect() as db:
        assert db.execute('select count(*) from report_cloud_sync').fetchone()[0] == 0
        assert db.execute('select count(*) from disturbance_reports').fetchone()[0] == 1
