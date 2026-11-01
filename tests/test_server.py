import json
import time
import zipfile
import io

import pytest
from fastapi.testclient import TestClient

from webagent import server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT", tmp_path)
    monkeypatch.setattr(server, "RUNS", {})
    monkeypatch.setattr(server, "TASKS", {})
    monkeypatch.setattr(server, "AGENTS", {})
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(server.app) as client:
        yield client


def headers(client):
    return {"x-session-token": client.get("/api/config").json()["session_token"]}


def test_requires_key_and_local_session(client):
    assert client.post("/api/runs", json={"company": "Jumbo"}).status_code == 403
    response = client.post("/api/runs", json={"company": "Jumbo"}, headers=headers(client))
    assert response.status_code == 400 and "API key" in response.json()["detail"]
    response = client.post("/api/runs", json={"company": "Jumbo", "demo": True}, headers=headers(client) | {"origin": "https://evil.example"})
    assert response.status_code == 403


def test_demo_progress_exports_and_history(client):
    h = headers(client)
    response = client.post("/api/runs", json={"company": "Sample Company", "demo": True}, headers=h)
    assert response.status_code == 200
    run_id = response.json()["id"]
    assert client.post("/api/runs", json={"company": "Another", "demo": True}, headers=h).status_code == 409
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = client.get(f"/api/runs/{run_id}").json()
        if result["status"] == "demo":
            break
        time.sleep(.1)
    assert result["status"] == "demo"
    assert result["finished_at"] and result["elapsed_seconds"] > 0
    assert len(result["documents"]) == 3
    assert result['queue_counts']['completed'] == 3 and result['queue_counts']['pending'] == 0
    assert result["usage"]["model_calls"] == 0
    zip_result = client.get(f"/api/runs/{run_id}/zip")
    assert zip_result.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zip_result.content)) as archive:
        assert any(x.endswith("catalogue.csv") for x in archive.namelist())
        assert len([x for x in archive.namelist() if x.endswith(".html")]) == 3
    file = client.get(f"/api/runs/{run_id}/files/documents/sample-results.html")
    assert "attachment" in file.headers["content-disposition"]
    assert b"DEMO ONLY" in file.content
    assert client.get("/api/runs").json()[0]["id"] == run_id
    assert client.get(f"/api/runs/{run_id}/files/%2e%2e/.env").status_code == 404
    server.RUNS.clear()
    reopened = client.get(f"/api/runs/{run_id}").json()
    assert reopened["status"] == "demo"
    assert reopened["elapsed_seconds"] == result["elapsed_seconds"]
    assert reopened["finished_at"] == result["finished_at"]


def test_cancel_demo_keeps_output(client):
    h = headers(client)
    run_id = client.post("/api/runs", json={"company": "Sample", "demo": True}, headers=h).json()["id"]
    client.post(f"/api/runs/{run_id}/cancel", json={}, headers=h)
    for _ in range(20):
        result = client.get(f"/api/runs/{run_id}").json()
        if result["status"] == "cancelled":
            break
        time.sleep(.05)
    assert result["status"] == "cancelled"
    assert result["finished_at"] and result["elapsed_seconds"] >= 0
    assert client.get(f"/api/runs/{run_id}/files/catalogue.json").status_code == 200
