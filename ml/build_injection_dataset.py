"""Arma ml/data/injection.csv (text,label,lang,source).

    python ml/build_injection_dataset.py

Fuentes:
  - deepset/prompt-injections (Hugging Face) — inglés y alemán, label 1 = inyección.
  - jackhhao/jailbreak-classification (Hugging Face) — inglés, type jailbreak/benign.
  - ml/injection_es.py — 100 positivos + 100 negativos propios en español.

Se unen los splits originales (el split propio se hace en los notebooks con seed fija),
se normalizan etiquetas a 0/1, se detecta el idioma (en/de/es) con una heurística de
palabras vacías y se quitan duplicados exactos (si un texto aparece con etiquetas
distintas se descarta y se reporta).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import injection_es  # noqa: E402

OUT = Path(__file__).resolve().parent / "data" / "injection.csv"

# Palabras vacías DISTINTIVAS ("es", "y", "a"… se omiten: son ambiguas entre idiomas).
STOP = {
    "en": {"the", "and", "you", "to", "of", "is", "are", "what", "how", "your", "with", "for", "that", "this", "not", "will"},
    "de": {"der", "die", "das", "und", "ist", "nicht", "ich", "sie", "du", "wie", "was", "mit", "ein", "eine", "zu", "für", "auf", "dich", "worum", "geht"},
    "es": {"el", "la", "los", "las", "que", "de", "por", "para", "con", "una", "como", "qué", "cómo", "tus", "todo", "eres", "olvida", "está", "del"},
    "fr": {"je", "le", "les", "des", "vous", "une", "pour", "est", "pas", "dans", "il", "te", "tu"},
}


def detect_lang(text: str) -> str:
    words = [w for w in re.findall(r"[a-záéíóúñäöüßàèç]+", text.lower()) if len(w) > 1]
    scores = {lang: sum(w in s for w in words) for lang, s in STOP.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "en"


def load_hf() -> list[pd.DataFrame]:
    from datasets import load_dataset

    frames = []
    ds = load_dataset("deepset/prompt-injections")
    for split in ds:
        df = ds[split].to_pandas()[["text", "label"]]
        df["source"] = "deepset/prompt-injections"
        frames.append(df)

    ds = load_dataset("jackhhao/jailbreak-classification")
    for split in ds:
        df = ds[split].to_pandas()
        df = pd.DataFrame({
            "text": df["prompt"],
            "label": (df["type"].str.lower() == "jailbreak").astype(int),
            "source": "jackhhao/jailbreak-classification",
        })
        frames.append(df)
    return frames


def main() -> None:
    frames = load_hf()
    es = pd.DataFrame(
        [{"text": t, "label": 1} for t in injection_es.POSITIVOS]
        + [{"text": t, "label": 0} for t in injection_es.NEGATIVOS]
    )
    es["source"] = "propio_es"
    es["lang"] = "es"
    df = pd.concat(frames, ignore_index=True)
    df["lang"] = df["text"].map(detect_lang)
    df = pd.concat([df, es], ignore_index=True)

    df["text"] = df["text"].astype(str).str.strip()
    df = df[df["text"].str.len() > 0]
    df["label"] = df["label"].astype(int)

    conflicts = df.groupby("text")["label"].nunique()
    bad = set(conflicts[conflicts > 1].index)
    before = len(df)
    df = df[~df["text"].isin(bad)].drop_duplicates(subset="text", keep="first")
    print(f"duplicados quitados: {before - len(df) - len(bad)} · textos con etiqueta contradictoria: {len(bad)}")

    df = df[["text", "label", "lang", "source"]].reset_index(drop=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False, encoding="utf-8")
    print(f"{len(df)} filas -> {OUT}")
    print(df.groupby(["source", "lang", "label"]).size().to_string())


if __name__ == "__main__":
    main()
