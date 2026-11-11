"""Detector de secretos por regex (Capa 2, backend).

Debe mantenerse sincronizado con `extension/patterns.js` (Fase 3): mismos
tipos y mismas expresiones.

El orden de PATTERNS importa: los patrones más específicos van primero
(p. ej. `sk-ant-` antes que `sk-`), y un fragmento ya reclamado por un patrón
no puede ser reclamado por otro.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from detectors.base import Finding, overlaps_any


@dataclass(frozen=True)
class SecretPattern:
    type: str
    regex: re.Pattern
    group: int = 0  # grupo que contiene el valor sensible (0 = todo el match)
    validator: object = None  # callable(str) -> bool, opcional


def luhn_ok(number: str) -> bool:
    """Valida un número de tarjeta con el algoritmo de Luhn."""
    digits = [int(c) for c in number if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _card_ok(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    # Descarta secuencias triviales (0000..., 1111...) que pasan Luhn.
    if len(set(digits)) == 1:
        return False
    return luhn_ok(digits)


# re.ASCII: `\b`, `\d`, `\s`, `\w` con semántica ASCII, igual que en JavaScript
# (patterns.js). Sin esto, p. ej. "ñsk-..." o "contraseña:\u00a0x" darían
# resultados distintos en la extensión y en el backend.
_A = re.ASCII

PATTERNS: list[SecretPattern] = [
    SecretPattern(
        "private_key",
        re.compile(
            r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"
            r"(?:[\s\S]*?-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----)?",
            _A,
        ),
    ),
    SecretPattern("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}", _A)),
    SecretPattern(
        "openai_key",
        re.compile(r"\bsk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}", _A),
    ),
    SecretPattern("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", _A)),
    SecretPattern(
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b", _A),
    ),
    SecretPattern("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}(?![0-9A-Za-z_\-])", _A)),
    SecretPattern(
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{5,}\.eyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}", _A),
    ),
    SecretPattern(
        "password",
        # [ñÑnN] explícito: con re.ASCII, IGNORECASE no relaciona ñ/Ñ.
        re.compile(
            r"\b(?:password|passwd|pwd|contrase[ñÑnN]a)\b\s*[:=]\s*[\"']?([^\s\"',;]{4,})",
            _A | re.IGNORECASE,
        ),
        group=1,
    ),
    SecretPattern(
        "credit_card",
        # (?<!\d[.,]) — no es la parte fraccionaria de un decimal (0.4111..., 3,1415...).
        re.compile(r"(?<![\d-])(?<!\d[.,])\d(?:[ -]?\d){12,18}(?![\d-])", _A),
        validator=_card_ok,
    ),
]


def detect(text: str, taken: list[Finding] | None = None) -> list[Finding]:
    """Devuelve los secretos encontrados (sin máscara; ver masking.py)."""
    taken = list(taken or [])
    found: list[Finding] = []
    for pat in PATTERNS:
        for m in pat.regex.finditer(text):
            start, end = m.span(pat.group)
            value = m.group(pat.group)
            if pat.validator and not pat.validator(value):
                continue
            if overlaps_any(start, end, taken):
                continue
            f = Finding(type=pat.type, start=start, end=end, value=value, source="secret")
            found.append(f)
            taken.append(f)
    return sorted(found, key=lambda f: f.start)
