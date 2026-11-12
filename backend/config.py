"""Configuración del backend (Fase 4B). Todo se puede cambiar por variable de entorno.

    AUDITOR_MODELS_DIR        carpeta de los .joblib            (default: <repo>/ml/models)
    AUDITOR_METRICS_PATH      metrics.json con el umbral final   (default: <repo>/ml/reports/metrics.json)
    AUDITOR_TOPIC_MODEL       archivo del clasificador de tema   (default: topic_clf.joblib)
    AUDITOR_INJECTION_MODEL   archivo del detector de inyección  (default: injection_final.joblib)
    AUDITOR_INJECTION_MEDIUM  umbral de riesgo "medio" por inyección (default: 0.50)
    AUDITOR_DISABLE_MODELS    "1" → no cargar ML (tema/inyección/Presidio); solo regex
    AUDITOR_HF_ONLINE         "1" → permitir que sentence-transformers consulte Hugging Face.
                              Por defecto el backend NO sale a internet (RNF-03): usa la caché local.

Las funciones leen el entorno cada vez (no al importar), para que los tests puedan cambiarlo.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ML_DIR = REPO / "ml"

TOPIC_FALLBACK = "sin_clasificar"


def _env_path(name: str, default: Path) -> Path:
    v = os.environ.get(name, "").strip()
    return Path(v) if v else default


def models_dir() -> Path:
    return _env_path("AUDITOR_MODELS_DIR", ML_DIR / "models")


def metrics_path() -> Path:
    return _env_path("AUDITOR_METRICS_PATH", ML_DIR / "reports" / "metrics.json")


def topic_model_path() -> Path:
    return models_dir() / (os.environ.get("AUDITOR_TOPIC_MODEL", "").strip() or "topic_clf.joblib")


def injection_model_path() -> Path:
    return models_dir() / (os.environ.get("AUDITOR_INJECTION_MODEL", "").strip() or "injection_final.joblib")


def injection_medium() -> float:
    return float(os.environ.get("AUDITOR_INJECTION_MEDIUM", "").strip() or 0.50)


def models_disabled() -> bool:
    return os.environ.get("AUDITOR_DISABLE_MODELS", "").strip() == "1"


def enable_ml_imports() -> None:
    """Hace importables los módulos de ml/ (mlutils, injection_model) SIN duplicar su lógica,
    y deja Hugging Face en modo offline salvo que se pida lo contrario."""
    if str(ML_DIR) not in sys.path:
        sys.path.insert(0, str(ML_DIR))
    if os.environ.get("AUDITOR_HF_ONLINE", "").strip() != "1":
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
