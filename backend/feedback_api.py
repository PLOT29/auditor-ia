"""Rutas del revisor humano y del aprendizaje asistido (Fase 5D). Todas exigen el token de sesión y un origen
local (middleware + security.py). Las que entrenan, adoptan, vuelven atrás o confirman propagaciones exigen
además que la petición venga del PANEL: nunca de la extensión ni del agente (el agente no tiene herramientas
para esto).

POST /labels                     corregir tema o inyección de un evento (panel o extensión)
POST /labels/{id}/deshacer       deshacer (y, si se pide, sus propagadas)
GET  /labels/{id}/similares      vecinos propuestos (solo panel; texto enmascarado solo sin modo presentación)
POST /labels/{id}/propagar       confirmar vecinos (solo panel)
PUT  /events/{id}/nota           nota libre del evento, una por evento (solo panel; 5D.1)
GET  /learning/estado            cifras, versiones, trabajo en curso
POST /learning/reentrenar | adoptar | descartar | volver   (solo panel)
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import feedback
import learning
import security

router = APIRouter()


class Correccion(BaseModel):
    event_id: int = Field(ge=1)
    tarea: Literal["tema", "inyeccion"]
    valor: str = Field(min_length=1, max_length=20)
    nota: str | None = Field(default=None, max_length=feedback.MAX_NOTE)


class Nota(BaseModel):
    nota: str | None = Field(default=None, max_length=feedback.MAX_EVENT_NOTE)


class Deshacer(BaseModel):
    incluir_propagadas: bool = False


class Propagar(BaseModel):
    event_ids: list[int] = Field(default_factory=list, max_length=feedback.MAX_CANDIDATES)


class Version(BaseModel):
    tarea: Literal["tema", "inyeccion"]
    version: str = Field(pattern=r"^v\d{1,4}$")


class Tarea(BaseModel):
    tarea: Literal["tema", "inyeccion"]


def _panel_only(request: Request) -> None:
    if not security.is_panel_request(request.headers, request.url.scheme):
        raise HTTPException(status_code=403, detail=security.PANEL_ONLY)


def _err(e: Exception, code: int = 422):
    raise HTTPException(status_code=code, detail=str(e)) from None


@router.post("/labels")
def corregir(c: Correccion) -> dict:
    try:
        label = feedback.correct(c.event_id, c.tarea, c.valor, c.nota)
    except feedback.FeedbackError as e:
        _err(e, 404 if "no existe" in str(e) else 422)
    return {"etiqueta": label, "decidido_por": feedback.decided_by(label, None)}


@router.put("/events/{event_id}/nota")
def guardar_nota(event_id: int, n: Nota, request: Request) -> dict:
    """Fase 5D.1 · nota libre del evento desde el panel (token + origen del panel). Nunca se loguea."""
    _panel_only(request)
    try:
        return feedback.save_event_note(event_id, n.nota)
    except feedback.FeedbackError as e:
        _err(e, 404 if "no existe" in str(e) else 422)


@router.post("/labels/{label_id}/deshacer")
def deshacer(label_id: int, d: Deshacer) -> dict:
    try:
        return feedback.undo(label_id, d.incluir_propagadas)
    except feedback.FeedbackError as e:
        _err(e, 404)


@router.get("/labels/{label_id}/similares")
def similares(label_id: int, request: Request, presentacion: bool = True) -> dict:
    _panel_only(request)
    try:
        return feedback.candidates(label_id, include_text=not presentacion)
    except feedback.FeedbackError as e:
        _err(e, 409)


@router.post("/labels/{label_id}/propagar")
def propagar(label_id: int, p: Propagar, request: Request) -> dict:
    _panel_only(request)
    try:
        return feedback.propagate(label_id, p.event_ids)
    except feedback.FeedbackError as e:
        _err(e, 409)


@router.get("/learning/estado")
def estado() -> dict:
    return learning.status()


@router.post("/learning/reentrenar")
def reentrenar(request: Request) -> dict:
    _panel_only(request)
    st = learning.status()
    if not st["puede_reentrenar"]:
        raise HTTPException(status_code=409, detail=f"faltan {st['faltan']} etiquetas nuevas o hay un trabajo en curso")
    learning.job.retrain()
    return learning.job.snapshot()


@router.post("/learning/adoptar")
def adoptar(v: Version, request: Request) -> dict:
    _panel_only(request)
    try:
        if not learning.adopt(v.tarea, v.version):
            raise HTTPException(status_code=409, detail="hay un trabajo en curso")
    except ValueError as e:
        _err(e, 409)
    return learning.job.snapshot()


@router.post("/learning/descartar")
def descartar(v: Version, request: Request) -> dict:
    _panel_only(request)
    try:
        return learning.discard(v.tarea, v.version)
    except ValueError as e:
        _err(e, 409)


@router.post("/learning/volver")
def volver(t: Tarea, request: Request) -> dict:
    _panel_only(request)
    try:
        if not learning.rollback(t.tarea):
            raise HTTPException(status_code=409, detail="hay un trabajo en curso")
    except ValueError as e:
        _err(e, 409)
    return learning.job.snapshot()
