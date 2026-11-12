"""Reglas de agregación de riesgo (SPEC §5.4).

- Alto:  cualquier secreto (incluidos los del cliente) o injection_score ≥ umbral ALTO.
- Medio: cualquier PII o injection_score ≥ umbral MEDIO.
- Bajo:  lo demás. injection_score = None (detector no disponible) no suma riesgo.

Umbrales (Fase 4B):
- ALTO  = umbral final del detector de inyección, leído de metrics.json
          (injection_4A1.final.umbral) por detectors/injection.py. Sin detector → 0.80.
- MEDIO = config.injection_medium() (AUDITOR_INJECTION_MEDIUM, default 0.50); nunca mayor que ALTO.
"""
from __future__ import annotations

import config

DEFAULT_HIGH = 0.80
RANK = {"bajo": 0, "medio": 1, "alto": 2}


def thresholds() -> tuple[float, float]:
    """(alto, medio) vigentes."""
    from detectors import injection  # noqa: PLC0415

    high = injection.threshold()
    high = DEFAULT_HIGH if high is None else high
    return high, min(config.injection_medium(), high)


def compute_risk(findings: list[dict], injection_score: float | None,
                 high: float | None = None, medium: float | None = None) -> str:
    """findings: lista pública [{"type", "masked", "source"}]."""
    if high is None or medium is None:
        h, m = thresholds()
        high = h if high is None else high
        medium = m if medium is None else medium
    sources = {f.get("source") for f in findings}
    has_secret = bool(sources & {"secret", "client"})
    has_pii = bool(sources & {"pii", "client_pii"})  # client_pii: PII/término propio visto solo por la extensión
    inj = injection_score if injection_score is not None else -1.0

    if has_secret or inj >= high:
        return "alto"
    if has_pii or inj >= medium:
        return "medio"
    return "bajo"


def max_risk(a: str, b: str) -> str:
    """El mayor de dos niveles (para que una reclasificación nunca baje el riesgo)."""
    return a if RANK.get(a, 0) >= RANK.get(b, 0) else b
