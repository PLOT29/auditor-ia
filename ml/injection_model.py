"""Inferencia del detector de inyección/jailbreak para todos los tipos de modelo.

Lo usan ml/select_injection.py (CV, test y exportación) y lo reutilizará la Fase 4B,
para que el score que se evalúa sea EXACTAMENTE el que se usa en producción.

Tipos (`model["kind"]`):
  tfidf+logreg                 pipeline TF-IDF (palabras + caracteres) + LR
  embeddings+logreg            MiniLM (trunca a 128 tokens) + LR
  embeddings_windows+logreg    misma LR, pero el texto se parte en ventanas de
                               WINDOW_TOKENS tokens con paso STRIDE; score = máximo de las ventanas
  ensemble                     promedio de las probabilidades de `members`
"""
from __future__ import annotations

import numpy as np

import mlutils as U

WINDOW_TOKENS = 126  # 128 de MiniLM menos [CLS] y [SEP]
STRIDE = 63          # solape del 50 %


def _tokenizer():
    return U.get_encoder().tokenizer


def windows(text: str, size: int = WINDOW_TOKENS, stride: int = STRIDE) -> list[str]:
    """Parte `text` en ventanas de `size` tokens. Un texto corto devuelve [text]."""
    tok = _tokenizer()
    ids = tok(text, add_special_tokens=False, truncation=False, verbose=False)["input_ids"]
    if len(ids) <= size:
        return [text]
    out = []
    for start in range(0, len(ids), stride):
        out.append(tok.decode(ids[start:start + size], skip_special_tokens=True))
        if start + size >= len(ids):
            break
    return out


def embed_windows(texts: list[str], cache: str | None = None, size: int = WINDOW_TOKENS,
                  stride: int = STRIDE) -> tuple[np.ndarray, np.ndarray]:
    """Embeddings de todas las ventanas + índice del texto al que pertenece cada una."""
    pieces, owner = [], []
    for i, t in enumerate(texts):
        w = windows(str(t), size, stride)
        pieces += w
        owner += [i] * len(w)
    return U.embed(pieces, cache=cache), np.asarray(owner)


def max_by_owner(scores: np.ndarray, owner: np.ndarray, n: int) -> np.ndarray:
    out = np.full(n, -np.inf)
    np.maximum.at(out, owner, scores)
    return out


def predict_proba(model: dict, texts: list[str], X_emb: np.ndarray | None = None) -> np.ndarray:
    """Probabilidad de inyección por texto.

    `X_emb` (opcional): embeddings MiniLM de `texts` ya calculados (U.embed). El backend los
    pasa para calcularlos una sola vez por prompt y reutilizarlos en tema e inyección.
    Las ventanas deslizantes siempre recalculan (necesitan un embedding por ventana).
    """
    texts = [str(t) for t in texts]
    kind = model["kind"]
    if kind == "tfidf+logreg":
        return model["pipeline"].predict_proba(texts)[:, 1]
    if kind in ("embeddings+logreg", "embeddings_windows+logreg"):
        assert model["embedding_model"] == U.EMB_MODEL, "modelo de embeddings distinto al de entrenamiento"
        if kind == "embeddings+logreg":
            X = X_emb if X_emb is not None else U.embed(texts)
            return model["clf"].predict_proba(X)[:, 1]
        Xw, owner = embed_windows(texts, size=model["window_tokens"], stride=model["stride"])
        return max_by_owner(model["clf"].predict_proba(Xw)[:, 1], owner, len(texts))
    if kind == "ensemble":
        return np.mean([predict_proba(m, texts, X_emb) for m in model["members"]], axis=0)
    raise ValueError(f"tipo de modelo desconocido: {kind}")


def predict(model: dict, texts: list[str]) -> np.ndarray:
    return (predict_proba(model, texts) >= model["threshold"]).astype(int)
