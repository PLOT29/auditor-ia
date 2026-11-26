"""Persistencia en SQLite (SPEC §5.5).

Regla de oro: aquí solo llegan datos YA enmascarados. Esta capa nunca recibe
el texto original; `text_sha256` se calcula antes, en main.py.

Ruta de la BD: variable de entorno AUDITOR_DB_PATH
(default: C:\\auditor-data\\auditor.db — NUNCA dentro del repo; SPEC §5.5, regla 8).
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB_PATH = Path(r"C:\auditor-data\auditor.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS prompts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    platform        TEXT    NOT NULL,
    text_masked     TEXT    NOT NULL,
    text_sha256     TEXT    NOT NULL,
    length          INTEGER NOT NULL,
    category        TEXT,
    category_conf   REAL,
    injection_score REAL,
    risk            TEXT    NOT NULL,
    findings_json   TEXT    NOT NULL,
    blocked         INTEGER NOT NULL DEFAULT 0,
    user_decision   TEXT,
    latency_ms      INTEGER,
    source          TEXT    NOT NULL DEFAULT 'extension'
);
CREATE INDEX IF NOT EXISTS idx_prompts_ts ON prompts(ts);
CREATE INDEX IF NOT EXISTS idx_prompts_sha ON prompts(text_sha256);
"""

# Fase 5D · tabla NUEVA de correcciones humanas (no se toca `prompts`: las predicciones originales del
# modelo nunca se borran). Valor efectivo = última etiqueta no deshecha > predicción del modelo.
LABELS_SCHEMA = """
CREATE TABLE IF NOT EXISTS labels (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         INTEGER NOT NULL REFERENCES prompts(id),
    tarea            TEXT    NOT NULL CHECK (tarea IN ('tema', 'inyeccion')),
    valor            TEXT    NOT NULL,
    origen           TEXT    NOT NULL CHECK (origen IN ('manual', 'propagado')),
    similitud_origen REAL,
    label_padre_id   INTEGER REFERENCES labels(id),
    creado_en        TEXT    NOT NULL,
    deshecho         INTEGER NOT NULL DEFAULT 0,
    nota             TEXT
);
CREATE INDEX IF NOT EXISTS idx_labels_event ON labels(event_id, tarea);
CREATE INDEX IF NOT EXISTS idx_labels_padre ON labels(label_padre_id);
"""

# Fase 5D.1 · tabla NUEVA: una nota libre por evento, editable, independiente de las correcciones.
EVENT_NOTES_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_notes (
    event_id       INTEGER PRIMARY KEY REFERENCES prompts(id),
    nota           TEXT    NOT NULL,
    actualizado_en TEXT    NOT NULL
);
"""

SOURCES = ("extension", "import")

# Migraciones idempotentes: (columna, DDL). Se aplican solo si la columna no existe.
MIGRATIONS = [
    ("source", "ALTER TABLE prompts ADD COLUMN source TEXT NOT NULL DEFAULT 'extension'"),
]


def db_path() -> Path:
    env = os.environ.get("AUDITOR_DB_PATH", "").strip()
    return Path(env) if env else DEFAULT_DB_PATH


_tx_conn: sqlite3.Connection | None = None  # conexión de la transacción en curso (ver transaction())


def _open() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def connect():
    if _tx_conn is not None:  # dentro de transaction(): misma conexión, sin commit parcial
        yield _tx_conn
        return
    conn = _open()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def transaction():
    """Agrupa varias operaciones (p. ej. una importación) en UNA transacción:
    commit al final, rollback completo si hay cualquier excepción. Todas las llamadas
    a connect() dentro del bloque usan la misma conexión (ven sus propias inserciones)."""
    global _tx_conn
    if _tx_conn is not None:
        raise RuntimeError("ya hay una transacción en curso")
    conn = _open()
    _tx_conn = conn
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        _tx_conn = None
        conn.close()


def init_db() -> None:
    """Crea la tabla si no existe y aplica las migraciones pendientes (idempotente)."""
    with connect() as conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(prompts)")}
        for column, ddl in MIGRATIONS:
            if column not in cols:
                conn.execute(ddl)  # filas existentes quedan con el DEFAULT ('extension')
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompts_source ON prompts(source)")
        conn.executescript(LABELS_SCHEMA)  # Fase 5D: idempotente (IF NOT EXISTS)
        conn.executescript(EVENT_NOTES_SCHEMA)  # Fase 5D.1: idempotente


# ---------------------------------------------------------------- etiquetas (Fase 5D)
def event_exists(event_id: int) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM prompts WHERE id = ?", (event_id,)).fetchone() is not None


def insert_label(event_id: int, tarea: str, valor: str, origen: str, creado_en: str,
                 similitud: float | None = None, padre: int | None = None, nota: str | None = None) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO labels (event_id, tarea, valor, origen, similitud_origen, label_padre_id, creado_en, nota) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, tarea, valor, origen, similitud, padre, creado_en, nota))
        return int(cur.lastrowid)


def get_label(label_id: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM labels WHERE id = ?", (label_id,)).fetchone()
    return dict(r) if r else None


def undo_labels(ids: list[int]) -> int:
    if not ids:
        return 0
    with connect() as conn:
        cur = conn.execute(f"UPDATE labels SET deshecho = 1 WHERE deshecho = 0 AND id IN ({','.join('?' * len(ids))})", ids)
        return cur.rowcount


def children_of(label_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM labels WHERE label_padre_id = ? AND deshecho = 0 ORDER BY id",
                            (label_id,)).fetchall()
    return [dict(r) for r in rows]


def fetch_labels(include_undone: bool = False) -> list[dict]:
    where = "" if include_undone else "WHERE deshecho = 0"
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM labels {where} ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def fetch_texts(ids: list[int] | None = None) -> dict[int, str]:
    """text_masked (lo ÚNICO que existe) por id. Solo para embeddings y reentrenamiento: nunca se loguea."""
    with connect() as conn:
        if ids is None:
            rows = conn.execute("SELECT id, text_masked FROM prompts ORDER BY id").fetchall()
        else:
            if not ids:
                return {}
            rows = conn.execute(f"SELECT id, text_masked FROM prompts WHERE id IN ({','.join('?' * len(ids))})",
                                ids).fetchall()
    return {int(r["id"]): r["text_masked"] or "" for r in rows}


def get_event_note(event_id: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT event_id, nota, actualizado_en FROM event_notes WHERE event_id = ?",
                         (event_id,)).fetchone()
    return dict(r) if r else None


def set_event_note(event_id: int, nota: str, actualizado_en: str) -> None:
    """Una nota por evento: inserta o reemplaza; una nota vacía la borra."""
    with connect() as conn:
        if nota:
            conn.execute("INSERT INTO event_notes (event_id, nota, actualizado_en) VALUES (?, ?, ?) "
                         "ON CONFLICT(event_id) DO UPDATE SET nota = excluded.nota, actualizado_en = excluded.actualizado_en",
                         (event_id, nota, actualizado_en))
        else:
            conn.execute("DELETE FROM event_notes WHERE event_id = ?", (event_id,))


def fetch_shas() -> dict[int, str]:
    with connect() as conn:
        return {int(r[0]): r[1] for r in conn.execute("SELECT id, text_sha256 FROM prompts")}


def fetch_for_reclassify(only_unclassified: bool = False) -> list[dict]:
    """Filas a reclasificar (ml/reclassify.py). Solo campos necesarios; el texto es el ENMASCARADO."""
    where = "WHERE category_conf IS NULL OR category_conf = 0" if only_unclassified else ""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id, text_masked, findings_json, risk, category, category_conf, injection_score "
            f"FROM prompts {where} ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def update_classification(prompt_id: int, category: str, category_conf: float,
                          injection_score: float | None, risk: str) -> None:
    """Actualiza SOLO la clasificación (no toca findings, blocked ni user_decision)."""
    with connect() as conn:
        conn.execute(
            "UPDATE prompts SET category = ?, category_conf = ?, injection_score = ?, risk = ? WHERE id = ?",
            (category, category_conf, injection_score, risk, prompt_id),
        )


def sha256_exists(text_sha256: str) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM prompts WHERE text_sha256 = ? LIMIT 1", (text_sha256,)).fetchone() is not None


def insert_prompt(row: dict) -> int:
    """row: dict con las columnas de la tabla (excepto id). findings como lista.

    `source` es opcional ('extension' por defecto, 'import' para el historial importado).
    """
    data = dict(row)
    data.setdefault("source", "extension")
    if data["source"] not in SOURCES:
        raise ValueError(f"source inválido: {data['source']!r}")
    data["findings_json"] = json.dumps(data.pop("findings"), ensure_ascii=False)
    data["blocked"] = int(bool(data["blocked"]))
    cols = ", ".join(data)
    marks = ", ".join(f":{k}" for k in data)
    with connect() as conn:
        cur = conn.execute(f"INSERT INTO prompts ({cols}) VALUES ({marks})", data)
        return int(cur.lastrowid)


def _counts(conn, column: str, where: str = "", params: tuple = ()) -> dict:
    rows = conn.execute(
        f"SELECT {column} AS k, COUNT(*) AS n FROM prompts {where} GROUP BY {column} ORDER BY n DESC", params
    ).fetchall()
    return {r["k"]: r["n"] for r in rows}


def get_stats(recent_limit: int = 20, since: str | None = None) -> dict:
    """Agregados para /stats. `since` (ISO-8601 con zona, opcional): solo eventos desde ese instante
    (Fase 5A: el popup pide el resumen de hoy)."""
    where, params = ("WHERE julianday(ts) >= julianday(?)", (since,)) if since else ("", ())
    and_ = "AND" if since else "WHERE"
    with connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM prompts {where}", params).fetchone()[0]
        high = conn.execute(f"SELECT COUNT(*) FROM prompts {where} {and_} risk = 'alto'", params).fetchone()[0]
        blocked = conn.execute(f"SELECT COUNT(*) FROM prompts {where} {and_} blocked = 1", params).fetchone()[0]
        by_platform = _counts(conn, "platform", where, params)
        by_category = _counts(conn, "category", where, params)
        by_risk = _counts(conn, "risk", where, params)
        by_decision = _counts(conn, "user_decision", where, params)
        by_source = _counts(conn, "source", where, params)  # CE-11: lo importado se reporta aparte

        risk_by_platform: dict = {}
        for r in conn.execute(
            f"SELECT platform, risk, COUNT(*) AS n FROM prompts {where} GROUP BY platform, risk", params
        ):
            risk_by_platform.setdefault(r["platform"], {})[r["risk"]] = r["n"]

        latency = [
            r[0]
            for r in conn.execute(
                f"SELECT latency_ms FROM prompts {where} {and_} latency_ms IS NOT NULL ORDER BY latency_ms", params
            )
        ]
        p95 = latency[min(len(latency) - 1, int(round(0.95 * (len(latency) - 1))))] if latency else None

        recent = [
            {
                "id": r["id"],
                "ts": r["ts"],
                "platform": r["platform"],
                "risk": r["risk"],
                "category": r["category"],
                "text_masked": r["text_masked"],
                "findings": json.loads(r["findings_json"]),
                "blocked": bool(r["blocked"]),
                "user_decision": r["user_decision"],
            }
            for r in conn.execute(
                f"SELECT * FROM prompts {where} {and_} risk IN ('alto', 'medio') ORDER BY id DESC LIMIT ?",
                (*params, recent_limit),
            )
        ]

    return {
        "desde": since,
        "total": total,
        "pct_riesgo_alto": round(100 * high / total, 2) if total else 0.0,
        "bloqueados": blocked,
        "plataforma_top": next(iter(by_platform), None),
        "por_plataforma": by_platform,
        "por_categoria": by_category,
        "por_riesgo": by_risk,
        "por_decision": by_decision,
        "por_origen": by_source,
        "riesgo_por_plataforma": risk_by_platform,
        "latencia_p95_ms": p95,
        "alertas_recientes": recent,
    }
