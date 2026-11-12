"""Fase 5A: decisiones "censurado"/"advertido", hallazgos del cliente y /stats?desde. Datos FALSOS."""
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


def post(client, **body):
    r = client.post("/analyze", json={"platform": "chatgpt", **body})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("decision", ["censurado", "advertido", "enviado", "cancelado", "no_aplica"])
def test_new_user_decisions_accepted_and_stored(client, decision):
    data = post(client, text="hola", user_decision=decision)
    conn = sqlite3.connect(client.db_file)
    stored = conn.execute("SELECT user_decision FROM prompts WHERE id = ?", (data["id"],)).fetchone()[0]
    conn.close()
    assert stored == decision


def test_unknown_decision_rejected(client):
    r = client.post("/analyze", json={"text": "hola", "platform": "chatgpt", "user_decision": "otra"})
    assert r.status_code == 422


def test_censored_text_arrives_censored(client):
    """Con "Censurar y enviar" el backend recibe el texto con marcadores, nunca el secreto."""
    data = post(client, text="Corrige: client = OpenAI(api_key='[API_KEY_OCULTA]')", blocked=True,
                user_decision="censurado", client_findings=[{"type": "openai_key", "masked": "sk-****abcd"}])
    assert data["risk"] == "alto"  # hubo un secreto (lo reporta el cliente), aunque ya no esté en el texto
    assert data["findings"] == [{"type": "openai_key", "masked": "sk-****abcd", "source": "client"}]
    conn = sqlite3.connect(client.db_file)
    row = conn.execute("SELECT text_masked, user_decision FROM prompts").fetchone()
    conn.close()
    assert row == ("Corrige: client = OpenAI(api_key='[API_KEY_OCULTA]')", "censurado")
    assert F.OPENAI_KEY.encode() not in client.db_file.read_bytes()


def test_dictionary_term_is_medium_risk_and_never_stored(client):
    """El término propio llega ya reemplazado por la extensión; solo viaja el tipo."""
    data = post(client, text="Revisa el plan de <TERMINO_PROPIO> para el lunes", user_decision="advertido",
                client_findings=[{"type": "termino_propio", "masked": "****"}])
    assert data["risk"] == "medio"
    assert data["findings"] == [{"type": "termino_propio", "masked": "****", "source": "client_pii"}]


def test_client_only_pii_is_medium_not_high(client):
    import risk

    # La regla, sin depender del detector de inyección:
    assert risk.compute_risk([{"type": "curp", "masked": "****", "source": "client_pii"}], 0.0) == "medio"
    assert risk.compute_risk([{"type": "openai_key", "masked": "****", "source": "client"}], 0.0) == "alto"
    # Y de punta a punta con un prompt natural (una frase corta y rara puede dar score de inyección alto).
    data = post(client, text="Llena el formulario de inscripción con mis datos, por favor",
                client_findings=[{"type": "curp", "masked": "****RN09"}])
    assert data["risk"] == "medio"


def test_stats_since_filter(client):
    post(client, text="viejo", timestamp="2026-11-01T10:00:00Z", blocked=True, user_decision="cancelado")
    post(client, text="hoy 1", timestamp="2026-11-12T15:00:00Z", user_decision="advertido",
         client_findings=[{"type": "email", "masked": "d****@x.com"}])
    post(client, text="hoy 2", timestamp="2026-11-12T16:00:00Z", blocked=True, user_decision="censurado")
    all_ = client.get("/stats").json()
    today = client.get("/stats", params={"desde": "2026-11-12T00:00:00-06:00"}).json()
    assert all_["total"] == 3 and all_["desde"] is None
    assert today["total"] == 2 and today["bloqueados"] == 1
    assert today["por_decision"] == {"advertido": 1, "censurado": 1}
    assert today["desde"].startswith("2026-11-12T00:00:00")
    assert client.get("/stats", params={"desde": "no-es-fecha"}).status_code == 422
