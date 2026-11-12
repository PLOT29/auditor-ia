"""Importa tu historial de ChatGPT, Claude o Gemini a la BD del auditor (CE-11).

    python ml/import_history.py --file "C:\\ruta\\conversations.json" --dry-run   # solo cuenta
    python ml/import_history.py --file "C:\\ruta\\conversations.json"
    python ml/import_history.py --file "C:\\ruta\\MiActividad.json"           # Gemini (Takeout)
    python ml/import_history.py --file "..." --include-attachments   # + texto extraído de adjuntos (Claude)

- Detecta el formato automáticamente:
    ChatGPT → lista de conversaciones con "mapping" (nodos con author.role == "user")
    Claude  → lista de conversaciones con "chat_messages" (sender == "human")
    Gemini  → Google Takeout › Mi actividad › Apps de Gemini (MiActividad.json /
              My Activity.json): lista de actividades {header, title, time, subtitles?,
              safeHtmlItem?} cuyo header o products menciona "Gemini".
- Extrae SOLO los mensajes del usuario, con su fecha.
    Claude: (a) bloques `content` con type == "text"; (b) si queda vacío, `text`;
            (c) con --include-attachments, además `attachments[].extracted_content`.
    Gemini: solo actividades cuyo `title` empieza con un prefijo de prompt
            (GEMINI_PROMPT_PREFIXES, ampliable con --gemini-prefix); se quita el prefijo.
            Las respuestas (safeHtmlItem, HTML) y las actividades que no son prompts
            (Gems, configuración, comentarios…) se ignoran. La fecha (`time`, ISO UTC) se
            convierte a America/Mexico_City.
- Cada mensaje pasa por el MISMO pipeline que /analyze (backend/main.py → process()):
  detección de secretos y PII, enmascarado, riesgo e inserción con
  platform = chatgpt | claude | gemini y source = 'import'.
- Toda la importación va en UNA transacción (db.transaction()): si algo falla, no queda
  nada a medias. Deduplica por text_sha256 (contra la BD y dentro del archivo).
- --dry-run no escribe nada: cuenta por mes y año, cuántos traen secretos o PII, la
  longitud mediana y cuántos se descartarían por duplicados.
- Nunca imprime ni guarda el texto original: solo conteos y fechas.
- El archivo debe estar FUERA del repo (un export contiene tus conversaciones completas).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, NamedTuple
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

LOCAL_TZ = ZoneInfo("America/Mexico_City")

# Prefijos con los que Takeout escribe un prompt en `title` (según el idioma de la cuenta).
# El orden importa: primero los más largos/específicos.
GEMINI_PROMPT_PREFIXES = [
    "Prompted ",          # inglés
    "Instrucción: ",      # español
    "Instrucción ",
    "Petición: ",
    "Preguntaste: ",
    "Pregunta: ",
    "Consulta: ",
]


class Message(NamedTuple):
    text: str
    ts: datetime
    has_files: bool = False  # trae archivos/imágenes (para explicar los mensajes vacíos)


# ------------------------------------------------------------------ formatos
def _is_gemini_activity(a) -> bool:
    if not isinstance(a, dict) or "title" not in a or "time" not in a:
        return False
    tags = [str(a.get("header", ""))] + [str(p) for p in a.get("products") or []]
    return any("gemini" in t.lower() for t in tags)


def detect_format(data) -> str:
    if isinstance(data, dict) and isinstance(data.get("conversations"), list):
        data = data["conversations"]
    if not isinstance(data, list):
        raise ValueError("se esperaba una lista de conversaciones o actividades")
    for item in data:
        if isinstance(item, dict):
            if "mapping" in item:
                return "chatgpt"
            if "chat_messages" in item:
                return "claude"
    if any(_is_gemini_activity(a) for a in data):
        return "gemini"
    raise ValueError("formato no reconocido: no es un export de ChatGPT, Claude ni Gemini (Takeout)")


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


def iter_chatgpt(convs: list, include_attachments: bool = False, skipped: Counter | None = None) -> Iterator[Message]:
    # include_attachments no aplica: el export de ChatGPT no trae el texto de los adjuntos.
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
            parts = content.get("parts") or []
            text = "\n".join(p for p in parts if isinstance(p, str))
            ts = _from_epoch(msg.get("create_time")) or conv_ts
            if ts:
                yield Message(text, ts, any(not isinstance(p, str) for p in parts))


def claude_text(msg: dict, include_attachments: bool = False) -> str:
    """(a) bloques `content` type=text → (b) campo `text` → (c) opcional: adjuntos extraídos."""
    blocks = [b.get("text") or "" for b in msg.get("content") or []
              if isinstance(b, dict) and b.get("type") == "text"]
    text = "\n".join(t for t in blocks if t.strip()).strip()
    if not text:
        text = (msg.get("text") or "").strip()
    if include_attachments:
        extra = [a["extracted_content"].strip() for a in msg.get("attachments") or []
                 if isinstance(a, dict) and isinstance(a.get("extracted_content"), str) and a["extracted_content"].strip()]
        text = "\n\n".join([t for t in [text, *extra] if t])
    return text


def iter_claude(convs: list, include_attachments: bool = False, skipped: Counter | None = None) -> Iterator[Message]:
    for conv in convs:
        conv_ts = _from_iso(conv.get("created_at"))
        for msg in conv.get("chat_messages") or []:
            if msg.get("sender") != "human":
                continue
            ts = _from_iso(msg.get("created_at")) or conv_ts
            if ts:
                has_files = bool(msg.get("files") or msg.get("attachments")) or any(
                    isinstance(b, dict) and b.get("type") not in ("text", None) for b in msg.get("content") or [])
                yield Message(claude_text(msg, include_attachments), ts, has_files)


def gemini_prompt(activity: dict, prefixes: list[str] = GEMINI_PROMPT_PREFIXES) -> str | None:
    """Texto del prompt (sin prefijo) o None si la actividad no es un prompt."""
    title = activity.get("title")
    if not isinstance(title, str):
        return None
    for p in prefixes:
        if title.startswith(p):
            return title[len(p):]
    return None


def iter_gemini(acts: list, include_attachments: bool = False, skipped: Counter | None = None,
                prefixes: list[str] = GEMINI_PROMPT_PREFIXES) -> Iterator[Message]:
    skipped = skipped if skipped is not None else Counter()
    for a in acts:
        if not _is_gemini_activity(a):
            skipped["actividades_no_gemini"] += 1
            continue
        prompt = gemini_prompt(a, prefixes)
        if prompt is None:
            skipped["actividades_no_prompt"] += 1  # respuestas sueltas, Gems, configuración…
            continue
        ts = _from_iso(a.get("time"))
        if not ts:
            skipped["sin_fecha"] += 1
            continue
        # safeHtmlItem (la respuesta de Gemini en HTML) se ignora a propósito.
        has_files = bool(a.get("attachedFiles") or a.get("imageFile"))
        yield Message(prompt, ts.astimezone(LOCAL_TZ), has_files)


ITERATORS = {"chatgpt": iter_chatgpt, "claude": iter_claude, "gemini": iter_gemini}


def load_messages(path: Path, fmt: str = "auto", include_attachments: bool = False,
                  gemini_prefixes: list[str] | None = None) -> tuple[str, list[Message], Counter]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if fmt == "auto":
        fmt = detect_format(data)
    items = data["conversations"] if isinstance(data, dict) else data
    skipped: Counter = Counter()
    kwargs = {"prefixes": gemini_prefixes} if fmt == "gemini" and gemini_prefixes else {}
    msgs = list(ITERATORS[fmt](items, include_attachments, skipped, **kwargs))
    return fmt, msgs, skipped


# ------------------------------------------------------------------ importación
def run(file: Path, fmt: str = "auto", dry_run: bool = False, include_attachments: bool = False,
        gemini_prefixes: list[str] | None = None) -> dict:
    import db  # noqa: PLC0415 — después de fijar AUDITOR_DB_PATH
    import main as backend  # noqa: PLC0415

    logging.getLogger("auditor").setLevel(logging.WARNING)  # sin una línea de log por prompt
    platform, messages, skipped = load_messages(file, fmt, include_attachments, gemini_prefixes)
    stats: Counter = Counter(skipped)
    years: Counter = Counter()
    months: Counter = Counter()
    lengths: list[int] = []
    seen: set[str] = set()
    first = last = None
    db_exists = db.db_path().exists()

    def handle(m: Message) -> None:
        nonlocal first, last
        text = m.text.strip()
        if not text:
            # Vacío en el export: no hay nada que importar. Se separa si traía archivos.
            stats["vacios_solo_archivos" if m.has_files else "vacios_sin_contenido_en_export"] += 1
            return
        if len(text) > backend.MAX_TEXT_LEN:
            stats["demasiado_largos"] += 1
            return
        first = min(first, m.ts) if first else m.ts
        last = max(last, m.ts) if last else m.ts
        years[str(m.ts.year)] += 1
        months[m.ts.strftime("%Y-%m")] += 1
        lengths.append(len(text))

        if dry_run:
            h = backend.sha256_of(text)
            if h in seen or (db_exists and db.sha256_exists(h)):
                stats["duplicados"] += 1
                return
            seen.add(h)
            stats["por_importar"] += 1
            found, _ = backend.analyze_text(text)  # mismos detectores; nada se guarda
            sources = {f.source for f in found}
            stats["con_secretos"] += "secret" in sources
            stats["con_pii"] += "pii" in sources
            return

        res = backend.process(backend.AnalyzeRequest(text=text, platform=platform, timestamp=m.ts),
                              source="import", dedupe=True)
        if res is None:
            stats["duplicados"] += 1
        else:
            stats["insertados"] += 1
            stats[f"riesgo_{res.risk}"] += 1

    if dry_run:
        for m in messages:
            handle(m)
    else:
        db.init_db()  # aplica la migración de la columna `source` si hace falta
        with db.transaction():  # todo o nada
            for m in messages:
                handle(m)

    return {
        "formato": platform,
        "dry_run": dry_run,
        "incluye_adjuntos": include_attachments,
        "mensajes_de_usuario": len(messages),
        **{k: stats[k] for k in sorted(stats)},
        "longitud_mediana": int(statistics.median(lengths)) if lengths else 0,
        "desde": first.date().isoformat() if first else None,
        "hasta": last.date().isoformat() if last else None,
        "por_anio": dict(sorted(years.items())),
        "por_mes": dict(sorted(months.items())),
    }


def main(argv: list[str] | None = None) -> dict:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", required=True, help="ruta al export (fuera del repo)")
    ap.add_argument("--format", choices=["auto", "chatgpt", "claude", "gemini"], default="auto")
    ap.add_argument("--db", help="ruta de la BD (por defecto AUDITOR_DB_PATH o C:\\auditor-data\\auditor.db)")
    ap.add_argument("--dry-run", action="store_true", help="solo cuenta; no escribe en la BD")
    ap.add_argument("--include-attachments", action="store_true",
                    help="agrega el texto extraído de los adjuntos (Claude); apagado por defecto")
    ap.add_argument("--gemini-prefix", action="append", default=[],
                    help="prefijo extra de prompt en el title de Gemini (repetible)")
    args = ap.parse_args(argv)

    file = Path(args.file).expanduser().resolve()
    if not file.is_file():
        ap.error(f"no existe el archivo: {file}")
    if REPO in file.parents:
        ap.error("el export no puede estar dentro del repo: muévelo fuera (contiene tus conversaciones)")
    if args.db:
        os.environ["AUDITOR_DB_PATH"] = args.db
    prefixes = args.gemini_prefix + GEMINI_PROMPT_PREFIXES if args.gemini_prefix else None

    summary = run(file, args.format, args.dry_run, args.include_attachments, prefixes)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    main()
