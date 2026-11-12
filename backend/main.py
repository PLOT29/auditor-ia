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

import hashlib
import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import config
import db
import masking
import risk
from detectors import embeddings, injection, pii, secrets, topic

log = logging.getLogger("auditor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

MAX_TEXT_LEN = 100_000


# ---------- Modelos (contrato de la API) ----------

class ClientFinding(BaseModel):
    type: str = Field(max_length=64)
    masked: str = Field(default="", max_length=256)


class AnalyzeRequest(BaseModel):
    text: str = Field(max_length=MAX_TEXT_LEN)
    # "test" = extension/test/test.html (solo desarrollo; el dashboard puede filtrarlo).
    platform: Literal["chatgpt", "claude", "gemini", "test"]
    timestamp: datetime | None = None
    client_findings: list[ClientFinding] = Field(default_factory=list)
    blocked: bool = False
    user_decision: Literal["enviado", "cancelado", "no_aplica"] = "no_aplica"
    # Fase 3 (opcional): latencia de la decisión de bloqueo en content.js (CE-6).
    # Solo se loguea; no se guarda (el esquema de la BD no cambia).
    client_latency_ms: float | None = Field(default=None, ge=0, le=60_000)


class FindingOut(BaseModel):
    type: str
    masked: str
    source: str


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

# CORS: NO se habilita a propósito. La extensión llama desde su service worker
# (background.js) con host_permissions sobre localhost:8000, y Chrome no aplica
# CORS a esas peticiones. Sin CORSMiddleware, una página web cualquiera NO puede
# leer /stats ni mandar JSON a /analyze (el preflight falla). Ver test_cors.py.


@app.middleware("http")
async def latency_log(request: Request, call_next):
    """Loguea método, ruta, estado y latencia total. Nunca el cuerpo."""
    t0 = time.perf_counter()
    response = await call_next(request)
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
        out.append({"type": cf.type, "masked": masked, "source": "client"})
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
    return process(req)


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
def stats() -> dict:
    return db.get_stats()


@app.get("/health")
def health() -> dict:
    """{"ok": true, "models": {...: {"loaded": bool, ...}}, "thresholds": {...}}"""
    st = models_status()
    return {"ok": True, **st}
