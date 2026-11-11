"""Importa tu historial de ChatGPT o Claude a la BD del auditor (CE-11).

    python ml/import_history.py --file "C:\\ruta\\conversations.json"
    python ml/import_history.py --file "...\\conversations.json" --dry-run   # solo cuenta

- Detecta el formato automáticamente:
    ChatGPT → lista de conversaciones con "mapping" (nodos con author.role == "user")
    Claude  → lista de conversaciones con "chat_messages" (sender == "human")
- Extrae SOLO los mensajes del usuario, con su fecha.
- Cada mensaje pasa por el MISMO pipeline que /analyze (backend/main.py → process()):
  detección de secretos y PII, enmascarado, riesgo e inserción en la BD con
  platform = chatgpt | claude y source = 'import'.
- Deduplica por text_sha256 (contra la BD y dentro del mismo archivo), así que se puede
  volver a correr sin duplicar.
- Nunca imprime ni guarda el texto original: solo conteos y fechas.
- El archivo debe estar FUERA del repo (un export contiene tus conversaciones completas).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

Message = tuple[str, datetime]


# ------------------------------------------------------------------ formatos
def detect_format(data) -> str:
    if isinstance(data, dict) and isinstance(data.get("conversations"), list):
        data = data["conversations"]
    if not isinstance(data, list):
        raise ValueError("se esperaba una lista de conversaciones")
    for conv in data:
        if isinstance(conv, dict):
            if "mapping" in conv:
                return "chatgpt"
            if "chat_messages" in conv:
                return "claude"
    raise ValueError("formato no reconocido: no es un export de ChatGPT ni de Claude")


def _from_epoch(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc) if value is not None else None
    except (TypeError, ValueError, OSError):
        return None


def _from_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def iter_chatgpt(convs: list) -> Iterator[Message]:
    for conv in convs:
        conv_ts = _from_epoch(conv.get("create_time"))
        for node in (conv.get("mapping") or {}).values():
            msg = (node or {}).get("message") or {}
            if (msg.get("author") or {}).get("role") != "user":
                continue
            if (msg.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
                continue  # instrucciones personalizadas y contexto oculto: no los escribió en el chat
            content = msg.get("content") or {}
            if content.get("content_type") not in ("text", "multimodal_text"):
                continue
            text = "\n".join(p for p in content.get("parts") or [] if isinstance(p, str))
            ts = _from_epoch(msg.get("create_time")) or conv_ts
            if ts:
                yield text, ts


def iter_claude(convs: list) -> Iterator[Message]:
    for conv in convs:
        conv_ts = _from_iso(conv.get("created_at"))
        for msg in conv.get("chat_messages") or []:
            if msg.get("sender") != "human":
                continue
            text = msg.get("text") or ""
            if not text.strip():
                text = "\n".join(c.get("text", "") for c in msg.get("content") or []
                                 if isinstance(c, dict) and c.get("type") == "text")
            ts = _from_iso(msg.get("created_at")) or conv_ts
            if ts:
                yield text, ts


def load_messages(path: Path, fmt: str = "auto") -> tuple[str, list[Message]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if fmt == "auto":
        fmt = detect_format(data)
    convs = data["conversations"] if isinstance(data, dict) else data
    it = iter_chatgpt(convs) if fmt == "chatgpt" else iter_claude(convs)
    return fmt, list(it)


# ------------------------------------------------------------------ importación
def run(file: Path, fmt: str = "auto", dry_run: bool = False) -> dict:
    import db  # noqa: PLC0415 — después de fijar AUDITOR_DB_PATH
    import main as backend  # noqa: PLC0415

    logging.getLogger("auditor").setLevel(logging.WARNING)  # sin una línea de log por prompt
    platform, messages = load_messages(file, fmt)
    stats = Counter()
    years = Counter()
    first = last = None
    if not dry_run:
        db.init_db()  # aplica la migración de la columna `source` si hace falta

    for text, ts in messages:
        text = text.strip()
        if not text:
            stats["vacios"] += 1
            continue
        if len(text) > backend.MAX_TEXT_LEN:
            stats["demasiado_largos"] += 1
            continue
        first = min(first, ts) if first else ts
        last = max(last, ts) if last else ts
        if dry_run:
            stats["por_importar"] += 1
            continue
        req = backend.AnalyzeRequest(text=text, platform=platform, timestamp=ts)
        res = backend.process(req, source="import", dedupe=True)
        if res is None:
            stats["duplicados"] += 1
        else:
            stats["insertados"] += 1
            stats[f"riesgo_{res.risk}"] += 1
            years[ts.year] += 1

    return {
        "formato": platform,
        "mensajes_de_usuario": len(messages),
        **dict(stats),
        "por_anio": dict(sorted(years.items())),
        "desde": first.date().isoformat() if first else None,
        "hasta": last.date().isoformat() if last else None,
        "dry_run": dry_run,
    }


def main(argv: list[str] | None = None) -> dict:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", required=True, help="ruta a conversations.json (fuera del repo)")
    ap.add_argument("--format", choices=["auto", "chatgpt", "claude"], default="auto")
    ap.add_argument("--db", help="ruta de la BD (por defecto AUDITOR_DB_PATH o C:\\auditor-data\\auditor.db)")
    ap.add_argument("--dry-run", action="store_true", help="solo cuenta; no escribe en la BD")
    args = ap.parse_args(argv)

    file = Path(args.file).expanduser().resolve()
    if not file.is_file():
        ap.error(f"no existe el archivo: {file}")
    if REPO in file.parents:
        ap.error("el export no puede estar dentro del repo: muévelo fuera (contiene tus conversaciones)")
    if args.db:
        os.environ["AUDITOR_DB_PATH"] = args.db

    summary = run(file, args.format, args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    main()
