"""Fase 5D · reentrenamiento manual (ml/retrain_from_feedback.py + backend/learning.py). Datos FALSOS.

Con embeddings simulados y un modelo «actual» falso en carpetas temporales: pytest no usa MiniLM ni toca
ml/models ni ml/reports reales.
- nada del test real ni del probe set entra al entrenamiento (y `assert_no_leak` falla si se cuela);
- mínimo de etiquetas nuevas; peso menor para las propagadas;
- un candidato peor se rechaza (con motivo) y no se guarda; uno apto queda como candidato hasta que una
  persona lo adopta; volver a la versión anterior funciona y recarga el modelo.
"""
import hashlib
import sys
import time

import joblib
import numpy as np
import pandas as pd
import pytest

import config

config.enable_ml_imports()
import retrain_from_feedback as R  # noqa: E402

LABELS = ["programacion", "academico", "redaccion", "ciberseguridad", "consulta_general", "personal"]


def emb(texts, **_):
    """384 dims: la categoría escrita como «CAT=<label>» fija la dirección; el resto es ruido pequeño fijo."""
    out = []
    for t in texts:
        v = np.zeros(384)
        for i, lab in enumerate(LABELS):
            if f"CAT={lab}" in t:
                v[i] = 1.0
        seed = int(hashlib.sha256(t.encode()).hexdigest()[:8], 16)
        v += np.random.default_rng(seed).normal(0, 0.05, 384)
        out.append(v / np.linalg.norm(v))
    return np.array(out)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("AUDITOR_REGISTRY_PATH", str(tmp_path / "registry.json"))
    monkeypatch.setenv("AUDITOR_MODELS_DIR", str(tmp_path / "models"))
    (tmp_path / "models").mkdir()
    import db
    import feedback

    data = tmp_path / "mldata"
    data.mkdir()
    monkeypatch.setattr(feedback, "DATA_ML", data)
    db.init_db()
    syn = pd.DataFrame({"text": [f"sintetico {i} CAT={lab}" for i in range(15) for lab in LABELS],
                        "label": [lab for i in range(15) for lab in LABELS]})
    probe = pd.DataFrame({"text": [f"probe {i} CAT={lab}" for i in range(3) for lab in LABELS],
                          "label": [lab for i in range(3) for lab in LABELS]})
    from sklearn.linear_model import LogisticRegression

    clf = LogisticRegression(max_iter=2000).fit(emb(list(syn["text"])), syn["label"])
    joblib.dump({"kind": "embeddings+logreg", "embedding_model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                 "clf": clf, "labels": list(clf.classes_), "C": 1.0, "version": "v2"}, tmp_path / "models" / "topic_clf.joblib")
    return {"db": db, "feedback": feedback, "data": data, "tmp": tmp_path,
            "over": {"synthetic": syn, "probe": probe, "test_real": None}}


def add(db, text, category="ciberseguridad"):
    return db.insert_prompt({
        "ts": "2026-11-20T18:00:00+00:00", "platform": "claude", "text_masked": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "length": len(text), "category": category,
        "category_conf": 0.6, "injection_score": 0.1, "risk": "bajo", "findings": [], "blocked": False,
        "user_decision": "no_aplica", "latency_ms": 10, "source": "extension"})


def label(db, eid, valor, origen="manual", padre=None):
    return db.insert_label(eid, "tema", valor, origen, "2026-11-25T00:00:00+00:00", similitud=0.9 if padre else None,
                           padre=padre)


def seed_labels(env, n=6, valor="consulta_general"):
    db = env["db"]
    ids = [add(db, f"real {i} CAT={valor}") for i in range(n)]
    first = label(db, ids[0], valor)
    for e in ids[1:]:
        label(db, e, valor, "propagado", padre=first)
    return ids


def test_no_test_real_or_probe_in_training(env):
    db, data = env["db"], env["data"]
    ok = seed_labels(env, 4)
    leak_test = add(db, "evento del test real CAT=personal")
    leak_probe = add(db, "texto idéntico a un caso del probe CAT=personal")
    label(db, leak_test, "personal")
    label(db, leak_probe, "personal")
    (data / "topic_to_label.csv").write_text(f"id,platform,text,label\n{leak_test},claude,x,\n", encoding="utf-8")
    (data / "topic_probe.csv").write_text("id,text,label,tipo,regla,nota\n"
                                          "c1,texto idéntico a un caso del probe CAT=personal,personal,positivo,1,\n",
                                          encoding="utf-8")
    rows, excluded = R.feedback_rows("tema")
    assert {leak_test, leak_probe} <= excluded
    assert {r["event_id"] for r in rows} == set(ok)
    R.assert_no_leak(rows, excluded)  # no lanza
    with pytest.raises(R.LeakError):
        R.assert_no_leak(rows + [{"event_id": leak_test, "texto": "x", "valor": "personal", "origen": "manual", "label_id": 99}], excluded)
    with pytest.raises(R.LeakError):
        R.assert_no_leak(rows + [{"event_id": 12345, "texto": "Texto idéntico a un caso del PROBE CAT=personal",
                                  "valor": "personal", "origen": "manual", "label_id": 99}], set())
    # y de punta a punta: lo que llega a train_topic no contiene ningún excluido
    seen = {}
    real = R.train_topic

    def spy(rows, *a, **k):
        seen["ids"] = {r["event_id"] for r in rows}
        return real(rows, *a, **k)

    R_train, R.train_topic = R.train_topic, spy
    try:
        R.run(min_labels=1, embed=emb, data_overrides=env["over"])
    finally:
        R.train_topic = R_train
    assert seen["ids"] == set(ok)


def test_probe_duplicates_in_synthetic_are_dropped(env):
    seed_labels(env, 3)
    over = dict(env["over"])
    over["synthetic"] = pd.concat([over["synthetic"], over["probe"].head(2)], ignore_index=True)
    rows, _ = R.feedback_rows("tema")
    cur = joblib.load(env["tmp"] / "models" / "topic_clf.joblib")
    _, report = R.train_topic(rows, cur, emb, 0.5, over["synthetic"], over["probe"], None)
    assert report["sinteticos_descartados_por_probe"] == 2


def test_minimum_new_labels(env):
    seed_labels(env, 3)
    with pytest.raises(R.NotEnoughLabels):
        R.run(min_labels=20, embed=emb, data_overrides=env["over"])


def test_propagated_get_lower_weight(env):
    seed_labels(env, 5)
    rows, _ = R.feedback_rows("tema")
    w = R.weights(rows, 0.3)
    assert sorted(w.tolist()) == [0.3, 0.3, 0.3, 0.3, 1.0]


def test_good_candidate_waits_for_human_and_rollback_works(env):
    import learning
    import registry
    from detectors import topic

    seed_labels(env, 8)
    res = R.run(min_labels=1, embed=emb, data_overrides=env["over"])
    cand = next(c for c in res["candidatos"] if c["tarea"] == "tema")
    assert cand["estado"] == "candidato" and cand["version"] == "v3" and cand["anterior"] == "v2"
    assert cand["etiquetas"] == {"manual": 1, "propagado": 7} and "probe" in cand["metricas"]
    assert (env["tmp"] / "models" / "topic_clf_v3.joblib").is_file()
    assert registry.load()["activo"]["tema"] == "v2"  # NO se adopta solo
    # Las etiquetas ya usadas por la versión candidata siguen pendientes hasta adoptarla
    assert learning.status()["estadisticas"]["pendientes"]["tema"] == 8

    assert learning.adopt("tema", "v3")
    for _ in range(100):
        if not learning.job.busy():
            break
        time.sleep(0.05)
    snap = learning.job.snapshot()
    assert snap["estado"] == "listo", snap
    assert registry.load()["activo"]["tema"] == "v3"
    assert topic.status()["path"].endswith("topic_clf_v3.joblib")
    assert learning.status()["estadisticas"]["pendientes"]["tema"] == 0  # ya las usó la versión activa

    assert learning.rollback("tema")
    for _ in range(100):
        if not learning.job.busy():
            break
        time.sleep(0.05)
    reg = registry.load()
    assert reg["activo"]["tema"] == "v2" and registry.find(reg, "tema", "v3")["estado"] == "retirado"
    assert topic.status()["path"].endswith("topic_clf.joblib")
    with pytest.raises(ValueError):
        learning.rollback("tema")  # v2 es la base: no hay anterior


def test_worse_candidate_is_rejected_and_not_saved(env, monkeypatch):
    import registry

    seed_labels(env, 4)
    cur = joblib.load(env["tmp"] / "models" / "topic_clf.joblib")
    monkeypatch.setattr(R, "train_topic", lambda *a, **k: (
        {**cur, "feedback": {"manual": 1, "propagado": 3}},
        {"probe": {"actual": 0.83, "candidato": 0.79, "n": 60}}))
    res = R.run(min_labels=1, embed=emb, data_overrides=env["over"])
    c = next(x for x in res["candidatos"] if x["tarea"] == "tema")
    assert c["estado"] == "rechazado" and "baja de 0.830 a 0.790" in c["motivo"]
    assert not (env["tmp"] / "models" / "topic_clf_v3.joblib").exists()
    assert registry.load()["activo"]["tema"] == "v2"
    with pytest.raises(ValueError):
        R.adopt("tema", "v3")  # un rechazado no se puede adoptar


def test_decide_rule():
    assert R.decide("tema", {"probe": {"actual": 0.83, "candidato": 0.825}})[0] is True  # baja 0.005: apto
    assert R.decide("tema", {"probe": {"actual": 0.83, "candidato": 0.81}})[0] is False
    ok, why = R.decide("tema", {"probe": {"actual": 0.80, "candidato": 0.85}, "test_real": {"actual": 0.7, "candidato": 0.6}})
    assert not ok and "test_real" in why
    assert R.decide("inyeccion", {"test": {"actual": {"f1": 0.92}, "candidato": {"f1": 0.915}}})[0] is True


def test_discard_candidate(env):
    import learning
    import registry

    seed_labels(env, 4)
    R.run(min_labels=1, embed=emb, data_overrides=env["over"])
    learning.discard("tema", "v3")
    assert registry.find(registry.load(), "tema", "v3")["estado"] == "descartado"
    assert not (env["tmp"] / "models" / "topic_clf_v3.joblib").exists()


def test_retrain_logs_no_text(env, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    seed_labels(env, 4)
    R.run(min_labels=1, embed=emb, data_overrides=env["over"])
    assert "real 0" not in caplog.text and "CAT=" not in caplog.text


def test_script_entry_point_reports_not_enough(env, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["retrain_from_feedback.py"])
    out = R.main(["--min", "50"])
    assert "error" in out and "No se reentrena" in capsys.readouterr().out
