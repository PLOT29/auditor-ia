"""Fase 5C · GET /events/{id}: detalle de un evento para el panel lateral. Datos FALSOS.

- existe → metadatos, hallazgos (tipo + valor enmascarado) y text_masked con marcadores;
- no existe → 404;
- con ?presentacion=1 nunca trae texto (ni siquiera lo lee de la BD);
- nunca aparece el original (no existe) y requiere token.
"""
import pytest
from fastapi.testclient import TestClient

import fakes as F


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_DB_PATH", str(tmp_path / "t.db"))
    from main import app

    with TestClient(app, headers=F.auth()) as c:
        yield c


def post(client, **body):
    r = client.post("/analyze", json={"platform": "chatgpt", **body})
    assert r.status_code == 200, r.text
    return r.json()


def test_detail_with_text_masked_and_findings(client):
    text = f"Corrige client = OpenAI(api_key='{F.OPENAI_KEY}') y escríbeme a {F.EMAIL}"
    data = post(client, text=text, blocked=True, user_decision="cancelado",
                client_findings=[{"type": "openai_key", "masked": "sk-****abcd"}])
    d = client.get(f"/events/{data['id']}").json()
    assert d["id"] == data["id"] and d["plataforma"] == "chatgpt" and d["origen"] == "extension"
    assert d["riesgo"] == "alto" and d["motivo"] == ["secreto", "dato personal"]
    assert d["decision"] == "cancelado" and d["bloqueado"] is True
    assert {(f["type"], f["masked"]) for f in d["hallazgos"]} == {("openai_key", "sk-****abcd"), ("email", "o****@gmail.com")}
    assert "<OPENAI_KEY>" in d["texto"] and "<EMAIL>" in d["texto"] and d["texto_oculto"] is False
    assert F.OPENAI_KEY not in str(d) and F.EMAIL not in str(d)
    for k in ("confianza", "injection_score", "latencia_ms", "categoria", "longitud", "fecha", "hora"):
        assert k in d


def test_detail_shows_censor_markers(client):
    data = post(client, text="Mi domicilio es [DIRECCIÓN_OCULTA] y la key [API_KEY_OCULTA]", user_decision="censurado",
                blocked=True, client_findings=[{"type": "address", "masked": "****"}])
    d = client.get(f"/events/{data['id']}").json()
    assert "[DIRECCIÓN_OCULTA]" in d["texto"] and "[API_KEY_OCULTA]" in d["texto"]


def test_presentation_mode_never_returns_text(client, monkeypatch):
    data = post(client, text="Texto falso de prueba MARCA_UNICA_5C", user_decision="no_aplica")
    import analytics

    seen = {}
    real = analytics.event_detail

    def spy(path, event_id, include_text=False, threshold=None):
        seen["include_text"] = include_text
        return real(path, event_id, include_text=include_text, threshold=threshold)

    monkeypatch.setattr(analytics, "event_detail", spy)
    r = client.get(f"/events/{data['id']}", params={"presentacion": 1})
    d = r.json()
    assert d["texto"] is None and d["texto_oculto"] is True
    assert "MARCA_UNICA_5C" not in r.text
    assert seen["include_text"] is False  # ni siquiera se lee la columna


def test_missing_event_is_404(client):
    assert client.get("/events/999999").status_code == 404
    assert client.get("/events/no-es-numero").status_code == 422


def test_detail_needs_token(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_DB_PATH", str(tmp_path / "t.db"))
    from main import app

    with TestClient(app) as anon:
        assert anon.get("/events/1").status_code == 403
