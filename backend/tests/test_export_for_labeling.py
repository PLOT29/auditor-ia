"""ml/export_for_labeling.py: muestreo estratificado por plataforma, dedupe y CSV existente."""
import csv
import importlib.util
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("export_for_labeling", REPO / "ml" / "export_for_labeling.py")
EX = importlib.util.module_from_spec(spec)
spec.loader.exec_module(EX)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db_file = tmp_path / "auditor.db"
    monkeypatch.setenv("AUDITOR_DB_PATH", str(db_file))
    import db

    db.init_db()
    n = 0

    def add(platform, text, sha=None):
        nonlocal n
        n += 1
        db.insert_prompt({"ts": "2026-11-01T00:00:00+00:00", "platform": platform, "text_masked": text,
                          "text_sha256": sha or f"{n:064d}", "length": len(text), "risk": "bajo",
                          "findings": [], "blocked": False})

    for i in range(50):
        add("chatgpt", f"prompt chatgpt {i}")
    for i in range(10):
        add("claude", f"prompt claude {i}")
    for i in range(5):
        add("gemini", f"prompt gemini {i}")
    for i in range(5):
        add("test", f"prompt de test.html {i}")
    add("claude", "duplicado", sha="d" * 64)
    add("claude", "duplicado", sha="d" * 64)
    paths = {"db": str(db_file), "out": tmp_path / "to_label.csv", "labeled": tmp_path / "real.csv"}
    return paths


def run(p, n):
    return EX.main(["--n", str(n), "--db", p["db"], "--out", str(p["out"]), "--labeled", str(p["labeled"])])


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_stratified_as_even_as_possible(setup):
    s = run(setup, 30)
    rows = read(setup["out"])
    assert list(rows[0]) == ["id", "platform", "text", "label"]
    # gemini solo tiene 5 y claude 11 distintos: el resto lo completa chatgpt
    assert s["por_plataforma"] == {"chatgpt": 14, "claude": 11, "gemini": 5}
    assert "test" not in {r["platform"] for r in rows}
    assert sum(r["text"] == "duplicado" for r in rows) <= 1  # dedupe por text_sha256
    assert all(r["label"] == "" for r in rows)


def test_even_split_when_enough(setup):
    s = run(setup, 15)
    assert s["por_plataforma"] == {"chatgpt": 5, "claude": 5, "gemini": 5}


def test_deterministic(setup):
    run(setup, 20)
    first = read(setup["out"])
    setup["out"].unlink()
    run(setup, 20)
    assert read(setup["out"]) == first


def test_respects_existing_labels(setup):
    run(setup, 12)
    rows = read(setup["out"])
    rows[0]["label"], rows[1]["label"] = "programacion", "personal"
    labeled = {rows[0]["id"]: "programacion", rows[1]["id"]: "personal"}
    with open(setup["out"], "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "platform", "text", "label"])
        w.writeheader()
        w.writerows(rows)

    s = run(setup, 20)  # ampliar no toca lo etiquetado
    after = {r["id"]: r for r in read(setup["out"])}
    assert len(after) == 20 and s["ya_etiquetadas_conservadas"] == 2 and s["nuevas"] == 8
    for i, lab in labeled.items():
        assert after[i]["label"] == lab
    assert {r["id"] for r in rows} <= set(after)  # las 12 anteriores siguen

    s = run(setup, 1)  # aunque --n sea menor, nunca se pierden etiquetas
    assert {i for i, r in ((r["id"], r) for r in read(setup["out"])) if r["label"]} == set(labeled)


def test_old_csv_without_platform_column(setup):
    with open(setup["out"], "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "text", "label"])
        w.writerow(["51", "prompt claude 0", "academico"])  # formato viejo
    run(setup, 5)
    rows = {r["id"]: r for r in read(setup["out"])}
    assert rows["51"]["platform"] == "claude" and rows["51"]["label"] == "academico"


def test_skips_ids_already_in_final_test(setup):
    run(setup, 65)
    ids = [r["id"] for r in read(setup["out"])]
    with open(setup["labeled"], "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "platform", "text", "label"])
        w.writerow([ids[0], "chatgpt", "x", "personal"])
    setup["out"].unlink()
    run(setup, 65)
    assert ids[0] not in {r["id"] for r in read(setup["out"])}
    assert Counter(r["platform"] for r in read(setup["out"]))["test"] == 0
