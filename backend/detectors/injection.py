"""Detector de inyección / jailbreak — STUB de Fase 1.

En Fase 4 se reemplaza por embeddings + clasificador binario
(`ml/models/injection.joblib`). Por ahora devuelve 0.0.
"""
from __future__ import annotations


def score(text: str) -> float:
    """Probabilidad (0–1) de que el prompt sea inyección/jailbreak."""
    return 0.0
