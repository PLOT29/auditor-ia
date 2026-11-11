"""Utilidades compartidas por los notebooks de ml/ (Fase 4A).

Todo con semilla fija (SEED). Los embeddings se cachean en ml/cache/ (ignorado por git)
para no recalcularlos en cada notebook.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
EMB_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
TOPIC_LABELS = ["programacion", "academico", "redaccion", "ciberseguridad", "consulta_general", "personal"]

ML = Path(__file__).resolve().parent
DATA = ML / "data"
MODELS = ML / "models"
REPORTS = ML / "reports"
CACHE = ML / "cache"
for _d in (DATA, MODELS, REPORTS, CACHE):
    _d.mkdir(parents=True, exist_ok=True)


def set_seeds(seed: int = SEED) -> None:
    random.seed(seed)
    np.random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    try:
        import torch

        torch.manual_seed(seed)
    except Exception:  # torch puede no estar disponible (TF-IDF sigue funcionando)
        pass


# ------------------------------------------------------------------ embeddings
_encoder = None


def get_encoder():
    global _encoder
    if _encoder is None:
        from sentence_transformers import SentenceTransformer

        _encoder = SentenceTransformer(EMB_MODEL, device="cpu")
    return _encoder


def embed(texts: list[str], cache: str | None = None, batch_size: int = 64) -> np.ndarray:
    """Embeddings normalizados (L2). Con `cache`, se guardan en ml/cache/<cache>-<hash>.npy."""
    texts = [str(t) for t in texts]
    path = None
    if cache:
        h = hashlib.sha256(("\n".join(texts) + EMB_MODEL).encode("utf-8")).hexdigest()[:16]
        path = CACHE / f"{cache}-{h}.npy"
        if path.exists():
            return np.load(path)
    X = get_encoder().encode(texts, batch_size=batch_size, normalize_embeddings=True,
                             show_progress_bar=False, convert_to_numpy=True)
    if path:
        np.save(path, X)
    return X


# ------------------------------------------------------------------ baseline
def tfidf_pipeline(C: float = 1.0, class_weight=None):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import FeatureUnion, Pipeline

    features = FeatureUnion([
        ("word", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=1, sublinear_tf=True,
                                 strip_accents="unicode", lowercase=True)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2, sublinear_tf=True,
                                 strip_accents="unicode", lowercase=True, max_features=100_000)),
    ])
    return Pipeline([
        ("tfidf", features),
        ("clf", LogisticRegression(C=C, max_iter=3000, class_weight=class_weight, random_state=SEED)),
    ])


def embedding_lr(C: float = 1.0):
    from sklearn.linear_model import LogisticRegression

    return LogisticRegression(C=C, max_iter=5000, class_weight="balanced", random_state=SEED)


# ------------------------------------------------------------------ datos
def load_topic_train() -> pd.DataFrame:
    df = pd.read_csv(DATA / "topic_synthetic.csv")
    df["group"] = df["source"].str.split(":").str[1]  # id de la base (para GroupKFold)
    return df


def load_topic_test_real() -> pd.DataFrame | None:
    """Test REAL etiquetado a mano (ml/data/topic_test_real.csv). None si aún no existe."""
    path = DATA / "topic_test_real.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, dtype=str).fillna("")
    df = df[df["label"].str.strip() != ""].copy()
    df["label"] = df["label"].str.strip()
    unknown = set(df["label"]) - set(TOPIC_LABELS)
    if unknown:
        raise ValueError(f"etiquetas no válidas en topic_test_real.csv: {unknown}")
    return df if len(df) else None


def injection_group(df: pd.DataFrame) -> pd.Series:
    """Grupo de evaluación: en, de, es_propio (ejemplos propios) u otros (es/fr sueltos de HF)."""
    g = df["lang"].where(df["lang"].isin(["en", "de"]), "otros")
    return g.mask(df["source"] == "propio_es", "es_propio")


def load_injection_split(test_size: float = 0.2):
    """Split 80/20 estratificado por label × grupo de idioma, con SEED. Devuelve (train, test).

    El test en español = filas `es_propio` del test (ejemplos propios, nunca vistos en train).
    """
    from sklearn.model_selection import train_test_split

    df = pd.read_csv(DATA / "injection.csv")
    df["group"] = injection_group(df)
    strat = df["label"].astype(str) + "_" + df["group"]
    tr, te = train_test_split(df, test_size=test_size, stratify=strat, random_state=SEED)
    return tr.reset_index(drop=True), te.reset_index(drop=True)


# ------------------------------------------------------------------ métricas
def pick_threshold(y_true, scores, min_recall=0.85, min_precision=0.80):
    """Umbral más ALTO con recall ≥ min_recall (maximiza precision). Indica si cumple CE-5."""
    from sklearn.metrics import precision_recall_curve

    p, r, t = precision_recall_curve(y_true, scores)
    p, r = p[:-1], r[:-1]  # alinear con t
    ok = np.where(r >= min_recall)[0]
    if len(ok) == 0:
        return {"threshold": 0.5, "precision": None, "recall": None, "meets": False}
    i = ok[np.argmax(t[ok])]
    return {"threshold": float(t[i]), "precision": float(p[i]), "recall": float(r[i]),
            "meets": bool(p[i] >= min_precision)}


def latency_ms(fn, texts: list[str], warmup: int = 5) -> dict:
    """Latencia por prompt (uno a la vez, CPU): media y p95 en ms."""
    for t in texts[:warmup]:
        fn([t])
    times = []
    for t in texts:
        t0 = time.perf_counter()
        fn([t])
        times.append((time.perf_counter() - t0) * 1000)
    a = np.array(times)
    return {"n": int(len(a)), "mean_ms": round(float(a.mean()), 2), "p95_ms": round(float(np.percentile(a, 95)), 2),
            "max_ms": round(float(a.max()), 2)}


def update_metrics(section: str, data: dict) -> None:
    """Fusiona `data` en ml/reports/metrics.json bajo `section`."""
    path = REPORTS / "metrics.json"
    allm = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    allm[section] = data
    path.write_text(json.dumps(allm, indent=2, ensure_ascii=False), encoding="utf-8")


def mask_preview(text: str, n: int = 140) -> str:
    """Vista corta para mostrar errores (los datos ya son sintéticos/públicos/enmascarados)."""
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[: n - 1] + "…"
