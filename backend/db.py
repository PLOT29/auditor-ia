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
    latency_ms      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_prompts_ts ON prompts(ts);
CREATE INDEX IF NOT EXISTS idx_prompts_sha ON prompts(text_sha256);
"""


def db_path() -> Path:
    env = os.environ.get("AUDITOR_DB_PATH", "").strip()
    return Path(env) if env else DEFAULT_DB_PATH


@contextmanager
def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def insert_prompt(row: dict) -> int:
    """row: dict con las columnas de la tabla (excepto id). findings como lista."""
    data = dict(row)
    data["findings_json"] = json.dumps(data.pop("findings"), ensure_ascii=False)
    data["blocked"] = int(bool(data["blocked"]))
    cols = ", ".join(data)
    marks = ", ".join(f":{k}" for k in data)
    with connect() as conn:
        cur = conn.execute(f"INSERT INTO prompts ({cols}) VALUES ({marks})", data)
        return int(cur.lastrowid)


def _counts(conn, column: str) -> dict:
    rows = conn.execute(
        f"SELECT {column} AS k, COUNT(*) AS n FROM prompts GROUP BY {column} ORDER BY n DESC"
    ).fetchall()
    return {r["k"]: r["n"] for r in rows}


def get_stats(recent_limit: int = 20) -> dict:
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]
        high = conn.execute("SELECT COUNT(*) FROM prompts WHERE risk = 'alto'").fetchone()[0]
        blocked = conn.execute("SELECT COUNT(*) FROM prompts WHERE blocked = 1").fetchone()[0]
        by_platform = _counts(conn, "platform")
        by_category = _counts(conn, "category")
        by_risk = _counts(conn, "risk")
        by_decision = _counts(conn, "user_decision")

        risk_by_platform: dict = {}
        for r in conn.execute(
            "SELECT platform, risk, COUNT(*) AS n FROM prompts GROUP BY platform, risk"
        ):
            risk_by_platform.setdefault(r["platform"], {})[r["risk"]] = r["n"]

        latency = [
            r[0]
            for r in conn.execute(
                "SELECT latency_ms FROM prompts WHERE latency_ms IS NOT NULL ORDER BY latency_ms"
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
                "SELECT * FROM prompts WHERE risk IN ('alto', 'medio') ORDER BY id DESC LIMIT ?",
                (recent_limit,),
            )
        ]

    return {
        "total": total,
        "pct_riesgo_alto": round(100 * high / total, 2) if total else 0.0,
        "bloqueados": blocked,
        "plataforma_top": next(iter(by_platform), None),
        "por_plataforma": by_platform,
        "por_categoria": by_category,
        "por_riesgo": by_risk,
        "por_decision": by_decision,
        "riesgo_por_plataforma": risk_by_platform,
        "latencia_p95_ms": p95,
        "alertas_recientes": recent,
    }
