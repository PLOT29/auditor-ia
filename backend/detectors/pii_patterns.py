"""Regex de PII mexicana (fuente única). Las usan:
  - detectors/pii.py         → detección solo-regex (fallback y baseline de pii_eval)
  - detectors/presidio_es.py → recognizers propios de Presidio (CURP, RFC, teléfono MX)
"""
from __future__ import annotations

import re

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


def phone_ok(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if value.lstrip().startswith("+") or len(digits) > 10:
        if digits.startswith("52"):
            digits = digits[2:]
        if len(digits) == 11 and digits.startswith("1"):  # prefijo móvil antiguo
            digits = digits[1:]
    return len(digits) == 10


# (tipo, regex, validador) — CURP antes que RFC (el RFC es prefijo de la CURP).
RULES = [
    ("email", EMAIL_RE, None),
    ("curp", CURP_RE, None),
    ("rfc", RFC_RE, None),
    ("phone_mx", PHONE_RE, phone_ok),
]
