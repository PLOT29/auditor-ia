"""Consultas de estadística (Fases 5 → 5C). TODO el SQL de lectura vive aquí.

Lo usan, sin duplicar SQL:
  - el centro de control (`GET /stats?detalle=1` → `panel()`, Fase 5B), servido por main.py;
  - el detalle de evento (`GET /events/{id}` → `event_detail()`, Fase 5C);
  - las herramientas cerradas del agente (agent/tools.py, Fase 5C).
(Antes vivía en dashboard/queries.py; se movió al backend en la 5B. Streamlit se retiró en la 5C.)

Reglas:
- Conexión SQLite en SOLO LECTURA (URI `mode=ro`): nada de aquí escribe en la BD.
- Funciones puras: reciben la ruta de la BD + `Filters` y devuelven DataFrames (o un dict).
- Solo dos funciones leen `text_masked`, y solo si se pide explícitamente: `event_detail(include_text=True)`
  (el panel con el modo presentación APAGADO) y `recent_alerts(include_text=True)` (trunca a 100
  caracteres; hoy sin uso en la interfaz). `events()` —la base de los agregados y de las herramientas del
  agente— nunca la selecciona.
- Fechas: el usuario filtra por días de la Ciudad de México. El filtro se hace en SQL con
  julianday() (que entiende los offsets ISO de `ts`) y la hora local se calcula con zoneinfo.
- BD inexistente, vacía o sin tabla → DataFrames vacíos, nunca una excepción.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

TZ = ZoneInfo("America/Mexico_City")
REPO = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path(r"C:\auditor-data\auditor.db")
DEFAULT_METRICS = REPO / "ml" / "reports" / "metrics.json"

PLATFORMS = ["chatgpt", "claude", "gemini"]
RISKS = ["bajo", "medio", "alto"]
SOURCES = {"ambos": None, "extension": "extension", "import": "import"}
UNCERTAIN = "incierto"
UNCLASSIFIED = "sin_clasificar"
WEEKDAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


from detectors import catalog as CATALOG  # noqa: E402  (fuente única de nombres, categorías y motivos)

TYPE_NAMES = {t: v["nombre"] for t, v in CATALOG.CATALOG.items()}

EVENT_COLUMNS = ["id", "ts", "platform", "risk", "category", "category_conf", "blocked",
                 "user_decision", "findings_json", "source", "latency_ms"]


@dataclass(frozen=True)
class Filters:
    start: date            # día local (Ciudad de México), inclusive
    end: date              # día local, inclusive
    platforms: tuple[str, ...] = tuple(PLATFORMS)
    source: str = "ambos"  # ambos | extension | import

    def previous(self) -> "Filters":
        """Periodo anterior de la misma duración, justo antes de `start`."""
        days = (self.end - self.start).days + 1
        return replace(self, start=self.start - timedelta(days=days), end=self.start - timedelta(days=1))


# ------------------------------------------------------------------ conexión
def db_path() -> Path:
    env = os.environ.get("AUDITOR_DB_PATH", "").strip()
    return Path(env) if env else DEFAULT_DB


def connect_ro(path: Path | str) -> sqlite3.Connection:
    """Conexión de SOLO LECTURA. Falla si el archivo no existe (no lo crea)."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _has_table(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prompts'").fetchone() is not None


def _columns(conn) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(prompts)")}


def _utc_bounds(f: Filters) -> tuple[str, str]:
    """[inicio, fin) en UTC para los días locales del filtro."""
    lo = datetime.combine(f.start, time.min, TZ)
    hi = datetime.combine(f.end + timedelta(days=1), time.min, TZ)
    return lo.astimezone(ZoneInfo("UTC")).isoformat(), hi.astimezone(ZoneInfo("UTC")).isoformat()


def _where(f: Filters, cols: set[str]) -> tuple[str, list]:
    lo, hi = _utc_bounds(f)
    parts = ["julianday(ts) >= julianday(?)", "julianday(ts) < julianday(?)"]
    params: list = [lo, hi]
    plats = [p for p in f.platforms if p in PLATFORMS]
    parts.append(f"platform IN ({','.join('?' * len(plats))})" if plats else "0")
    params += plats
    src = SOURCES.get(f.source)
    if src:
        if "source" in cols:
            parts.append("source = ?")
            params.append(src)
        elif src != "extension":  # BD vieja sin columna source: todo es de la extensión
            parts.append("0")
    return " AND ".join(parts), params


# ------------------------------------------------------------------ base
def events(path: Path | str, f: Filters) -> pd.DataFrame:
    """Eventos filtrados SIN texto. Agrega ts_local (Ciudad de México) y la lista de hallazgos."""
    empty = pd.DataFrame(columns=EVENT_COLUMNS + ["ts_local", "finding_types"])
    path = Path(path)
    if not path.is_file():
        return empty
    with closing(connect_ro(path)) as conn:
        if not _has_table(conn):
            return empty
        cols = _columns(conn)
        where, params = _where(f, cols)
        src = "source" if "source" in cols else "'extension' AS source"
        rows = conn.execute(
            f"SELECT id, ts, platform, risk, category, category_conf, blocked, user_decision, findings_json, {src}, "
            f"latency_ms "
            f"FROM prompts WHERE {where} ORDER BY julianday(ts)", params).fetchall()
    if not rows:
        return empty
    df = pd.DataFrame([dict(r) for r in rows], columns=EVENT_COLUMNS)
    df["ts_local"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601").dt.tz_convert(TZ)
    df["finding_types"] = df["findings_json"].map(_types)
    df["blocked"] = df["blocked"].fillna(0).astype(int)
    return df


def _types(findings_json) -> list[str]:
    try:
        return sorted({f.get("type", "?") for f in json.loads(findings_json or "[]")})
    except (ValueError, TypeError, AttributeError):
        return []


def date_bounds(path: Path | str) -> tuple[date, date] | None:
    """Primer y último día local con datos (de todas las plataformas reales), o None."""
    path = Path(path)
    if not path.is_file():
        return None
    with closing(connect_ro(path)) as conn:
        if not _has_table(conn):
            return None
        row = conn.execute(
            "SELECT MIN(julianday(ts)), MAX(julianday(ts)) FROM prompts WHERE platform IN ('chatgpt','claude','gemini')"
        ).fetchone()
    if row[0] is None:
        return None
    to_local = lambda jd: pd.to_datetime(jd - 2440587.5, unit="D", utc=True).tz_convert(TZ).date()  # noqa: E731
    return to_local(row[0]), to_local(row[1])


# ------------------------------------------------------------------ umbral de "incierto"
def confidence_threshold(metrics_path: Path | str = DEFAULT_METRICS) -> float | None:
    """Umbral de confianza del tema (metrics.json → topic_v2.umbral_confianza.umbral)."""
    try:
        data = json.loads(Path(metrics_path).read_text(encoding="utf-8"))
        return float(data["topic_v2"]["umbral_confianza"]["umbral"])
    except (OSError, KeyError, ValueError, TypeError):
        return None


def topic_bucket(category, conf, threshold: float | None) -> str:
    """Categoría para las gráficas: 'incierto' si la confianza < umbral; 'sin_clasificar' si no hay."""
    if not category or category == UNCLASSIFIED or conf is None or pd.isna(conf) or conf == 0:
        return UNCLASSIFIED
    if threshold is not None and conf < threshold:
        return UNCERTAIN
    return category


# ------------------------------------------------------------------ KPIs
def _kpi_values(df: pd.DataFrame, threshold: float | None) -> dict:
    n = len(df)
    sensitive = int(df["risk"].isin(["medio", "alto"]).sum()) if n else 0
    blocked = df[df["blocked"] == 1] if n else df
    topics = df.apply(lambda r: topic_bucket(r["category"], r["category_conf"], threshold), axis=1) if n else pd.Series(dtype=str)
    topics = topics[~topics.isin([UNCERTAIN, UNCLASSIFIED])]
    return {
        "total": n,
        "pct_sensibles": round(100 * sensitive / n, 1) if n else 0.0,
        "sensibles": sensitive,
        "bloqueados": len(blocked),
        "cancelados": int((blocked["user_decision"] == "cancelado").sum()) if n else 0,
        "enviados_igual": int((blocked["user_decision"] == "enviado").sum()) if n else 0,
        # Fase 5B: censurados (se envió la versión con marcadores) y fugas evitadas (cancelado + censurado).
        "censurados": int((blocked["user_decision"] == "censurado").sum()) if n else 0,
        "evitados": int(blocked["user_decision"].isin(["cancelado", "censurado"]).sum()) if n else 0,
        "plataforma_top": df["platform"].mode().iat[0] if n else None,
        "tema_top": topics.mode().iat[0] if len(topics) else None,
    }


def kpis(path: Path | str, f: Filters, threshold: float | None = None) -> dict:
    """KPIs del periodo y del periodo anterior de la misma duración, con sus variaciones."""
    cur = _kpi_values(events(path, f), threshold)
    prev = _kpi_values(events(path, f.previous()), threshold)
    delta = {k: (cur[k] - prev[k]) for k in ("total", "pct_sensibles", "bloqueados", "cancelados", "enviados_igual",
                                              "censurados", "evitados")}
    delta["pct_sensibles"] = round(delta["pct_sensibles"], 1)
    return {"actual": cur, "anterior": prev, "delta": delta}


# ------------------------------------------------------------------ gráficas
def by_platform(path: Path | str, f: Filters) -> pd.DataFrame:
    df = events(path, f)
    out = df.groupby("platform").size().reindex(PLATFORMS, fill_value=0).rename("prompts").reset_index()
    return out[out["platform"].isin(f.platforms)]


def topics(path: Path | str, f: Filters, threshold: float | None) -> pd.DataFrame:
    """Prompts por tema; los de confianza < umbral van en 'incierto' (y los sin modelo en 'sin_clasificar')."""
    df = events(path, f)
    if df.empty:
        return pd.DataFrame(columns=["tema", "prompts"])
    b = df.apply(lambda r: topic_bucket(r["category"], r["category_conf"], threshold), axis=1)
    out = b.value_counts().rename_axis("tema").rename("prompts").reset_index()
    special = out["tema"].isin([UNCERTAIN, UNCLASSIFIED])
    return pd.concat([out[~special], out[special]], ignore_index=True)  # incierto/sin_clasificar al final


def risk_by_platform(path: Path | str, f: Filters) -> pd.DataFrame:
    df = events(path, f)
    grid = pd.MultiIndex.from_product([[p for p in PLATFORMS if p in f.platforms], RISKS], names=["platform", "risk"])
    counts = df.groupby(["platform", "risk"]).size() if not df.empty else pd.Series(dtype=int)
    return counts.reindex(grid, fill_value=0).rename("prompts").reset_index()


def heatmap(path: Path | str, f: Filters) -> pd.DataFrame:
    """Matriz 7×24 (día de la semana × hora local) con el número de prompts."""
    df = events(path, f)
    m = pd.DataFrame(0, index=WEEKDAYS, columns=range(24))
    if not df.empty:
        c = df.groupby([df["ts_local"].dt.weekday, df["ts_local"].dt.hour]).size()
        for (wd, h), n in c.items():
            m.iat[wd, h] = n
    return m


def daily_trend(path: Path | str, f: Filters) -> pd.DataFrame:
    """Por día local: total de prompts y prompts riesgosos (medio + alto). Incluye días en cero."""
    df = events(path, f)
    days = pd.date_range(f.start, f.end, freq="D").date
    out = pd.DataFrame({"dia": days})
    if df.empty:
        out["prompts"] = 0
        out["riesgosos"] = 0
        return out
    d = df.assign(dia=df["ts_local"].dt.date, riesgoso=df["risk"].isin(["medio", "alto"]))
    g = d.groupby("dia").agg(prompts=("id", "size"), riesgosos=("riesgoso", "sum"))
    return out.merge(g, on="dia", how="left").fillna(0).astype({"prompts": int, "riesgosos": int})


def finding_types(path: Path | str, f: Filters, top: int = 12) -> pd.DataFrame:
    """Tipos de hallazgo más comunes (un prompt cuenta una vez por tipo), leídos de findings_json."""
    df = events(path, f)
    if df.empty:
        return pd.DataFrame(columns=["tipo", "prompts"])
    s = df["finding_types"].explode().dropna()
    return s.value_counts().head(top).rename_axis("tipo").rename("prompts").reset_index()


def recent_alerts(path: Path | str, f: Filters, limit: int = 20, include_text: bool = False,
                  threshold: float | None = None) -> pd.DataFrame:
    """Últimos eventos de riesgo medio o alto. El texto enmascarado (≤ 100 caracteres) SOLO
    con include_text=True; si no, la columna ni siquiera se lee de la BD."""
    cols = ["hora", "plataforma", "riesgo", "motivo", "hallazgos", "bloqueo", "tema"] + (["texto"] if include_text else [])
    df = events(path, f)
    df = df[df["risk"].isin(["medio", "alto"])].sort_values("ts_local", ascending=False).head(limit)
    if df.empty:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame({
        "hora": df["ts_local"].dt.strftime("%Y-%m-%d %H:%M"),
        "plataforma": df["platform"],
        "riesgo": df["risk"],
        # Fase 5A.1: secreto / dato personal / término propio / inyección (riesgo sin hallazgos).
        "motivo": [", ".join(CATALOG.motives(t, r)) or "—" for t, r in zip(df["finding_types"], df["risk"])],
        "hallazgos": df["finding_types"].map(lambda t: ", ".join(t) if t else "—"),
        "bloqueo": [("bloqueado · " + (d or "?")) if b else "—" for b, d in zip(df["blocked"], df["user_decision"])],
        "tema": [topic_bucket(c, p, threshold) for c, p in zip(df["category"], df["category_conf"])],
    })
    if include_text:
        ids = df["id"].tolist()
        with closing(connect_ro(path)) as conn:
            texts = dict(conn.execute(
                f"SELECT id, text_masked FROM prompts WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall())
        out["texto"] = [_truncate(texts.get(i, "")) for i in ids]
    return out.reset_index(drop=True)


def _truncate(text: str, n: int = 100) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


# ------------------------------------------------------------------ hallazgo (golpe 3)
# Datos personales = categoría "dato_personal" del catálogo (correo, teléfono, CURP, RFC, DNI, NIE, nombre, dirección).
PII_TYPES = {t for t, v in CATALOG.CATALOG.items() if v["categoria"] == "dato_personal"}


def headline(path: Path | str, f: Filters, names: dict | None = None) -> dict:
    """Cifras para la frase del 'Hallazgo'. Solo números y nombres de categorías.
    `names` traduce la clave de la plataforma a su nombre para mostrar (p. ej. claude → Claude)."""
    df = events(path, f)
    n = len(df)
    if not n:
        return {"total": 0, "frase": "Todavía no hay prompts en este periodo."}
    has_pii = df["finding_types"].map(lambda t: bool(PII_TYPES & set(t)))
    risky = df["risk"].isin(["medio", "alto"])
    by_plat = df.assign(r=risky).groupby("platform")["r"].agg(["mean", "size"])
    by_plat = by_plat[by_plat["size"] >= 3] if (by_plat["size"] >= 3).any() else by_plat
    worst = by_plat["mean"].idxmax()
    pct_pii = round(100 * has_pii.mean(), 1)
    pct_risky_worst = round(100 * by_plat.loc[worst, "mean"], 1)
    frase = (f"El {pct_pii:g} % de tus prompts contenían datos personales; "
             f"la plataforma con más riesgo fue {(names or {}).get(worst, worst)} "
             f"({pct_risky_worst:g} % de sus prompts con riesgo medio o alto).")
    return {"total": n, "pct_pii": pct_pii, "plataforma_mas_riesgo": worst,
            "pct_riesgo_plataforma": pct_risky_worst, "frase": frase}


# ------------------------------------------------------------------ centro de control (Fase 5B)
PLATFORM_NAMES = {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini", "test": "Prueba"}
TOPIC_NAMES = {"programacion": "Programación", "academico": "Académico", "redaccion": "Redacción",
               "ciberseguridad": "Ciberseguridad", "consulta_general": "Consulta general", "personal": "Personal",
               UNCERTAIN: "Incierto", UNCLASSIFIED: "Sin clasificar"}
MOTIVES = ["secreto", "dato personal", "inyección", "término propio"]
# Índice de exposición (SPEC §5.6): peso 0 a un evento bajo, 0.5 a uno medio y 1 a uno alto.
EXPOSURE_WEIGHTS = {"bajo": 0.0, "medio": 0.5, "alto": 1.0}
EXPOSURE_FORMULA = "IE = 100 × (0.5 × eventos de riesgo medio + 1 × eventos de riesgo alto) ÷ total de eventos"


def exposure_index(df: pd.DataFrame) -> dict:
    """Índice de exposición 0–100 del periodo: proporción ponderada de eventos medio/alto.
    0 = ningún prompt con riesgo; 100 = todos de riesgo alto; 50 = todos medio (o la mitad alto)."""
    n = len(df)
    counts = {r: int((df["risk"] == r).sum()) if n else 0 for r in RISKS}
    value = round(100 * sum(EXPOSURE_WEIGHTS[r] * c for r, c in counts.items()) / n, 1) if n else 0.0
    return {"valor": value, "por_riesgo": counts, "total": n, "formula": EXPOSURE_FORMULA,
            "bandas": {"bajo": [0, 20], "moderado": [20, 50], "alto": [50, 100]}}


def event_summary(*, id, ts, platform, risk, category, category_conf, types, blocked, decision, latency_ms,
                  threshold: float | None = None, simulated: bool = False) -> dict:
    """Resumen de UN evento para el feed y para /events (SSE). Nunca incluye texto ni valores:
    solo metadatos, tipos de hallazgo y el motivo."""
    when = pd.Timestamp(ts)
    when = (when.tz_localize("UTC") if when.tzinfo is None else when).tz_convert(TZ)
    types = sorted(set(types))
    return {
        "id": id,
        "ts": when.isoformat(),
        "hora": when.strftime("%H:%M:%S"),
        "plataforma": platform,
        "riesgo": risk,
        "motivo": CATALOG.motives(types, risk),
        "tipos": types,
        "categoria": topic_bucket(category, category_conf, threshold),
        "decision": decision,
        "bloqueado": bool(blocked),
        "latencia_ms": None if latency_ms is None or pd.isna(latency_ms) else int(latency_ms),
        "simulado": simulated,
    }


def risk_trend(df: pd.DataFrame, f: Filters) -> list[dict]:
    """Por día local: prompts de riesgo bajo, medio y alto (tendencia apilada). Incluye días en cero."""
    days = pd.date_range(f.start, f.end, freq="D").date
    counts = (df.assign(dia=df["ts_local"].dt.date).groupby(["dia", "risk"]).size()
              if len(df) else pd.Series(dtype=int))
    return [{"dia": d.isoformat(), **{r: int(counts.get((d, r), 0)) for r in RISKS}} for d in days]


def sankey(df: pd.DataFrame, threshold: float | None) -> dict:
    """Flujo plataforma → tema → riesgo (nodos con nombre para mostrar, enlaces con conteos)."""
    if not len(df):
        return {"nodes": [], "links": []}
    d = df.assign(tema=[topic_bucket(c, p, threshold) for c, p in zip(df["category"], df["category_conf"])])
    plat = lambda p: PLATFORM_NAMES.get(p, p)  # noqa: E731
    topic = lambda t: TOPIC_NAMES.get(t, t)  # noqa: E731
    risk = lambda r: f"Riesgo {r}"  # noqa: E731
    links = []
    for (p, t), n in d.groupby(["platform", "tema"]).size().items():
        links.append({"source": plat(p), "target": topic(t), "value": int(n)})
    for (t, r), n in d.groupby(["tema", "risk"]).size().items():
        links.append({"source": topic(t), "target": risk(r), "value": int(n)})
    names = []
    for group in ([plat(p) for p in PLATFORMS if p in set(d["platform"])],
                  [topic(t) for t in d["tema"].value_counts().index],
                  [risk(r) for r in RISKS if r in set(d["risk"])]):
        names += [x for x in group if x not in names]
    return {"nodes": [{"name": n} for n in names], "links": links}


def motive_counts(df: pd.DataFrame) -> dict:
    """Eventos de riesgo medio/alto por motivo (un evento con secreto y correo cuenta en ambos)."""
    out = {m: 0 for m in MOTIVES}
    if not len(df):
        return out
    risky = df[df["risk"].isin(["medio", "alto"])]
    for types, r in zip(risky["finding_types"], risky["risk"]):
        for m in CATALOG.motives(types, r):
            out[m] = out.get(m, 0) + 1
    return out


def recent_events(df: pd.DataFrame, threshold: float | None, limit: int = 30) -> list[dict]:
    """Últimos eventos (los más nuevos primero) con la forma del feed. Sin texto."""
    if not len(df):
        return []
    d = df.sort_values("ts_local", ascending=False).head(limit)
    return [event_summary(id=int(r.id), ts=r.ts, platform=r.platform, risk=r.risk, category=r.category,
                          category_conf=r.category_conf, types=r.finding_types, blocked=r.blocked,
                          decision=r.user_decision, latency_ms=r.latency_ms, threshold=threshold)
            for r in d.itertuples()]


def panel(path: Path | str, f: Filters, threshold: float | None = None) -> dict:
    """Todo lo que dibuja el centro de control, en un dict serializable a JSON. Sin texto de prompts."""
    df = events(path, f)
    hm = heatmap(path, f)
    ft = finding_types(path, f)
    return {
        "filtros": {"inicio": f.start.isoformat(), "fin": f.end.isoformat(), "plataformas": list(f.platforms),
                    "origen": f.source},
        "kpis": kpis(path, f, threshold),
        "exposicion": exposure_index(df),
        "sankey": sankey(df, threshold),
        "mapa_calor": {"dias": WEEKDAYS, "horas": list(range(24)), "valores": hm.values.tolist()},
        "tendencia": risk_trend(df, f),
        "tipos_hallazgo": [{"tipo": t, "nombre": TYPE_NAMES.get(t, t), "prompts": int(n)}
                           for t, n in zip(ft["tipo"], ft["prompts"])],
        "motivos": motive_counts(df),
        "hallazgo": headline(path, f, PLATFORM_NAMES),
        "recientes": recent_events(df, threshold),
        "rango_datos": [d.isoformat() for d in (date_bounds(path) or ())],
        "nombres_tipos": TYPE_NAMES,  # para los chips del feed (también tipos que no salen en el periodo)
    }


# ------------------------------------------------------------------ detalle de evento (Fase 5C)
MARKER_PATTERN = r"<[A-Z_]+>|\[[A-ZÁÉÍÓÚÑ_]+\]"  # <OPENAI_KEY> (backend) y [API_KEY_OCULTA] (censura)


def event_detail(path: Path | str, event_id: int, include_text: bool = False,
                 threshold: float | None = None) -> dict | None:
    """Detalle de UN evento (conexión de solo lectura). El único texto que existe es `text_masked`; solo
    se lee de la BD si `include_text` (el panel lo pide con el modo presentación apagado)."""
    path = Path(path)
    if not path.is_file():
        return None
    with closing(connect_ro(path)) as conn:
        if not _has_table(conn):
            return None
        cols = _columns(conn)
        src = "source" if "source" in cols else "'extension' AS source"
        text_col = ", text_masked" if include_text else ""
        row = conn.execute(
            f"SELECT id, ts, platform, risk, category, category_conf, injection_score, findings_json, blocked, "
            f"user_decision, latency_ms, length, {src}{text_col} FROM prompts WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        return None
    r = dict(row)
    try:
        findings = json.loads(r["findings_json"] or "[]")
    except ValueError:
        findings = []
    types = [f.get("type", "?") for f in findings]
    base = event_summary(id=r["id"], ts=r["ts"], platform=r["platform"], risk=r["risk"], category=r["category"],
                         category_conf=r["category_conf"], types=types, blocked=r["blocked"],
                         decision=r["user_decision"], latency_ms=r["latency_ms"], threshold=threshold)
    base.update({
        "fecha": base["ts"][:10],
        "origen": r["source"] or "extension",
        "categoria_modelo": r["category"],
        "confianza": None if r["category_conf"] is None else round(float(r["category_conf"]), 3),
        "injection_score": None if r["injection_score"] is None else round(float(r["injection_score"]), 3),
        "longitud": r["length"],
        "hallazgos": [{"type": f.get("type", "?"), "nombre": TYPE_NAMES.get(f.get("type"), f.get("type", "?")),
                       "masked": f.get("masked", "****"), "source": f.get("source"),
                       **({"valido": f["valido"]} if "valido" in f else {})} for f in findings],
        "texto": r.get("text_masked") if include_text else None,
        "texto_oculto": not include_text,
        "marcadores": MARKER_PATTERN,
    })
    return base
