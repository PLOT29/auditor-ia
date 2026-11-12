"""Clasificador de tema (Fase 4B/4C): embeddings MiniLM + LogisticRegression.

Carga `topic_clf.joblib` (ruta: config.topic_model_path(); v2 desde la Fase 4C) una sola vez.
Recibe los embeddings ya calculados del prompt (no los vuelve a calcular). Sin modelo o sin
embeddings devuelve ("sin_clasificar", 0.0).

Incertidumbre (Fase 4C): umbral de confianza de metrics.json → topic_v2.umbral_confianza.umbral
(elegido con predicciones out-of-fold, nunca con el probe set). `is_uncertain(conf)` devuelve
True si conf < umbral; None si no hay modelo o umbral.
"""
from __future__ import annotations

import json
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

_state = {"model": None, "error": None, "path": None, "threshold": None, "threshold_source": None}


def read_confidence_threshold(path=None) -> float:
    """Umbral de confianza de metrics.json (KeyError/OSError si no está)."""
    path = path or config.metrics_path()
    data = json.loads(open(path, encoding="utf-8").read())
    return float(data["topic_v2"]["umbral_confianza"]["umbral"])


def _load_threshold(model: dict) -> None:
    try:
        thr, src = read_confidence_threshold(), str(config.metrics_path())
    except (OSError, KeyError, ValueError, TypeError) as e:
        thr = model.get("confidence_threshold")
        src = "modelo (.joblib)" if thr is not None else None
        log.warning("sin umbral de confianza de tema en metrics.json (%r); se usa: %s", e, src or "ninguno")
    _state.update(threshold=thr, threshold_source=src)


def load() -> bool:
    import joblib  # noqa: PLC0415

    path = config.topic_model_path()
    _state.update(model=None, error=None, path=str(path), threshold=None, threshold_source=None)
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
        _load_threshold(model)
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


def confidence_threshold() -> float | None:
    return _state["threshold"]


def is_uncertain(confidence: float) -> bool | None:
    """True si la confianza está por debajo del umbral; None sin modelo o sin umbral."""
    if _state["model"] is None or _state["threshold"] is None:
        return None
    return bool(confidence < _state["threshold"])


def status() -> dict:
    m = _state["model"]
    return {"loaded": is_loaded(), "path": _state["path"], "version": m.get("version", "v1") if m else None,
            "confidence_threshold": _state["threshold"], "threshold_source": _state["threshold_source"],
            "error": _state["error"]}


def reset() -> None:
    _state.update(model=None, error=None, path=None, threshold=None, threshold_source=None)
