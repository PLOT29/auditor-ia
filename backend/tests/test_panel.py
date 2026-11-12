"""Fase 5B · Centro de control: /panel, /events (SSE) y /stats?detalle=1. Datos FALSOS.

- /panel responde (redirige a /panel/), con CSP que prohíbe orígenes externos, y solo para localhost.
- El HTML/JS/CSS del panel no contiene URLs externas; el ECharts copiado no hace peticiones y es el
  archivo publicado (SHA-256 del README de vendor).
- /stats?detalle=1 respeta los filtros (fechas, plataformas, origen), no trae texto y funciona con la
  BD vacía.
- /events entrega un evento tras /analyze, sin texto del prompt ni valores de hallazgos.
"""
import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import fakes as F

BACKEND = Path(__file__).resolve().parents[1]
PANEL = BACKEND / "panel"
VENDOR = PANEL / "vendor" / "echarts"


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


# ------------------------------------------------------------------ /panel
def test_panel_redirects_and_serves_files(client):
    r = client.get("/panel", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].endswith("/panel/")
    r = client.get("/panel/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "Centro de control" in r.text
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "connect-src 'self'" in csp and "script-src 'self'" in csp
    for path in ("panel.css", "panel.js", "vendor/echarts/echarts.min.js"):
        f = client.get(f"/panel/{path}")
        assert f.status_code == 200, path
        assert f.headers["content-security-policy"] == csp


def test_panel_and_events_only_for_localhost():
    from main import app

    with TestClient(app, client=("203.0.113.7", 50000)) as remote:
        assert remote.get("/panel/").status_code == 403
        assert remote.get("/events").status_code == 403


URL_RE = re.compile(r"(?:https?:)?//[A-Za-z0-9.-]+\.[A-Za-z]{2,}[^\s\"'`)<>]*")


@pytest.mark.parametrize("name", ["index.html", "panel.js", "panel.css"])
def test_panel_sources_have_no_external_urls(name):
    text = (PANEL / name).read_text(encoding="utf-8")
    assert not URL_RE.findall(text), f"URL externa en backend/panel/{name}"
    assert "@import" not in text and "fonts.googleapis" not in text
    assert not re.search(r"<script[^>]*>[^<]+</script>", text), "sin scripts en línea (la CSP los bloquea)"
    assert not re.search(r"\son[a-z]+=", text), "sin manejadores en línea (onclick=…)"


def test_vendor_echarts_is_offline_and_licensed():
    js = (VENDOR / "echarts.min.js").read_bytes()
    readme = (VENDOR / "README.md").read_text(encoding="utf-8")
    assert hashlib.sha256(js).hexdigest() in readme  # el archivo publicado, sin modificar
    text = js.decode("utf-8")
    for api in ("fetch(", "XMLHttpRequest", "importScripts", "WebSocket", "sendBeacon"):
        assert api not in text, api
    allowed = {"http://www.apache.org/licenses/LICENSE-2.0", "http://www.w3.org/1999/xlink",
               "http://www.w3.org/2000/svg", "http://www.w3.org/2000/xmlns/", "http://www.w3.org/XML/1998/namespace",
               "https://github.com/ecomfe/zrender/blob/master/LICENSE.txt"}
    assert set(re.findall(r"https?://[^\s\"'`)<>]+", text)) <= allowed  # licencia y namespaces, no peticiones
    for f in ("LICENSE", "NOTICE", "LICENSE-zrender", "LICENSE-d3"):
        assert (VENDOR / f).is_file(), f
    assert "Apache License" in (VENDOR / "LICENSE").read_text(encoding="utf-8")


# ------------------------------------------------------------------ /stats?detalle=1
def test_stats_panel_with_empty_db(client):
    j = client.get("/stats", params={"detalle": 1, "inicio": "2026-11-01", "fin": "2026-11-12"}).json()
    p = j["panel"]
    assert p["kpis"]["actual"]["total"] == 0
    assert p["exposicion"]["valor"] == 0.0
    assert p["sankey"] == {"nodes": [], "links": []}
    assert p["recientes"] == [] and p["tipos_hallazgo"] == []
    assert len(p["tendencia"]) == 12 and all(d["alto"] == 0 for d in p["tendencia"])
    assert p["hallazgo"]["total"] == 0
    assert j["total"] == 0  # los campos de siempre siguen ahí (popup, compatibilidad)


def test_stats_panel_filters_and_no_text(client):
    key_text = f"client = OpenAI(api_key='{F.OPENAI_KEY}')"
    post(client, text=key_text, platform="chatgpt", timestamp="2026-11-10T17:00:00Z", blocked=True,
         user_decision="cancelado", client_findings=[{"type": "openai_key", "masked": "sk-****abcd"}])
    post(client, text=f"Mi correo es {F.EMAIL}", platform="claude", timestamp="2026-11-11T17:00:00Z",
         user_decision="advertido")
    post(client, text="Explícame qué es un firewall", platform="gemini", timestamp="2026-11-11T18:00:00Z")
    post(client, text="Resume la Revolución Mexicana", platform="claude", timestamp="2026-10-01T18:00:00Z")
    post(client, text="hola", platform="test", timestamp="2026-11-11T18:00:00Z")  # nunca cuenta en estadísticas

    r = client.get("/stats", params={"detalle": 1, "inicio": "2026-11-05", "fin": "2026-11-12"})
    p = r.json()["panel"]
    k = p["kpis"]["actual"]
    assert k["total"] == 3 and k["evitados"] == 1 and k["bloqueados"] == 1
    assert p["exposicion"]["por_riesgo"]["alto"] >= 1
    assert p["motivos"]["secreto"] == 1 and p["motivos"]["dato personal"] == 1
    assert [e["plataforma"] for e in p["recientes"]] == ["gemini", "claude", "chatgpt"]  # más nuevo primero
    assert {n["name"] for n in p["sankey"]["nodes"]} >= {"ChatGPT", "Claude", "Gemini", "Riesgo alto"}
    assert sum(sum(row) for row in p["mapa_calor"]["valores"]) == 3
    body = json.dumps(p)
    assert F.OPENAI_KEY not in body and F.EMAIL not in body and "firewall" not in body
    assert "text" not in body and "text_masked" not in body

    only_claude = client.get("/stats", params={"detalle": 1, "inicio": "2026-09-01", "fin": "2026-11-12",
                                               "plataformas": "claude"}).json()["panel"]
    assert only_claude["kpis"]["actual"]["total"] == 2
    imported = client.get("/stats", params={"detalle": 1, "inicio": "2026-09-01", "fin": "2026-11-12",
                                            "origen": "import"}).json()["panel"]
    assert imported["kpis"]["actual"]["total"] == 0
    assert client.get("/stats", params={"detalle": 1, "inicio": "2026-11-12", "fin": "2026-11-01"}).status_code == 422


def test_panel_never_writes_to_db(client):
    post(client, text="hola mundo", platform="claude")
    before = client.db_file.read_bytes()
    for _ in range(3):
        client.get("/stats", params={"detalle": 1})
        client.get("/panel/")
    assert client.db_file.read_bytes() == before


def test_exposure_index_formula():
    import analytics
    import pandas as pd

    df = pd.DataFrame({"risk": ["bajo", "bajo", "medio", "alto"]})
    e = analytics.exposure_index(df)
    assert e["valor"] == 37.5  # 100 × (0.5·1 + 1·1) / 4
    assert analytics.exposure_index(pd.DataFrame({"risk": ["alto"] * 3}))["valor"] == 100.0
    assert analytics.exposure_index(pd.DataFrame({"risk": []}))["valor"] == 0.0
    assert "0.5" in e["formula"]


# ------------------------------------------------------------------ /events (SSE)
def _read_sse(client, lines, limit=1):
    with client.stream("GET", f"/events?limit={limit}") as r:
        lines.append(("status", r.status_code, r.headers.get("content-type")))
        for line in r.iter_lines():
            lines.append(line)


def _wait_subscribers(n, timeout=5.0):
    from events_bus import bus

    t0 = time.time()
    while bus.subscribers < n:
        assert time.time() - t0 < timeout, "el cliente SSE no se conectó"
        time.sleep(0.02)


def test_events_delivers_event_after_analyze_without_text(client):
    lines: list = []
    t = threading.Thread(target=_read_sse, args=(client, lines), daemon=True)
    t.start()
    _wait_subscribers(1)
    text = f"Corrige: client = OpenAI(api_key='{F.OPENAI_KEY}') y escribe a {F.EMAIL}"
    data = post(client, text=text, blocked=True, user_decision="cancelado",
                client_findings=[{"type": "openai_key", "masked": "sk-****abcd"}])
    t.join(timeout=15)
    assert not t.is_alive(), "el flujo SSE no cerró tras 1 evento"
    assert lines[0][1] == 200 and lines[0][2].startswith("text/event-stream")
    raw = "\n".join(x for x in lines[1:] if isinstance(x, str))
    assert "retry: 3000" in raw and "event: prompt" in raw
    payload = json.loads(next(x for x in lines if isinstance(x, str) and x.startswith("data: {\"id\""))[6:])
    assert payload["id"] == data["id"] and payload["riesgo"] == "alto" and payload["bloqueado"] is True
    assert set(payload["tipos"]) == {"openai_key", "email"} and payload["motivo"] == ["secreto", "dato personal"]
    assert payload["decision"] == "cancelado" and payload["plataforma"] == "chatgpt" and payload["simulado"] is False
    # Sin texto ni valores (ni enmascarados): solo metadatos.
    assert F.OPENAI_KEY not in raw and F.EMAIL not in raw and "Corrige" not in raw and "sk-****" not in raw
    assert set(payload) <= {"id", "ts", "hora", "plataforma", "riesgo", "motivo", "tipos", "categoria", "decision",
                            "bloqueado", "latencia_ms", "simulado", "publicado_ms"}


def test_events_bus_drops_oldest_for_slow_clients():
    import asyncio

    from events_bus import MAX_QUEUE, Broadcaster

    async def run():
        b = Broadcaster()
        q = b.subscribe()
        for i in range(MAX_QUEUE + 5):
            b.publish({"id": i})
        await asyncio.sleep(0.05)
        assert q.qsize() == MAX_QUEUE
        assert (await q.get())["id"] == 5  # se perdieron los 5 más viejos; /analyze nunca se bloquea
        b.unsubscribe(q)
        assert b.publish({"id": -1}) == 0

    asyncio.run(run())


def test_analyze_still_works_without_subscribers(client):
    from events_bus import bus

    assert bus.subscribers == 0
    assert post(client, text="hola")["risk"] in ("bajo", "medio", "alto")
    conn = sqlite3.connect(client.db_file)
    assert conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0] == 1
    conn.close()
