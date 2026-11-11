"""Clasificador de tema — STUB de Fase 1.

En Fase 4 se reemplaza por embeddings `paraphrase-multilingual-MiniLM-L12-v2`
+ LogisticRegression cargado desde `ml/models/topic.joblib`.
Mientras tanto devuelve una categoría por defecto con confianza 0.0 para que
el contrato de la API ya esté completo.
"""
from __future__ import annotations

CATEGORIES = (
    "programacion",
    "academico",
    "redaccion",
    "ciberseguridad",
    "consulta_general",
    "personal",
)
DEFAULT_CATEGORY = "consulta_general"


def classify(text: str) -> tuple[str, float]:
    """Devuelve (categoria, confianza)."""
    return DEFAULT_CATEGORY, 0.0
