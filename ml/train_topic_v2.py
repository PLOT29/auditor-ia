"""Fase 4C · Reentrena el clasificador de tema con los sintéticos v2 y lo compara con v1.

    python ml/train_topic_v2.py

1. CV 5-fold AGRUPADA (GroupKFold por base/plantilla) sobre v2 para elegir C (F1 macro), con
   el mismo pipeline que v1: TF-IDF + LR (baseline) y MiniLM + LR (class_weight="balanced").
2. Umbral de confianza: con las predicciones OUT-OF-FOLD del modelo de embeddings, el valor que
   maximiza el F1 macro de las predicciones aceptadas cubriendo ≥ 85 % de los prompts.
   (El probe set NO se usa para nada de esto.)
3. Exporta models/topic_clf.joblib y models/topic_tfidf.joblib (v2). Los v1 quedan como
   topic_clf_v1.joblib y topic_tfidf_v1.joblib.
4. Compara v1 vs. v2: CV sobre v2 (v1 se evalúa sobre v2 SIN las bases que heredó de v1, que
   ya vio al entrenar) y, al final y una sola vez, el probe set (ml/probe_eval.py).
5. Figuras en ml/reports/ y metrics.json["topic_v2"].
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ML = Path(__file__).resolve().parent
sys.path.insert(0, str(ML))
sys.stdout.reconfigure(encoding="utf-8")

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import sklearn  # noqa: E402
from sklearn.metrics import classification_report, confusion_matrix, f1_score  # noqa: E402
from sklearn.model_selection import GroupKFold, cross_val_predict, cross_val_score  # noqa: E402

import mlutils as U  # noqa: E402
import probe_eval  # noqa: E402

LABELS = U.TOPIC_LABELS
MIN_COVERAGE = 0.85


def f1m(y, p):
    return round(float(f1_score(y, p, labels=LABELS, average="macro", zero_division=0)), 4)


def per_class(y, p):
    rep = classification_report(y, p, labels=LABELS, output_dict=True, zero_division=0)
    return {c: round(rep[c]["f1-score"], 3) for c in LABELS}


def pick_confidence_threshold(y, pred, conf, min_coverage=MIN_COVERAGE):
    """Umbral que maximiza el F1 macro de las predicciones aceptadas (conf ≥ t) con cobertura ≥ min."""
    y, pred, conf = np.asarray(y), np.asarray(pred), np.asarray(conf)
    best = None
    for t in np.round(np.arange(0.0, 1.0, 0.005), 3):
        acc = conf >= t
        cov = float(acc.mean())
        if cov < min_coverage:
            break
        f = f1m(y[acc], pred[acc])
        if best is None or f > best["f1_macro_aceptadas"]:
            best = {"umbral": float(t), "cobertura": round(cov, 4), "f1_macro_aceptadas": f}
    best["f1_macro_todas"] = f1m(y, pred)
    best["criterio"] = (f"max F1 macro de las aceptadas con cobertura ≥ {min_coverage:.0%}; "
                        "predicciones out-of-fold de la CV sobre v2 (sin el probe set)")
    return best


def main() -> None:
    U.set_seeds()
    train = U.load_topic_train("v2")
    X_text, y, groups = train["text"].tolist(), train["label"].to_numpy(), train["group"].to_numpy()
    cv = GroupKFold(n_splits=5)
    print(f"v2: {len(train)} prompts, {train['group'].nunique()} grupos", flush=True)

    def grid(make, X, Cs):
        rows = []
        for C in Cs:
            s = cross_val_score(make(C), X, y, groups=groups, cv=cv, scoring="f1_macro", n_jobs=1)
            rows.append((C, float(s.mean()), float(s.std())))
        return max(rows, key=lambda r: r[1]), rows

    (C_t, f_t, sd_t), grid_t = grid(lambda C: U.tfidf_pipeline(C, class_weight="balanced"), X_text, [0.3, 1, 3, 10, 30])
    X = U.embed(X_text, cache="topic_v2_train")
    (C_e, f_e, sd_e), grid_e = grid(U.embedding_lr, X, [0.1, 0.3, 1, 3, 10, 30])
    print(f"CV tfidf  C={C_t}: F1 {f_t:.3f} ± {sd_t:.3f}", flush=True)
    print(f"CV emb    C={C_e}: F1 {f_e:.3f} ± {sd_e:.3f}", flush=True)

    # out-of-fold (predicción + confianza) para el umbral y la matriz de confusión
    oof_proba = cross_val_predict(U.embedding_lr(C_e), X, y, groups=groups, cv=cv, method="predict_proba")
    classes = sorted(set(y))
    oof_pred = np.array(classes)[oof_proba.argmax(1)]
    oof_conf = oof_proba.max(1)
    oof_tfidf = cross_val_predict(U.tfidf_pipeline(C_t, class_weight="balanced"), X_text, y, groups=groups, cv=cv)
    thr = pick_confidence_threshold(y, oof_pred, oof_conf)
    print(f"umbral de confianza: {thr}", flush=True)

    # v1 evaluado sobre v2, sin las bases heredadas de v1 (las vio al entrenar)
    v1 = joblib.load(U.MODELS / "topic_clf_v1.joblib")
    v1t = joblib.load(U.MODELS / "topic_tfidf_v1.joblib")
    fresh = ~train["group"].str.startswith("v1b").to_numpy()
    v1_pred = v1["clf"].predict(X[fresh])
    v1t_pred = v1t["pipeline"].predict([t for t, f in zip(X_text, fresh) if f])

    # exportar v2
    meta = {"seed": U.SEED, "sklearn_version": sklearn.__version__, "trained_at": datetime.now(timezone.utc).isoformat(),
            "train_data": "ml/data/topic_synthetic_v2.csv", "n_train": len(train), "version": "v2",
            "labeling_guide": "ml/LABELING.md"}
    tfidf = U.tfidf_pipeline(C_t, class_weight="balanced").fit(X_text, y)
    joblib.dump({**meta, "kind": "tfidf+logreg", "pipeline": tfidf, "labels": list(tfidf.classes_), "C": float(C_t)},
                U.MODELS / "topic_tfidf.joblib")
    lr = U.embedding_lr(C_e).fit(X, y)
    joblib.dump({**meta, "kind": "embeddings+logreg", "embedding_model": U.EMB_MODEL, "normalize_embeddings": True,
                 "clf": lr, "labels": list(lr.classes_), "C": float(C_e),
                 "confidence_threshold": thr["umbral"]}, U.MODELS / "topic_clf.joblib")

    # figuras de CV
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from sklearn.metrics import ConfusionMatrixDisplay

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.4))
    y_fresh = y[fresh]
    for ax, (title, yt, yp) in zip(axes, [
        (f"v1 sobre v2 (sin bases v1, n={fresh.sum()}) · F1 {f1m(y_fresh, v1_pred):.3f}", y_fresh, v1_pred),
        (f"v2 · CV agrupada (OOF, n={len(y)}) · F1 {f1m(y, oof_pred):.3f}", y, oof_pred),
    ]):
        ConfusionMatrixDisplay(confusion_matrix(yt, yp, labels=LABELS), display_labels=LABELS).plot(
            ax=ax, xticks_rotation=45, colorbar=False, cmap="Blues")
        ax.set_title(title, fontsize=10)
    fig.suptitle("Tema · embeddings · datos sintéticos v2")
    fig.tight_layout()
    fig.savefig(U.REPORTS / "topic_v1_vs_v2_cv.png", dpi=150)
    plt.close(fig)

    # cobertura vs. F1 según el umbral (OOF)
    ts = np.round(np.arange(0.0, 0.8, 0.01), 2)
    cov = [float((oof_conf >= t).mean()) for t in ts]
    f1s = [f1m(y[oof_conf >= t], oof_pred[oof_conf >= t]) if (oof_conf >= t).any() else np.nan for t in ts]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(ts, f1s, label="F1 macro de las aceptadas")
    ax.plot(ts, cov, label="cobertura")
    ax.axhline(MIN_COVERAGE, ls="--", c="gray", lw=1)
    ax.axvline(thr["umbral"], ls=":", c="red", lw=1, label=f"umbral {thr['umbral']}")
    ax.set_xlabel("umbral de confianza")
    ax.set_title("Tema v2 · umbral de confianza (OOF)")
    ax.legend(loc="lower left")
    fig.tight_layout()
    fig.savefig(U.REPORTS / "topic_v2_umbral_confianza.png", dpi=150)
    plt.close(fig)

    # probe set: UNA sola evaluación al final, con los modelos ya fijados
    res_probe = {
        "v2": probe_eval.evaluate(U.MODELS / "topic_clf.joblib", "v2"),
        "v2_tfidf": probe_eval.evaluate(U.MODELS / "topic_tfidf.joblib", "v2_tfidf"),
    }

    path = U.REPORTS / "metrics.json"
    allm = json.loads(path.read_text(encoding="utf-8"))
    allm["topic_v2"] = {
        "datos": {"archivo": "ml/data/topic_synthetic_v2.csv", "n": len(train), "por_clase": int(len(train) / 6),
                  "grupos": int(train["group"].nunique()), "guia": "ml/LABELING.md"},
        "cv": {"esquema": "GroupKFold(5) por base/plantilla",
               "tfidf": {"C": float(C_t), "f1_macro": round(f_t, 4), "std": round(sd_t, 4),
                         "f1_por_clase_oof": per_class(y, oof_tfidf)},
               "embeddings": {"C": float(C_e), "f1_macro": round(f_e, 4), "std": round(sd_e, 4),
                              "f1_macro_oof": f1m(y, oof_pred), "f1_por_clase_oof": per_class(y, oof_pred)},
               "grid_tfidf": [{"C": c, "f1": round(m, 4)} for c, m, _ in grid_t],
               "grid_embeddings": [{"C": c, "f1": round(m, 4)} for c, m, _ in grid_e]},
        "v1_sobre_v2_sin_bases_v1": {"n": int(fresh.sum()),
                                     "embeddings": {"f1_macro": f1m(y_fresh, v1_pred), "f1_por_clase": per_class(y_fresh, v1_pred)},
                                     "tfidf": {"f1_macro": f1m(y_fresh, v1t_pred), "f1_por_clase": per_class(y_fresh, v1t_pred)}},
        "umbral_confianza": thr,
        "probe": {k: {"f1_macro": v["f1_macro"], "accuracy_frontera": v["accuracy_frontera"],
                      "tarjeta_grafica": v["tarjeta_grafica"]["pred"]} for k, v in res_probe.items()},
        "figuras": ["ml/reports/topic_v1_vs_v2_cv.png", "ml/reports/topic_v2_umbral_confianza.png",
                    "ml/reports/topic_probe_confusion_v1.png", "ml/reports/topic_probe_confusion_v2.png"],
    }
    path.write_text(json.dumps(allm, indent=2, ensure_ascii=False), encoding="utf-8")
    print("probe v2:", {k: (v["f1_macro"], v["tarjeta_grafica"]["pred"]) for k, v in res_probe.items()}, flush=True)


if __name__ == "__main__":
    main()
