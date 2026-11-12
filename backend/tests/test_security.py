"""Fase 5C · seguridad de la API local: token de sesión, Origin, Host (DNS rebinding) y CORS cerrado.

Un sitio externo abierto en el navegador (Origin https://evil.example) o una petición sin token a una ruta
sensible reciben 403. Datos FALSOS.
"""
import pytest
from fastapi.testclient import TestClient

import fakes as F
import security

EVIL = {"Origin": "https://evil.example"}
EXT = {"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"}
LOCAL = {"Origin": "http://127.0.0.1:8000"}
SENSITIVE = [
    ("post", "/analyze", {"json": {"text": "hola", "platform": "test"}}),
    ("get", "/stats", {}),
    ("get", "/stats?detalle=1", {}),
    ("get", "/events/1", {}),
    ("get", "/agent/sugeridas", {}),
    ("post", "/agent/preguntar", {"json": {"pregunta": "¿Qué plataforma tiene más riesgo?"}}),
    ("get", "/ia/estado", {}),
    ("post", "/ia/nivel", {"json": {"nivel": 1}}),
    ("post", "/ia/local/activar", {}),
    ("post", "/ia/local/confirmar", {"json": {"aceptar": True}}),
    ("post", "/ia/local/desactivar", {}),
    ("post", "/ia/nube/configurar", {"json": {"base_url": "https://api.example.com/v1", "modelo": "m"}}),
    ("post", "/ia/nube/probar", {}),
    ("post", "/ia/nube/borrar", {}),
]


@pytest.fixture
def anon(tmp_path, monkeypatch):
    """Cliente SIN token (como una página web cualquiera o un proceso ajeno)."""
    monkeypatch.setenv("AUDITOR_DB_PATH", str(tmp_path / "t.db"))
    from main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_DB_PATH", str(tmp_path / "t.db"))
    from main import app

    with TestClient(app, headers=F.auth()) as c:
        yield c


@pytest.mark.parametrize("method,path,kw", SENSITIVE, ids=[f"{m} {p}" for m, p, _ in SENSITIVE])
def test_sensitive_routes_without_token_are_403(anon, method, path, kw):
    r = getattr(anon, method)(path, **kw)
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("method,path,kw", SENSITIVE, ids=[f"{m} {p}" for m, p, _ in SENSITIVE])
def test_external_origin_is_403_even_with_token(client, method, path, kw):
    r = getattr(client, method)(path, headers=EVIL, **kw)
    assert r.status_code == 403
    assert r.json()["detail"] == "origen no permitido"


def test_wrong_token_is_403(anon):
    r = anon.post("/analyze", json={"text": "hola", "platform": "test"}, headers={security.TOKEN_HEADER: "x" * 43})
    assert r.status_code == 403


def test_token_and_local_or_extension_origin_work(client):
    for h in (LOCAL, EXT, {"Origin": "http://localhost:8000"}, {}):
        r = client.post("/analyze", json={"text": "hola", "platform": "test"}, headers=h)
        assert r.status_code == 200, h


@pytest.mark.parametrize("origin", ["null", "http://127.0.0.1.evil.example", "https://localhost.evil.example",
                                    "chrome-extension://", "file://", "http://192.168.1.10:8000"])
def test_lookalike_origins_rejected(client, origin):
    assert client.get("/stats", headers={"Origin": origin}).status_code == 403


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "127.0.0.1.nip.io", "192.168.1.10:8000"])
def test_dns_rebinding_host_rejected_everywhere(client, host):
    for path in ("/health", "/panel/", "/stats"):
        r = client.get(path, headers={"Host": host})
        assert r.status_code == 403, (path, host)
        assert security.SESSION_TOKEN not in r.text


def test_local_hosts_accepted():
    for h in ("127.0.0.1:8000", "localhost:8000", "[::1]:8000", "localhost"):
        assert security.host_ok(h), h
    assert not security.host_ok(None) and not security.host_ok("")


def test_panel_html_embeds_token_but_only_for_local_host(anon):
    r = anon.get("/panel/")
    assert r.status_code == 200
    assert f'content="{security.SESSION_TOKEN}"' in r.text
    assert "__CENTINELA_TOKEN__" not in r.text
    assert r.headers["cache-control"] == "no-store"
    assert anon.get("/panel/", headers={"Host": "evil.example"}).status_code == 403


def test_extension_token_endpoint_only_for_extension_origin(anon):
    r = anon.get("/auth/token", headers=EXT)
    assert r.status_code == 200 and r.json()["token"] == security.SESSION_TOKEN
    assert anon.get("/auth/token").status_code == 403  # sin Origin (curl, otro proceso)
    assert anon.get("/auth/token", headers=LOCAL).status_code == 403
    assert anon.get("/auth/token", headers=EVIL).status_code == 403


def test_pinned_extension_id(anon, monkeypatch):
    monkeypatch.setenv("AUDITOR_EXTENSION_ID", "abcdefghijklmnopabcdefghijklmnop")
    assert anon.get("/auth/token", headers=EXT).status_code == 200
    other = {"Origin": "chrome-extension://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"}
    assert anon.get("/auth/token", headers=other).status_code == 403


def test_events_stream_accepts_token_in_query_only_there(anon):
    assert anon.get("/events?token=malo").status_code == 403
    # En otras rutas el token por query NO vale (no queda en historiales ni en Referer).
    assert anon.get(f"/stats?token={security.SESSION_TOKEN}").status_code == 403


def test_cors_closed_no_wildcards(client):
    for path in ("/analyze", "/stats", "/agent/preguntar", "/ia/estado"):
        r = client.options(path, headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
        assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
    r = client.get("/health", headers=LOCAL)
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_token_never_logged(client, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    client.post("/analyze", json={"text": "hola", "platform": "test"})
    client.get("/stats")
    assert security.SESSION_TOKEN not in caplog.text
