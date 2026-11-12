"""Enmascarado de valores sensibles y del texto completo.

- `mask_value`  → valor corto para mostrar al usuario: `sk-****abcd`, `o****@gmail.com`.
- `mask_text`   → texto que se guarda en BD con marcadores: `<OPENAI_KEY>`, `<EMAIL>`.
"""
from __future__ import annotations

from detectors.base import Finding

STARS = "****"

# Prefijo público que se conserva al enmascarar (no es secreto por sí mismo).
_PREFIXES = {
    "anthropic_key": "sk-ant-",
    "openai_key": "sk-",
    "aws_access_key": None,  # se calcula: AKIA / ASIA
    "github_token": None,    # ghp_, gho_, github_pat_ ...
    "google_api_key": "AIza",
    "jwt": "eyJ",
}

# Tipos para los que NO se muestra ningún carácter del valor.
_FULLY_HIDDEN = {"password", "private_key", "person", "address"}  # ni un pedazo del apellido o la calle


def _github_prefix(value: str) -> str:
    return "github_pat_" if value.startswith("github_pat_") else value[:4]


def mask_value(type_: str, value: str) -> str:
    if type_ in _FULLY_HIDDEN:
        return STARS
    if type_ == "email":
        local, _, domain = value.partition("@")
        return f"{local[:1]}{STARS}@{domain}"
    if type_ in ("credit_card", "phone_mx"):
        digits = "".join(c for c in value if c.isdigit())
        return f"{STARS}{digits[-4:]}"

    if type_ == "aws_access_key":
        prefix = value[:4]
    elif type_ == "github_token":
        prefix = _github_prefix(value)
    else:
        prefix = _PREFIXES.get(type_) or ""

    tail_len = 4 if len(value) >= 12 else 2
    if len(value) <= len(prefix) + tail_len:
        return f"{prefix}{STARS}"
    return f"{prefix}{STARS}{value[-tail_len:]}"


def apply_masks(findings: list[Finding]) -> list[Finding]:
    for f in findings:
        f.masked = mask_value(f.type, f.value)
    return findings


def placeholder(type_: str) -> str:
    return f"<{type_.upper()}>"


def mask_text(text: str, findings: list[Finding]) -> str:
    """Reemplaza cada fragmento sensible por su marcador `<TIPO>`."""
    out = text
    for f in sorted(findings, key=lambda f: f.start, reverse=True):
        out = out[: f.start] + placeholder(f.type) + out[f.end :]
    return out
