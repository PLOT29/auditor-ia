"""Evalúa un modelo de tema sobre el PROBE SET (ml/data/topic_probe.csv) — Fase 4C.

    python ml/probe_eval.py --model models/topic_clf_v1.joblib --tag v1
    python ml/probe_eval.py --model models/topic_tfidf_v1.joblib --tag v1_tfidf

- El probe set son 60 casos difíciles escritos a mano según ml/LABELING.md (10 por categoría).
  Es solo para EVALUAR al final: nunca para entrenar ni para elegir hiperparámetros o umbrales.
  No sustituye al test real (CE-4).
- La primera vez guarda su SHA-256 en metrics.json["topic_probe"]["sha256"]. Si después el
  archivo cambia, el script se niega a evaluar (para no comparar contra un probe distinto).
- Guarda metrics.json["topic_probe"]["resultados"][tag] y, para modelos de embeddings,
  una matriz de confusión ml/reports/topic_probe_confusion_<tag>.png.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ML = Path(__file__).resolve().parent
sys.path.insert(0, str(ML))
sys.stdout.reconfigure(encoding="utf-8")

import joblib  # noqa: E402
import numpy as np  # noqa: E402

import mlutils as U  # noqa: E402

PROBE = U.DATA / "topic_probe.csv"
KEY_CASE = "g01"  # "Explícame qué es una tarjeta gráfica": el caso que motivó la Fase 4C


def predict(model: dict, texts: list[str]):
    """(predicciones, probabilidades, clases) para un modelo de tema (embeddings o tfidf)."""
    if model["kind"] == "tfidf+logreg":
        clf = model["pipeline"]
        proba = clf.predict_proba(texts)
    else:
        assert model["embedding_model"] == U.EMB_MODEL
        clf = model["clf"]
        proba = clf.predict_proba(U.embed(texts))
    classes = list(clf.classes_)
    return [classes[i] for i in proba.argmax(1)], proba, classes


def check_hash() -> str:
    path = U.REPORTS / "metrics.json"
    metrics = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    sha = U.file_sha256(PROBE)
    stored = metrics.get("topic_probe", {}).get("sha256")
    if stored and stored != sha:
        sys.exit(f"ERROR: topic_probe.csv cambió (sha256 {sha[:12]}… ≠ registrado {stored[:12]}…). "
                 "El probe set es fijo: no se puede editar después de la línea base.")
    return sha


def evaluate(model_path: Path, tag: str, figure: bool = True) -> dict:
    from sklearn.metrics import classification_report, confusion_matrix, f1_score

    sha = check_hash()
    probe = U.load_topic_probe()
    model = joblib.load(model_path)
    y = probe["label"].tolist()
    pred, proba, classes = predict(model, probe["text"].tolist())
    labels = U.TOPIC_LABELS
    rep = classification_report(y, pred, labels=labels, output_dict=True, zero_division=0)
    tipo = probe["tipo"].to_numpy()
    acc = lambda m: round(float((np.array(y)[m] == np.array(pred)[m]).mean()), 4)  # noqa: E731
    key = probe.index[probe["id"] == KEY_CASE][0]
    errors = [{"id": i, "real": t, "pred": p} for i, t, p in zip(probe["id"], y, pred) if t != p]
    res = {
        "modelo": str(model_path.relative_to(ML)) if model_path.is_relative_to(ML) else str(model_path),
        "f1_macro": round(float(f1_score(y, pred, labels=labels, average="macro", zero_division=0)), 4),
        "accuracy": acc(np.ones(len(y), bool)),
        "accuracy_frontera": acc(tipo == "frontera"),
        "accuracy_positivos": acc(tipo == "positivo"),
        "f1_por_clase": {c: round(rep[c]["f1-score"], 3) for c in labels},
        "matriz_confusion": {"labels": labels, "valores": confusion_matrix(y, pred, labels=labels).tolist()},
        "tarjeta_grafica": {"pred": pred[key],
                            "proba": {c: round(float(p), 3) for c, p in zip(classes, proba[key])}},
        "errores": errors,
    }
    allm = json.loads((U.REPORTS / "metrics.json").read_text(encoding="utf-8"))
    sec = allm.setdefault("topic_probe", {})
    sec.update({"sha256": sha, "n": len(probe), "archivo": "ml/data/topic_probe.csv",
                "uso": "solo evaluación final; nunca entrenar ni elegir hiperparámetros/umbrales; no sustituye a CE-4"})
    sec.setdefault("resultados", {})[tag] = res
    (U.REPORTS / "metrics.json").write_text(json.dumps(allm, indent=2, ensure_ascii=False), encoding="utf-8")

    # Figura solo para modelos de embeddings: en esta máquina, matplotlib sin torch cargado
    # antes muere con una excepción nativa de Windows (0xc06d007f, DLL) al hacer tight_layout.
    if figure and model["kind"] != "tfidf+logreg":
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from sklearn.metrics import ConfusionMatrixDisplay

        fig, ax = plt.subplots(figsize=(7, 6))
        ConfusionMatrixDisplay(np.array(res["matriz_confusion"]["valores"]), display_labels=labels).plot(
            ax=ax, xticks_rotation=45, colorbar=False, cmap="Blues")
        ax.set_title(f"Probe set · {tag} · F1 macro {res['f1_macro']:.3f}")
        fig.tight_layout()
        fig.savefig(U.REPORTS / f"topic_probe_confusion_{tag}.png", dpi=150)
        plt.close(fig)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--tag", required=True)
    args = ap.parse_args()
    path = Path(args.model)
    path = path if path.is_absolute() else (ML / path)
    res = evaluate(path, args.tag)
    print(f"{args.tag}: F1 macro {res['f1_macro']} · acc {res['accuracy']} "
          f"(frontera {res['accuracy_frontera']}, positivos {res['accuracy_positivos']})", flush=True)
    print("F1 por clase:", res["f1_por_clase"], flush=True)
    print("tarjeta gráfica ->", res["tarjeta_grafica"], flush=True)


if __name__ == "__main__":
    main()
