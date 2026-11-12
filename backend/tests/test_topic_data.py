"""Fase 4C: higiene del probe set (nunca se usa para entrenar ni para elegir umbrales)."""
import csv
import hashlib
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "ml" / "data"
PROBE = DATA / "topic_probe.csv"
METRICS = REPO / "ml" / "reports" / "metrics.json"
LABELS = {"programacion", "academico", "redaccion", "ciberseguridad", "consulta_general", "personal"}


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def norm(t):
    return " ".join(t.lower().split())


def test_probe_has_60_cases_10_per_category():
    rows = read(PROBE)
    assert len(rows) == 60
    counts = {c: sum(r["label"] == c for r in rows) for c in LABELS}
    assert set(counts.values()) == {10}
    assert any("tarjeta gráfica" in r["text"] and r["label"] == "consulta_general" for r in rows)


def test_probe_hash_matches_metrics():
    stored = json.loads(METRICS.read_text(encoding="utf-8"))["topic_probe"]["sha256"]
    assert hashlib.sha256(PROBE.read_bytes()).hexdigest() == stored, "el probe set cambió después de la línea base"


@pytest.mark.parametrize("train_file", ["topic_synthetic_v1.csv", "topic_synthetic_v2.csv"])
def test_probe_texts_not_in_training_data(train_file):
    probe = {norm(r["text"]) for r in read(PROBE)}
    train = {norm(r["text"]) for r in read(DATA / train_file)}
    assert not probe & train


def test_v2_balanced_and_valid_labels():
    rows = read(DATA / "topic_synthetic_v2.csv")
    assert {r["label"] for r in rows} == LABELS
    counts = {c: sum(r["label"] == c for r in rows) for c in LABELS}
    assert set(counts.values()) == {150}
    assert all(r["source"].startswith("v2:") for r in rows)
