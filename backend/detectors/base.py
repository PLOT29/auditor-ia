"""Tipos compartidos por los detectores.

Un `Finding` vive SOLO en memoria: contiene el valor original (`value`) para
poder enmascararlo, pero nunca se serializa tal cual. Usa `to_public()` para
obtener la versión segura (tipo + valor enmascarado + fuente).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Finding:
    type: str          # p. ej. "openai_key", "email"
    start: int         # índice de inicio del fragmento sensible en el texto
    end: int           # índice final (exclusivo)
    value: str = field(repr=False)  # valor en claro — NO loguear, NO persistir
    source: str = "secret"          # "secret" | "pii" | "pii_ner" | "client" | "client_pii"
    masked: str = ""
    valido: bool | None = None      # DNI/NIE: ¿la letra de control coincide? (Fase 5A.1)

    def overlaps(self, start: int, end: int) -> bool:
        return self.start < end and start < self.end

    def to_public(self) -> dict:
        out = {"type": self.type, "masked": self.masked, "source": self.source}
        if self.valido is not None:
            out["valido"] = self.valido
        return out


def overlaps_any(start: int, end: int, taken: list[Finding]) -> bool:
    return any(f.overlaps(start, end) for f in taken)
