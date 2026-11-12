import importlib

from fastapi.testclient import TestClient


def test_ingestion_alerts_and_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv('LOA_DB', str(tmp_path / 'test.sqlite3'))
    monkeypatch.setenv('LOA_SIMULATOR', 'false')
    from backend import main
    importlib.reload(main)
    with TestClient(main.app) as client:
        assert client.get('/api/readings').json() == []
        assert client.put('/api/settings', json={'threshold': 60}).status_code == 200
        with client.websocket_connect('/ws') as ws:
            for value in [40, 75, 80, 50, 65]:
                result = client.post('/api/readings', json={'value': value})
                assert result.status_code == 201
                assert ws.receive_json()['data']['value'] == value
        assert len(client.get('/api/alerts').json()) == 2
        assert len(client.get('/api/readings').json()) == 5
        assert client.post('/api/readings', json={'value': 999}).status_code == 422
        assert client.put('/api/settings', json={'threshold': 0}).status_code == 422
        assert client.get('/api/readings?limit=10001').status_code == 422
        assert client.get('/api/status').json()['devices'][0]['online']
    with TestClient(main.app) as client:
        assert client.get('/api/settings').json()['threshold'] == 60
        assert len(client.get('/api/readings').json()) == 5


def test_simulator_and_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv('LOA_DB', str(tmp_path / 'demo.sqlite3'))
    monkeypatch.setenv('LOA_SIMULATOR', 'true')
    from backend import main
    importlib.reload(main)
    with TestClient(main.app) as client:
        rows = client.get('/api/readings').json()
        assert rows and rows[0]['source'] == 'simulator'
        assert client.get('/api/status').json()['simulator'] is True
        if main.dist.exists():
            page = client.get('/')
            assert page.status_code == 200
            assert 'Noise Observatory' in page.text
