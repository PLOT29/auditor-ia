"""Regex de PII mexicana (fuente única). Las usan:
  - detectors/pii.py         → detección solo-regex (fallback y baseline de pii_eval)
  - detectors/presidio_es.py → recognizers propios de Presidio (CURP, RFC, teléfono MX)
  - extension/patterns.js    → MISMAS expresiones (paridad verificada en test_patterns_parity.py)

Semántica ASCII explícita (Fase 5A): `re.ASCII` + clases escritas a mano (`Ñ`/`ñ` y vocales
acentuadas incluidas literalmente), para que Python y JavaScript den exactamente lo mismo.
"""
from __future__ import annotations

import re

_STATES = (
    "AS|BC|BS|CC|CL|CM|CS|CH|DF|DG|GT|GR|HG|JC|MC|MN|MS|NT|NL|OC|PL|QT|QR|SP|SL|SR|TC|TS|TL|VZ|YN|ZS|NE"
)
_DATE = r"[0-9]{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01])"
_WORD = "A-Za-z0-9_"
_LETTER_ES = "ÁÉÍÓÚÜÑáéíóúüñ"
_SP = r"[ \t\n\r\f\v.\-]"  # separadores de teléfono (espacios ASCII, punto, guion)
_A = re.ASCII
_AI = re.ASCII | re.IGNORECASE

EMAIL_RE = re.compile(
    rf"(?<![{_WORD}.+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{{2,}}(?![{_WORD}])", _A)
CURP_RE = re.compile(
    rf"(?<![{_WORD}])[A-Z][AEIOUX][A-Z]{{2}}{_DATE}[HM](?:{_STATES})[B-DF-HJ-NP-TV-Z]{{3}}[A-Z0-9][0-9](?![{_WORD}])",
    _AI)
RFC_RE = re.compile(
    rf"(?<![{_WORD}{_LETTER_ES}&])[A-ZÑñ&]{{3,4}}{_DATE}[A-Z0-9]{{2}}[A0-9](?![{_WORD}])", _AI)
# Candidato de teléfono; se valida después que tenga 10 dígitos nacionales.
PHONE_RE = re.compile(
    rf"(?<![{_WORD}+])(?:\+?52{_SP}?)?(?:1{_SP}?)?(?:\([0-9]{{2,3}}\)|[0-9]{{2,3}}){_SP}?[0-9]{{3,4}}{_SP}?[0-9]{{4}}(?![0-9])",
    _A)


def phone_ok(value: str) -> bool:
    digits = re.sub(r"[^0-9]", "", value)
    if value.startswith("+") or len(digits) > 10:
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
