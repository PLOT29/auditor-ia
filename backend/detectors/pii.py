"""Detector de PII (Fase 4B): regex propias + Presidio en español.

- `detect()`        → Presidio (detectors/presidio_es.py) con recognizers propios de CURP,
                      RFC y teléfono MX, email, NER de spaCy (persona y dirección), lista
                      negra y fusión de direcciones de la 4A.1. Si Presidio no cargó, cae a
                      `detect_regex()` y se avisa en el log.
- `detect_regex()`  → solo regex (email, curp, rfc, dni, nie, dirección heurística, phone_mx).
                      Fallback, baseline de ml/pii_eval.py y paridad con patterns.js.
Fase 5A.1: DNI, NIE y la dirección HEURÍSTICA corren siempre (Presidio no los conoce). Las
direcciones que solo encuentra Presidio (NER) se marcan con fuente `pii_ner`: cuentan como dato
personal con riesgo medio; el riesgo alto por dirección solo lo da la heurística (catalog.py).
En ambos casos se ignoran los fragmentos ya reclamados por secretos (`taken`).
"""
from __future__ import annotations

import logging

import config
from detectors import presidio_es
from detectors.base import Finding, overlaps_any
from detectors.catalog import NER_SOURCE
from detectors.pii_patterns import ALWAYS_RULES, CURP_RE, EMAIL_RE, PHONE_RE, RFC_RE, RULES  # noqa: F401 (re-export)
from detectors.pii_patterns import phone_ok as _phone_ok  # noqa: F401 (compatibilidad)

log = logging.getLogger("auditor")

_state = {"analyzer": None, "error": None, "config": presidio_es.PostConfig()}


def load() -> bool:
    _state.update(analyzer=None, error=None)
    try:
        if config.models_disabled():
            raise RuntimeError("modelos desactivados (AUDITOR_DISABLE_MODELS=1)")
        _state["analyzer"] = presidio_es.build_analyzer()
        presidio_es.detect(_state["analyzer"], "calentamiento", _state["config"])
    except Exception as e:  # noqa: BLE001
        _state["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        log.warning("Presidio NO disponible (%s) → PII solo con regex", _state["error"])
    return _state["analyzer"] is not None


def is_loaded() -> bool:
    return _state["analyzer"] is not None


def status() -> dict:
    return {"loaded": is_loaded(), "error": _state["error"]}


def reset() -> None:
    _state.update(analyzer=None, error=None)


def _run_rules(rules, text: str, taken: list[Finding]) -> list[Finding]:
    found: list[Finding] = []
    for type_, rule, validator, annotate in rules:
        spans = rule(text) if callable(rule) else (m.span() for m in rule.finditer(text))
        for start, end in spans:
            value = text[start:end]
            if validator and not validator(value):
                continue
            if overlaps_any(start, end, taken):
                continue
            f = Finding(type=type_, start=start, end=end, value=value, source="pii")
            if annotate:
                f.valido = annotate(value)["valido"]
            found.append(f)
            taken.append(f)
    return found


def detect_regex(text: str, taken: list[Finding] | None = None) -> list[Finding]:
    """PII solo con regex, ignorando fragmentos ya reclamados por secretos."""
    taken = list(taken or [])
    return sorted(_run_rules(RULES, text, taken), key=lambda f: f.start)


def detect(text: str, taken: list[Finding] | None = None) -> list[Finding]:
    """PII con Presidio (o regex si Presidio no está disponible).

    Con Presidio: primero DNI/NIE/dirección heurística (deterministas, iguales a la extensión) y
    luego Presidio sobre lo que quede libre.
    """
    analyzer = _state["analyzer"]
    if analyzer is None:
        return detect_regex(text, taken)
    taken = list(taken or [])
    found = _run_rules(ALWAYS_RULES, text, taken)
    for e in presidio_es.detect(analyzer, text, _state["config"]):
        if overlaps_any(e["start"], e["end"], taken):
            continue
        source = NER_SOURCE if e["type"] == "address" else "pii"
        f = Finding(type=e["type"], start=e["start"], end=e["end"], value=text[e["start"]:e["end"]], source=source)
        found.append(f)
        taken.append(f)
    return sorted(found, key=lambda f: f.start)
