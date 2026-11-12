"""Genera los notebooks de la Fase 4A a partir de este archivo (fuente única, fácil de revisar).

    python ml/build_notebooks.py            # escribe los .ipynb
    python ml/build_notebooks.py --run      # además los ejecuta y guarda las salidas
    python ml/build_notebooks.py --run --only evaluate.ipynb   # solo evalúa (no reentrena)

Notebooks: train_topic.ipynb, train_injection.ipynb, evaluate.ipynb.

--run ejecuta las celdas EN ESTE PROCESO con IPython (sin kernel de Jupyter): en esta
máquina el kernel no arranca porque ZeroMQ falla con "Bad file descriptor (epoll.cpp:73)",
probablemente por software que intercepta sockets. Las salidas quedan guardadas en cada
.ipynb igual que con nbconvert. Con --nbconvert se usa el kernel normal.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import nbformat as nbf

ML = Path(__file__).resolve().parent
md = nbf.v4.new_markdown_cell
code = nbf.v4.new_code_cell

SETUP = """\
import sys, warnings, json, time
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, joblib, sklearn
import mlutils as U
U.set_seeds()
pd.set_option("display.max_colwidth", 120)
"""

# ------------------------------------------------------------------ train_topic
TOPIC = [
    md("# Entrenamiento · clasificador de TEMA (Fase 4A)\n\n"
       "- **Train:** `data/topic_synthetic.csv` (sintéticos, ~80 por categoría).\n"
       "- **Test:** SOLO prompts reales etiquetados a mano (`data/topic_test_real.csv`) → se evalúa en `evaluate.ipynb`.\n"
       "- Validación cruzada **5-fold agrupada** (`GroupKFold`): las variantes de un mismo prompt base nunca "
       "quedan en train y validación a la vez.\n"
       "- Baseline: TF-IDF (palabras 1–2 + caracteres 2–5) + LogisticRegression.\n"
       "- Principal: embeddings `paraphrase-multilingual-MiniLM-L12-v2` + LogisticRegression (`class_weight='balanced'`)."),
    code(SETUP),
    code("""\
train = U.load_topic_train()
test_real = U.load_topic_test_real()
print(train["label"].value_counts().to_string())
print("grupos (prompts base):", train["group"].nunique())
print("test real etiquetado:", 0 if test_real is None else len(test_real))
"""),
    md("## Baseline TF-IDF · elección de C con CV 5-fold agrupada (F1 macro)"),
    code("""\
from sklearn.model_selection import GroupKFold, cross_val_score, cross_val_predict
cv = GroupKFold(n_splits=5)
X_text, y, groups = train["text"].tolist(), train["label"].to_numpy(), train["group"].to_numpy()

def grid(make, X, Cs):
    rows = []
    for C in Cs:
        s = cross_val_score(make(C), X, y, groups=groups, cv=cv, scoring="f1_macro", n_jobs=1)
        rows.append({"C": C, "f1_macro_mean": s.mean(), "f1_macro_std": s.std()})
    return pd.DataFrame(rows)

g_tfidf = grid(lambda C: U.tfidf_pipeline(C, class_weight="balanced"), X_text, [0.3, 1, 3, 10, 30])
best_tfidf = g_tfidf.loc[g_tfidf.f1_macro_mean.idxmax()]
g_tfidf.round(4)
"""),
    md("## Embeddings + LogisticRegression · elección de C"),
    code("""\
t0 = time.perf_counter()
X_emb = U.embed(X_text, cache="topic_train")
print(f"embeddings {X_emb.shape} en {time.perf_counter()-t0:.1f}s")
g_emb = grid(U.embedding_lr, X_emb, [0.1, 0.3, 1, 3, 10, 30])
best_emb = g_emb.loc[g_emb.f1_macro_mean.idxmax()]
g_emb.round(4)
"""),
    md("## Predicciones fuera de fold (para el análisis de errores mientras no exista el test real)"),
    code("""\
oof = train[["text", "label", "source"]].copy()
oof["pred_tfidf"] = cross_val_predict(U.tfidf_pipeline(best_tfidf.C, class_weight="balanced"), X_text, y, groups=groups, cv=cv)
oof["pred_emb"] = cross_val_predict(U.embedding_lr(best_emb.C), X_emb, y, groups=groups, cv=cv)
oof.to_csv(U.CACHE / "topic_oof.csv", index=False)
print("acierto OOF  tfidf:", (oof.pred_tfidf == oof.label).mean().round(3), " emb:", (oof.pred_emb == oof.label).mean().round(3))
"""),
    md("## Entrenamiento final y exportación a `ml/models/`"),
    code("""\
from datetime import datetime, timezone
meta = {"seed": U.SEED, "sklearn_version": sklearn.__version__, "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_data": "ml/data/topic_synthetic.csv", "n_train": len(train)}

tfidf = U.tfidf_pipeline(best_tfidf.C, class_weight="balanced").fit(X_text, y)
joblib.dump({**meta, "kind": "tfidf+logreg", "pipeline": tfidf, "labels": list(tfidf.classes_),
             "C": float(best_tfidf.C)}, U.MODELS / "topic_tfidf.joblib")

lr = U.embedding_lr(best_emb.C).fit(X_emb, y)
joblib.dump({**meta, "kind": "embeddings+logreg", "embedding_model": U.EMB_MODEL, "normalize_embeddings": True,
             "clf": lr, "labels": list(lr.classes_), "C": float(best_emb.C)}, U.MODELS / "topic_clf.joblib")

U.update_metrics("topic_train", {
    "n_train": len(train), "cv": "GroupKFold(5) por prompt base",
    "tfidf": {"C": float(best_tfidf.C), "cv_f1_macro": round(float(best_tfidf.f1_macro_mean), 4), "cv_std": round(float(best_tfidf.f1_macro_std), 4)},
    "embeddings": {"C": float(best_emb.C), "cv_f1_macro": round(float(best_emb.f1_macro_mean), 4), "cv_std": round(float(best_emb.f1_macro_std), 4)},
})
sorted(p.name for p in U.MODELS.glob("topic_*.joblib"))
"""),
]

# ------------------------------------------------------------------ train_injection
INJ = [
    md("# Entrenamiento · detector de INYECCIÓN / JAILBREAK (Fase 4A)\n\n"
       "- Datos: `data/injection.csv` = deepset/prompt-injections + jackhhao/jailbreak-classification + 200 ejemplos propios en español.\n"
       "- Split 80/20 estratificado por etiqueta × grupo de idioma (seed 42). El **test en español** son los ejemplos propios del 20 %.\n"
       "- CV 5-fold estratificada en train para elegir C (métrica: average precision).\n"
       "- El **umbral** se elige con las predicciones fuera de fold del *train* (recall ≥ 0.85 con la máxima precisión); "
       "el test no se usa para elegir nada."),
    code(SETUP),
    code("""\
train, test = U.load_injection_split()
print("train", len(train), " test", len(test))
pd.crosstab([train["group"]], train["label"]).rename(columns={0: "benigno", 1: "inyección"})
"""),
    code("""\
from sklearn.model_selection import StratifiedKFold, cross_val_score, cross_val_predict
cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=U.SEED)
X_text, y = train["text"].tolist(), train["label"].to_numpy()

def grid(make, X, Cs):
    rows = []
    for C in Cs:
        s = cross_val_score(make(C), X, y, cv=cv, scoring="average_precision", n_jobs=1)
        rows.append({"C": C, "ap_mean": s.mean(), "ap_std": s.std()})
    return pd.DataFrame(rows)

g_tfidf = grid(lambda C: U.tfidf_pipeline(C, class_weight="balanced"), X_text, [0.3, 1, 3, 10, 30])
best_tfidf = g_tfidf.loc[g_tfidf.ap_mean.idxmax()]
g_tfidf.round(4)
"""),
    code("""\
t0 = time.perf_counter()
X_emb = U.embed(X_text, cache="inj_train")
print(f"embeddings {X_emb.shape} en {time.perf_counter()-t0:.1f}s  (MiniLM trunca a {U.get_encoder().max_seq_length} tokens)")
g_emb = grid(U.embedding_lr, X_emb, [0.1, 0.3, 1, 3, 10, 30])
best_emb = g_emb.loc[g_emb.ap_mean.idxmax()]
g_emb.round(4)
"""),
    md("## Umbral con predicciones fuera de fold (train)"),
    code("""\
oof_tfidf = cross_val_predict(U.tfidf_pipeline(best_tfidf.C, class_weight="balanced"), X_text, y, cv=cv, method="predict_proba")[:, 1]
oof_emb = cross_val_predict(U.embedding_lr(best_emb.C), X_emb, y, cv=cv, method="predict_proba")[:, 1]
thr_tfidf = U.pick_threshold(y, oof_tfidf)
thr_emb = U.pick_threshold(y, oof_emb)
pd.DataFrame({"tfidf": thr_tfidf, "embeddings": thr_emb}).T
"""),
    code("""\
from datetime import datetime, timezone
meta = {"seed": U.SEED, "sklearn_version": sklearn.__version__, "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_data": "ml/data/injection.csv (split 80 %)", "n_train": len(train), "positive_label": 1}

tfidf = U.tfidf_pipeline(best_tfidf.C, class_weight="balanced").fit(X_text, y)
joblib.dump({**meta, "kind": "tfidf+logreg", "pipeline": tfidf, "C": float(best_tfidf.C),
             "threshold": thr_tfidf["threshold"]}, U.MODELS / "injection_tfidf.joblib")

lr = U.embedding_lr(best_emb.C).fit(X_emb, y)
joblib.dump({**meta, "kind": "embeddings+logreg", "embedding_model": U.EMB_MODEL, "normalize_embeddings": True,
             "clf": lr, "C": float(best_emb.C), "threshold": thr_emb["threshold"]}, U.MODELS / "injection_clf.joblib")

U.update_metrics("injection_train", {
    "n_train": len(train), "n_test": len(test), "cv": "StratifiedKFold(5, shuffle, seed=42)",
    "tfidf": {"C": float(best_tfidf.C), "cv_average_precision": round(float(best_tfidf.ap_mean), 4), "threshold_from_oof": thr_tfidf},
    "embeddings": {"C": float(best_emb.C), "cv_average_precision": round(float(best_emb.ap_mean), 4), "threshold_from_oof": thr_emb},
})
sorted(p.name for p in U.MODELS.glob("injection_*.joblib"))
"""),
]

# ------------------------------------------------------------------ evaluate
EVAL = [
    md("# Evaluación (Fase 4A)\n\n"
       "- **Tema (CE-4 ≥ 0.80 F1 macro):** SOLO sobre el test real etiquetado a mano (~100 prompts, estratificado por "
       "plataforma), con IC 95 % por bootstrap (1000 remuestreos) y F1 por plataforma. Mientras no exista, se muestran "
       "métricas *provisionales* de validación cruzada sobre sintéticos, marcadas como tales (no cuentan para CE-4).\n"
       "- **Inyección (CE-5: recall ≥ 0.85 con precision ≥ 0.80):** test 20 %, por idioma; umbral fijado en train.\n"
       "- Latencia por prompt en CPU, tabla baseline vs. embeddings y 10 errores por modelo.\n"
       "- Todo se guarda en `reports/metrics.json` y `reports/*.png`."),
    code(SETUP + """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from IPython.display import Image, display

def save(fig, name):
    path = U.REPORTS / name
    fig.savefig(path, dpi=150)
    plt.close(fig)
    display(Image(filename=str(path)))

from sklearn.metrics import (f1_score, classification_report, confusion_matrix, ConfusionMatrixDisplay,
                             precision_recall_fscore_support, precision_recall_curve, average_precision_score)
topic_tfidf = joblib.load(U.MODELS / "topic_tfidf.joblib")
topic_emb = joblib.load(U.MODELS / "topic_clf.joblib")
inj_tfidf = joblib.load(U.MODELS / "injection_tfidf.joblib")
inj_emb = joblib.load(U.MODELS / "injection_clf.joblib")
assert topic_emb["embedding_model"] == inj_emb["embedding_model"] == U.EMB_MODEL

def topic_pred_tfidf(texts): return topic_tfidf["pipeline"].predict(texts)
def topic_pred_emb(texts): return topic_emb["clf"].predict(U.embed(texts))
def inj_score_tfidf(texts): return inj_tfidf["pipeline"].predict_proba(texts)[:, 1]
def inj_score_emb(texts): return inj_emb["clf"].predict_proba(U.embed(texts))[:, 1]
"""),
    md("## 1 · Tema"),
    code("""\
test_real = U.load_topic_test_real()
LABELS = U.TOPIC_LABELS

N_BOOT = 1000

def f1_macro(y_true, y_pred):
    return float(f1_score(y_true, y_pred, average="macro", labels=LABELS, zero_division=0))

def bootstrap_ci(y_true, y_pred, n_boot=N_BOOT, alpha=0.05):
    \"\"\"IC del F1 macro por bootstrap (remuestreo con reemplazo de los prompts, seed fija).\"\"\"
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    rng = np.random.default_rng(U.SEED)
    stats = [f1_macro(y_true[idx], y_pred[idx])
             for idx in (rng.integers(0, len(y_true), len(y_true)) for _ in range(n_boot))]
    lo, hi = np.percentile(stats, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {"ic95": [round(float(lo), 4), round(float(hi), 4)], "n_boot": n_boot,
            "semiamplitud": round(float(hi - lo) / 2, 4)}

def topic_block(y_true, preds, tag, platforms=None):
    out = {}
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))
    for ax, (name, y_pred) in zip(axes, preds.items()):
        y_pred = np.asarray(y_pred)
        out[name] = {"f1_macro": round(f1_macro(y_true, y_pred), 4),
                     "f1_macro_bootstrap": bootstrap_ci(y_true, y_pred),
                     "accuracy": round(float((np.array(y_true) == y_pred).mean()), 4),
                     "por_clase": {k: {m: round(v[m], 3) for m in ("precision", "recall", "f1-score", "support")}
                                   for k, v in classification_report(y_true, y_pred, labels=LABELS, output_dict=True, zero_division=0).items()
                                   if k in LABELS}}
        if platforms is not None:
            plat = np.asarray(platforms)
            out[name]["por_plataforma"] = {
                p: {"n": int((plat == p).sum()),
                    "f1_macro": round(f1_macro(np.asarray(y_true)[plat == p], y_pred[plat == p]), 4)}
                for p in sorted(set(plat))}
        ci = out[name]["f1_macro_bootstrap"]["ic95"]
        ConfusionMatrixDisplay(confusion_matrix(y_true, y_pred, labels=LABELS), display_labels=LABELS).plot(
            ax=ax, xticks_rotation=45, colorbar=False, cmap="Blues")
        ax.set_title(f"{name} · F1 macro {out[name]['f1_macro']:.3f} [IC95 {ci[0]:.2f}–{ci[1]:.2f}]")
    fig.suptitle(f"Tema — {tag}")
    fig.tight_layout()
    return out, fig

if test_real is not None:
    status = "test_real"
    preds = {"tfidf": topic_pred_tfidf(test_real.text.tolist()), "embeddings": topic_pred_emb(test_real.text.tolist())}
    topic_metrics, fig = topic_block(test_real.label.tolist(), preds, f"TEST REAL (n={len(test_real)})",
                                     platforms=test_real.platform.tolist())
    save(fig, "topic_confusion_test_real.png")
    errors_src = test_real.assign(pred_tfidf=preds["tfidf"], pred_emb=preds["embeddings"])
else:
    status = "pendiente_etiquetado"
    oof = pd.read_csv(U.CACHE / "topic_oof.csv")
    topic_metrics, fig = topic_block(oof.label.tolist(), {"tfidf": oof.pred_tfidf, "embeddings": oof.pred_emb},
                                     "PROVISIONAL: CV agrupada sobre SINTÉTICOS (no cuenta para CE-4)")
    save(fig, "topic_confusion_cv_synthetic.png")
    errors_src = oof
print("estado:", status)
pd.DataFrame({k: {"f1_macro": v["f1_macro"], "IC95 bootstrap": v["f1_macro_bootstrap"]["ic95"],
                  "± (semiamplitud)": v["f1_macro_bootstrap"]["semiamplitud"], "accuracy": v["accuracy"]}
              for k, v in topic_metrics.items()}).T
"""),
    code("""\
# F1 macro por plataforma (solo con el test real; con n pequeño por plataforma es muy inestable)
if status == "test_real":
    display(pd.DataFrame({k: {p: f"{d['f1_macro']:.3f} (n={d['n']})" for p, d in v["por_plataforma"].items()}
                          for k, v in topic_metrics.items()}))
else:
    print("F1 por plataforma: pendiente (requiere el test real etiquetado)")
"""),
    code("""\
pd.DataFrame({k: {c: v["por_clase"][c]["f1-score"] for c in LABELS} for k, v in topic_metrics.items()}).round(3)
"""),
    md("## 2 · Inyección / jailbreak (test 20 %)"),
    code("""\
_, test = U.load_injection_split()
yt = test.label.to_numpy()
scores = {"tfidf": inj_score_tfidf(test.text.tolist()), "embeddings": inj_score_emb(test.text.tolist())}
thr = {"tfidf": inj_tfidf["threshold"], "embeddings": inj_emb["threshold"]}

def prf(y, s, t):
    p, r, f, _ = precision_recall_fscore_support(y, (s >= t).astype(int), average="binary", zero_division=0)
    return {"precision": round(float(p), 4), "recall": round(float(r), 4), "f1": round(float(f), 4), "n": int(len(y)),
            "positivos": int(y.sum())}

inj_metrics = {}
for name, s in scores.items():
    m = {"threshold": round(float(thr[name]), 4), "average_precision": round(float(average_precision_score(yt, s)), 4),
         "global": prf(yt, s, thr[name])}
    for g in ["en", "de", "es_propio", "otros"]:
        mask = (test.group == g).to_numpy()
        if mask.sum():
            m[g] = prf(yt[mask], s[mask], thr[name])
    best = U.pick_threshold(yt, s)  # diagnóstico: ¿existe en test un umbral que cumpla CE-5?
    m["umbral_que_cumpliria_en_test"] = best
    m["CE5_cumple"] = bool(m["global"]["recall"] >= 0.85 and m["global"]["precision"] >= 0.80)
    m["CE5_es_cumple"] = bool(m["es_propio"]["recall"] >= 0.85 and m["es_propio"]["precision"] >= 0.80)
    inj_metrics[name] = m

rows = []
for name, m in inj_metrics.items():
    for g in ["global", "en", "de", "es_propio", "otros"]:
        if g in m:
            rows.append({"modelo": name, "grupo": g, **m[g]})
pd.DataFrame(rows)
"""),
    code("""\
fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
for ax, subset in zip(axes, ["todo el test", "español (propios)"]):
    mask = np.ones(len(test), bool) if subset == "todo el test" else (test.group == "es_propio").to_numpy()
    for name, s in scores.items():
        p, r, t = precision_recall_curve(yt[mask], s[mask])
        ax.plot(r, p, label=f"{name} (AP={average_precision_score(yt[mask], s[mask]):.3f})")
        pt = prf(yt[mask], s[mask], thr[name])
        ax.scatter([pt["recall"]], [pt["precision"]], s=40, zorder=5)
    ax.axvline(0.85, ls="--", c="gray", lw=1); ax.axhline(0.80, ls="--", c="gray", lw=1)
    ax.fill_between([0.85, 1.0], 0.80, 1.0, color="green", alpha=0.08, label="zona CE-5")
    ax.set_xlabel("recall"); ax.set_ylabel("precision"); ax.set_xlim(0, 1.01); ax.set_ylim(0, 1.01)
    ax.set_title(f"Curva PR · {subset}"); ax.legend(loc="lower left")
fig.tight_layout(); save(fig, "injection_pr_curve.png")
pd.DataFrame({k: {"umbral (train)": v["threshold"], "AP": v["average_precision"], **{f"global_{x}": v["global"][x] for x in ("precision", "recall", "f1")},
                  "CE-5": v["CE5_cumple"], "CE-5 español": v["CE5_es_cumple"]} for k, v in inj_metrics.items()}).T
"""),
    md("## 3 · Latencia de inferencia por prompt (CPU, un prompt a la vez)"),
    code("""\
import torch
torch.set_num_threads(max(1, torch.get_num_threads()))
sample = test.text.sample(200, random_state=U.SEED).tolist()
lat = {
    "topic_tfidf": U.latency_ms(topic_pred_tfidf, sample),
    "topic_embeddings": U.latency_ms(topic_pred_emb, sample),
    "injection_tfidf": U.latency_ms(inj_score_tfidf, sample),
    "injection_embeddings": U.latency_ms(inj_score_emb, sample),
    "tema+inyeccion_embeddings_compartidos": U.latency_ms(
        lambda t: (lambda X: (topic_emb["clf"].predict(X), inj_emb["clf"].predict_proba(X)))(U.embed(t)), sample),
}
lat["torch_threads"] = torch.get_num_threads()
pd.DataFrame({k: v for k, v in lat.items() if isinstance(v, dict)}).T
"""),
    md("## 4 · Baseline vs. embeddings"),
    code("""\
comp = pd.DataFrame([
    {"tarea": "tema", "métrica": f"F1 macro ({status})", "tfidf": topic_metrics["tfidf"]["f1_macro"], "embeddings": topic_metrics["embeddings"]["f1_macro"]},
    {"tarea": "inyección", "métrica": "average precision (test)", "tfidf": inj_metrics["tfidf"]["average_precision"], "embeddings": inj_metrics["embeddings"]["average_precision"]},
    {"tarea": "inyección", "métrica": "F1 global @umbral", "tfidf": inj_metrics["tfidf"]["global"]["f1"], "embeddings": inj_metrics["embeddings"]["global"]["f1"]},
    {"tarea": "inyección", "métrica": "recall español @umbral", "tfidf": inj_metrics["tfidf"]["es_propio"]["recall"], "embeddings": inj_metrics["embeddings"]["es_propio"]["recall"]},
    {"tarea": "inyección", "métrica": "precision español @umbral", "tfidf": inj_metrics["tfidf"]["es_propio"]["precision"], "embeddings": inj_metrics["embeddings"]["es_propio"]["precision"]},
    {"tarea": "latencia", "métrica": "inyección p95 ms", "tfidf": lat["injection_tfidf"]["p95_ms"], "embeddings": lat["injection_embeddings"]["p95_ms"]},
])
U.update_metrics("topic_eval", {"status": status, "n": int(len(test_real)) if test_real is not None else 0,
                               "CE4_meta": 0.80, "models": topic_metrics,
                               "CE4_cumple": (topic_metrics["embeddings"]["f1_macro"] >= 0.80) if status == "test_real" else None})
U.update_metrics("injection_eval", {"CE5_meta": {"recall": 0.85, "precision": 0.80}, "models": inj_metrics})
U.update_metrics("latency_cpu", lat)
U.update_metrics("comparison", comp.to_dict(orient="records"))
comp
"""),
    md("## 5 · Errores (10 por modelo)\n\nLos textos son sintéticos, públicos o ya enmascarados. "
       "Las hipótesis automáticas son solo una primera pista; el análisis está en `reports/error_analysis.md`."),
    code("""\
def hint_topic(text, true, pred):
    t = text.lower()
    if len(t) < 45: return "texto corto, poco contexto"
    if {true, pred} <= {"ciberseguridad", "programacion", "academico"}: return "frontera técnica: seguridad/código/teoría"
    if {true, pred} == {"personal", "consulta_general"}: return "pregunta práctica vs. situación personal"
    if "redaccion" in (true, pred): return "pide escribir SOBRE un tema: compite la tarea con el tema"
    return "vocabulario compartido entre categorías"

errs = errors_src[errors_src.label != errors_src.pred_emb].head(10)
errs_t = errors_src[errors_src.label != errors_src.pred_tfidf].head(10)
topic_errors = {
    "embeddings": [{"texto": U.mask_preview(r.text), "real": r.label, "pred": r.pred_emb, "hipotesis": hint_topic(r.text, r.label, r.pred_emb)} for r in errs.itertuples()],
    "tfidf": [{"texto": U.mask_preview(r.text), "real": r.label, "pred": r.pred_tfidf, "hipotesis": hint_topic(r.text, r.label, r.pred_tfidf)} for r in errs_t.itertuples()],
}
pd.DataFrame(topic_errors["embeddings"])
"""),
    code("pd.DataFrame(topic_errors['tfidf'])"),
    code("""\
def hint_inj(text, true, group):
    t = text.lower()
    if group == "es_propio" and true == 0: return "negativo difícil en español (habla DE seguridad/jailbreaks u 'olvida/ignora' benigno)"
    if group == "es_propio": return "ataque en español: el modelo vio pocos ejemplos en este idioma"
    if len(t) > 600: return "texto largo: MiniLM solo ve los primeros 128 tokens"
    if true == 1 and len(t) < 80: return "ataque corto/sutil sin frases típicas ('ignore', 'DAN')"
    if true == 0 and any(w in t for w in ("ignore", "forget", "pretend", "act as", "role")): return "benigno con palabras típicas de ataque"
    if group == "de": return "alemán: menos datos de entrenamiento"
    return "ataque indirecto o etiqueta discutible del dataset"

inj_errors = {}
for name, s in scores.items():
    pred = (s >= thr[name]).astype(int)
    bad = test.assign(score=s, pred=pred)[pred != yt]
    # mitad falsos negativos, mitad falsos positivos si hay
    fn, fp = bad[bad.label == 1].head(5), bad[bad.label == 0].head(5)
    pick = pd.concat([fn, fp, bad.drop(fn.index.union(fp.index))]).head(10)
    inj_errors[name] = [{"texto": U.mask_preview(r.text), "real": int(r.label), "score": round(float(r.score), 3), "grupo": r.group,
                         "hipotesis": hint_inj(r.text, r.label, r.group)} for r in pick.itertuples()]
U.update_metrics("errors", {"topic": topic_errors, "injection": inj_errors})
pd.DataFrame(inj_errors["embeddings"])
"""),
    code("pd.DataFrame(inj_errors['tfidf'])"),
]


def write(name: str, cells: list) -> Path:
    nb = nbf.v4.new_notebook()
    nb["cells"] = cells
    nb["metadata"]["kernelspec"] = {"name": "python3", "display_name": "Python 3 (auditor)", "language": "python"}
    path = ML / name
    nbf.write(nb, path)
    return path


def main() -> None:
    # --only evaluate.ipynb → escribe/ejecuta solo ese notebook (no reentrena ni toca los modelos).
    notebooks = {"train_topic.ipynb": TOPIC, "train_injection.ipynb": INJ, "evaluate.ipynb": EVAL}
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    if only and only not in notebooks:
        sys.exit(f"--only debe ser uno de: {', '.join(notebooks)}")
    paths = [write(name, cells) for name, cells in notebooks.items() if not only or name == only]
    for p in paths:
        print("escrito", p.name)
    if "--nbconvert" in sys.argv:
        jupyter = Path(sys.executable).parent / "Scripts" / "jupyter.exe"
        if not jupyter.exists():
            jupyter = Path(sys.executable).parent / "jupyter"
        for p in paths:
            print("ejecutando", p.name, "...", flush=True)
            subprocess.run([str(jupyter), "nbconvert", "--to", "notebook", "--execute", "--inplace",
                            "--ExecutePreprocessor.timeout=3600", str(p)], cwd=ML, check=True)
    elif "--run" in sys.argv:
        for p in paths:
            print("ejecutando", p.name, "...", flush=True)
            run_inprocess(p)


def run_inprocess(path: Path) -> None:
    """Ejecuta un notebook celda por celda con IPython en este proceso y guarda las salidas."""
    import os
    import traceback

    from IPython.core.interactiveshell import InteractiveShell
    from IPython.utils.capture import capture_output

    os.chdir(ML)
    InteractiveShell.clear_instance()
    shell = InteractiveShell.instance()
    nb = nbf.read(path, as_version=4)
    count = 0
    failed = None
    for cell in nb.cells:
        if cell.cell_type != "code":
            continue
        count += 1
        with capture_output(stdout=True, stderr=False, display=True) as cap:
            res = shell.run_cell(cell.source, store_history=True)
        outs = []
        if cap.stdout:
            outs.append(nbf.v4.new_output("stream", name="stdout", text=cap.stdout))
        for o in cap.outputs:
            outs.append(nbf.v4.new_output("display_data", data=o.data, metadata=o.metadata))
        if res.result is not None:
            data, meta = shell.display_formatter.format(res.result)
            outs.append(nbf.v4.new_output("execute_result", data=data, metadata=meta, execution_count=count))
        err = res.error_before_exec or res.error_in_exec
        if err is not None:
            tb = traceback.format_exception(type(err), err, err.__traceback__)
            outs.append(nbf.v4.new_output("error", ename=type(err).__name__, evalue=str(err), traceback=tb))
            failed = err
        cell.outputs = outs
        cell.execution_count = count
        if failed:
            break
    nbf.write(nb, path)
    if failed:
        raise RuntimeError(f"{path.name}: falló la celda {count}: {failed!r}")


if __name__ == "__main__":
    main()
