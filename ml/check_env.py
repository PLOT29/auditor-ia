"""Valida el entorno de ML antes de entrenar (Fase 4A).

    conda activate auditor
    python ml/check_env.py

Importa cada librería, carga es_core_news_md y el modelo de embeddings, y hace una
inferencia de prueba. Si Windows (Smart App Control) bloquea una DLL, lo indica
explícitamente en vez de solo fallar. Sale con código 1 si algo crítico falla.
"""
from __future__ import annotations

import importlib
import sys
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8")

LIBS = ["numpy", "pandas", "sklearn", "joblib", "matplotlib", "jupyter", "nbformat", "datasets",
        "torch", "sentence_transformers", "spacy", "presidio_analyzer"]
CRITICAL_FOR_TFIDF = {"numpy", "pandas", "sklearn", "joblib", "matplotlib"}

results: dict[str, str] = {}


def blocked(err: BaseException) -> bool:
    msg = str(err).lower()
    return "dll load failed" in msg or "control de aplicaciones" in msg or "application control" in msg


def check(name: str, fn) -> bool:
    t0 = time.perf_counter()
    try:
        info = fn()
        results[name] = f"OK   {info or ''} ({(time.perf_counter() - t0):.1f}s)"
        return True
    except Exception as e:  # noqa: BLE001
        tag = "BLOQUEADO por Windows (Smart App Control / DLL)" if blocked(e) else "FALLA"
        results[name] = f"{tag}: {type(e).__name__}: {str(e).splitlines()[0][:160]}"
        if "--verbose" in sys.argv:
            traceback.print_exc()
        return False


for lib in LIBS:
    check(lib, lambda lib=lib: getattr(importlib.import_module(lib), "__version__", ""))


def spacy_es():
    import spacy

    nlp = spacy.load("es_core_news_md")
    ents = [(e.text, e.label_) for e in nlp("María López vive en Guadalajara, Jalisco.").ents]
    return f"ents={ents}"


def embeddings():
    from sentence_transformers import SentenceTransformer

    m = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", device="cpu")
    v = m.encode(["Explícame qué es un firewall", "What is a firewall?"], normalize_embeddings=True)
    return f"dim={v.shape[1]} sim(es,en)={float(v[0] @ v[1]):.2f}"


def presidio_es():
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    nlp = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy", "models": [{"lang_code": "es", "model_name": "es_core_news_md"}]}).create_engine()
    an = AnalyzerEngine(nlp_engine=nlp, supported_languages=["es"])
    res = an.analyze("Escríbele a Juan Pérez", language="es")
    return f"entidades={[r.entity_type for r in res]}"


if results["spacy"].startswith("OK"):
    check("es_core_news_md", spacy_es)
if results["sentence_transformers"].startswith("OK") and results["torch"].startswith("OK"):
    check("embeddings MiniLM", embeddings)
if results["presidio_analyzer"].startswith("OK") and results.get("es_core_news_md", "").startswith("OK"):
    check("presidio (es)", presidio_es)

width = max(map(len, results))
for k, v in results.items():
    print(f"{k:<{width}}  {v}")

bad = [k for k, v in results.items() if not v.startswith("OK")]
tfidf_ok = all(results[k].startswith("OK") for k in CRITICAL_FOR_TFIDF)
emb_ok = results.get("embeddings MiniLM", "").startswith("OK")
print()
if not bad:
    print("Entorno completo: TF-IDF + embeddings + Presidio.")
elif tfidf_ok and not emb_ok:
    print("AVISO: torch/sentence-transformers no disponibles -> continuar solo con TF-IDF.")
else:
    print(f"Faltan: {bad}")
sys.exit(0 if tfidf_ok else 1)
