"""El fetch de la extensión (Origin chrome-extension://) funciona sin CORS,
y una página web cualquiera no obtiene permiso CORS para leer el backend."""
import logging

import pytest
from fastapi.testclient import TestClient

import fakes as F


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_DB_PATH", str(tmp_path / "t.db"))
    from main import app

    with TestClient(app, headers=F.auth()) as c:
        yield c


def test_extension_origin_is_accepted(client):
    r = client.post(
        "/analyze",
        json={"text": "hola", "platform": "test"},
        headers={"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"},
    )
    assert r.status_code == 200
    assert r.json()["risk"] == "bajo"


def test_web_page_gets_no_cors_grant(client):
    r = client.options(
        "/analyze",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
    r = client.get("/stats", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_latency_logged_without_text(client, caplog):
    caplog.set_level(logging.INFO, logger="auditor")
    r = client.post("/analyze", json={"text": f"key {F.OPENAI_KEY}", "platform": "test"})
    assert "X-Process-Time-Ms" in r.headers
    assert "POST /analyze -> 200" in caplog.text
    assert F.OPENAI_KEY not in caplog.text
    assert "key " not in caplog.text
