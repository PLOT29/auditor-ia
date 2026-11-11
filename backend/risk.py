"""Reglas de agregación de riesgo (SPEC §5.4)."""
from __future__ import annotations

HIGH_INJECTION = 0.80
MEDIUM_INJECTION = 0.50


def compute_risk(findings: list[dict], injection_score: float) -> str:
    """findings: lista pública [{"type", "masked", "source"}]."""
    sources = {f.get("source") for f in findings}
    has_secret = bool(sources & {"secret", "client"})
    has_pii = "pii" in sources

    if has_secret or injection_score >= HIGH_INJECTION:
        return "alto"
    if has_pii or injection_score >= MEDIUM_INJECTION:
        return "medio"
    return "bajo"
