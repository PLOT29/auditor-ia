"""Importador de historial (ml/import_history.py) y migración de la columna `source`."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

import fakes as F

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("import_history", REPO / "ml" / "import_history.py")
IH = importlib.util.module_from_spec(spec)
spec.loader.exec_module(IH)


def chatgpt_export():
    return [{
        "title": "x", "create_time": 1731300000.0,
        "mapping": {
            "a": {"message": None},
            "b": {"message": {"author": {"role": "system"}, "create_time": 1731300001.0,
                              "content": {"content_type": "text", "parts": ["eres un asistente"]}}},
            "c": {"message": {"author": {"role": "user"}, "create_time": 1731300002.0,
                              "content": {"content_type": "text", "parts": ["Explícame qué es un firewall"]}}},
            "d": {"message": {"author": {"role": "assistant"}, "create_time": 1731300003.0,
                              "content": {"content_type": "text", "parts": ["Un firewall es…"]}}},
            "e": {"message": {"author": {"role": "user"}, "create_time": 1731300004.0,
                              "content": {"content_type": "multimodal_text",
                                          "parts": [{"content_type": "image_asset_pointer"}, f"mi key es {F.OPENAI_KEY}"]}}},
            "f": {"message": {"author": {"role": "user"}, "create_time": None,
                              "metadata": {"is_visually_hidden_from_conversation": True},
                              "content": {"content_type": "user_editable_context", "parts": ["instrucciones"]}}},
            "g": {"message": {"author": {"role": "user"}, "create_time": None,  # usa la fecha de la conversación
                              "content": {"content_type": "text", "parts": ["Explícame qué es un firewall"]}}},
        },
    }]


def claude_export():
    return [{
        "uuid": "1", "name": "x", "created_at": "2025-03-01T10:00:00Z",
        "chat_messages": [
            {"sender": "human", "created_at": "2025-03-01T10:00:01.123456Z", "text": f"Mi correo es {F.EMAIL}"},
            {"sender": "assistant", "created_at": "2025-03-01T10:00:02Z", "text": "Ok"},
            {"sender": "human", "created_at": "2025-03-02T09:00:00Z", "text": "",
             "content": [{"type": "text", "text": "Resume este texto"}]},
            {"sender": "human", "created_at": "2025-03-02T09:01:00Z", "text": "   "},
        ],
    }]


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_file = tmp_path / "db" / "auditor.db"
    monkeypatch.setenv("AUDITOR_DB_PATH", str(db_file))
    return tmp_path, db_file


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def rows(db_file):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    out = [dict(r) for r in conn.execute("SELECT * FROM prompts ORDER BY id")]
    conn.close()
    return out


def test_detect_format():
    assert IH.detect_format(chatgpt_export()) == "chatgpt"
    assert IH.detect_format(claude_export()) == "claude"
    assert IH.detect_format({"conversations": claude_export()}) == "claude"
    with pytest.raises(ValueError):
        IH.detect_format([{"foo": 1}])


def test_chatgpt_only_user_messages_with_dates():
    msgs = list(IH.iter_chatgpt(chatgpt_export()))
    assert len(msgs) == 3  # c, e (solo la parte de texto) y g; no system/assistant/oculto
    assert all(m.ts.tzinfo for m in msgs)
    assert sorted(m.text for m in msgs)[0].startswith("Explícame")
    assert [m.has_files for m in msgs].count(True) == 1  # el multimodal con imagen


def test_claude_only_human_messages():
    msgs = list(IH.iter_claude(claude_export()))
    assert msgs[1].text == "Resume este texto"  # texto desde `content`
    assert len(msgs) == 3  # incluido el vacío; se descarta al importar


def test_import_chatgpt_masks_dedupes_and_marks_source(env, capsys):
    tmp_path, db_file = env
    f = write(tmp_path, "conversations.json", chatgpt_export())
    s = IH.main(["--file", str(f)])
    assert s["formato"] == "chatgpt"
    assert s["insertados"] == 2 and s["duplicados"] == 1  # "firewall" aparece dos veces
    data = rows(db_file)
    assert {r["platform"] for r in data} == {"chatgpt"}
    assert {r["source"] for r in data} == {"import"}
    assert any(r["text_masked"] == "mi key es <OPENAI_KEY>" and r["risk"] == "alto" for r in data)
    assert data[0]["ts"].startswith("2024-11-11T")

    raw = db_file.read_bytes()
    out = capsys.readouterr().out
    assert F.OPENAI_KEY.encode() not in raw
    assert F.OPENAI_KEY not in out and "firewall" not in out  # nunca imprime el texto

    s2 = IH.main(["--file", str(f)])  # segunda corrida: todo duplicado
    assert s2.get("insertados", 0) == 0 and s2["duplicados"] == 3
    assert len(rows(db_file)) == 2


def test_import_claude(env):
    tmp_path, db_file = env
    s = IH.main(["--file", str(write(tmp_path, "c.json", claude_export()))])
    assert s["formato"] == "claude" and s["insertados"] == 2 and s["vacios_sin_contenido_en_export"] == 1
    data = rows(db_file)
    assert {r["platform"] for r in data} == {"claude"}
    assert data[0]["text_masked"] == "Mi correo es <EMAIL>"
    assert data[0]["risk"] == "medio"
    assert F.EMAIL.encode() not in db_file.read_bytes()


def test_dry_run_writes_nothing(env):
    tmp_path, db_file = env
    s = IH.main(["--file", str(write(tmp_path, "c.json", claude_export())), "--dry-run"])
    assert s["por_importar"] == 2 and not db_file.exists()


def test_refuses_file_inside_repo():
    with pytest.raises(SystemExit):
        IH.main(["--file", str(REPO / "SPEC.md")])


def test_stats_report_import_separately(env):
    tmp_path, db_file = env
    IH.main(["--file", str(write(tmp_path, "c.json", claude_export()))])
    import db
    db.insert_prompt({"ts": "2026-11-11T00:00:00+00:00", "platform": "chatgpt", "text_masked": "hola",
                      "text_sha256": "0" * 64, "length": 4, "category": None, "category_conf": None,
                      "injection_score": 0.0, "risk": "bajo", "findings": [], "blocked": False,
                      "user_decision": "no_aplica", "latency_ms": 1})
    assert db.get_stats()["por_origen"] == {"import": 2, "extension": 1}


OLD_SCHEMA = """
CREATE TABLE prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, platform TEXT NOT NULL,
    text_masked TEXT NOT NULL, text_sha256 TEXT NOT NULL, length INTEGER NOT NULL,
    category TEXT, category_conf REAL, injection_score REAL, risk TEXT NOT NULL,
    findings_json TEXT NOT NULL, blocked INTEGER NOT NULL DEFAULT 0, user_decision TEXT, latency_ms INTEGER);
INSERT INTO prompts (ts, platform, text_masked, text_sha256, length, risk, findings_json)
VALUES ('2026-11-01T00:00:00+00:00', 'chatgpt', 'hola', 'abc', 4, 'bajo', '[]');
"""


def test_migration_is_idempotent(env):
    _, db_file = env
    db_file.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_file)
    conn.executescript(OLD_SCHEMA)
    conn.close()
    import db
    db.init_db()
    db.init_db()  # segunda vez: no falla ni duplica la columna
    data = rows(db_file)
    assert data[0]["source"] == "extension"  # filas viejas quedan como 'extension'
    conn = sqlite3.connect(db_file)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(prompts)")]
    conn.close()
    assert cols.count("source") == 1


def test_insert_rejects_unknown_source(env):
    import db
    db.init_db()
    with pytest.raises(ValueError):
        db.insert_prompt({"ts": "x", "platform": "chatgpt", "text_masked": "x", "text_sha256": "1", "length": 1,
                          "risk": "bajo", "findings": [], "blocked": False, "source": "otro"})


# ---------------------------------------------------------------- fix: texto solo en `content`
def claude_content_only_export():
    """Fixture FALSO con la forma real del export de Claude: `text` vacío y el texto en `content`."""
    def block(t):
        return {"start_timestamp": "2026-10-31T10:00:00Z", "stop_timestamp": "2026-10-31T10:00:00Z",
                "flags": None, "type": "text", "text": t, "citations": []}
    return [{
        "uuid": "c1", "name": "x", "summary": "", "created_at": "2026-10-31T10:00:00Z",
        "updated_at": "2026-10-31T10:05:00Z", "account": {"uuid": "a"},
        "chat_messages": [
            {"uuid": "m1", "sender": "human", "created_at": "2026-10-31T10:00:00Z", "updated_at": "",
             "text": "", "content": [block("Hola, "), block(f"mi key es {F.OPENAI_KEY}")],
             "attachments": [], "files": [], "parent_message_uuid": "0"},
            {"uuid": "m2", "sender": "human", "created_at": "2026-11-02T09:00:00Z", "updated_at": "",
             "text": "texto viejo que NO debe usarse",
             "content": [block(f"Escríbeme a {F.EMAIL}")], "attachments": [], "files": [],
             "parent_message_uuid": "m1"},
            {"uuid": "m3", "sender": "human", "created_at": "2026-11-03T09:00:00Z", "updated_at": "",
             "text": "", "content": [{"type": "tool_result", "content": [], "name": "x"},
                                     {"type": "document", "file_uuid": "f"}],
             "attachments": [{"file_name": "notas.txt", "extracted_content": "contenido del adjunto falso"}],
             "files": [{"file_name": "notas.txt"}], "parent_message_uuid": "m2"},
            {"uuid": "m4", "sender": "human", "created_at": "2026-11-04T09:00:00Z", "updated_at": "",
             "text": "", "content": [], "attachments": [], "files": [{"file_name": "foto.png"}],
             "parent_message_uuid": "m3"},
            {"uuid": "m5", "sender": "human", "created_at": "2026-11-05T09:00:00Z", "updated_at": "",
             "text": "", "content": [], "attachments": [], "files": [], "parent_message_uuid": "m4"},
            {"uuid": "m6", "sender": "human", "created_at": "2026-11-06T09:00:00Z", "updated_at": "",
             "text": "", "content": [block("Hola, "), block(f"mi key es {F.OPENAI_KEY}")],  # repetido
             "attachments": [], "files": [], "parent_message_uuid": "m5"},
        ],
    }]


def test_claude_text_only_in_content_blocks():
    msgs = list(IH.iter_claude(claude_content_only_export()))
    assert msgs[0].text == f"Hola, \nmi key es {F.OPENAI_KEY}"
    assert msgs[1].text == f"Escríbeme a {F.EMAIL}"  # (a) content gana sobre (b) text
    assert msgs[2].text == ""  # tool_result / document no son texto del usuario
    assert msgs[3].text == "" and msgs[3].has_files
    assert msgs[4].text == "" and not msgs[4].has_files


def test_claude_falls_back_to_text_field():
    msg = {"sender": "human", "text": "solo en text", "content": []}
    assert IH.claude_text(msg) == "solo en text"


def test_attachments_only_with_flag():
    msgs_off = list(IH.iter_claude(claude_content_only_export()))
    msgs_on = list(IH.iter_claude(claude_content_only_export(), include_attachments=True))
    assert msgs_off[2].text == ""
    assert msgs_on[2].text == "contenido del adjunto falso"


def test_dry_run_counts(env):
    tmp_path, db_file = env
    f = write(tmp_path, "claude.json", claude_content_only_export())
    s = IH.main(["--file", str(f), "--dry-run"])
    assert s["mensajes_de_usuario"] == 6
    assert s["por_importar"] == 2 and s["duplicados"] == 1  # m6 repite m1
    assert s["con_secretos"] == 1 and s["con_pii"] == 1
    assert s["vacios_solo_archivos"] == 2 and s["vacios_sin_contenido_en_export"] == 1
    assert s["por_anio"] == {"2026": 3}
    assert s["por_mes"] == {"2026-10": 1, "2026-11": 2}
    assert s["longitud_mediana"] > 0
    assert not db_file.exists()  # dry-run no crea la BD

    s_att = IH.main(["--file", str(f), "--dry-run", "--include-attachments"])
    assert s_att["por_importar"] == 3 and s_att["vacios_solo_archivos"] == 1


def test_dry_run_counts_duplicates_already_in_db(env, capsys):
    tmp_path, db_file = env
    f = write(tmp_path, "claude.json", claude_content_only_export())
    IH.main(["--file", str(f)])  # importación real: 2 insertados
    s = IH.main(["--file", str(f), "--dry-run"])
    assert s.get("por_importar", 0) == 0 and s["duplicados"] == 3
    out = capsys.readouterr().out
    assert F.OPENAI_KEY not in out and F.EMAIL not in out
