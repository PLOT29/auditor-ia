"""Detector de PII — versión básica de Fase 1 (solo regex).

Tipos: email, phone_mx, curp, rfc.
En Fase 4 se agrega Presidio + spaCy (es_core_news_md) para nombre de persona
y dirección; estos recognizers regex se conservarán como recognizers propios.
"""
from __future__ import annotations

import re

from detectors.base import Finding, overlaps_any

_STATES = (
    "AS|BC|BS|CC|CL|CM|CS|CH|DF|DG|GT|GR|HG|JC|MC|MN|MS|NT|NL|OC|PL|QT|QR|SP|SL|SR|TC|TS|TL|VZ|YN|ZS|NE"
)
_DATE = r"\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"

EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
CURP_RE = re.compile(
    rf"\b[A-Z][AEIOUX][A-Z]{{2}}{_DATE}[HM](?:{_STATES})[B-DF-HJ-NP-TV-Z]{{3}}[A-Z\d]\d\b",
    re.IGNORECASE,
)
RFC_RE = re.compile(rf"\b[A-ZÑ&]{{3,4}}{_DATE}[A-Z\d]{{2}}[A\d]\b", re.IGNORECASE)
# Candidato de teléfono; se valida después que tenga 10 dígitos nacionales.
PHONE_RE = re.compile(
    r"(?<![\w+])(?:\+?52[\s.-]?)?(?:1[\s.-]?)?(?:\(\d{2,3}\)|\d{2,3})[\s.-]?\d{3,4}[\s.-]?\d{4}(?!\d)"
)


def _phone_ok(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if value.lstrip().startswith("+") or len(digits) > 10:
        if digits.startswith("52"):
            digits = digits[2:]
        if len(digits) == 11 and digits.startswith("1"):  # prefijo móvil antiguo
            digits = digits[1:]
    return len(digits) == 10


# (tipo, regex, validador) — CURP antes que RFC (el RFC es prefijo de la CURP).
_RULES = [
    ("email", EMAIL_RE, None),
    ("curp", CURP_RE, None),
    ("rfc", RFC_RE, None),
    ("phone_mx", PHONE_RE, _phone_ok),
]


def detect(text: str, taken: list[Finding] | None = None) -> list[Finding]:
    """Devuelve PII encontrada, ignorando fragmentos ya reclamados por secretos."""
    taken = list(taken or [])
    found: list[Finding] = []
    for type_, regex, validator in _RULES:
        for m in regex.finditer(text):
            start, end = m.span()
            value = m.group()
            if validator and not validator(value):
                continue
            if overlaps_any(start, end, taken):
                continue
            f = Finding(type=type_, start=start, end=end, value=value, source="pii")
            found.append(f)
            taken.append(f)
    return sorted(found, key=lambda f: f.start)
