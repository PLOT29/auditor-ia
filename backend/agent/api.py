"""Rutas del agente y de los niveles de IA (Fase 5C). Todas exigen el token de sesión y un Origin local
(lo aplica el middleware de main.py con security.py).

/agent/sugeridas            preguntas sugeridas (nivel 1)
/agent/preguntar  (POST)    respuesta en streaming (NDJSON): eventos tool / delta / done
/ia/estado                  nivel activo, IA local (pasos del instalador) y nube (sin la key)
/ia/nivel         (POST)    cambiar de nivel (2 y 3 solo si están listos)
/ia/local/activar|confirmar|desactivar (POST)
/ia/nube/configurar|probar|borrar      (POST)

Las preguntas al agente NO se guardan en la BD (ni en logs): solo se loguean nivel, nº de herramientas y ms.
"""
from __future__ import annotations

import json
import logging
import time

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent import installer, llm, rules, state

log = logging.getLogger("auditor")
router = APIRouter()


class Pregunta(BaseModel):
    pregunta: str = Field(min_length=1, max_length=500)


class Nivel(BaseModel):
    nivel: int = Field(ge=1, le=3)


class Confirmacion(BaseModel):
    aceptar: bool


class Nube(BaseModel):
    base_url: str = Field(min_length=8, max_length=200)
    modelo: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:/\-]+$")
    api_key: str | None = Field(default=None, min_length=8, max_length=300)
    acepto_envio: bool = False
    dominio_aceptado: str | None = Field(default=None, max_length=200)


def _provider(data: dict) -> tuple[llm.Provider | None, str]:
    """Proveedor del nivel activo, o None (nivel 1 o nivel no disponible) y su etiqueta."""
    if data["nivel"] == 2 and data["ollama"]["lista"]:
        m = data["ollama"]["modelo"]
        return llm.Provider(base_url=llm.OLLAMA_BASE, model=m, name="ollama"), f"IA local · {m}"
    if data["nivel"] == 3:
        nube = data["nube"]
        key = state.get_key()
        if nube["base_url"] and nube["modelo"] and nube["aceptado"] and key:
            return (llm.Provider(base_url=nube["base_url"], model=nube["modelo"], api_key=key, name="nube"),
                    f"IA en la nube · {nube['dominio']}")
    return None, "Sin IA (reglas)"


def _line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


def _stream(question: str):
    t0 = time.perf_counter()
    data = state.load()
    provider, label = _provider(data)
    used: list[dict] = []
    nivel = data["nivel"] if provider else 1
    texted = False

    def rules_events(note: str | None = None):
        if note:
            yield {"type": "delta", "text": note}
        for ev in rules.answer(question):
            yield ev

    def chunked(ev):
        # Nivel 1: se emite por palabras para que el panel lo pinte en streaming.
        words = ev["text"].split(" ")
        for i in range(0, len(words), 3):
            yield {"type": "delta", "text": " ".join(words[i:i + 3]) + (" " if i + 3 < len(words) else "")}
            time.sleep(0.02)

    events = rules_events() if provider is None else llm.run(question, provider)
    try:
        for ev in events:
            if ev["type"] == "tool":
                used.append({k: ev[k] for k in ("nombre", "argumentos", "ok") if k in ev} | ({"error": ev["error"]} if ev.get("error") else {}))
                yield _line(ev)
            elif provider is None:
                for part in chunked(ev):
                    yield _line(part)
            else:
                texted = True
                yield _line(ev)
    except llm.ProviderError as e:
        # La demo nunca queda rota: si la IA falla, contesta el nivel 1.
        log.warning("agente: el proveedor falló (%s); se responde con reglas", type(e).__name__)
        note = (f"(La IA no respondió: {e}. Contesto con reglas.)\n\n" if not texted
                else "\n\n(La respuesta de la IA se interrumpió.)")
        nivel, label = 1, "Sin IA (reglas, respaldo)"
        if texted:
            yield _line({"type": "delta", "text": note})
        else:
            for ev in rules_events(note):
                if ev["type"] == "tool":
                    used.append({k: ev[k] for k in ("nombre", "argumentos", "ok")})
                    yield _line(ev)
                else:
                    for part in chunked(ev):
                        yield _line(part)
    except Exception as e:  # noqa: BLE001
        log.warning("agente: error %s", type(e).__name__)
        yield _line({"type": "error", "text": "No pude responder esa pregunta."})
    ms = round((time.perf_counter() - t0) * 1000)
    log.info("agente nivel=%s herramientas=%d ms=%d", nivel, len(used), ms)  # nunca la pregunta
    yield _line({"type": "done", "nivel": nivel, "proveedor": label, "herramientas": used, "ms": ms})


@router.get("/agent/sugeridas")
def sugeridas() -> dict:
    return {"preguntas": rules.SUGERIDAS}


@router.post("/agent/preguntar")
def preguntar(p: Pregunta):
    return StreamingResponse(_stream(p.pregunta), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ niveles
@router.get("/ia/estado")
def ia_estado() -> dict:
    return {**state.public(), "local": installer.setup.snapshot(), "descarga_manual": installer.DOWNLOAD_PAGE}


@router.post("/ia/nivel")
def ia_nivel(n: Nivel) -> dict:
    data = state.load()
    if n.nivel == 2 and not data["ollama"]["lista"]:
        raise HTTPException(409, "La IA local no está lista: usa «Activar IA local».")
    if n.nivel == 3 and not (data["nube"]["aceptado"] and data["nube"]["base_url"] and state.get_key()):
        raise HTTPException(409, "Configura el proveedor en la nube y acepta el aviso primero.")
    return state.public(state.set_level(n.nivel))


@router.post("/ia/local/activar")
def ia_local_activar() -> dict:
    installer.setup.start()
    return installer.setup.snapshot()


@router.post("/ia/local/confirmar")
def ia_local_confirmar(c: Confirmacion) -> dict:
    if not installer.setup.confirm(c.aceptar):
        raise HTTPException(409, "No hay ninguna instalación esperando confirmación.")
    return installer.setup.snapshot()


@router.post("/ia/local/desactivar")
def ia_local_desactivar() -> dict:
    """Deja de usar Ollama (no lo desinstala ni borra el modelo)."""
    data = state.load()
    data["ollama"]["lista"] = False
    if data["nivel"] == 2:
        data["nivel"] = 1
    state.save(data)
    installer.setup.reset()
    return state.public(data)


@router.post("/ia/nube/configurar")
def ia_nube_configurar(n: Nube) -> dict:
    try:
        base = state.validate_base_url(n.base_url)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    domain = state.domain_of(base)
    if not n.acepto_envio or (n.dominio_aceptado or "").lower() != domain:
        raise HTTPException(422, f"Debes aceptar que se enviarán cifras agregadas (nunca prompts) a {domain}.")
    if n.api_key:
        state.set_key(n.api_key)
    elif not state.get_key():
        raise HTTPException(422, "Falta la API key.")
    data = state.load()
    data["nube"] = {"base_url": base, "modelo": n.modelo, "dominio": domain, "aceptado": True}
    data["nivel"] = 3
    state.save(data)
    return state.public(data)


@router.post("/ia/nube/probar")
def ia_nube_probar() -> dict:
    data = state.load()
    nube, key = data["nube"], state.get_key()
    if not (nube["base_url"] and nube["modelo"] and key):
        raise HTTPException(409, "No hay un proveedor configurado.")
    t0 = time.perf_counter()
    try:
        r = httpx.post(f"{nube['base_url']}/chat/completions", timeout=30,
                       headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                       json={"model": nube["modelo"], "max_tokens": 5,
                             "messages": [{"role": "user", "content": "Responde solo: OK"}]})
    except httpx.HTTPError as e:
        return {"ok": False, "detalle": f"No se pudo conectar con {nube['dominio']} ({type(e).__name__})."}
    ms = round((time.perf_counter() - t0) * 1000)
    if r.status_code == 200:
        return {"ok": True, "detalle": f"{nube['dominio']} respondió en {ms} ms con el modelo {nube['modelo']}."}
    hint = {401: "la API key fue rechazada", 403: "acceso denegado", 404: "modelo o URL no encontrados",
            429: "límite de uso o saldo agotado"}.get(r.status_code, "error del proveedor")
    return {"ok": False, "detalle": f"{nube['dominio']} respondió HTTP {r.status_code}: {hint}."}


@router.post("/ia/nube/borrar")
def ia_nube_borrar() -> dict:
    state.delete_key()
    data = state.load()
    data["nube"]["aceptado"] = False
    if data["nivel"] == 3:
        data["nivel"] = 1
    state.save(data)
    return state.public(data)
