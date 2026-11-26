"""Registro de versiones de modelos (Fase 5D): ml/reports/model_registry.json.

    {"activo": {"tema": "v2", "inyeccion": "v1"},
     "versiones": [{"version": "v3", "tarea": "tema", "archivo": "topic_clf_v3.joblib", "estado": "adoptado",
                    "fecha": ..., "etiquetas": {"manual": 18, "propagado": 7}, "max_label_id": 42,
                    "metricas": {...antes/después...}, "anterior": "v2", "motivo": ...}, ...]}

Estados: candidato (pasó la evaluación, espera que una persona lo adopte) · adoptado · rechazado (no pasó;
`motivo`) · descartado (candidato que la persona no adoptó) · retirado (se volvió a la versión anterior).
Sin dependencias de ML: lo usan config.py (qué archivo cargar), el backend y ml/retrain_from_feedback.py.
AUDITOR_REGISTRY_PATH cambia la ruta (tests).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_PATH = REPO / "ml" / "reports" / "model_registry.json"
TASKS = ("tema", "inyeccion")
BASE = {
    "tema": {"version": "v2", "tarea": "tema", "archivo": "topic_clf.joblib", "estado": "adoptado",
             "fecha": "2026-11-12T03:45:39+00:00", "etiquetas": {"manual": 0, "propagado": 0}, "max_label_id": 0,
             "metricas": {"probe_f1_macro": 0.8313}, "anterior": None,
             "motivo": "base: sintéticos v2 (Fase 4C)"},
    "inyeccion": {"version": "v1", "tarea": "inyeccion", "archivo": "injection_final.joblib", "estado": "adoptado",
                  "fecha": "2026-11-11T21:12:32+00:00", "etiquetas": {"manual": 0, "propagado": 0},
                  "max_label_id": 0, "metricas": {}, "anterior": None,
                  "motivo": "base: ensamble TF-IDF + MiniLM (Fase 4A.1)"},
}
_lock = threading.RLock()


def path() -> Path:
    env = os.environ.get("AUDITOR_REGISTRY_PATH", "").strip()
    return Path(env) if env else DEFAULT_PATH


def load() -> dict:
    with _lock:
        try:
            data = json.loads(path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("activo", {t: BASE[t]["version"] for t in TASKS})
        data.setdefault("versiones", [dict(BASE[t]) for t in TASKS])
        return data


def save(data: dict) -> None:
    with _lock:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)


def find(data: dict, tarea: str, version: str) -> dict | None:
    return next((v for v in data["versiones"] if v["tarea"] == tarea and v["version"] == version), None)


def active(tarea: str, data: dict | None = None) -> dict:
    data = data or load()
    return find(data, tarea, data["activo"][tarea]) or dict(BASE[tarea])


def active_file(tarea: str) -> str:
    return active(tarea)["archivo"]


def next_version(tarea: str, data: dict | None = None) -> str:
    data = data or load()
    nums = [int(v["version"][1:]) for v in data["versiones"] if v["tarea"] == tarea and v["version"][1:].isdigit()]
    return f"v{max(nums, default=1) + 1}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def max_label_ids(data: dict | None = None) -> dict[str, int]:
    """Etiquetas ya usadas por la versión ACTIVA de cada tarea (las de id mayor están pendientes)."""
    data = data or load()
    return {t: int(active(t, data).get("max_label_id") or 0) for t in TASKS}
