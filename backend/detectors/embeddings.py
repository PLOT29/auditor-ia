"""Embeddings MiniLM compartidos por tema e inyección (se calculan UNA vez por prompt).

Reutiliza ml/mlutils.py (get_encoder/embed): mismo modelo y misma normalización que en
el entrenamiento. Si no se puede cargar, `encode()` devuelve None y los detectores que
dependen de embeddings quedan en modo fallback.
"""
from __future__ import annotations

import logging

import config

log = logging.getLogger("auditor")

_state = {"loaded": False, "error": None, "model": None}


def load() -> bool:
    config.enable_ml_imports()
    try:
        import mlutils as U  # noqa: PLC0415 — ml/, vía config.enable_ml_imports()

        U.get_encoder()  # carga el modelo (desde la caché local) una sola vez
        _state.update(loaded=True, error=None, model=U.EMB_MODEL)
    except Exception as e:  # noqa: BLE001 — cualquier fallo → fallback, nunca tumba el backend
        _state.update(loaded=False, error=f"{type(e).__name__}: {str(e)[:200]}", model=None)
        log.warning("embeddings NO disponibles (%s)", _state["error"])
    return _state["loaded"]


def is_loaded() -> bool:
    return _state["loaded"]


def model_name() -> str | None:
    return _state["model"]


def encode(texts: list[str]):
    """np.ndarray (n, 384) normalizado, o None si los embeddings no están disponibles."""
    if not _state["loaded"]:
        return None
    import mlutils as U  # noqa: PLC0415

    return U.embed(texts)


def status() -> dict:
    return {"loaded": _state["loaded"], "model": _state["model"], "error": _state["error"]}


def reset() -> None:
    _state.update(loaded=False, error=None, model=None)
