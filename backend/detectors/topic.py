"""Clasificador de tema (Fase 4B): embeddings MiniLM + LogisticRegression.

Carga `topic_clf.joblib` (ruta: config.topic_model_path()) una sola vez. Recibe los
embeddings ya calculados del prompt (no los vuelve a calcular). Sin modelo o sin
embeddings devuelve ("sin_clasificar", 0.0).
"""
from __future__ import annotations

import logging

import config

log = logging.getLogger("auditor")

CATEGORIES = (
    "programacion",
    "academico",
    "redaccion",
    "ciberseguridad",
    "consulta_general",
    "personal",
)
FALLBACK = config.TOPIC_FALLBACK
DEFAULT_CATEGORY = FALLBACK  # compatibilidad

_state = {"model": None, "error": None, "path": None}


def load() -> bool:
    import joblib  # noqa: PLC0415

    path = config.topic_model_path()
    _state.update(model=None, error=None, path=str(path))
    try:
        if not path.is_file():
            raise FileNotFoundError(f"no existe {path}")
        model = joblib.load(path)
        if model.get("kind") != "embeddings+logreg":
            raise ValueError(f"tipo de modelo de tema no soportado: {model.get('kind')}")
        unknown = set(model["labels"]) - set(CATEGORIES)
        if unknown:
            raise ValueError(f"etiquetas desconocidas en el modelo: {unknown}")
        _state["model"] = model
    except Exception as e:  # noqa: BLE001
        _state["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        log.warning("modelo de TEMA no disponible (%s) → category=%s", _state["error"], FALLBACK)
    return _state["model"] is not None


def is_loaded() -> bool:
    return _state["model"] is not None


def embedding_model() -> str | None:
    return _state["model"]["embedding_model"] if _state["model"] else None


def classify(text: str, X_emb=None) -> tuple[str, float]:
    """(categoria, confianza). X_emb = embedding del prompt, shape (1, d)."""
    m = _state["model"]
    if m is None or X_emb is None:
        return FALLBACK, 0.0
    proba = m["clf"].predict_proba(X_emb)[0]
    i = int(proba.argmax())
    return str(m["clf"].classes_[i]), round(float(proba[i]), 4)


def status() -> dict:
    return {"loaded": is_loaded(), "path": _state["path"], "error": _state["error"]}


def reset() -> None:
    _state.update(model=None, error=None, path=None)
