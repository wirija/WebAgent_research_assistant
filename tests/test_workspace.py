import time

import pytest
from fastapi.testclient import TestClient

from workspace_ui import server as workspace
from webagent import server as collection
from company_understanding import server as understanding
from external_research import server as external


@pytest.fixture
def client(tmp_path, monkeypatch):
    for module, name in [(collection, 'collection'), (understanding, 'understanding'), (external, 'external')]:
        monkeypatch.setattr(module, 'OUTPUT', tmp_path / name)
        monkeypatch.setattr(module, 'TASKS', {})
    monkeypatch.setattr(collection, 'RUNS', {})
    monkeypatch.setattr(collection, 'AGENTS', {})
    monkeypatch.setattr(understanding, 'JOBS', {})
    monkeypatch.setattr(external, 'JOBS', {})
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    with TestClient(workspace.app) as client:
        yield client


def test_mounted_apps_assets_and_mutation_security(client):
    assert 'Company Research Workspace' in client.get('/').text
    assert collection.OUTPUT.is_dir() and understanding.OUTPUT.is_dir()
    tokens = {}
    for prefix, endpoint, body in [('collection', 'runs', {'company': 'Example', 'demo': True}),
                                   ('understanding', 'demo', {}), ('external', 'demo', {})]:
        assert client.get(f'/{prefix}/').status_code == 200
        script = client.get(f'/{prefix}/app.js')
        assert script.status_code == 200 and 'api/config' in script.text
        tokens[prefix] = client.get(f'/{prefix}/api/config').json()['session_token']
        url = f'/{prefix}/api/{endpoint}'
        assert client.post(url, json=body).status_code == 403
        assert client.post(url, json=body, headers={'x-session-token':tokens[prefix], 'origin':'https://evil.example'}).status_code == 403
    assert tokens['collection'] != tokens['understanding']
    assert client.post('/understanding/api/demo', json={}, headers={'x-session-token':tokens['collection']}).status_code == 403
    assert client.get('/', headers={'host':'evil.example'}).status_code == 400


def test_both_modules_run_and_export_through_workspace(client):
    def headers(prefix):
        return {'x-session-token':client.get(f'/{prefix}/api/config').json()['session_token'], 'origin':'http://testserver'}
    a = client.post('/collection/api/runs', json={'company':'Example', 'demo':True}, headers=headers('collection'))
    b = client.post('/understanding/api/demo', json={}, headers=headers('understanding'))
    assert a.status_code == b.status_code == 200
    collection_id, job_id = a.json()['id'], b.json()['id']
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        first = client.get(f'/collection/api/runs/{collection_id}').json()
        second = client.get(f'/understanding/api/jobs/{job_id}').json()
        if first['status'] == 'demo' and second['status'] != 'running':
            break
        time.sleep(.05)
    assert first['status'] == 'demo' and second['status'] == 'finished'
    assert first['folder'] != second['folder']
    assert client.get(f'/collection/api/runs/{collection_id}/zip').status_code == 200
    report = client.get(f'/understanding/api/jobs/{job_id}/files/report.html')
    assert report.status_code == 200 and 'attachment' in report.headers['content-disposition']
    assert len(client.get('/collection/api/runs').json()) == 1
    assert len(client.get('/understanding/api/jobs').json()) == 1


def test_workspace_shutdown_cancels_child_jobs(client):
    # Exercise mounted lifespan shutdown, which ASGI does not propagate itself.
    import asyncio
    with TestClient(workspace.app) as nested:
        async def start_waiters():
            collection.TASKS['wait'] = asyncio.create_task(asyncio.sleep(120))
            understanding.TASKS['wait'] = asyncio.create_task(asyncio.sleep(120))
        nested.portal.call(start_waiters)
    assert collection.TASKS['wait'].cancelled()
    assert understanding.TASKS['wait'].cancelled()
