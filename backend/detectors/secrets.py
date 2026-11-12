"""Detector de secretos por regex (Capa 2, backend).

Debe mantenerse sincronizado con `extension/patterns.js`: mismos tipos, mismo orden y mismas
expresiones (paridad en tests/test_patterns_parity.py). Fase 5A.1: Stripe, AWS tolerante y
secret key por contexto, Bearer, URIs con credenciales, asignaciones .env/YAML/JSON y los
identificadores que solo advierten (org- de OpenAI, pk_ de Stripe).

El orden de PATTERNS importa: los patrones más específicos van primero
(p. ej. `sk-ant-` antes que `sk-`, la key de OpenAI antes que el token Bearer que la contiene),
y un fragmento ya reclamado por un patrón no puede ser reclamado por otro.
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


# Valores de relleno que NO son secretos (igual que isPlaceholder en patterns.js).
PLACEHOLDER_WORDS = {
    "changeme", "change_me", "change-me", "changeit", "example", "ejemplo", "placeholder", "redacted",
    "password", "contraseña", "secret", "secreto", "null", "none", "undefined", "dummy",
}
_PH_WRAPPED = re.compile(r"^[<\[{(].*[>\]})]$", re.ASCII | re.DOTALL)
_PH_VAR = re.compile(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$", re.ASCII)
_PH_PYFMT = re.compile(r"^%\(.*\)s$", re.ASCII | re.DOTALL)
_PH_FILL = re.compile(r"^[xX*.#_-]+$", re.ASCII)
_PH_YOUR = re.compile(r"^(?:your|tu)[_-]", re.ASCII)
_PH_REPEAT = re.compile(r"^(.)\1+$", re.ASCII | re.DOTALL)


def is_placeholder(value: str) -> bool:
    v = value.lower()
    return bool(
        v in PLACEHOLDER_WORDS
        or _PH_WRAPPED.match(value) or _PH_VAR.match(value) or _PH_PYFMT.match(value)
        or _PH_FILL.match(value)
        or "xxxx" in v or "****" in v
        or "..." in v or "…" in v  # sk_live_..., abc…
        or _PH_YOUR.match(v)
        or _PH_REPEAT.match(value)
    )


def _not_placeholder(value: str) -> bool:
    return not is_placeholder(value)


_ONLY_LETTERS = re.compile(r"^[A-Za-z_]+$", re.ASCII)
_ONLY_DIGITS = re.compile(r"^[0-9]+$", re.ASCII)
_DOTTED = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+$", re.ASCII)
_SNAKE = re.compile(r"^[a-z]+(?:_[a-z]+)+$", re.ASCII)
_CAMEL = re.compile(r"^[a-z]+(?:[A-Z][a-z]+)+$", re.ASCII)


def _password_ok(value: str) -> bool:
    """Además del relleno, descarta código: getpass(), user_password_hash, newPassword, self.password."""
    return not (is_placeholder(value) or "(" in value or _DOTTED.match(value) or _SNAKE.match(value)
                or _CAMEL.match(value))


def _assignment_ok(value: str) -> bool:
    """Asignación genérica: descarta relleno, variables sin dígitos, números y expresiones (process.env.X)."""
    return not (is_placeholder(value) or _ONLY_LETTERS.match(value) or _ONLY_DIGITS.match(value)
                or _DOTTED.match(value))


# re.ASCII: `\b`, `\d`, `\s`, `\w` con semántica ASCII, igual que en JavaScript
# (patterns.js). Sin esto, p. ej. "ñsk-..." o "contraseña:\u00a0x" darían
# resultados distintos en la extensión y en el backend.
_A = re.ASCII
_AI = re.ASCII | re.IGNORECASE

_VAL = r"[^ \t\n\r\f\v\"'`,;<>(){}\[\]]"  # carácter de un valor de asignación
_SECRET_WORD = (r"(?:SECRET|TOKEN|PASSWORD|PASSWD|PWD|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|"
                r"CLIENT[_-]?SECRET|CONNECTION[_-]?STRING)")
_URI_SCHEMES = r"(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|rediss?|amqps?|mssql|ftps?|smtps?|https?)"

PATTERNS: list[SecretPattern] = [
    SecretPattern(
        "private_key",
        # Bloque completo hasta su END (multilínea, con sangría o dentro de ```). Sin END:
        # el encabezado y las líneas base64 que lo siguen.
        re.compile(
            r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"
            r"(?:[\s\S]*?-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----"
            r"|(?:[ \t]*\r?\n[ \t]*[A-Za-z0-9+/=]{8,}[ \t]*(?![^\r\n]))*)",
            _A,
        ),
    ),
    SecretPattern("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}", _A)),
    SecretPattern(
        "openai_key",
        re.compile(r"\bsk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}", _A),
    ),
    SecretPattern("stripe_key", re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{10,}", _A)),
    SecretPattern("stripe_webhook_secret", re.compile(r"\bwhsec_[A-Za-z0-9+/=]{16,}", _A)),
    SecretPattern("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16,}\b", _A)),
    SecretPattern(
        "aws_secret_key",
        # Por contexto: AWS_SECRET_ACCESS_KEY / aws_secret_access_key / SecretAccessKey, con =, : o comillas.
        re.compile(
            r"(?:aws_?secret_?access_?key|aws_?secret_?key|secret_?access_?key)[\"']?[ \t]*(?::=|=>|[:=])"
            r"[ \t]*[\"']?([A-Za-z0-9/+=]{16,})",
            _AI,
        ),
        group=1,
        validator=_not_placeholder,
    ),
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
        "uri_credentials",
        # esquema://usuario:contraseña@host — la contraseña llega hasta la ÚLTIMA @ antes del host.
        re.compile(rf"\b{_URI_SCHEMES}://[^ \t\n\r\f\v:/@\"'<>`]*:([^ \t\n\r\f\v\"'<>`/]+)@", _A),
        group=1,
        validator=_not_placeholder,
    ),
    SecretPattern(
        "bearer_token",
        re.compile(r"\b(?:[Bb]earer|BEARER)[ \t]+([A-Za-z0-9\-._~+/]{20,}=*)", _A),
        group=1,
        validator=_not_placeholder,
    ),
    SecretPattern(
        "password",
        # [ñÑnN] explícito: con re.ASCII, IGNORECASE no relaciona ñ/Ñ.
        re.compile(
            r"\b(?:password|passwd|pwd|contrase[ñÑnN]a)\b\s*[:=]\s*[\"']?([^\s\"',;]{4,})",
            _AI,
        ),
        group=1,
        validator=_password_ok,
    ),
    SecretPattern(
        "secret_assignment",
        # NOMBRE_CON_SECRET=valor · "client_secret": "valor" · dbPassword: valor (8+ caracteres).
        re.compile(
            rf"(?<![A-Za-z0-9_.-])[\"']?[A-Za-z0-9_.-]*?{_SECRET_WORD}(?:[_.-][A-Za-z0-9]+)*[\"']?[ \t]*"
            rf"(?::=|=>|[:=])[ \t]*[\"']?({_VAL}{{8,}})(?!{_VAL}|[(\[])",
            _AI,
        ),
        group=1,
        validator=_assignment_ok,
    ),
    SecretPattern(
        "credit_card",
        # (?<!\d[.,]) — no es la parte fraccionaria de un decimal (0.4111..., 3,1415...).
        re.compile(r"(?<![\d-])(?<!\d[.,])\d(?:[ -]?\d){12,18}(?![\d-])", _A),
        validator=_card_ok,
    ),
    # Identificadores que solo ADVIERTEN (riesgo medio, ver catalog.py).
    SecretPattern("openai_org", re.compile(r"\borg-[A-Za-z0-9]{20,}\b", _A)),
    SecretPattern("stripe_publishable_key", re.compile(r"\bpk_(?:live|test)_[A-Za-z0-9]{10,}", _A)),
]


def detect(text: str, taken: list[Finding] | None = None) -> list[Finding]:
    """Devuelve los secretos encontrados (sin máscara; ver masking.py)."""
    taken = list(taken or [])
    found: list[Finding] = []
    for pat in PATTERNS:
        for m in pat.regex.finditer(text):
            start, end = m.span(pat.group)
            value = m.group(pat.group)
            if not value:
                continue
            if pat.validator and not pat.validator(value):
                continue
            if overlaps_any(start, end, taken):
                continue
            f = Finding(type=pat.type, start=start, end=end, value=value, source="secret")
            found.append(f)
            taken.append(f)
    return sorted(found, key=lambda f: f.start)
