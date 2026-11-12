"""Fase 5: consultas del dashboard (dashboard/queries.py) con BD temporal y datos FALSOS."""
import importlib.util
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("dash_queries", REPO / "dashboard" / "queries.py")
Q = importlib.util.module_from_spec(spec)
sys.modules["dash_queries"] = Q  # los dataclasses necesitan su módulo registrado
spec.loader.exec_module(Q)

THR = 0.61


def _row(ts, platform, risk="bajo", category="programacion", conf=0.9, findings=(), blocked=False,
         decision="no_aplica", source="extension", text="texto falso"):
    return {"ts": ts, "platform": platform, "text_masked": text, "text_sha256": f"{abs(hash((ts, platform, text))):064d}"[:64],
            "length": len(text), "category": category, "category_conf": conf, "injection_score": 0.1, "risk": risk,
            "findings": [{"type": t, "masked": "****", "source": "pii"} for t in findings], "blocked": blocked,
            "user_decision": decision, "latency_ms": 5, "source": source}


@pytest.fixture
def dbfile(tmp_path, monkeypatch):
    path = tmp_path / "auditor.db"
    monkeypatch.setenv("AUDITOR_DB_PATH", str(path))
    import db

    db.init_db()
    rows = [
        # 10 nov — hora CDMX = UTC-6
        _row("2026-11-10T16:30:00+00:00", "chatgpt"),                                    # 10:30 local
        _row("2026-11-10T17:00:00+00:00", "chatgpt", "medio", "consulta_general", 0.8, ["email"]),
        _row("2026-11-10T18:00:00+00:00", "claude", "alto", "programacion", 0.4, ["openai_key"],
             blocked=True, decision="cancelado", text="key <OPENAI_KEY> FALSA"),         # incierto (0.4 < 0.61)
        # 11 nov
        _row("2026-11-11T15:00:00+00:00", "gemini", "medio", "personal", 0.7, ["curp", "email"], source="import"),
        _row("2026-11-11T05:30:00+00:00", "claude", "alto", "ciberseguridad", 0.9, ["password"],
             blocked=True, decision="enviado"),                                            # 23:30 del 10 nov local
        _row("2026-11-11T20:00:00-06:00", "gemini", source="import"),                      # 20:00 local, 11 nov
        _row("2026-11-11T19:00:00+00:00", "test"),                                         # test.html: se excluye
        # periodo anterior (8–9 nov)
        _row("2026-11-08T18:00:00+00:00", "chatgpt"),
        _row("2026-11-09T18:00:00+00:00", "chatgpt", "medio", "redaccion", 0.0, ["email"]),  # sin clasificar
    ]
    for r in rows:
        db.insert_prompt(r)
    return path


F = Q.Filters(start=date(2026, 11, 10), end=date(2026, 11, 11))


def test_events_filters_dates_platforms_and_local_time(dbfile):
    df = Q.events(dbfile, F)
    assert len(df) == 6  # sin 'test' ni el periodo anterior
    assert set(df["platform"]) == {"chatgpt", "claude", "gemini"}
    assert "text_masked" not in df.columns  # la consulta base no lee texto
    late = df[df["ts"] == "2026-11-11T05:30:00+00:00"].iloc[0]
    assert late["ts_local"].day == 10 and late["ts_local"].hour == 23


def test_platform_and_source_filters(dbfile):
    assert len(Q.events(dbfile, Q.Filters(F.start, F.end, ("claude",)))) == 2
    assert len(Q.events(dbfile, Q.Filters(F.start, F.end, source="import"))) == 2
    assert len(Q.events(dbfile, Q.Filters(F.start, F.end, source="extension"))) == 4
    assert Q.events(dbfile, Q.Filters(F.start, F.end, ())).empty


def test_kpis_with_previous_period(dbfile):
    k = Q.kpis(dbfile, F, THR)
    cur, prev = k["actual"], k["anterior"]
    assert cur["total"] == 6 and prev["total"] == 2
    assert cur["sensibles"] == 4 and cur["pct_sensibles"] == pytest.approx(66.7)
    assert cur["bloqueados"] == 2 and cur["cancelados"] == 1 and cur["enviados_igual"] == 1
    assert cur["plataforma_top"] in {"chatgpt", "claude", "gemini"}
    assert k["delta"]["total"] == 4 and k["delta"]["pct_sensibles"] == pytest.approx(16.7)


def test_previous_period_same_duration():
    p = F.previous()
    assert (p.start, p.end) == (date(2026, 11, 8), date(2026, 11, 9))


def test_by_platform_and_risk(dbfile):
    bp = Q.by_platform(dbfile, F).set_index("platform")["prompts"].to_dict()
    assert bp == {"chatgpt": 2, "claude": 2, "gemini": 2}
    r = Q.risk_by_platform(dbfile, F)
    assert len(r) == 9 and r["prompts"].sum() == 6
    assert int(r[(r.platform == "claude") & (r.risk == "alto")]["prompts"].iat[0]) == 2


def test_topics_uncertain_uses_threshold(dbfile):
    t = Q.topics(dbfile, F, THR).set_index("tema")["prompts"].to_dict()
    assert t["incierto"] == 1  # programacion con conf 0.4
    assert t["programacion"] == 2 and t["ciberseguridad"] == 1  # chatgpt + gemini (conf 0.9)
    assert list(Q.topics(dbfile, F, THR)["tema"])[-1] == "incierto"  # va al final
    assert "incierto" not in Q.topics(dbfile, F, None)["tema"].tolist()  # sin umbral no hay inciertos
    assert Q.topic_bucket("redaccion", 0.0, THR) == "sin_clasificar"
    assert Q.topic_bucket("redaccion", 0.6, THR) == "incierto"
    assert Q.topic_bucket("redaccion", 0.61, THR) == "redaccion"


def test_confidence_threshold_from_metrics(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"topic_v2": {"umbral_confianza": {"umbral": 0.42}}}), encoding="utf-8")
    assert Q.confidence_threshold(p) == 0.42
    assert Q.confidence_threshold(tmp_path / "no.json") is None


def test_heatmap_local_weekday_hour(dbfile):
    m = Q.heatmap(dbfile, F)
    assert m.shape == (7, 24) and m.values.sum() == 6
    d10 = Q.WEEKDAYS[date(2026, 11, 10).weekday()]
    d11 = Q.WEEKDAYS[date(2026, 11, 11).weekday()]
    assert m.loc[d10, 23] == 1 and m.loc[d10, 10] == 1 and m.loc[d11, 20] == 1


def test_daily_trend_includes_zero_days(dbfile):
    d = Q.daily_trend(dbfile, Q.Filters(date(2026, 11, 9), date(2026, 11, 12)))
    assert list(d["prompts"]) == [1, 4, 2, 0]  # el de 05:30 UTC del 11 cae el 10 en hora local
    assert list(d["riesgosos"]) == [1, 3, 1, 0]


def test_finding_types(dbfile):
    ft = Q.finding_types(dbfile, F).set_index("tipo")["prompts"].to_dict()
    assert ft == {"email": 2, "openai_key": 1, "curp": 1, "password": 1}


def test_recent_alerts_text_only_when_asked(dbfile):
    a = Q.recent_alerts(dbfile, F, threshold=THR)
    assert len(a) == 4 and "texto" not in a.columns
    assert a["riesgo"].isin(["medio", "alto"]).all()
    assert "bloqueado · cancelado" in a["bloqueo"].tolist()
    t = Q.recent_alerts(dbfile, F, include_text=True)
    assert "texto" in t.columns and any("<OPENAI_KEY>" in x for x in t["texto"])
    assert t["texto"].str.len().max() <= 100


def test_headline_only_numbers_and_names(dbfile):
    h = Q.headline(dbfile, F)
    assert h["total"] == 6 and h["pct_pii"] == pytest.approx(33.3)
    assert h["plataforma_mas_riesgo"] == "claude"
    assert "texto falso" not in h["frase"] and "OPENAI" not in h["frase"]


def test_read_only_connection_rejects_writes(dbfile):
    conn = Q.connect_ro(dbfile)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO prompts (ts, platform, text_masked, text_sha256, length, risk, findings_json) "
                     "VALUES ('x', 'chatgpt', 'x', 'x', 1, 'bajo', '[]')")
    conn.close()


def test_read_only_does_not_create_missing_db(tmp_path):
    missing = tmp_path / "no_existe.db"
    with pytest.raises(sqlite3.OperationalError):
        Q.connect_ro(missing)
    assert not missing.exists()
    assert Q.events(missing, F).empty and Q.date_bounds(missing) is None


def test_empty_db_does_not_fail(tmp_path, monkeypatch):
    path = tmp_path / "vacia.db"
    monkeypatch.setenv("AUDITOR_DB_PATH", str(path))
    import db

    db.init_db()
    assert Q.kpis(path, F, THR)["actual"]["total"] == 0
    assert Q.by_platform(path, F)["prompts"].sum() == 0
    assert Q.topics(path, F, THR).empty and Q.finding_types(path, F).empty
    assert Q.risk_by_platform(path, F)["prompts"].sum() == 0
    assert Q.heatmap(path, F).values.sum() == 0
    assert Q.daily_trend(path, F)["prompts"].sum() == 0
    assert Q.recent_alerts(path, F).empty
    assert Q.headline(path, F)["total"] == 0 and Q.date_bounds(path) is None
    sqlite3.connect(tmp_path / "sin_tabla.db").close()  # archivo sin tabla prompts
    assert Q.events(tmp_path / "sin_tabla.db", F).empty


def test_old_db_without_source_column(tmp_path):
    path = tmp_path / "vieja.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE prompts (id INTEGER PRIMARY KEY, ts TEXT, platform TEXT, text_masked TEXT, text_sha256 TEXT,
          length INTEGER, category TEXT, category_conf REAL, injection_score REAL, risk TEXT, findings_json TEXT,
          blocked INTEGER, user_decision TEXT, latency_ms INTEGER);
        INSERT INTO prompts VALUES (1, '2026-11-10T18:00:00+00:00', 'chatgpt', 'x', 'h', 1, 'personal', 0.9, 0.1,
          'bajo', '[]', 0, 'no_aplica', 5);""")
    conn.close()
    assert len(Q.events(path, Q.Filters(F.start, F.end, source="extension"))) == 1
    assert Q.events(path, Q.Filters(F.start, F.end, source="import")).empty
