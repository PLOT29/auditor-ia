"""Configuración del backend (Fase 4B). Todo se puede cambiar por variable de entorno.

    AUDITOR_MODELS_DIR        carpeta de los .joblib            (default: <repo>/ml/models)
    AUDITOR_METRICS_PATH      metrics.json con el umbral final   (default: <repo>/ml/reports/metrics.json)
    AUDITOR_TOPIC_MODEL       archivo del clasificador de tema   (default: topic_clf.joblib)
    AUDITOR_INJECTION_MODEL   archivo del detector de inyección  (default: injection_final.joblib)
    AUDITOR_INJECTION_MEDIUM  umbral de riesgo "medio" por inyección (default: 0.50)
    AUDITOR_DISABLE_MODELS    "1" → no cargar ML (tema/inyección/Presidio); solo regex
    AUDITOR_HF_ONLINE         "1" → permitir que sentence-transformers consulte Hugging Face.
    AUDITOR_EXTENSION_ID      id de la extensión permitida (default: el que resulta de la "key" de
                              extension/manifest.json, ver extension_id())
                              Por defecto el backend NO sale a internet (RNF-03): usa la caché local.

Las funciones leen el entorno cada vez (no al importar), para que los tests puedan cambiarlo.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from functools import lru_cache
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


def _registry_file(tarea: str, default: str) -> str:
    """Fase 5D: archivo de la versión ACTIVA según ml/reports/model_registry.json (si existe ese archivo)."""
    import registry  # noqa: PLC0415

    name = registry.active_file(tarea)
    return name if (models_dir() / name).is_file() else default


def topic_model_path() -> Path:
    return models_dir() / (os.environ.get("AUDITOR_TOPIC_MODEL", "").strip() or _registry_file("tema", "topic_clf.joblib"))


def injection_model_path() -> Path:
    return models_dir() / (os.environ.get("AUDITOR_INJECTION_MODEL", "").strip()
                           or _registry_file("inyeccion", "injection_final.joblib"))


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


MANIFEST = REPO / "extension" / "manifest.json"


@lru_cache(maxsize=4)
def _manifest_extension_id(path: str, mtime: float) -> str | None:
    """Id de Chrome que resulta de la "key" pública del manifest: los 32 primeros dígitos hex del
    SHA-256 de la clave (DER), con 0-f → a-p. Es estable aunque se recargue o se mueva la carpeta."""
    try:
        key = json.loads(Path(path).read_text(encoding="utf-8"))["key"]
        digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    except (OSError, ValueError, KeyError):
        return None
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def extension_id() -> str | None:
    """Id de la extensión Centinela que el backend acepta como Origin (fuente única: el manifest)."""
    env = os.environ.get("AUDITOR_EXTENSION_ID", "").strip()
    if env:
        return env
    try:
        mtime = MANIFEST.stat().st_mtime
    except OSError:
        return None
    return _manifest_extension_id(str(MANIFEST), mtime)
