"""Backend del Auditor de Prompts (FastAPI) — SPEC §5.2.

Ejecutar (desde backend/):
    python -m uvicorn main:app --reload

Privacidad: el texto original solo existe en memoria durante la petición.
Nunca se loguea ni se persiste; a la BD llega `text_masked` + `text_sha256`.

Fase 4B: los modelos (embeddings MiniLM, tema, inyección y Presidio) se cargan UNA vez,
al arrancar (lifespan) o en la primera llamada a process()/classify_text() si este módulo
se usa sin servidor (ml/import_history.py, ml/reclassify.py). Si alguno falla, el backend
sigue funcionando en modo degradado (category="sin_clasificar", injection_score=null,
PII solo con regex) y /health lo reporta.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_serializer

import analytics
import config
import db
import masking
import risk
import security
from agent.api import router as agent_router
from detectors import catalog, embeddings, injection, pii, secrets, topic
from events_bus import bus

log = logging.getLogger("auditor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

MAX_TEXT_LEN = 100_000


# ---------- Modelos (contrato de la API) ----------

class ClientFinding(BaseModel):
    type: str = Field(max_length=64)
    masked: str = Field(default="", max_length=256)


class AnalyzeRequest(BaseModel):
    text: str = Field(max_length=MAX_TEXT_LEN)
    # "test" = extension/test/test.html (solo desarrollo; las estadísticas lo excluyen).
    platform: Literal["chatgpt", "claude", "gemini", "test"]
    timestamp: datetime | None = None
    client_findings: list[ClientFinding] = Field(default_factory=list)
    blocked: bool = False
    # Fase 5A (aditivo): "censurado" = se reemplazaron los hallazgos por marcadores y se envió el texto
    # censurado (es el que llega aquí); "advertido" = política "advertir" y el usuario envió igual.
    user_decision: Literal["enviado", "cancelado", "no_aplica", "censurado", "advertido"] = "no_aplica"
    # Fase 3 (opcional): latencia de la decisión de bloqueo en content.js (CE-6).
    # Solo se loguea; no se guarda (el esquema de la BD no cambia).
    client_latency_ms: float | None = Field(default=None, ge=0, le=60_000)


class FindingOut(BaseModel):
    type: str
    masked: str
    source: str
    # Fase 5A.1 (opcional, aditivo): solo DNI/NIE — ¿la letra de control coincide? En los demás
    # tipos el campo NO aparece (el contrato de siempre: type, masked, source).
    valido: bool | None = None

    @model_serializer(mode="wrap")
    def _omit_empty_valido(self, handler):
        data = handler(self)
        if data.get("valido") is None:
            data.pop("valido", None)
        return data


class AnalyzeResponse(BaseModel):
    id: int
    risk: Literal["bajo", "medio", "alto"]
    category: str
    category_confidence: float
    injection_score: float | None  # null si el detector de inyección no está disponible
    findings: list[FindingOut]
    latency_ms: int
    # Fase 4C (opcional, aditivo): true si la confianza del tema < umbral de metrics.json;
    # null si no hay clasificador de tema. La BD guarda igual la categoría top y su confianza.
    category_uncertain: bool | None = None


# ---------- Carga única de modelos ----------

_models_lock = threading.Lock()
_models = {"loaded": False, "load_count": 0, "load_ms": None}


def load_models() -> dict:
    """Carga embeddings, tema, inyección y Presidio UNA vez (idempotente y thread-safe)."""
    if _models["loaded"]:
        return models_status()
    with _models_lock:
        if _models["loaded"]:
            return models_status()
        t0 = time.perf_counter()
        if config.models_disabled():
            log.warning("AUDITOR_DISABLE_MODELS=1 → sin modelos de ML (solo regex)")
        else:
            embeddings.load()
            topic.load()
            injection.load()
            pii.load()
            if topic.is_loaded() and embeddings.is_loaded() and topic.embedding_model() != embeddings.model_name():
                log.warning("el modelo de tema usa otros embeddings (%s ≠ %s) → se desactiva",
                            topic.embedding_model(), embeddings.model_name())
                topic.reset()
        _models.update(loaded=True, load_count=_models["load_count"] + 1,
                       load_ms=round((time.perf_counter() - t0) * 1000))
        st = models_status()
        log.info("modelos cargados en %sms: %s", _models["load_ms"],
                 {k: v["loaded"] for k, v in st["models"].items()})
        return st


def reset_models() -> None:
    """Olvida los modelos cargados (para tests); el siguiente uso los vuelve a cargar."""
    with _models_lock:
        for mod in (embeddings, topic, injection, pii):
            mod.reset()
        _models.update(loaded=False, load_ms=None)


def models_status() -> dict:
    high, medium = risk.thresholds()
    return {
        "models": {"embeddings": embeddings.status(), "topic": topic.status(),
                   "injection": injection.status(), "presidio": pii.status()},
        "thresholds": {"injection_high": high, "injection_medium": medium,
                       "topic_confidence": topic.confidence_threshold()},
        "load_ms": _models["load_ms"],
    }


# ---------- App ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    log.info("BD lista en %s", db.db_path())
    load_models()
    yield


app = FastAPI(title="Auditor de Prompts", version="0.4.0", lifespan=lifespan)

# CORS: NO se habilita a propósito (ni comodines ni lista). La extensión llama desde su service worker
# (background.js) con host_permissions sobre localhost:8000, y Chrome no aplica CORS a esas peticiones;
# el panel es del mismo origen. Sin CORSMiddleware, una página web cualquiera NO puede leer las
# respuestas. Fase 5C: además, Host/Origin validados y token de sesión (security.py). Ver test_cors.py
# y test_security.py.


PANEL_DIR = Path(__file__).resolve().parent / "panel"
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}  # "testclient" = TestClient de los tests
# Fase 5B · el centro de control solo pide cosas a su propio origen (RNF-03): el navegador bloquea
# cualquier petición, script, fuente o imagen de otro sitio aunque alguien la agregara al HTML.
PANEL_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
             "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
LOCAL_ONLY_PREFIXES = ("/panel", "/events")


@app.middleware("http")
async def latency_log(request: Request, call_next):
    """Loguea método, ruta, estado y latencia total. Nunca el cuerpo.

    Fase 5B: /panel y /events solo responden a clientes locales (además de que uvicorn escucha en
    127.0.0.1) y el panel se sirve con una CSP que prohíbe cualquier origen externo.
    Fase 5C (security.py): en TODAS las rutas, Host local (DNS rebinding) y Origin local o de la extensión
    (CSRF); en las sensibles, además, el token de sesión (X-Centinela-Token; en /events, ?token=)."""
    t0 = time.perf_counter()
    path = request.url.path
    if not security.host_ok(request.headers.get("host")):
        return JSONResponse(status_code=403, content={"detail": "host no permitido"})
    if not security.origin_ok(request.headers.get("origin")):
        return JSONResponse(status_code=403, content={"detail": "origen no permitido"})
    if path.startswith(LOCAL_ONLY_PREFIXES) and (request.client is None or request.client.host not in LOOPBACK):
        return JSONResponse(status_code=403, content={"detail": "solo localhost"})
    if security.requires_token(path) and not security.token_ok(
            request.headers.get(security.TOKEN_HEADER), request.query_params.get(security.TOKEN_QUERY),
            allow_query=(path == "/events")):
        return JSONResponse(status_code=403, content={"detail": "falta el token de sesión o no es válido"})
    response = await call_next(request)
    if path.startswith("/panel"):
        response.headers["Content-Security-Policy"] = PANEL_CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.setdefault("Cache-Control", "no-cache")  # el HTML con token: no-store
    ms = (time.perf_counter() - t0) * 1000
    response.headers["X-Process-Time-Ms"] = f"{ms:.1f}"
    log.info("%s %s -> %s %.1fms", request.method, request.url.path, response.status_code, ms)
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    # FastAPI por defecto devuelve el `input` recibido en el error, lo cual
    # podría reflejar el prompt (con secretos). Se omite.
    errors = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


def _safe_client_findings(client: list[ClientFinding], server_types: set[str]) -> list[dict]:
    """Agrega hallazgos del cliente que el servidor no detectó.

    Defensa en profundidad: si el `masked` del cliente no parece enmascarado,
    se reemplaza por `****` para no persistir un valor en claro.
    """
    out = []
    for cf in client:
        if cf.type in server_types:
            continue
        masked = cf.masked if masking.STARS in cf.masked else masking.STARS
        # Fase 5A: PII y términos propios que solo vio el cliente llevan fuente client_pii; secretos e
        # identificadores, client. El riesgo sale del tipo (catalog.py): un CURP o una dirección del
        # cliente son "alto" (la extensión los bloquea por default); un correo, "medio".
        # El término propio nunca llega: solo el tipo "termino_propio" y "****".
        source = "client_pii" if catalog.is_personal(cf.type) else "client"
        out.append({"type": cf.type, "masked": masked, "source": source})
    return out


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)


# ---------- Pipeline (reutilizable) ----------

def detect_findings(text: str) -> tuple[list, str, dict]:
    """Etapas 1–2: secretos → PII (sin solapes) → enmascarado. Devuelve (findings, text_masked, tiempos)."""
    load_models()
    t = time.perf_counter()
    found = secrets.detect(text)
    t_sec = _ms(t)
    t = time.perf_counter()
    found += pii.detect(text, taken=found)
    t_pii = _ms(t)
    masking.apply_masks(found)
    return found, masking.mask_text(text, found), {"secretos": t_sec, "pii": t_pii}


def analyze_text(text: str) -> tuple[list, str]:
    """Secretos + PII. Devuelve (findings, text_masked). Lo usa también el dry-run del importador."""
    found, masked, _ = detect_findings(text)
    return found, masked


def classify_text(text: str) -> dict:
    """Etapas 3–5: UN solo cálculo de embeddings, reutilizado por tema e inyección.

    Lo usan process() y ml/reclassify.py (sobre text_masked).
    """
    load_models()
    timings = {}
    t = time.perf_counter()
    X = embeddings.encode([text]) if (topic.is_loaded() or injection.needs_embeddings()) else None
    timings["embeddings"] = _ms(t)
    t = time.perf_counter()
    category, conf = topic.classify(text, X)
    uncertain = topic.is_uncertain(conf)
    timings["tema"] = _ms(t)
    t = time.perf_counter()
    inj = injection.score(text, X)
    timings["inyeccion"] = _ms(t)
    return {"category": category, "category_conf": conf, "category_uncertain": uncertain,
            "injection_score": inj, "timings": timings}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    resp = process(req)
    publish_event(req, resp)
    return resp


def publish_event(req: AnalyzeRequest, resp: AnalyzeResponse) -> None:
    """Fase 5B: emite al feed en vivo (/events) el resumen SIN texto del evento recién guardado."""
    try:
        ev = analytics.event_summary(
            id=resp.id, ts=(req.timestamp or datetime.now(timezone.utc)).isoformat(), platform=req.platform,
            risk=resp.risk, category=resp.category, category_conf=resp.category_confidence,
            types=[f.type for f in resp.findings], blocked=req.blocked, decision=req.user_decision,
            latency_ms=resp.latency_ms, threshold=topic.confidence_threshold())
        ev["publicado_ms"] = round(time.time() * 1000, 1)  # para medir /analyze → feed en el panel
        bus.publish(ev)
    except Exception as e:  # noqa: BLE001  — el feed nunca debe romper /analyze
        log.warning("no se pudo publicar el evento id=%s en /events: %s", resp.id, type(e).__name__)


def sha256_of(text: str) -> str:
    """Huella del texto ORIGINAL usada para deduplicar (columna text_sha256)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def process(req: AnalyzeRequest, source: str = "extension", dedupe: bool = False) -> AnalyzeResponse | None:
    """Pipeline completo: secretos → PII → embeddings → tema → inyección → riesgo → BD.

    Lo usan /analyze y ml/import_history.py (source="import"), para que ambos pasen
    exactamente por la misma lógica. Con `dedupe=True`, si ya existe un prompt con el mismo
    text_sha256 no se inserta y devuelve None.
    """
    load_models()  # no-op si ya están cargados
    t0 = time.perf_counter()
    text = req.text
    text_sha256 = sha256_of(text)
    if dedupe and db.sha256_exists(text_sha256):
        return None

    found, text_masked, timings = detect_findings(text)
    public = [f.to_public() for f in found]
    public += _safe_client_findings(req.client_findings, {f.type for f in found})

    cls = classify_text(text)
    timings.update(cls["timings"])
    inj = cls["injection_score"]
    level = risk.compute_risk(public, inj)

    ts = (req.timestamp or datetime.now(timezone.utc)).isoformat()
    latency_ms = int(round((time.perf_counter() - t0) * 1000))
    timings["total"] = latency_ms

    prompt_id = db.insert_prompt(
        {
            "ts": ts,
            "platform": req.platform,
            "text_masked": text_masked,
            "text_sha256": text_sha256,
            "length": len(text),
            "category": cls["category"],
            "category_conf": cls["category_conf"],
            "injection_score": inj,
            "risk": level,
            "findings": public,
            "blocked": req.blocked,
            "user_decision": req.user_decision,
            "latency_ms": latency_ms,
            "source": source,
        }
    )
    # Solo metadatos en el log — nunca el texto.
    client_ms = "-" if req.client_latency_ms is None else f"{req.client_latency_ms:.2f}ms"
    log.info("prompt id=%s platform=%s source=%s risk=%s category=%s findings=%d blocked=%s decision=%s "
             "cliente=%s etapas_ms=%s",
             prompt_id, req.platform, source, level, cls["category"], len(public), req.blocked,
             req.user_decision, client_ms, " ".join(f"{k}={v}" for k, v in timings.items()))

    return AnalyzeResponse(
        id=prompt_id,
        risk=level,
        category=cls["category"],
        category_confidence=cls["category_conf"],
        injection_score=inj,
        findings=[FindingOut(**f) for f in public],
        latency_ms=latency_ms,
        category_uncertain=cls["category_uncertain"],
    )


@app.get("/stats")
def stats(
    desde: datetime | None = None,
    detalle: bool = False,
    inicio: date | None = None,
    fin: date | None = None,
    plataformas: str | None = Query(default=None, max_length=64),
    origen: Literal["ambos", "extension", "import"] = "ambos",
) -> dict:
    """Agregados. `?desde=<ISO-8601>` (opcional, Fase 5A): solo eventos desde ese instante.

    Fase 5B (aditivo): con `?detalle=1` agrega `panel` — KPIs con el periodo anterior, índice de
    exposición, Sankey, mapa de calor, tendencia por riesgo, tipos de hallazgo, motivos, frase de
    hallazgo y últimos eventos — calculados por analytics.py (consultas compartidas, conexión de
    SOLO LECTURA) con los filtros `inicio`/`fin` (días de la Ciudad de México; por defecto los últimos
    14), `plataformas` (chatgpt,claude,gemini) y `origen`. Nunca incluye texto de prompts.
    """
    out = db.get_stats(since=desde.isoformat() if desde else None)
    if detalle:
        today = datetime.now(analytics.TZ).date()
        end = fin or today
        start = inicio or (end - timedelta(days=13))
        if start > end:
            raise HTTPException(status_code=422, detail="inicio debe ser anterior o igual a fin")
        plats = tuple(p for p in (plataformas or ",".join(analytics.PLATFORMS)).split(",") if p in analytics.PLATFORMS)
        f = analytics.Filters(start=start, end=end, platforms=plats, source=origen)
        out["panel"] = analytics.panel(db.db_path(), f, topic.confidence_threshold())
    return out


@app.get("/events")
async def events(request: Request, limit: int | None = Query(default=None, ge=1, le=1000)):
    """Fase 5B · Server-Sent Events: un `event: prompt` por cada evento que /analyze guarda.

    Solo resúmenes SIN texto (ver analytics.event_summary). `retry: 3000` hace que EventSource
    reconecte solo; cada 15 s sin eventos se manda un comentario `: ping` para mantener viva la
    conexión. `limit` (opcional) cierra el flujo tras N eventos (tests, depuración con curl).
    """
    q = bus.subscribe()

    async def stream():
        sent = 0
        try:
            yield "retry: 3000\n\n"
            yield f"event: hola\ndata: {json.dumps({'clientes': bus.subscribers})}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": ping\n\n"
                    continue
                yield f"id: {ev['id']}\nevent: prompt\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                sent += 1
                if limit and sent >= limit:
                    break
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/events/{event_id}")
def event_detail(event_id: int, presentacion: bool = False) -> dict:
    """Fase 5C · detalle de UN evento para el panel lateral, desde la conexión de SOLO LECTURA.

    El único texto que existe es `text_masked` (el original nunca se guardó). Con `?presentacion=1` el
    panel está en modo presentación y la respuesta NO lo incluye (solo metadatos).
    """
    d = analytics.event_detail(db.db_path(), event_id, include_text=not presentacion,
                               threshold=topic.confidence_threshold())
    if d is None:
        raise HTTPException(status_code=404, detail="evento no encontrado")
    return d


@app.get("/auth/token", include_in_schema=False)
def auth_token(request: Request) -> JSONResponse:
    """Fase 5C · token de sesión para la extensión (background.js). Solo responde a un Origin
    chrome-extension:// (una página web no puede leerlo: no hay CORS y su Origin se rechaza)."""
    if not security.is_extension_origin(request.headers.get("origin")):
        return JSONResponse(status_code=403, content={"detail": "solo la extensión"})
    return JSONResponse({"token": security.SESSION_TOKEN}, headers={"Cache-Control": "no-store"})


@app.get("/panel", include_in_schema=False)
def panel_root() -> RedirectResponse:
    return RedirectResponse(url="/panel/", status_code=307)


@app.get("/panel/", include_in_schema=False)
@app.get("/panel/index.html", include_in_schema=False)
def panel_index() -> HTMLResponse:
    """Fase 5C · el HTML del panel lleva embebido el token de sesión (<meta name="centinela-token">).
    Otra página no puede leerlo: no hay CORS y el Host se valida (DNS rebinding)."""
    html = (PANEL_DIR / "index.html").read_text(encoding="utf-8").replace("__CENTINELA_TOKEN__", security.SESSION_TOKEN)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


app.include_router(agent_router)


# Centro de control (Fase 5B): archivos estáticos de backend/panel/ (index.html, panel.css, panel.js y
# vendor/echarts). Solo lectura; nunca escribe en la BD.
app.mount("/panel/", StaticFiles(directory=PANEL_DIR, html=True), name="panel")


@app.get("/health")
def health() -> dict:
    """{"ok": true, "models": {...: {"loaded": bool, ...}}, "thresholds": {...}}"""
    st = models_status()
    return {"ok": True, **st}
