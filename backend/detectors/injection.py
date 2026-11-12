"""Detector de inyección / jailbreak (Fase 4B).

- Modelo: `injection_final.joblib` (config.injection_model_path()), el elegido en la 4A.1.
- Umbral: metrics.json → injection_4A1.final.umbral (config.metrics_path()). Nunca hardcodeado.
- Inferencia: ml/injection_model.py (`predict_proba`), la MISMA función que se evaluó;
  soporta tfidf, embeddings, ventanas y ensamble. Se le pasan los embeddings ya calculados.
- Sin modelo: `score()` devuelve None (injection_score = null) y se avisa en el log.
"""
from __future__ import annotations

import json
import logging

import config

log = logging.getLogger("auditor")

_state = {"model": None, "threshold": None, "threshold_source": None, "error": None, "path": None}


def read_threshold(path=None) -> float:
    """Umbral del bloque final de metrics.json (KeyError/OSError si no está)."""
    path = path or config.metrics_path()
    data = json.loads(open(path, encoding="utf-8").read())
    return float(data["injection_4A1"]["final"]["umbral"])


def load() -> bool:
    import joblib  # noqa: PLC0415

    config.enable_ml_imports()
    path = config.injection_model_path()
    _state.update(model=None, threshold=None, threshold_source=None, error=None, path=str(path))
    try:
        if not path.is_file():
            raise FileNotFoundError(f"no existe {path}")
        model = joblib.load(path)
        import injection_model  # noqa: F401,PLC0415 — valida que la inferencia de ml/ sea importable
        try:
            thr, src = read_threshold(), str(config.metrics_path())
        except (OSError, KeyError, ValueError, TypeError) as e:
            raise RuntimeError(f"no se pudo leer el umbral final de {config.metrics_path()}: {e!r}") from e
        if abs(thr - float(model.get("threshold", thr))) > 1e-3:
            log.warning("el umbral de metrics.json (%.4f) difiere del guardado en el modelo (%.4f); se usa metrics.json",
                        thr, model["threshold"])
        _state.update(model=model, threshold=thr, threshold_source=src)
    except Exception as e:  # noqa: BLE001
        _state["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        log.warning("detector de INYECCIÓN no disponible (%s) → injection_score=null", _state["error"])
    return _state["model"] is not None


def is_loaded() -> bool:
    return _state["model"] is not None


def threshold() -> float | None:
    return _state["threshold"]


def needs_embeddings() -> bool:
    m = _state["model"]
    if m is None:
        return False
    kinds = [m["kind"]] + [x["kind"] for x in m.get("members", [])]
    return any(k.startswith("embeddings") for k in kinds)


def score(text: str, X_emb=None) -> float | None:
    """Probabilidad 0–1 de inyección/jailbreak, o None si el detector no está disponible."""
    m = _state["model"]
    if m is None:
        return None
    if needs_embeddings() and X_emb is None:
        return None
    import injection_model as IM  # noqa: PLC0415

    return round(float(IM.predict_proba(m, [text], X_emb=X_emb)[0]), 4)


def status() -> dict:
    m = _state["model"]
    return {"loaded": is_loaded(), "path": _state["path"], "model": m.get("name") if m else None,
            "threshold": _state["threshold"], "threshold_source": _state["threshold_source"],
            "error": _state["error"]}


def reset() -> None:
    _state.update(model=None, threshold=None, threshold_source=None, error=None, path=None)
