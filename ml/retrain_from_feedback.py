"""Fase 5D · Reentrenamiento MANUAL con las correcciones humanas (revisor humano + aprendizaje asistido).

    python ml/retrain_from_feedback.py                 # entrena candidatos y los compara (no adopta)
    python ml/retrain_from_feedback.py --adoptar       # además adopta los que pasen la evaluación
    python ml/retrain_from_feedback.py --min 20 --peso-propagado 0.5 [--db C:\\auditor-data\\auditor.db]

Lo mismo hace el botón «Reentrenar» del centro de control (backend/learning.py), en segundo plano.

Reglas (SPEC §5.9):
1. Datos: sintéticos v2 (peso 1) + etiquetas MANUALES (peso 1) + PROPAGADAS confirmadas (peso menor,
   --peso-propagado / AUDITOR_PROPAGATED_WEIGHT, default 0.5). De cada evento cuenta su última etiqueta no
   deshecha; «otro / no sé» no se usa. El texto es `text_masked` (el original no existe).
2. Excluye SIEMPRE los eventos del test real (ml/data/topic_to_label.csv y topic_test_real.csv) y los que
   coinciden con el probe set; además se descartan sintéticos idénticos a un caso del probe. Si algo se cuela,
   `LeakError` (y un test lo verifica).
3. Mínimo de etiquetas nuevas (las de id mayor que las que usó la versión activa): --min /
   AUDITOR_RETRAIN_MIN, default 20, contando ambas tareas. Cada tarea se reentrena solo si tiene alguna nueva.
4. Tema: MiniLM + LR con la misma C que el modelo activo. Se evalúa el candidato contra el modelo ACTIVO en
   el probe set (y en el test real si ya está etiquetado). Inyección: ensamble TF-IDF + MiniLM con las mismas C,
   evaluado en el split de test de ml/data/injection.csv con el umbral vigente. Un candidato solo es apto si
   su F1 (macro, en tema) no baja más de 0.01 en NINGÚN conjunto; si no, se rechaza y se guarda el motivo.
5. Versiona: models/topic_clf_vN.joblib / injection_vN.joblib y ml/reports/model_registry.json (fecha,
   etiquetas usadas, métricas antes/después). Adoptar es un paso aparte (persona en el panel o --adoptar).
Nunca imprime ni loguea texto: solo conteos, ids y métricas.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ML = Path(__file__).resolve().parent
REPO = ML.parent
sys.path.insert(0, str(ML))
sys.path.insert(0, str(REPO / "backend"))

import numpy as np  # noqa: E402

import mlutils as U  # noqa: E402

MAX_DROP = 0.01


class LeakError(AssertionError):
    """Un evento del test real o del probe set llegó a los datos de entrenamiento."""


class NotEnoughLabels(RuntimeError):
    pass


def min_new_labels() -> int:
    return int(os.environ.get("AUDITOR_RETRAIN_MIN", "").strip() or 20)


def propagated_weight() -> float:
    return float(os.environ.get("AUDITOR_PROPAGATED_WEIGHT", "").strip() or 0.5)


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())


# ------------------------------------------------------------------ datos de retroalimentación
def feedback_rows(tarea: str) -> tuple[list[dict], set[int]]:
    """Última etiqueta no deshecha por evento (de la tarea), sin «otro», sin test real ni probe.
    Devuelve (filas con event_id, texto, valor, origen, label_id) y el conjunto de excluidos."""
    import db  # noqa: PLC0415
    import feedback  # noqa: PLC0415

    db.init_db()
    texts = db.fetch_texts()
    excluded = feedback.excluded_ids(texts)
    latest: dict[int, dict] = {}
    for r in db.fetch_labels():
        if r["tarea"] == tarea:
            latest[r["event_id"]] = r
    rows = [{"event_id": e, "texto": texts.get(e, ""), "valor": r["valor"], "origen": r["origen"], "label_id": r["id"]}
            for e, r in latest.items() if r["valor"] != "otro" and e not in excluded and e in texts]
    return rows, excluded


def assert_no_leak(rows: list[dict], excluded: set[int], extra_texts: list[str] = ()) -> None:
    """Falla si un evento excluido o un texto del probe set llegó al entrenamiento."""
    import feedback  # noqa: PLC0415

    leaked = {r["event_id"] for r in rows} & excluded
    if leaked:
        raise LeakError(f"{len(leaked)} eventos del test real o del probe set en el entrenamiento")
    probe = {_norm(t) for t in feedback.probe_texts()}
    if any(_norm(r["texto"]) in probe for r in rows) or any(_norm(t) in probe for t in extra_texts):
        raise LeakError("un texto del probe set llegó al entrenamiento")


def weights(rows: list[dict], w_prop: float) -> np.ndarray:
    return np.array([1.0 if r["origen"] == "manual" else w_prop for r in rows], dtype=float)


# ------------------------------------------------------------------ tema
def f1_macro(y, p) -> float:
    from sklearn.metrics import f1_score

    return round(float(f1_score(y, p, labels=U.TOPIC_LABELS, average="macro", zero_division=0)), 4)


def train_topic(rows: list[dict], current: dict, embed=U.embed, w_prop: float | None = None,
                synthetic=None, probe=None, test_real=None) -> tuple[dict, dict]:
    """Candidato de tema + evaluación contra el modelo activo. Devuelve (modelo, informe)."""
    import feedback  # noqa: PLC0415

    w_prop = propagated_weight() if w_prop is None else w_prop
    syn = U.load_topic_train("v2") if synthetic is None else synthetic
    probe = U.load_topic_probe() if probe is None else probe
    test_real = U.load_topic_test_real() if test_real is None else test_real
    probe_norm = {_norm(t) for t in feedback.probe_texts()} | {_norm(t) for t in probe["text"]}
    keep = [_norm(t) not in probe_norm for t in syn["text"]]
    syn = syn[keep]
    rows = [r for r in rows if r["valor"] in U.TOPIC_LABELS]
    texts = list(syn["text"]) + [r["texto"] for r in rows]
    y = np.array(list(syn["label"]) + [r["valor"] for r in rows])
    w = np.concatenate([np.ones(len(syn)), weights(rows, w_prop)])
    X = np.vstack([embed(list(syn["text"]), cache="topic_v2_train") if embed is U.embed else embed(list(syn["text"])),
                   embed([r["texto"] for r in rows]) if rows else np.zeros((0, 384))])
    lr = U.embedding_lr(current.get("C", 1.0)).fit(X, y, sample_weight=w)
    cand = {k: v for k, v in current.items() if k not in ("clf", "labels")}
    cand.update(clf=lr, labels=list(lr.classes_), n_train=len(texts), n_feedback=len(rows),
                feedback={"manual": sum(r["origen"] == "manual" for r in rows),
                          "propagado": sum(r["origen"] == "propagado" for r in rows)},
                propagated_weight=w_prop, train_data="topic_synthetic_v2.csv + correcciones humanas (Fase 5D)")
    report = {"n_sinteticos": int(len(syn)), "n_correcciones": len(rows), "sinteticos_descartados_por_probe": int(len(keep) - sum(keep))}
    sets = {"probe": probe}
    if test_real is not None and len(test_real):
        sets["test_real"] = test_real
    for name, df in sets.items():
        Xs = embed(list(df["text"]))
        report[name] = {"actual": f1_macro(df["label"], current["clf"].predict(Xs)),
                        "candidato": f1_macro(df["label"], lr.predict(Xs)), "n": int(len(df))}
    return cand, report


# ------------------------------------------------------------------ inyección
def prf(y, s, thr) -> dict:
    from sklearn.metrics import f1_score, precision_score, recall_score

    p = (np.asarray(s) >= thr).astype(int)
    return {"f1": round(float(f1_score(y, p, zero_division=0)), 4),
            "precision": round(float(precision_score(y, p, zero_division=0)), 4),
            "recall": round(float(recall_score(y, p, zero_division=0)), 4)}


def train_injection(rows: list[dict], current: dict, embed=U.embed, w_prop: float | None = None,
                    split=None) -> tuple[dict, dict]:
    """Candidato del ensamble de inyección (mismas C) + evaluación en el split de test, umbral vigente."""
    import injection_model as IM  # noqa: PLC0415

    w_prop = propagated_weight() if w_prop is None else w_prop
    train, test = U.load_injection_split() if split is None else split
    if current.get("kind") != "ensemble":
        raise RuntimeError(f"solo se reentrena el ensamble de inyección (activo: {current.get('kind')})")
    tf, em = current["members"]
    texts = list(train["text"]) + [r["texto"] for r in rows]
    y = np.array(list(train["label"]) + [1 if r["valor"] == "si" else 0 for r in rows])
    w = np.concatenate([np.ones(len(train)), weights(rows, w_prop)])
    tfidf = U.tfidf_pipeline(tf["C"], class_weight="balanced").fit(texts, y, clf__sample_weight=w)
    X = np.vstack([embed(list(train["text"]), cache="inj_train") if embed is U.embed else embed(list(train["text"])),
                   embed([r["texto"] for r in rows]) if rows else np.zeros((0, 384))])
    emb = U.embedding_lr(em["C"]).fit(X, y, sample_weight=w)
    cand = {**current, "members": [{**tf, "pipeline": tfidf}, {**em, "clf": emb}], "n_train": len(texts),
            "n_feedback": len(rows), "propagated_weight": w_prop,
            "feedback": {"manual": sum(r["origen"] == "manual" for r in rows),
                         "propagado": sum(r["origen"] == "propagado" for r in rows)}}
    thr = current["threshold"]
    texts_te = list(test["text"])
    Xte = embed(texts_te)
    report = {"umbral": thr, "n_correcciones": len(rows),
              "test": {"actual": prf(test["label"], IM.predict_proba(current, texts_te, Xte), thr),
                       "candidato": prf(test["label"], IM.predict_proba(cand, texts_te, Xte), thr),
                       "n": int(len(test))}}
    return cand, report


# ------------------------------------------------------------------ decisión y versionado
def decide(tarea: str, report: dict) -> tuple[bool, str]:
    """Apto si el F1 no baja más de MAX_DROP en ningún conjunto de evaluación."""
    sets = ["probe", "test_real"] if tarea == "tema" else ["test"]
    reasons, notes = [], []
    for name in sets:
        if name not in report:
            continue
        a, c = report[name]["actual"], report[name]["candidato"]
        a, c = (a["f1"], c["f1"]) if isinstance(a, dict) else (a, c)
        notes.append(f"{name}: {a:.3f} → {c:.3f}")
        if c < a - MAX_DROP:
            reasons.append(f"F1 en {name} baja de {a:.3f} a {c:.3f} (más de {MAX_DROP})")
    if reasons:
        return False, "Rechazado: " + "; ".join(reasons)
    return True, "Apto: " + "; ".join(notes)


def pending(tarea: str, rows: list[dict], reg: dict) -> int:
    import registry  # noqa: PLC0415

    return sum(1 for r in rows if r["label_id"] > registry.max_label_ids(reg)[tarea])


def run(min_labels: int | None = None, w_prop: float | None = None, embed=U.embed, progress=lambda *a: None,
        models_dir: Path | None = None, data_overrides: dict | None = None) -> dict:
    """Entrena y evalúa candidatos; los registra como 'candidato' o 'rechazado'. No adopta."""
    import joblib  # noqa: PLC0415

    import registry  # noqa: PLC0415

    models_dir = Path(models_dir or os.environ.get("AUDITOR_MODELS_DIR", "") or U.MODELS)
    data_overrides = data_overrides or {}
    min_labels = min_new_labels() if min_labels is None else min_labels
    reg = registry.load()
    progress("datos", "Leyendo correcciones (sin test real ni probe set)…")
    rows = {t: feedback_rows(t) for t in registry.TASKS}
    new = {t: pending(t, rows[t][0], reg) for t in registry.TASKS}
    if sum(new.values()) < min_labels:
        raise NotEnoughLabels(f"hay {sum(new.values())} etiquetas nuevas; se necesitan al menos {min_labels}")
    result = {"etiquetas_nuevas": new, "candidatos": []}
    for tarea in registry.TASKS:
        tr_rows, excluded = rows[tarea]
        if not new[tarea]:
            result["candidatos"].append({"tarea": tarea, "estado": "sin_cambios", "motivo": "no hay etiquetas nuevas"})
            continue
        assert_no_leak(tr_rows, excluded)
        active = registry.active(tarea, reg)
        current = joblib.load(models_dir / active["archivo"])
        progress(tarea, f"Entrenando {tarea} con {len(tr_rows)} correcciones…")
        try:
            if tarea == "tema":
                cand, report = train_topic(tr_rows, current, embed, w_prop, data_overrides.get("synthetic"),
                                           data_overrides.get("probe"), data_overrides.get("test_real"))
            else:
                cand, report = train_injection(tr_rows, current, embed, w_prop, data_overrides.get("injection_split"))
        except FileNotFoundError as e:
            result["candidatos"].append({"tarea": tarea, "estado": "sin_cambios",
                                         "motivo": f"faltan datos de entrenamiento ({Path(str(e.filename or e)).name})"})
            continue
        progress(tarea, "Comparando con el modelo activo…")
        ok, motivo = decide(tarea, report)
        version = registry.next_version(tarea, reg)
        archivo = f"topic_clf_{version}.joblib" if tarea == "tema" else f"injection_{version}.joblib"
        entry = {"version": version, "tarea": tarea, "archivo": archivo, "estado": "candidato" if ok else "rechazado",
                 "fecha": registry.now(), "etiquetas": cand["feedback"],
                 "max_label_id": max((r["label_id"] for r in tr_rows), default=0), "metricas": report,
                 "anterior": active["version"], "motivo": motivo}
        if ok:
            cand["version"] = version
            joblib.dump(cand, models_dir / archivo)
        reg["versiones"] = [v for v in reg["versiones"]
                            if not (v["tarea"] == tarea and v["estado"] == "candidato")] + [entry]
        registry.save(reg)
        result["candidatos"].append(entry)
    return result


def adopt(tarea: str, version: str) -> dict:
    """Marca un candidato como activo (lo que la persona confirma en el panel o con --adoptar)."""
    import registry  # noqa: PLC0415

    reg = registry.load()
    v = registry.find(reg, tarea, version)
    if not v or v["estado"] != "candidato":
        raise ValueError("solo se adopta un candidato que pasó la evaluación")
    v.update(estado="adoptado", anterior=reg["activo"][tarea], adoptado_en=registry.now())
    reg["activo"][tarea] = version
    registry.save(reg)
    return v


def rollback(tarea: str) -> dict:
    """Vuelve a la versión anterior de la ACTIVA (la activa queda «retirada»)."""
    import registry  # noqa: PLC0415

    reg = registry.load()
    cur = registry.active(tarea, reg)
    prev = cur.get("anterior")
    if not prev or not registry.find(reg, tarea, prev):
        raise ValueError("no hay una versión anterior a la que volver")
    cur.update(estado="retirado", retirado_en=registry.now())
    reg["activo"][tarea] = prev
    registry.save(reg)
    return registry.active(tarea, reg)


def main(argv=None) -> dict:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Reentrenamiento manual con las correcciones humanas (Fase 5D)")
    ap.add_argument("--min", type=int, default=None, help="mínimo de etiquetas nuevas (default 20)")
    ap.add_argument("--peso-propagado", type=float, default=None, help="peso de las propagadas (default 0.5)")
    ap.add_argument("--adoptar", action="store_true", help="adoptar los candidatos aptos")
    ap.add_argument("--db", help="ruta de la BD (default AUDITOR_DB_PATH o C:\\auditor-data\\auditor.db)")
    args = ap.parse_args(argv)
    if args.db:
        os.environ["AUDITOR_DB_PATH"] = args.db
    try:
        res = run(args.min, args.peso_propagado, progress=lambda paso, msg: print(f"[{paso}] {msg}", flush=True))
    except NotEnoughLabels as e:
        print(f"No se reentrena: {e}.")
        return {"error": str(e)}
    for c in res["candidatos"]:
        print(f"{c['tarea']}: {c.get('version', '-')} · {c['estado']} · {c['motivo']}")
        if args.adoptar and c["estado"] == "candidato":
            adopt(c["tarea"], c["version"])
            print(f"  adoptado {c['version']} (reinicia el backend o usa el panel para recargar y reclasificar)")
    print(json.dumps(res["etiquetas_nuevas"]))
    return res


if __name__ == "__main__":
    main()
