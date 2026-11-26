"""Revisor humano (Fase 5D): correcciones, deshacer y propagación por similitud.

Principios (SPEC §5.9):
- Una corrección es una fila NUEVA en `labels`; las predicciones del modelo en `prompts` nunca se borran.
  Valor efectivo = última etiqueta no deshecha > predicción del modelo (lo aplica analytics.events()).
- La propagación PROPONE vecinos (similitud coseno ≥ umbral, máx. 25) y solo guarda los que el humano
  confirma, como origen 'propagado' y con `label_padre_id` = la corrección manual.
- Excluidos SIEMPRE de la propagación y del reentrenamiento: los eventos del test real
  (ml/data/topic_to_label.csv y topic_test_real.csv) y los que coinciden con el probe set.
- Embeddings: el mismo MiniLM del backend sobre `text_masked`, con caché en la carpeta de datos
  (C:\\auditor-data\\, fuera del repo): solo ids, huellas SHA-256 del texto enmascarado y vectores; nunca texto.
- Nada de esto loguea texto ni notas: solo ids y conteos.
"""
from __future__ import annotations

import csv
import hashlib
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import config
import db
from detectors import catalog

log = logging.getLogger("auditor")

TOPICS = ("programacion", "academico", "redaccion", "ciberseguridad", "consulta_general", "personal")
TOPIC_VALUES = TOPICS + ("otro",)
INJECTION_VALUES = ("si", "no")
VALUES = {"tema": TOPIC_VALUES, "inyeccion": INJECTION_VALUES}
MAX_NOTE = 300
MAX_CANDIDATES = 25

DATA_ML = config.REPO / "ml" / "data"
_cache_lock = threading.Lock()


class FeedbackError(ValueError):
    pass


def similarity_threshold() -> float:
    return float(os.environ.get("AUDITOR_PROPAGATION_THRESHOLD", "").strip() or 0.85)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------ exclusiones (test real + probe)
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def test_real_ids() -> set[int]:
    """Ids del test real: los exportados en topic_to_label.csv (aunque no tengan etiqueta aún) y los de
    topic_test_real.csv si existe."""
    ids: set[int] = set()
    for name in ("topic_to_label.csv", "topic_test_real.csv"):
        path = DATA_ML / name
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                try:
                    ids.add(int(str(row.get("id", "")).strip()))
                except ValueError:
                    continue
    return ids


def probe_texts() -> list[str]:
    path = DATA_ML / "topic_probe.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [row["text"] for row in csv.DictReader(f) if row.get("text")]


def probe_event_ids(texts: dict[int, str] | None = None, shas: dict[int, str] | None = None) -> set[int]:
    """Eventos de la BD que son un caso del probe set (mismo texto o misma huella del original)."""
    probe = probe_texts()
    if not probe:
        return set()
    norm = {_norm(t) for t in probe}
    digests = {hashlib.sha256(t.encode("utf-8")).hexdigest() for t in probe}
    texts = db.fetch_texts() if texts is None else texts
    shas = db.fetch_shas() if shas is None else shas
    return {i for i, t in texts.items() if _norm(t) in norm} | {i for i, h in shas.items() if h in digests}


def excluded_ids(texts: dict[int, str] | None = None, shas: dict[int, str] | None = None) -> set[int]:
    return test_real_ids() | probe_event_ids(texts, shas)


# ------------------------------------------------------------------ etiquetas
def effective(event_id: int, tarea: str) -> dict | None:
    """Última etiqueta no deshecha de un evento y tarea, o None."""
    rows = [r for r in db.fetch_labels() if r["event_id"] == event_id and r["tarea"] == tarea]
    return rows[-1] if rows else None


def public(label: dict) -> dict:
    """Etiqueta para la API (la nota solo vuelve a quien la pidió; nunca se loguea)."""
    return {k: label.get(k) for k in ("id", "event_id", "tarea", "valor", "origen", "similitud_origen",
                                     "label_padre_id", "creado_en", "deshecho", "nota")}


def correct(event_id: int, tarea: str, valor: str, nota: str | None = None) -> dict:
    """Corrección MANUAL de un evento (tema o inyección)."""
    if tarea not in VALUES:
        raise FeedbackError("tarea inválida")
    if valor not in VALUES[tarea]:
        raise FeedbackError(f"valor inválido para {tarea}")
    if not db.event_exists(event_id):
        raise FeedbackError("el evento no existe")
    nota = (nota or "").strip()[:MAX_NOTE] or None
    lid = db.insert_label(event_id, tarea, valor, "manual", now(), nota=nota)
    log.info("corrección manual id=%s evento=%s tarea=%s", lid, event_id, tarea)  # sin valor ni nota
    return public(db.get_label(lid))


def undo(label_id: int, include_propagated: bool = False) -> dict:
    """Deshace una corrección. Si es manual y se pide, también sus propagadas."""
    label = db.get_label(label_id)
    if not label:
        raise FeedbackError("la etiqueta no existe")
    ids = [label_id]
    children = db.children_of(label_id) if label["origen"] == "manual" else []
    if include_propagated:
        ids += [c["id"] for c in children]
    n = db.undo_labels(ids)
    log.info("deshacer etiqueta id=%s (+%d propagadas)", label_id, n - 1 if n else 0)
    return {"deshechas": n, "propagadas_restantes": 0 if include_propagated else len(children)}


# ------------------------------------------------------------------ embeddings con caché
def cache_path() -> Path:
    from agent import state  # noqa: PLC0415  (misma carpeta de datos que agent.json, fuera del repo)

    return state.data_dir() / "embeddings_cache.npz"


def _encode(texts: list[str]) -> np.ndarray | None:
    from detectors import embeddings  # noqa: PLC0415

    return embeddings.encode(texts)


def _digest(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def event_vectors(texts: dict[int, str]) -> tuple[list[int], np.ndarray] | None:
    """Vectores normalizados de `text_masked`, recalculando solo los que no están (o cambiaron) en la caché."""
    with _cache_lock:
        path = cache_path()
        cached: dict[int, tuple[str, np.ndarray]] = {}
        if path.is_file():
            try:
                # `with`: en Windows, un .npz abierto no se puede reemplazar después (WinError 5).
                with np.load(path, allow_pickle=False) as z:
                    cached = {int(i): (str(h), np.array(v)) for i, h, v in zip(z["ids"], z["hashes"], z["vectors"])}
            except (OSError, ValueError, KeyError):
                cached = {}
        ids = sorted(texts)
        digests = {i: _digest(texts[i]) for i in ids}
        missing = [i for i in ids if i not in cached or cached[i][0] != digests[i]]
        if missing:
            X = _encode([texts[i] for i in missing])
            if X is None:
                return None
            X = np.asarray(X, dtype=np.float32)
            for i, v in zip(missing, X):
                cached[i] = (digests[i], v)
            keep = [i for i in ids if i in cached]
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp.npz")
                np.savez(tmp, ids=np.array(keep, dtype=np.int64),
                         hashes=np.array([cached[i][0] for i in keep]),
                         vectors=np.stack([cached[i][1] for i in keep]).astype(np.float32))
                tmp.replace(path)
            except OSError as e:  # sin caché en disco se sigue con lo calculado en memoria
                log.warning("no se pudo guardar la caché de embeddings (%s)", type(e).__name__)
        V = np.stack([cached[i][1] for i in ids]).astype(np.float32)
        V /= np.clip(np.linalg.norm(V, axis=1, keepdims=True), 1e-9, None)
        return ids, V


# ------------------------------------------------------------------ propagación
def _model_value(row: dict, tarea: str, inj_threshold: float | None) -> str | None:
    if tarea == "tema":
        return row.get("category")
    score = row.get("injection_score")
    if score is None or inj_threshold is None:
        return None
    return "si" if score >= inj_threshold else "no"


def candidates(label_id: int, threshold: float | None = None, limit: int = MAX_CANDIDATES,
               include_text: bool = False) -> dict:
    """Vecinos de una corrección MANUAL que podrían recibir la misma etiqueta (no se aplica nada)."""
    from detectors import injection  # noqa: PLC0415

    label = db.get_label(label_id)
    if not label or label["deshecho"]:
        raise FeedbackError("la etiqueta no existe o se deshizo")
    if label["origen"] != "manual":
        raise FeedbackError("solo se propagan correcciones manuales")
    if label["valor"] == "otro":
        return {"etiqueta": public(label), "umbral": threshold or similarity_threshold(), "candidatos": [],
                "motivo": "«otro / no sé» no se propaga"}
    thr = similarity_threshold() if threshold is None else threshold
    tarea = label["tarea"]
    texts = db.fetch_texts()
    excluded = excluded_ids(texts)
    manual = {r["event_id"] for r in db.fetch_labels() if r["tarea"] == tarea and r["origen"] == "manual"}
    vecs = event_vectors(texts)
    if vecs is None:
        raise FeedbackError("los embeddings no están disponibles (modelos sin cargar)")
    ids, V = vecs
    pos = {i: k for k, i in enumerate(ids)}
    if label["event_id"] not in pos:
        raise FeedbackError("el evento no existe")
    sims = V @ V[pos[label["event_id"]]]
    rows = {r["id"]: r for r in _event_rows()}
    current = {}
    for r in db.fetch_labels():
        if r["tarea"] == tarea:
            current[r["event_id"]] = r["valor"]
    inj_thr = injection.threshold()
    out = []
    for i, s in sorted(zip(ids, sims), key=lambda x: -x[1]):
        if i == label["event_id"] or i in excluded or i in manual or s < thr:
            continue
        actual = current.get(i) or _model_value(rows.get(i, {}), tarea, inj_thr)
        if actual == label["valor"]:
            continue  # ya tiene ese valor: no hay nada que proponer
        item = {"event_id": i, "similitud": round(float(s), 4), "actual": actual,
                "plataforma": rows.get(i, {}).get("platform"), "ts": rows.get(i, {}).get("ts")}
        if include_text:
            t = " ".join((texts.get(i) or "").split())
            item["texto"] = t if len(t) <= 140 else t[:139] + "…"
        out.append(item)
        if len(out) >= limit:
            break
    log.info("propagación: etiqueta=%s candidatos=%d umbral=%.2f", label_id, len(out), thr)
    return {"etiqueta": public(label), "umbral": thr, "maximo": limit, "candidatos": out,
            "excluidos_test_probe": len(excluded)}


def _event_rows() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute("SELECT id, ts, platform, category, injection_score FROM prompts").fetchall()
    return [dict(r) for r in rows]


def propagate(label_id: int, event_ids: list[int]) -> dict:
    """Guarda como 'propagado' SOLO los vecinos confirmados que siguen siendo candidatos válidos
    (se recalcula el conjunto: nunca se confía en la lista que manda el cliente)."""
    allowed = {c["event_id"]: c["similitud"] for c in candidates(label_id)["candidatos"]}
    label = db.get_label(label_id)
    chosen = [i for i in dict.fromkeys(event_ids) if i in allowed]
    rejected = [i for i in event_ids if i not in allowed]
    created = [db.insert_label(i, label["tarea"], label["valor"], "propagado", now(), similitud=allowed[i],
                               padre=label_id) for i in chosen]
    log.info("propagación confirmada: etiqueta=%s aplicadas=%d rechazadas=%d", label_id, len(created), len(rejected))
    return {"aplicadas": len(created), "ids": created, "rechazadas": rejected}


# ------------------------------------------------------------------ cifras de aprendizaje
def stats(max_label_ids: dict[str, int] | None = None) -> dict:
    """Correcciones (manuales vs propagadas), pendientes de usar y confusiones más corregidas."""
    labels = db.fetch_labels()
    excluded = excluded_ids()
    max_label_ids = max_label_ids or {}
    by = {"manual": 0, "propagado": 0}
    per_task = {t: {"manual": 0, "propagado": 0} for t in VALUES}
    pending = {t: 0 for t in VALUES}
    for r in labels:
        by[r["origen"]] += 1
        per_task[r["tarea"]][r["origen"]] += 1
        trainable = r["valor"] != "otro" and r["event_id"] not in excluded
        if trainable and r["id"] > max_label_ids.get(r["tarea"], 0):
            pending[r["tarea"]] += 1
    model_cat = {r["id"]: r["category"] for r in _event_rows()}
    conf: dict[str, int] = {}
    for r in labels:
        if r["tarea"] == "tema" and r["origen"] == "manual":
            before = model_cat.get(r["event_id"])
            if before and before != r["valor"]:
                key = f"{before} → {r['valor']}"
                conf[key] = conf.get(key, 0) + 1
    with db.connect() as conn:
        undone = conn.execute("SELECT COUNT(*) FROM labels WHERE deshecho = 1").fetchone()[0]
    return {"correcciones": by, "por_tarea": per_task, "pendientes": pending, "deshechas": undone,
            "confusiones": [{"de_a": k, "n": n} for k, n in sorted(conf.items(), key=lambda x: -x[1])[:8]],
            "excluidos_test_probe": len(excluded)}


def decided_by(label: dict | None, model_conf: float | None) -> str:
    if label is None:
        return f"modelo (conf. {model_conf:.2f})" if model_conf else "modelo"
    return "corregido por ti" if label["origen"] == "manual" else "propagado y confirmado"


def finding_level(findings: list[dict]) -> str:
    levels = {catalog.finding_risk(f.get("type", ""), f.get("source")) for f in findings}
    return "alto" if "alto" in levels else "medio" if levels else "bajo"
