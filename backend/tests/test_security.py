"""Fases 5C–5C.1 · seguridad de la API local: token de sesión, Origin + Sec-Fetch-Site, Host (DNS rebinding)
y CORS cerrado. Los clientes usan Host 127.0.0.1:8000, como el backend real. Datos FALSOS.

Regla (security.py): pasa si Origin == chrome-extension://<id de Centinela>, Origin == el propio backend,
o NO hay Origin y Sec-Fetch-Site ∈ {none, same-origin} (o no hay Sec-Fetch-Site: no es un navegador).
Se rechaza todo Origin externo, Sec-Fetch-Site cross-site/same-site sin Origin de la lista y Host ajeno.
"""
import logging

import pytest
from fastapi.testclient import TestClient

import config
import fakes as F
import security

BASE = "http://127.0.0.1:8000"
EXT_ID = config.extension_id()
EXT = {"Origin": f"chrome-extension://{EXT_ID}"}
OTHER_EXT = {"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"}
PANEL = {"Origin": BASE, "Sec-Fetch-Site": "same-origin"}
EVIL = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
SW = {"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}  # service worker, sin Origin
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
IDS = [f"{m} {p}" for m, p, _ in SENSITIVE]


@pytest.fixture
def anon(tmp_path, monkeypatch):
    """Cliente SIN token (como una página web cualquiera o un proceso ajeno)."""
    monkeypatch.setenv("AUDITOR_DB_PATH", str(tmp_path / "t.db"))
    from main import app

    with TestClient(app, base_url=BASE) as c:
        yield c


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_DB_PATH", str(tmp_path / "t.db"))
    from main import app

    with TestClient(app, base_url=BASE, headers=F.auth()) as c:
        yield c


def test_extension_id_comes_from_manifest_key():
    assert EXT_ID == "oaenmoogbjlciahffnfdffcpjlphgjic"
    assert len(EXT_ID) == 32 and set(EXT_ID) <= set("abcdefghijklmnop")


# ------------------------------------------------------------------ el bug: token para la extensión
def test_extension_token_without_origin_and_sec_fetch_site_none(anon):
    """Lo que manda de verdad el service worker: GET sin Origin, Sec-Fetch-Site: none → 200."""
    r = anon.get("/auth/token", headers=SW)
    assert r.status_code == 200 and r.json()["token"] == security.SESSION_TOKEN
    assert r.headers["cache-control"] == "no-store"


def test_extension_token_with_extension_origin(anon):
    assert anon.get("/auth/token", headers=EXT).status_code == 200
    assert anon.get("/auth/token", headers={**EXT, **SW}).status_code == 200


@pytest.mark.parametrize("headers,reason", [
    (EVIL, "origen no permitido"),
    ({"Sec-Fetch-Site": "cross-site"}, "origen no permitido"),
    ({"Sec-Fetch-Site": "same-site"}, "origen no permitido"),
    ({"Origin": "http://localhost:5500", "Sec-Fetch-Site": "same-site"}, "origen no permitido"),  # test.html
    (OTHER_EXT, "origen no permitido"),
    (PANEL, "solo la extensión"),  # el panel ya trae el token en su HTML
    ({}, "solo la extensión"),  # curl / otro proceso sin cabeceras de navegador
])
def test_extension_token_rejected(anon, headers, reason):
    r = anon.get("/auth/token", headers=headers)
    assert r.status_code == 403 and r.json() == {"detail": reason}


def test_extension_flow_after_backend_restart(anon, monkeypatch):
    """Reinicio del backend sin recargar la extensión: el token viejo da 403 «token inválido», la extensión
    pide otro (Sec-Fetch-Site: none) y el reintento pasa."""
    old = security.SESSION_TOKEN
    monkeypatch.setattr(security, "SESSION_TOKEN", "token-nuevo-" + "x" * 31)  # "reinicio"
    r = anon.post("/analyze", json={"text": "hola", "platform": "test"}, headers={**EXT, security.TOKEN_HEADER: old})
    assert r.status_code == 403 and r.json() == {"detail": "token inválido"}
    new = anon.get("/auth/token", headers=SW).json()["token"]
    assert new == security.SESSION_TOKEN != old
    r = anon.post("/analyze", json={"text": "hola", "platform": "test"}, headers={**EXT, security.TOKEN_HEADER: new})
    assert r.status_code == 200
    r = anon.get("/stats", headers={**SW, security.TOKEN_HEADER: new})  # GET sin Origin del service worker
    assert r.status_code == 200


# ------------------------------------------------------------------ rutas sensibles
@pytest.mark.parametrize("method,path,kw", SENSITIVE, ids=IDS)
def test_sensitive_routes_without_token_are_403(anon, method, path, kw):
    r = getattr(anon, method)(path, **kw)
    assert r.status_code == 403 and r.json() == {"detail": "token inválido"}


@pytest.mark.parametrize("method,path,kw", SENSITIVE, ids=IDS)
def test_external_origin_is_403_even_with_token(client, method, path, kw):
    r = getattr(client, method)(path, headers=EVIL, **kw)
    assert r.status_code == 403 and r.json() == {"detail": "origen no permitido"}


@pytest.mark.parametrize("method,path,kw", SENSITIVE, ids=IDS)
def test_cross_site_without_origin_is_403_even_with_token(client, method, path, kw):
    r = getattr(client, method)(path, headers={"Sec-Fetch-Site": "cross-site"}, **kw)
    assert r.status_code == 403 and r.json() == {"detail": "origen no permitido"}


@pytest.mark.parametrize("headers", [EXT, {**EXT, **SW}, PANEL, {"Origin": "http://localhost:8000"} | {"Host": "localhost:8000"},
                                     SW, {"Sec-Fetch-Site": "same-origin"}, {}],
                         ids=["extension", "extension+sw", "panel", "panel-localhost", "sw-sin-origin", "panel-get", "curl"])
def test_allowed_callers_with_token(client, headers):
    r = client.post("/analyze", json={"text": "hola", "platform": "test"}, headers=headers)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("origin", ["null", "http://127.0.0.1.evil.example", "https://localhost.evil.example",
                                    "chrome-extension://", "file://", "http://192.168.1.10:8000",
                                    "http://127.0.0.1:5500", "http://localhost:9999", "https://127.0.0.1:8000",
                                    "chrome-extension://abcdefghijklmnopabcdefghijklmnop"])
def test_lookalike_or_other_origins_rejected(client, origin):
    r = client.get("/stats", headers={"Origin": origin})
    assert r.status_code == 403 and r.json() == {"detail": "origen no permitido"}


def test_wrong_token_is_403(anon):
    r = anon.post("/analyze", json={"text": "hola", "platform": "test"}, headers={security.TOKEN_HEADER: "x" * 43})
    assert r.status_code == 403 and r.json() == {"detail": "token inválido"}


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "127.0.0.1.nip.io", "192.168.1.10:8000"])
def test_dns_rebinding_host_rejected_everywhere(client, host):
    for path in ("/health", "/panel/", "/stats", "/auth/token"):
        r = client.get(path, headers={"Host": host, **SW})
        assert r.status_code == 403 and r.json() == {"detail": "host no permitido"}, (path, host)
        assert security.SESSION_TOKEN not in r.text


def test_local_hosts_accepted():
    for h in ("127.0.0.1:8000", "localhost:8000", "[::1]:8000", "localhost"):
        assert security.host_ok(h), h
    assert not security.host_ok(None) and not security.host_ok("")


def test_panel_html_embeds_token_but_only_for_local_host(anon):
    r = anon.get("/panel/", headers={"Sec-Fetch-Site": "none"})
    assert r.status_code == 200
    assert f'content="{security.SESSION_TOKEN}"' in r.text and "__CENTINELA_TOKEN__" not in r.text
    assert r.headers["cache-control"] == "no-store"
    assert anon.get("/panel/", headers={"Host": "evil.example"}).status_code == 403


def test_events_stream_accepts_token_in_query_only_there(anon):
    assert anon.get("/events?token=malo").status_code == 403
    assert anon.get(f"/stats?token={security.SESSION_TOKEN}").status_code == 403  # solo /events por query


# ------------------------------------------------------------------ 403 explicable y CORS
def test_403_reason_is_logged_without_secrets(anon, caplog):
    caplog.set_level(logging.WARNING, logger="auditor")
    anon.get("/auth/token", headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    anon.post("/analyze", json={"text": f"key {F.OPENAI_KEY}", "platform": "test"}, headers={security.TOKEN_HEADER: "malo"})
    anon.get("/stats", headers={"Host": "evil.example"})
    text = caplog.text
    assert "403 GET /auth/token: origen no permitido (origin=https://evil.example sec-fetch-site=cross-site)" in text
    assert "403 POST /analyze: token inválido" in text and "403 GET /stats: host no permitido" in text
    assert security.SESSION_TOKEN not in text and "malo" not in text and F.OPENAI_KEY not in text


def test_cors_closed_no_wildcards(client):
    for path in ("/analyze", "/stats", "/agent/preguntar", "/ia/estado", "/auth/token"):
        r = client.options(path, headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
        assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
    r = client.get("/auth/token", headers=SW)
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_token_never_logged(client, caplog):
    caplog.set_level(logging.DEBUG)
    client.post("/analyze", json={"text": "hola", "platform": "test"})
    client.get("/stats")
    assert security.SESSION_TOKEN not in caplog.text
