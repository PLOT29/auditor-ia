"""Regex de PII (fuente única). Las usan:
  - detectors/pii.py         → detección por regex (siempre para DNI/NIE/dirección; fallback del resto)
  - detectors/presidio_es.py → recognizers propios de Presidio (CURP, RFC, teléfono MX)
  - extension/patterns.js    → MISMAS expresiones (paridad verificada en test_patterns_parity.py)

Semántica ASCII explícita (Fase 5A): `re.ASCII` + clases escritas a mano (`Ñ`/`ñ` y vocales
acentuadas incluidas literalmente), para que Python y JavaScript den exactamente lo mismo.
Las regex con letras acentuadas NO usan IGNORECASE (en JS la bandera `i` sí pliega Á/á).

Fase 5A.1: DNI y NIE (España) con letra de control, y dirección de vivienda por HEURÍSTICA de
señales (local, síncrona, sin Presidio): ver `address_spans`.
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
_DNI_CLASS = "[TRWAGMYFPDXBNJZSQVHLCKEtrwagmyfpdxbnjzsqvhlcke]"
_A = re.ASCII
_AI = re.ASCII | re.IGNORECASE

EMAIL_RE = re.compile(
    rf"(?<![{_WORD}.+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{{2,}}(?![{_WORD}])", _A)
CURP_RE = re.compile(
    rf"(?<![{_WORD}])[A-Z][AEIOUX][A-Z]{{2}}{_DATE}[HM](?:{_STATES})[B-DF-HJ-NP-TV-Z]{{3}}[A-Z0-9][0-9](?![{_WORD}])",
    _AI)
RFC_RE = re.compile(
    rf"(?<![{_WORD}{_LETTER_ES}&])[A-ZÑñ&]{{3,4}}{_DATE}[A-Z0-9]{{2}}[A0-9](?![{_WORD}])", _AI)
DNI_RE = re.compile(rf"(?<![{_WORD}])[0-9]{{8}}[ \-]?{_DNI_CLASS}(?![{_WORD}])", _A)
NIE_RE = re.compile(rf"(?<![{_WORD}])[XYZxyz][ \-]?[0-9]{{7}}[ \-]?{_DNI_CLASS}(?![{_WORD}])", _A)
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


_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def dni_valid(value: str) -> bool:
    """Letra de control de un DNI o NIE (módulo 23; en el NIE, X/Y/Z valen 0/1/2)."""
    s = re.sub(r"[ -]", "", value).upper()
    num, letter = s[:-1], s[-1]
    if num[:1] in "XYZ":
        num = str("XYZ".index(num[0])) + num[1:]
    return _DNI_LETTERS[int(num) % 23] == letter


# ---------------------------------------------------------------- dirección (heurística)
_LET = f"A-Za-z{_LETTER_ES}"
_NB = rf"(?<![{_LET}0-9_])"  # no precedido por letra/dígito
_NA = rf"(?![{_LET}0-9_])"   # no seguido por letra/dígito
_UPW = rf"[A-ZÁÉÍÓÚÜÑ0-9][{_LET}0-9]*\.?"  # palabra con mayúscula inicial (o número)
_NAME = rf"{_UPW}(?:[ \t]+(?:(?:de|del|la|las|los|el|y)[ \t]+)?{_UPW}){{0,4}}"
ADDRESS_SIGNALS: list[tuple[str, re.Pattern]] = [(kind, re.compile(src, _A)) for kind, src in [
    ("via", rf"{_NB}(?:[Cc]alle|C\.|[Aa]v\.?|[Aa]venida|[Bb]lvd\.?|[Bb]oulevard|[Cc]alz\.?|[Cc]alzada|[Pp]rivada|"
            rf"[Pp]riv\.|[Cc]errada|[Aa]ndador|[Cc]amino|[Cc]arretera)[ \t]+{_NAME}[ \t]*,?[ \t]*"
            rf"(?:(?:[Nn]o\.|[Nn]úm\.?|#)[ \t]*)?[0-9]{{1,5}}(?:[ \t]?[A-Za-z])?{_NA}"),
    ("numero", rf"(?:{_NB}[Nn]o\.|{_NB}[Nn]úm\.?|#)[ \t]*[0-9]{{1,5}}{_NA}"),
    ("interior", rf"{_NB}(?:[Ii]nt\.|[Ee]xt\.|[Ii]nterior|[Dd]epto\.?|[Dd]pto\.?|[Dd]epartamento)[ \t]*"
                 rf"(?:[0-9][A-Za-z0-9-]{{0,5}}|[A-Z]){_NA}"),
    ("colonia", rf"{_NB}(?:[Cc]ol\.|[Cc]olonia|[Ff]racc\.?|[Ff]raccionamiento|[Uu]nidad [Hh]abitacional|U\.[ ]?H\.)"
                rf"[ \t]+{_NAME}"),
    ("cp", rf"{_NB}(?:C\.[ ]?P\.?|CP|[Cc]ódigo [Pp]ostal)[ \t]*:?[ \t]*[0-9]{{5}}{_NA}"),
    ("municipio", rf"{_NB}(?:[Aa]lcald[íi]a|[Dd]elegaci[óo]n|[Mm]unicipio(?:[ \t]+de)?)[ \t]+{_NAME}"),
    ("manzana", rf"{_NB}(?:[Mm]z\.?|[Mm]za\.?|[Mm]anzana|[Ll]t\.?|[Ll]ote)[ \t]*[0-9]{{1,4}}{_NA}"),
]]
ADDRESS_MAX_GAP = 40
# "#12" o "lote 7" solos son ambiguos: un grupo necesita al menos una señal fuerte.
ADDRESS_WEAK = {"numero", "manzana"}
_BLANK_LINE = re.compile(r"\n[ \t\r]*\n")


def _is_address(kinds: set[str]) -> bool:
    return len(kinds) >= 2 and bool(kinds - ADDRESS_WEAK)


def address_spans(text: str) -> list[tuple[int, int]]:
    """Direcciones de vivienda por señales (igual que addressSpans en patterns.js).

    Señales: vía + nombre + número · No./#/Núm. + número · Int./Ext./Depto. · Col./Colonia/Fracc./
    Unidad Habitacional · C.P. + 5 dígitos · Alcaldía/Delegación/Municipio · Mz./Lt.
    Se agrupan las señales cercanas (≤ 40 caracteres entre una y otra, sin línea en blanco) y un
    grupo es dirección con 2 o más señales DISTINTAS (C.P. + cualquier otra incluido), de las que
    al menos una es fuerte (vía, interior, colonia, C.P., alcaldía). Una sola señal ("Av. Reforma
    222", "colonia Roma", "nos vemos en Reforma") o solo débiles ("issue #12 y lote 7") no bastan.
    """
    sig = []
    for k, (kind, regex) in enumerate(ADDRESS_SIGNALS):
        for m in regex.finditer(text):
            if m.end() > m.start():
                sig.append((m.start(), -m.end(), k, kind))
    sig.sort()
    kept: list[tuple[int, int, str]] = []
    for start, neg_end, _k, kind in sig:
        if not kept or start >= kept[-1][1]:
            kept.append((start, -neg_end, kind))
    out: list[tuple[int, int]] = []
    cur = None  # [start, end, {kinds}]
    for start, end, kind in kept:
        if cur and start - cur[1] <= ADDRESS_MAX_GAP and not _BLANK_LINE.search(text[cur[1]:start]):
            cur[1] = end
            cur[2].add(kind)
        else:
            if cur and _is_address(cur[2]):
                out.append((cur[0], cur[1]))
            cur = [start, end, {kind}]
    if cur and _is_address(cur[2]):
        out.append((cur[0], cur[1]))
    return out


def _dni_annotate(value: str) -> dict:
    return {"valido": dni_valid(value)}


# (tipo, regex o escáner, validador, anotador) — CURP antes que RFC (el RFC es prefijo de la CURP);
# la dirección antes que el teléfono (absorbe los números que caen dentro de ella).
RULES = [
    ("email", EMAIL_RE, None, None),
    ("curp", CURP_RE, None, None),
    ("rfc", RFC_RE, None, None),
    ("dni", DNI_RE, None, _dni_annotate),
    ("nie", NIE_RE, None, _dni_annotate),
    ("address", address_spans, None, None),
    ("phone_mx", PHONE_RE, phone_ok, None),
]

# Reglas deterministas que corren SIEMPRE en el backend, también con Presidio (que no las conoce).
ALWAYS_RULES = [r for r in RULES if r[0] in ("dni", "nie", "address")]
