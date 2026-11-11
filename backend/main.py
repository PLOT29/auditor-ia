"""Backend del Auditor de Prompts (FastAPI) — SPEC §5.2.

Ejecutar (desde backend/):
    uvicorn main:app --host 127.0.0.1 --port 8000

Privacidad: el texto original solo existe en memoria durante la petición.
Nunca se loguea ni se persiste; a la BD llega `text_masked` + `text_sha256`.
"""
from __future__ import annotations

import hashlib
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import db
import masking
import risk
from detectors import injection, pii, secrets, topic

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
    injection_score: float
    findings: list[FindingOut]
    latency_ms: int


# ---------- App ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    log.info("BD lista en %s", db.db_path())
    yield


app = FastAPI(title="Auditor de Prompts", version="0.2.0", lifespan=lifespan)

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


def analyze_text(text: str) -> tuple[list, str]:
    """Corre los detectores de secretos y PII. Devuelve (findings, text_masked)."""
    found = secrets.detect(text)
    found += pii.detect(text, taken=found)
    masking.apply_masks(found)
    return found, masking.mask_text(text, found)


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    t0 = time.perf_counter()
    text = req.text

    found, text_masked = analyze_text(text)
    public = [f.to_public() for f in found]
    public += _safe_client_findings(req.client_findings, {f.type for f in found})

    category, category_conf = topic.classify(text)
    inj = float(injection.score(text))
    level = risk.compute_risk(public, inj)

    ts = (req.timestamp or datetime.now(timezone.utc)).isoformat()
    latency_ms = int(round((time.perf_counter() - t0) * 1000))

    prompt_id = db.insert_prompt(
        {
            "ts": ts,
            "platform": req.platform,
            "text_masked": text_masked,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "length": len(text),
            "category": category,
            "category_conf": category_conf,
            "injection_score": inj,
            "risk": level,
            "findings": public,
            "blocked": req.blocked,
            "user_decision": req.user_decision,
            "latency_ms": latency_ms,
        }
    )
    # Solo metadatos en el log — nunca el texto.
    client_ms = "-" if req.client_latency_ms is None else f"{req.client_latency_ms:.2f}ms"
    log.info("prompt id=%s platform=%s risk=%s findings=%d blocked=%s decision=%s latency=%dms cliente=%s",
             prompt_id, req.platform, level, len(public), req.blocked, req.user_decision,
             latency_ms, client_ms)

    return AnalyzeResponse(
        id=prompt_id,
        risk=level,
        category=category,
        category_confidence=category_conf,
        injection_score=inj,
        findings=[FindingOut(**f) for f in public],
        latency_ms=latency_ms,
    )


@app.get("/stats")
def stats() -> dict:
    return db.get_stats()


@app.get("/health")
def health() -> dict:
    return {"ok": True}
