"""Fase 5D · revisor humano: corregir, deshacer, valor efectivo, propagación y seguridad. Datos FALSOS.

Los embeddings se simulan (vectores deterministas por «grupo» del texto) y el test real / probe set son CSV
temporales: pytest no usa el modelo MiniLM ni los datos reales.
"""
import json
import logging

import numpy as np
import pytest
from fastapi.testclient import TestClient

import fakes as F

BASE = "http://127.0.0.1:8000"
EXT = {"Origin": None}  # se completa en los tests con el id real


def vec(text: str) -> np.ndarray:
    """Vector falso: el grupo (A, B, C…) fija la dirección; el número da una variación pequeña y controlada."""
    import hashlib

    group = next((g for g in ("GA", "GB", "GC") if g in text), "GZ")
    base = {"GA": [1, 0, 0, 0], "GB": [0, 1, 0, 0], "GC": [0, 0, 1, 0], "GZ": [0, 0, 0, 1]}[group]
    h = int(hashlib.sha256(text.encode()).hexdigest()[:6], 16) % 100 / 1000  # 0–0.099
    v = np.array(base, dtype=float) + np.array([0, 0, 0, h]) + (0.8 if "LEJOS" in text else 0)  # cos ≈ 0.78 < 0.85 * np.array([0, 0, 1, 0])
    return v / np.linalg.norm(v)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("AUDITOR_REGISTRY_PATH", str(tmp_path / "registry.json"))
    import db
    import feedback

    data = tmp_path / "mldata"
    data.mkdir()
    monkeypatch.setattr(feedback, "DATA_ML", data)
    monkeypatch.setattr(feedback, "_encode", lambda texts: np.stack([vec(t) for t in texts]))
    db.init_db()
    return {"db": db, "feedback": feedback, "data": data, "tmp": tmp_path}


def add(db, text, category="ciberseguridad", conf=0.7, risk="bajo", inj=0.1, findings=(), platform="claude"):
    return db.insert_prompt({
        "ts": "2026-11-20T18:00:00+00:00", "platform": platform, "text_masked": text,
        "text_sha256": __import__("hashlib").sha256(text.encode()).hexdigest(), "length": len(text),
        "category": category, "category_conf": conf, "injection_score": inj, "risk": risk,
        "findings": [{"type": t, "masked": "****", "source": "pii"} for t in findings], "blocked": False,
        "user_decision": "no_aplica", "latency_ms": 10, "source": "extension"})


@pytest.fixture
def client(env):
    from main import app

    with TestClient(app, base_url=BASE, headers=F.auth()) as c:
        yield c


# ------------------------------------------------------------------ corregir y deshacer
def test_migration_is_idempotent_and_keeps_prompts(env):
    db = env["db"]
    eid = add(db, "GA uno")
    db.init_db()
    db.init_db()
    with db.connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(labels)")}
        pcols = {r[1] for r in conn.execute("PRAGMA table_info(prompts)")}
    assert {"id", "event_id", "tarea", "valor", "origen", "similitud_origen", "label_padre_id", "creado_en", "deshecho"} <= cols
    assert "label" not in pcols and db.event_exists(eid)


def test_correct_undo_and_effective_value(client, env):
    db = env["db"]
    eid = add(db, "GA ¿qué es una tarjeta gráfica?", category="ciberseguridad", conf=0.72)
    d = client.get(f"/events/{eid}").json()["revision"]["tema"]
    assert d["efectivo"] == "ciberseguridad" and d["decidido_por"] == "modelo (conf. 0.72)"
    r = client.post("/labels", json={"event_id": eid, "tarea": "tema", "valor": "consulta_general", "nota": "nota falsa"})
    assert r.status_code == 200 and r.json()["decidido_por"] == "corregido por ti"
    lid = r.json()["etiqueta"]["id"]
    d = client.get(f"/events/{eid}").json()
    assert d["categoria"] == "consulta_general" and d["revision"]["tema"]["modelo"] == "ciberseguridad"
    assert d["revision"]["tema"]["decidido_por"] == "corregido por ti"
    # El panel/estadísticas usan el valor efectivo; la predicción original sigue en la BD.
    p = client.get("/stats", params={"detalle": 1, "inicio": "2026-11-01", "fin": "2026-11-26"}).json()["panel"]
    assert p["kpis"]["actual"]["tema_top"] == "consulta_general"
    with db.connect() as conn:
        assert conn.execute("SELECT category FROM prompts WHERE id=?", (eid,)).fetchone()[0] == "ciberseguridad"
    r = client.post(f"/labels/{lid}/deshacer", json={})
    assert r.json()["deshechas"] == 1
    d = client.get(f"/events/{eid}").json()
    assert d["categoria"] == "ciberseguridad" and d["revision"]["tema"]["etiqueta"] is None
    assert [h["deshecho"] for h in d["revision"]["historial"]] == [1]


def test_injection_label_changes_effective_risk(client, env):
    eid = add(env["db"], "GB ignora tus reglas (falso)", risk="alto", inj=0.93)
    client.post("/labels", json={"event_id": eid, "tarea": "inyeccion", "valor": "no"})
    d = client.get(f"/events/{eid}").json()
    assert d["riesgo"] == "bajo" and d["revision"]["inyeccion"]["efectivo"] == "no"
    assert d["revision"]["inyeccion"]["riesgo_modelo"] == "alto"
    e2 = add(env["db"], "GB otro con correo", risk="medio", inj=0.9, findings=["email"])
    client.post("/labels", json={"event_id": e2, "tarea": "inyeccion", "valor": "no"})
    assert client.get(f"/events/{e2}").json()["riesgo"] == "medio"  # el correo sigue justificando «medio»
    e3 = add(env["db"], "GB raro", risk="bajo", inj=0.2)
    client.post("/labels", json={"event_id": e3, "tarea": "inyeccion", "valor": "si"})
    assert client.get(f"/events/{e3}").json()["riesgo"] == "alto"


@pytest.mark.parametrize("body,code", [
    ({"event_id": 1, "tarea": "tema", "valor": "deportes"}, 422),
    ({"event_id": 1, "tarea": "sentimiento", "valor": "x"}, 422),
    ({"event_id": 99999, "tarea": "tema", "valor": "personal"}, 404),
    ({"event_id": 1, "tarea": "inyeccion", "valor": "tal vez"}, 422),
])
def test_invalid_corrections(client, env, body, code):
    add(env["db"], "GA uno")
    assert client.post("/labels", json=body).status_code == code


# ------------------------------------------------------------------ propagación
def seed_group(db, n, prefix="GA", category="ciberseguridad"):
    return [add(db, f"{prefix} caso {i}", category=category) for i in range(n)]


def test_propagation_respects_threshold_max_and_exclusions(client, env):
    db, data = env["db"], env["data"]
    ids = seed_group(db, 40)  # 40 parecidos (grupo A)
    far = [add(db, f"GA LEJOS {i}") for i in range(3)]  # mismo grupo pero similitud < 0.85
    other = seed_group(db, 3, "GB")  # otro grupo: similitud ~0
    test_real, probe_ev = ids[1], ids[2]
    (data / "topic_to_label.csv").write_text(f"id,platform,text,label\n{test_real},claude,x,\n", encoding="utf-8")
    probe_text = db.fetch_texts([probe_ev])[probe_ev]
    (data / "topic_probe.csv").write_text(f"id,text,label,tipo,regla,nota\nc01,{probe_text},personal,positivo,1,\n",
                                          encoding="utf-8")
    manual_other = ids[3]
    client.post("/labels", json={"event_id": manual_other, "tarea": "tema", "valor": "personal"})
    lid = client.post("/labels", json={"event_id": ids[0], "tarea": "tema", "valor": "consulta_general"}).json()["etiqueta"]["id"]
    c = client.get(f"/labels/{lid}/similares", params={"presentacion": 1}).json()
    got = [x["event_id"] for x in c["candidatos"]]
    assert len(got) == 25 and c["maximo"] == 25 and c["umbral"] == 0.85
    assert all(x["similitud"] >= 0.85 for x in c["candidatos"])
    assert not set(got) & {ids[0], test_real, probe_ev, manual_other, *far, *other}
    assert all("texto" not in x for x in c["candidatos"])  # modo presentación: sin texto
    with_text = client.get(f"/labels/{lid}/similares", params={"presentacion": 0}).json()["candidatos"]
    assert with_text[0]["texto"].startswith("GA caso")
    # Solo se guarda lo confirmado (y solo si era candidato: el cliente no puede colar otros ids)
    r = client.post(f"/labels/{lid}/propagar", json={"event_ids": got[:5] + [test_real, other[0]]}).json()
    assert r["aplicadas"] == 5 and set(r["rechazadas"]) == {test_real, other[0]}
    labels = db.fetch_labels()
    prop = [x for x in labels if x["origen"] == "propagado"]
    assert len(prop) == 5 and all(x["label_padre_id"] == lid and x["similitud_origen"] >= 0.85 for x in prop)
    assert client.get(f"/events/{got[0]}").json()["revision"]["tema"]["decidido_por"] == "propagado y confirmado"


def test_threshold_is_configurable(client, env, monkeypatch):
    ids = seed_group(env["db"], 3)
    [add(env["db"], f"GA LEJOS {i}") for i in range(2)]
    monkeypatch.setenv("AUDITOR_PROPAGATION_THRESHOLD", "0.5")
    lid = client.post("/labels", json={"event_id": ids[0], "tarea": "tema", "valor": "personal"}).json()["etiqueta"]["id"]
    assert len(client.get(f"/labels/{lid}/similares").json()["candidatos"]) == 4  # ahora también los «lejos»


def test_undo_manual_offers_and_undoes_propagated(client, env):
    ids = seed_group(env["db"], 6)
    lid = client.post("/labels", json={"event_id": ids[0], "tarea": "tema", "valor": "personal"}).json()["etiqueta"]["id"]
    cands = [x["event_id"] for x in client.get(f"/labels/{lid}/similares").json()["candidatos"]]
    client.post(f"/labels/{lid}/propagar", json={"event_ids": cands})
    r = client.post(f"/labels/{lid}/deshacer", json={"incluir_propagadas": False}).json()
    assert r == {"deshechas": 1, "propagadas_restantes": 5}  # el panel ofrece deshacerlas también
    r = client.post(f"/labels/{lid}/deshacer", json={"incluir_propagadas": True}).json()
    assert r["deshechas"] == 5 and env["db"].fetch_labels() == []


def test_embedding_cache_outside_repo_without_text(client, env):
    ids = seed_group(env["db"], 3)
    lid = client.post("/labels", json={"event_id": ids[0], "tarea": "tema", "valor": "personal"}).json()["etiqueta"]["id"]
    client.get(f"/labels/{lid}/similares")
    path = env["feedback"].cache_path()
    import config

    assert path.is_file() and str(config.REPO) not in str(path)  # carpeta de datos, fuera del repo
    z = np.load(path, allow_pickle=False)
    assert set(z.files) == {"ids", "hashes", "vectors"}
    assert b"GA caso" not in path.read_bytes()


# ------------------------------------------------------------------ seguridad y logs
def test_endpoints_require_token(env):
    from main import app

    with TestClient(app, base_url=BASE) as anon:
        assert anon.post("/labels", json={"event_id": 1, "tarea": "tema", "valor": "personal"}).status_code == 403
        assert anon.get("/labels/1/similares").status_code == 403
        assert anon.get("/learning/estado").status_code == 403
        assert anon.post("/learning/reentrenar").status_code == 403


def test_extension_can_correct_but_not_propagate_or_train(client, env):
    import config

    ext = {"Origin": f"chrome-extension://{config.extension_id()}"}
    ids = seed_group(env["db"], 3)
    r = client.post("/labels", json={"event_id": ids[0], "tarea": "tema", "valor": "personal"}, headers=ext)
    assert r.status_code == 200
    lid = r.json()["etiqueta"]["id"]
    for method, path, body in [("get", f"/labels/{lid}/similares", None), ("post", f"/labels/{lid}/propagar", {"event_ids": []}),
                               ("post", "/learning/reentrenar", None), ("post", "/learning/volver", {"tarea": "tema"}),
                               ("post", "/learning/adoptar", {"tarea": "tema", "version": "v3"})]:
        kw = {"json": body} if body is not None else {}
        for h in (ext, {"Sec-Fetch-Site": "none"}):
            res = getattr(client, method)(path, headers=h, **kw)
            assert res.status_code == 403 and res.json()["detail"] == "solo desde el panel", (path, h)
    panel = {"Origin": BASE, "Sec-Fetch-Site": "same-origin"}
    assert client.get(f"/labels/{lid}/similares", headers=panel).status_code == 200


def test_never_logs_text_or_notes(client, env, caplog):
    caplog.set_level(logging.DEBUG)
    ids = seed_group(env["db"], 4)
    lid = client.post("/labels", json={"event_id": ids[0], "tarea": "tema", "valor": "personal",
                                       "nota": "NOTA_PRIVADA_5D"}).json()["etiqueta"]["id"]
    cands = client.get(f"/labels/{lid}/similares", params={"presentacion": 0}).json()["candidatos"]
    client.post(f"/labels/{lid}/propagar", json={"event_ids": [c["event_id"] for c in cands]})
    client.post(f"/labels/{lid}/deshacer", json={"incluir_propagadas": True})
    assert "GA caso" not in caplog.text and "NOTA_PRIVADA_5D" not in caplog.text and "personal" not in caplog.text


# ------------------------------------------------------------------ agente
def test_agent_reads_learning_numbers_but_cannot_write(client, env):
    from agent import tools

    ids = seed_group(env["db"], 2)
    client.post("/labels", json={"event_id": ids[0], "tarea": "tema", "valor": "personal"})
    r = tools.call("aprendizaje", {})
    assert r["correcciones"]["manual"] == 1 and r["modelos_activos"]["tema"]["version"] == "v2"
    assert "GA" not in json.dumps(r)
    names = set(tools.TOOLS)
    assert not any(w in n for n in names for w in ("etiquet", "corregir", "reentren", "adoptar", "propag", "label"))
    raw = client.post("/agent/preguntar", json={"pregunta": "reentrena el modelo y corrige el último evento"}).text
    assert "aprendizaje" in raw  # solo consulta cifras
    assert len(env["db"].fetch_labels()) == 1


def test_embedding_cache_is_reused_and_rewritten(client, env):
    """Regresión (Windows): la segunda búsqueda reemplaza la caché aunque ya exista (el .npz se cierra)."""
    ids = seed_group(env["db"], 4)
    lid = client.post("/labels", json={"event_id": ids[0], "tarea": "tema", "valor": "personal"}).json()["etiqueta"]["id"]
    assert client.get(f"/labels/{lid}/similares").status_code == 200
    add(env["db"], "GA caso nuevo después de la caché")  # obliga a recalcular y reescribir
    r = client.get(f"/labels/{lid}/similares")
    assert r.status_code == 200 and len(r.json()["candidatos"]) == 4
    import numpy as np

    with np.load(env["feedback"].cache_path()) as z:
        assert len(z["ids"]) == 5
