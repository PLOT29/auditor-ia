"""Fase 3: contrato de /analyze para eventos bloqueados y client_latency_ms."""
import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient

import fakes as F


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "t.db"
    monkeypatch.setenv("AUDITOR_DB_PATH", str(db_file))
    from main import app

    with TestClient(app) as c:
        c.db_file = db_file
        yield c


def test_client_latency_is_optional(client):
    r = client.post("/analyze", json={"text": "hola", "platform": "test"})
    assert r.status_code == 200
    r = client.post("/analyze", json={"text": "hola", "platform": "test", "client_latency_ms": None})
    assert r.status_code == 200


def test_client_latency_accepted_and_logged(client, caplog):
    caplog.set_level(logging.INFO, logger="auditor")
    r = client.post("/analyze", json={
        "text": f"key {F.OPENAI_KEY}", "platform": "chatgpt", "client_latency_ms": 0.42,
        "blocked": True, "user_decision": "cancelado",
        "client_findings": [{"type": "openai_key", "masked": "sk-****abcd"}],
    })
    assert r.status_code == 200
    assert set(r.json()) == {"id", "risk", "category", "category_confidence",
                             "injection_score", "findings", "latency_ms"}  # respuesta sin cambios
    assert "cliente=0.42ms" in caplog.text
    assert "decision=cancelado" in caplog.text
    assert F.OPENAI_KEY not in caplog.text


@pytest.mark.parametrize("bad", [-1, 120_000, "rápido"])
def test_client_latency_validated(client, bad):
    r = client.post("/analyze", json={"text": "hola", "platform": "test", "client_latency_ms": bad})
    assert r.status_code == 422


def test_db_schema_unchanged(client):
    client.post("/analyze", json={"text": "hola", "platform": "test", "client_latency_ms": 1.5})
    conn = sqlite3.connect(client.db_file)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(prompts)")]
    conn.close()
    assert cols == ["id", "ts", "platform", "text_masked", "text_sha256", "length", "category",
                    "category_conf", "injection_score", "risk", "findings_json", "blocked",
                    "user_decision", "latency_ms"]


def test_queued_event_with_client_masked_text(client):
    """Un evento de la cola offline llega con el texto ya enmascarado por la extensión."""
    r = client.post("/analyze", json={
        "text": "Corrige: api_key='<OPENAI_KEY>' y escribe a <EMAIL>",
        "platform": "claude", "timestamp": "2026-11-11T10:00:00Z",
        "client_findings": [{"type": "openai_key", "masked": "sk-****abcd"},
                            {"type": "email", "masked": "d****@gmail.com"}],
        "blocked": True, "user_decision": "enviado", "client_latency_ms": None,
    })
    data = r.json()
    assert data["risk"] == "alto"
    assert {f["type"] for f in data["findings"]} == {"openai_key", "email"}
    conn = sqlite3.connect(client.db_file)
    row = conn.execute("SELECT text_masked, blocked, user_decision, ts FROM prompts").fetchone()
    conn.close()
    assert row == ("Corrige: api_key='<OPENAI_KEY>' y escribe a <EMAIL>", 1, "enviado",
                   "2026-11-11T10:00:00+00:00")
