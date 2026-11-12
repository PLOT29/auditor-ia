"""Herramientas CERRADAS del agente (Fase 5C · OWASP LLM06 Excessive Agency).

El modelo de lenguaje SOLO puede pedir una de estas funciones, por nombre, con argumentos que se validan
aquí. Todas:
- reutilizan backend/analytics.py (conexión SQLite de SOLO LECTURA, `mode=ro`);
- devuelven únicamente agregados numéricos y nombres de categorías (plataforma, tema, tipo de hallazgo);
- nunca leen `text_masked` ni ningún texto de prompts (analytics.events() no selecciona esa columna);
- no aceptan SQL, rutas, URLs ni texto libre; no escriben en la BD; no salen a internet.

Por eso, aunque un prompt o una pregunta intenten manipular al modelo (OWASP LLM01), lo peor que puede
pasar es que pida otra cifra agregada.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

import analytics
import db
from detectors import topic

PLATFORMS = list(analytics.PLATFORMS)
MAX_DAYS = 366
DEFAULT_DAYS = 14
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ToolError(ValueError):
    """Argumentos inválidos o herramienta no permitida. El mensaje se le devuelve al modelo."""


# ------------------------------------------------------------------ validación de argumentos
def today() -> date:
    return datetime.now(analytics.TZ).date()


def _date(value, name: str) -> date:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise ToolError(f"{name} debe ser una fecha AAAA-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ToolError(f"{name} no es una fecha válida") from None


def _range(desde, hasta, prefix: str = "") -> tuple[date, date]:
    end = _date(hasta, f"{prefix}hasta") if hasta is not None else today()
    start = _date(desde, f"{prefix}desde") if desde is not None else end - timedelta(days=DEFAULT_DAYS - 1)
    if start > end:
        raise ToolError(f"{prefix}desde debe ser anterior o igual a {prefix}hasta")
    if (end - start).days + 1 > MAX_DAYS:
        raise ToolError(f"el periodo no puede pasar de {MAX_DAYS} días")
    if start < date(2020, 1, 1) or end > today() + timedelta(days=1):
        raise ToolError("fechas fuera de rango")
    return start, end


def _platforms(plataforma) -> tuple[str, ...]:
    if plataforma is None:
        return tuple(PLATFORMS)
    if not isinstance(plataforma, str) or plataforma.lower() not in PLATFORMS:
        raise ToolError(f"plataforma debe ser una de: {', '.join(PLATFORMS)}")
    return (plataforma.lower(),)


def _top(value, default: int, maximum: int = 20) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ToolError(f"top debe ser un entero entre 1 y {maximum}")
    return value


def _filters(desde, hasta, plataforma, prefix: str = "") -> analytics.Filters:
    start, end = _range(desde, hasta, prefix)
    return analytics.Filters(start=start, end=end, platforms=_platforms(plataforma))


def _period(f: analytics.Filters) -> dict:
    return {"desde": f.start.isoformat(), "hasta": f.end.isoformat(),
            "plataformas": list(f.platforms)}


def _thr():
    return topic.confidence_threshold()


# ------------------------------------------------------------------ herramientas
def resumen_periodo(desde=None, hasta=None, plataforma=None) -> dict:
    f = _filters(desde, hasta, plataforma)
    df = analytics.events(db.db_path(), f)
    k = analytics._kpi_values(df, _thr())
    exp = analytics.exposure_index(df)
    return {
        "periodo": _period(f),
        "prompts": k["total"],
        "con_datos_sensibles": k["sensibles"],
        "pct_con_datos_sensibles": k["pct_sensibles"],
        "por_riesgo": exp["por_riesgo"],
        "bloqueos": k["bloqueados"],
        "bloqueos_evitados": k["evitados"],
        "censurados": k["censurados"],
        "enviados_pese_al_bloqueo": k["enviados_igual"],
        "plataforma_principal": k["plataforma_top"],
        "tema_principal": k["tema_top"],
        "indice_exposicion": exp["valor"],
    }


def hallazgos_por_tipo(desde=None, hasta=None, plataforma=None, top=None) -> dict:
    f = _filters(desde, hasta, plataforma)
    ft = analytics.finding_types(db.db_path(), f, top=_top(top, 10))
    return {"periodo": _period(f),
            "tipos": [{"tipo": t, "nombre": analytics.TYPE_NAMES.get(t, t),
                       "categoria": analytics.CATALOG.category(t) or "otro", "prompts": int(n)}
                      for t, n in zip(ft["tipo"], ft["prompts"])]}


def riesgo_por_plataforma(desde=None, hasta=None) -> dict:
    f = _filters(desde, hasta, None)
    rp = analytics.risk_by_platform(db.db_path(), f)
    out = {}
    for p in PLATFORMS:
        rows = rp[rp["platform"] == p]
        counts = {r: int(rows[rows["risk"] == r]["prompts"].sum()) for r in analytics.RISKS}
        total = sum(counts.values())
        out[p] = {**counts, "total": total,
                  "pct_medio_o_alto": round(100 * (counts["medio"] + counts["alto"]) / total, 1) if total else 0.0}
    return {"periodo": _period(f), "plataformas": out}


def temas_frecuentes(desde=None, hasta=None, plataforma=None, top=None) -> dict:
    f = _filters(desde, hasta, plataforma)
    tp = analytics.topics(db.db_path(), f, _thr()).head(_top(top, 6))
    return {"periodo": _period(f),
            "temas": [{"tema": t, "nombre": analytics.TOPIC_NAMES.get(t, t), "prompts": int(n)}
                      for t, n in zip(tp["tema"], tp["prompts"])]}


def bloqueos(desde=None, hasta=None, plataforma=None) -> dict:
    f = _filters(desde, hasta, plataforma)
    df = analytics.events(db.db_path(), f)
    b = df[df["blocked"] == 1] if len(df) else df
    by_dec = {d: int((b["user_decision"] == d).sum()) if len(b) else 0 for d in ("cancelado", "censurado", "enviado")}
    by_plat = {p: int((b["platform"] == p).sum()) if len(b) else 0 for p in PLATFORMS}
    types: dict[str, int] = {}
    for ts in (b["finding_types"] if len(b) else []):
        for t in ts:
            types[t] = types.get(t, 0) + 1
    return {"periodo": _period(f), "bloqueos": int(len(b)), "por_decision": by_dec,
            "evitados": by_dec["cancelado"] + by_dec["censurado"], "por_plataforma": by_plat,
            "tipos_en_bloqueos": dict(sorted(types.items(), key=lambda x: -x[1])[:10])}


def tendencia(desde=None, hasta=None, plataforma=None) -> dict:
    f = _filters(desde, hasta, plataforma)
    df = analytics.events(db.db_path(), f)
    return {"periodo": _period(f), "por_dia": analytics.risk_trend(df, f)}


def comparar_periodos(desde_a, hasta_a, desde_b, hasta_b, plataforma=None) -> dict:
    if None in (desde_a, hasta_a, desde_b, hasta_b):
        raise ToolError("comparar_periodos necesita desde_a, hasta_a, desde_b y hasta_b")
    _range(desde_a, hasta_a, "a.")
    _range(desde_b, hasta_b, "b.")
    a = resumen_periodo(desde_a, hasta_a, plataforma)
    b = resumen_periodo(desde_b, hasta_b, plataforma)
    keys = ("prompts", "con_datos_sensibles", "pct_con_datos_sensibles", "bloqueos", "bloqueos_evitados",
            "indice_exposicion")
    return {"periodo_a": a, "periodo_b": b,
            "diferencia_b_menos_a": {k: round(b[k] - a[k], 1) for k in keys}}


# ------------------------------------------------------------------ registro (lista cerrada)
_DATE_PROP = {"type": "string", "description": "Fecha AAAA-MM-DD (día de la Ciudad de México)"}
_PLAT_PROP = {"type": "string", "enum": PLATFORMS, "description": "Opcional: limitar a una plataforma"}
_TOP_PROP = {"type": "integer", "minimum": 1, "maximum": 20}


@dataclass(frozen=True)
class Tool:
    name: str
    fn: Callable[..., dict]
    description: str
    properties: dict
    required: tuple[str, ...] = ()

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.properties,
                           "required": list(self.required), "additionalProperties": False}}}


_RANGE = {"desde": _DATE_PROP, "hasta": _DATE_PROP}
TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("resumen_periodo", resumen_periodo,
         "Totales de un periodo: prompts, % con datos sensibles, riesgo, bloqueos, censurados, plataforma y tema "
         "principales, índice de exposición. Sin fechas = últimos 14 días.",
         {**_RANGE, "plataforma": _PLAT_PROP}),
    Tool("hallazgos_por_tipo", hallazgos_por_tipo,
         "Tipos de hallazgo más frecuentes (API keys, correos, CURP…) y en cuántos prompts aparecen.",
         {**_RANGE, "plataforma": _PLAT_PROP, "top": _TOP_PROP}),
    Tool("riesgo_por_plataforma", riesgo_por_plataforma,
         "Prompts de riesgo bajo, medio y alto por plataforma (ChatGPT, Claude, Gemini) y % de medio o alto.",
         dict(_RANGE)),
    Tool("temas_frecuentes", temas_frecuentes,
         "Temas más frecuentes (programación, académico, redacción…) y cuántos prompts tiene cada uno.",
         {**_RANGE, "plataforma": _PLAT_PROP, "top": _TOP_PROP}),
    Tool("bloqueos", bloqueos,
         "Envíos bloqueados: cuántos, qué decidió el usuario (cancelado, censurado, enviado), por plataforma y "
         "qué tipos de hallazgo los causaron.",
         {**_RANGE, "plataforma": _PLAT_PROP}),
    Tool("tendencia", tendencia, "Prompts por día, separados por riesgo bajo/medio/alto.",
         {**_RANGE, "plataforma": _PLAT_PROP}),
    Tool("comparar_periodos", comparar_periodos,
         "Compara dos periodos (A y B) con los mismos totales que resumen_periodo y la diferencia B − A.",
         {"desde_a": _DATE_PROP, "hasta_a": _DATE_PROP, "desde_b": _DATE_PROP, "hasta_b": _DATE_PROP,
          "plataforma": _PLAT_PROP},
         ("desde_a", "hasta_a", "desde_b", "hasta_b")),
]}


def schemas() -> list[dict]:
    return [t.schema() for t in TOOLS.values()]


def call(name: str, args) -> dict:
    """Ejecuta UNA herramienta de la lista cerrada con argumentos validados. Nada más es ejecutable."""
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"herramienta no permitida: {str(name)[:40]}. Solo existen: {', '.join(TOOLS)}")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolError("los argumentos deben ser un objeto JSON")
    unknown = set(args) - set(tool.properties)
    if unknown:
        raise ToolError(f"argumentos no permitidos: {', '.join(sorted(str(u)[:30] for u in unknown))}")
    missing = [r for r in tool.required if args.get(r) is None]
    if missing:
        raise ToolError(f"faltan argumentos: {', '.join(missing)}")
    clean = {k: v for k, v in args.items() if v not in ("", None)}
    return tool.fn(**clean)
