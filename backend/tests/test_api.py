import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient

import fakes as F


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("AUDITOR_DB_PATH", str(db_file))
    from main import app

    with TestClient(app) as c:
        c.db_file = db_file
        yield c


def post(client, text, **extra):
    body = {"text": text, "platform": "chatgpt", "timestamp": "2026-11-11T10:00:00Z", **extra}
    r = client.post("/analyze", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_analyze_clean_prompt(client):
    data = post(client, "Explícame qué es un firewall")
    assert set(data) == {"id", "risk", "category", "category_confidence",
                         "injection_score", "findings", "latency_ms"}
    assert data["risk"] == "bajo"
    assert data["findings"] == []
    assert data["category"] == "consulta_general"
    assert isinstance(data["id"], int)
    assert data["latency_ms"] >= 0


def test_analyze_secret_is_high(client):
    data = post(client, f"client = OpenAI(api_key='{F.OPENAI_KEY}')",
                blocked=True, user_decision="cancelado",
                client_findings=[{"type": "openai_key", "masked": "sk-****abcd"}])
    assert data["risk"] == "alto"
    assert data["findings"] == [{"type": "openai_key", "masked": "sk-****abcd", "source": "secret"}]


def test_analyze_pii_is_medium(client):
    data = post(client, f"Mi correo es {F.EMAIL} y mi CURP {F.CURP}", platform="claude")
    assert data["risk"] == "medio"
    assert [f["type"] for f in data["findings"]] == ["email", "curp"]
    assert data["findings"][0]["masked"] == "o****@gmail.com"


def test_client_finding_not_seen_by_server_is_kept_and_sanitized(client):
    data = post(client, "texto sin secretos", blocked=True, user_decision="enviado",
                client_findings=[{"type": "custom_token", "masked": "valor-en-claro"}])
    assert data["risk"] == "alto"
    assert data["findings"] == [{"type": "custom_token", "masked": "****", "source": "client"}]


def test_invalid_platform_rejected_without_echoing_text(client):
    r = client.post("/analyze", json={"text": F.OPENAI_KEY, "platform": "bing"})
    assert r.status_code == 422
    assert F.OPENAI_KEY not in r.text


def test_row_persisted_masked(client):
    data = post(client, f"key {F.AWS_KEY}", blocked=True, user_decision="cancelado")
    conn = sqlite3.connect(client.db_file)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM prompts WHERE id = ?", (data["id"],)).fetchone()
    conn.close()
    assert row["text_masked"] == "key <AWS_ACCESS_KEY>"
    assert row["platform"] == "chatgpt"
    assert row["risk"] == "alto"
    assert row["blocked"] == 1
    assert row["user_decision"] == "cancelado"
    assert row["length"] == len(f"key {F.AWS_KEY}")
    assert len(row["text_sha256"]) == 64


def test_stats(client):
    post(client, "hola")
    post(client, f"key {F.GITHUB_TOKEN}", blocked=True, user_decision="cancelado")
    post(client, f"correo {F.EMAIL}", platform="gemini")
    s = client.get("/stats").json()
    assert s["total"] == 3
    assert s["bloqueados"] == 1
    assert s["plataforma_top"] == "chatgpt"
    assert s["por_plataforma"] == {"chatgpt": 2, "gemini": 1}
    assert s["por_riesgo"] == {"bajo": 1, "alto": 1, "medio": 1}
    assert s["pct_riesgo_alto"] == pytest.approx(33.33)
    assert len(s["alertas_recientes"]) == 2
    assert F.GITHUB_TOKEN not in str(s)


ALL_SENSITIVE = [F.OPENAI_KEY, F.ANTHROPIC_KEY, F.AWS_KEY, F.GITHUB_TOKEN, F.GOOGLE_KEY,
                 F.JWT, F.PASSWORD_VALUE, F.CARD, F.EMAIL, F.CURP, F.RFC, F.PHONE]


def test_privacy_no_plaintext_in_db_or_logs(client, caplog):
    """CE-9 / RNF-04: ningún secreto ni PII en claro en disco ni en logs."""
    caplog.set_level(logging.DEBUG)
    text = (f"{F.OPENAI_KEY} {F.ANTHROPIC_KEY} {F.AWS_KEY} {F.GITHUB_TOKEN} {F.GOOGLE_KEY} "
            f"{F.JWT} password={F.PASSWORD_VALUE} {F.CARD} {F.EMAIL} {F.CURP} {F.RFC} {F.PHONE} "
            f"{F.PEM}")
    post(client, text)

    raw_db = client.db_file.read_bytes()
    for v in ALL_SENSITIVE + ["MIIEFAKEFAKE"]:
        assert v.encode() not in raw_db, f"valor en claro en BD: {v[:6]}..."
        assert v not in caplog.text, f"valor en claro en logs: {v[:6]}..."
