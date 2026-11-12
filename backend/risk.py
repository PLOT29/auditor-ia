"""Reglas de agregación de riesgo (SPEC §5.4).

- Alto:  cualquier hallazgo de riesgo alto o injection_score ≥ umbral ALTO.
- Medio: cualquier otro hallazgo o injection_score ≥ umbral MEDIO.
- Bajo:  lo demás. injection_score = None (detector no disponible) no suma riesgo.

Riesgo de cada hallazgo (Fase 5A.1, detectors/catalog.py): el de su tipo en el catálogo, que
coincide con la política por defecto de la extensión → los tipos que BLOQUEAN (secretos, CURP,
RFC, DNI, NIE y dirección heurística) son "alto"; los que ADVIERTEN (correo, teléfono, nombre,
término propio, org- de OpenAI, pk_ de Stripe) son "medio". Excepción: una dirección que solo vio
Presidio (fuente `pii_ner`) es "medio". Tipos desconocidos: por su fuente (secret/client → alto).

Umbrales (Fase 4B):
- ALTO  = umbral final del detector de inyección, leído de metrics.json
          (injection_4A1.final.umbral) por detectors/injection.py. Sin detector → 0.80.
- MEDIO = config.injection_medium() (AUDITOR_INJECTION_MEDIUM, default 0.50); nunca mayor que ALTO.
"""
from __future__ import annotations

import config
from detectors import catalog

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
    levels = {catalog.finding_risk(f.get("type", ""), f.get("source")) for f in findings}
    inj = injection_score if injection_score is not None else -1.0

    if "alto" in levels or inj >= high:
        return "alto"
    if levels or inj >= medium:
        return "medio"
    return "bajo"


def max_risk(a: str, b: str) -> str:
    """El mayor de dos niveles (para que una reclasificación nunca baje el riesgo)."""
    return a if RANK.get(a, 0) >= RANK.get(b, 0) else b
