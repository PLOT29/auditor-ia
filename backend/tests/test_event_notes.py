"""Fase 5D.1 · nota libre por evento en el detalle del panel. Datos FALSOS.

- se guarda sin ninguna corrección, se edita (una sola nota por evento) y aparece al reabrir;
- vacía = se borra; máx. 500 caracteres; tabla nueva con migración idempotente;
- solo desde el panel, con token; nunca aparece en los logs;
- el autoguardado del panel (backend/panel/notes.js, con Node): tras 1 s sin escribir, al salir del campo
  y al cerrar con Esc ANTES del autoguardado, sin perder ni duplicar.
"""
import json
import logging
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import fakes as F

BASE = "http://127.0.0.1:8000"
PANEL = {"Origin": BASE, "Sec-Fetch-Site": "same-origin"}
SECRET_NOTE = "NOTA_PRIVADA_5D1: revisar este caso con el profe"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_DB_PATH", str(tmp_path / "t.db"))
    from main import app

    with TestClient(app, base_url=BASE, headers=F.auth()) as c:
        c.db_file = tmp_path / "t.db"
        yield c


def event(client, text="Explícame qué es un firewall (falso)"):
    r = client.post("/analyze", json={"text": text, "platform": "chatgpt"})
    assert r.status_code == 200
    return r.json()["id"]


def test_save_without_correction_edit_and_reopen(client):
    eid = event(client)
    assert client.get(f"/events/{eid}").json()["nota"] is None
    r = client.put(f"/events/{eid}/nota", json={"nota": "  primera versión  "}, headers=PANEL)
    assert r.status_code == 200 and r.json()["nota"] == "primera versión" and r.json()["actualizado_en"]
    assert client.get(f"/events/{eid}").json()["nota"]["nota"] == "primera versión"  # reabrir
    client.put(f"/events/{eid}/nota", json={"nota": "versión editada"}, headers=PANEL)
    d = client.get(f"/events/{eid}").json()
    assert d["nota"]["nota"] == "versión editada"
    assert d["revision"]["tema"]["etiqueta"] is None  # sin ninguna corrección
    import db

    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM event_notes WHERE event_id = ?", (eid,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 0


def test_note_visible_in_presentation_mode_too(client):
    """La nota es de la persona (no texto del prompt): se ve y se edita también en modo presentación."""
    eid = event(client)
    client.put(f"/events/{eid}/nota", json={"nota": "visible"}, headers=PANEL)
    d = client.get(f"/events/{eid}", params={"presentacion": 1}).json()
    assert d["nota"]["nota"] == "visible" and d["texto"] is None


def test_empty_note_deletes_and_limits(client):
    eid = event(client)
    client.put(f"/events/{eid}/nota", json={"nota": "algo"}, headers=PANEL)
    r = client.put(f"/events/{eid}/nota", json={"nota": "   "}, headers=PANEL)
    assert r.json() == {"event_id": eid, "nota": None, "actualizado_en": None}
    assert client.get(f"/events/{eid}").json()["nota"] is None
    assert client.put(f"/events/{eid}/nota", json={"nota": "x" * 500}, headers=PANEL).status_code == 200
    assert client.put(f"/events/{eid}/nota", json={"nota": "x" * 501}, headers=PANEL).status_code == 422
    assert client.put("/events/999999/nota", json={"nota": "hola"}, headers=PANEL).status_code == 404


def test_corrections_keep_their_own_note(client):
    eid = event(client)
    client.post("/labels", json={"event_id": eid, "tarea": "tema", "valor": "consulta_general", "nota": "por qué la corregí"})
    client.put(f"/events/{eid}/nota", json={"nota": "nota del evento"}, headers=PANEL)
    d = client.get(f"/events/{eid}").json()
    assert d["revision"]["tema"]["etiqueta"]["nota"] == "por qué la corregí"
    assert d["nota"]["nota"] == "nota del evento"


def test_requires_token_and_panel(client, tmp_path):
    import config
    from main import app

    eid = event(client)
    with TestClient(app, base_url=BASE) as anon:
        assert anon.put(f"/events/{eid}/nota", json={"nota": "x"}, headers=PANEL).status_code == 403
    ext = {"Origin": f"chrome-extension://{config.extension_id()}"}
    r = client.put(f"/events/{eid}/nota", json={"nota": "x"}, headers=ext)
    assert r.status_code == 403 and r.json()["detail"] == "solo desde el panel"
    r = client.put(f"/events/{eid}/nota", json={"nota": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_note_never_logged(client, caplog):
    caplog.set_level(logging.DEBUG)
    eid = event(client)
    client.put(f"/events/{eid}/nota", json={"nota": SECRET_NOTE}, headers=PANEL)
    client.get(f"/events/{eid}")
    assert "NOTA_PRIVADA_5D1" not in caplog.text and "profe" not in caplog.text
    assert "nota de evento guardada" in caplog.text  # solo el hecho, con el id


def test_migration_idempotent_without_touching_existing_tables(client):
    import db

    db.init_db()
    db.init_db()
    with db.connect() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(event_notes)")]
        assert cols == ["event_id", "nota", "actualizado_en"]
        assert "nota" not in [r[1] for r in conn.execute("PRAGMA table_info(prompts)")]


# ------------------------------------------------------------------ autoguardado del panel (Node)
NODE = shutil.which("node")
NOTES_JS = Path(__file__).resolve().parents[1] / "panel" / "notes.js"
SCRIPT = r"""
const N = require(process.argv[1]);
(async () => {
  const out = {};
  // reloj falso: los timers solo corren cuando el test lo dice
  let timers = []; const setT = (fn, ms) => { const t = { fn, ms }; timers.push(t); return t; };
  const clearT = (t) => { timers = timers.filter((x) => x !== t); };
  const runTimers = async () => { const ts = timers; timers = []; for (const t of ts) t.fn(); await new Promise((r) => setTimeout(r, 5)); };
  const mk = (fail = false) => { const saves = [], states = [];
    const a = N.autosaver({ delay: 1000, setTimer: setT, clearTimer: clearT, onState: (s) => states.push(s),
      save: async (t) => { await new Promise((r) => setTimeout(r, 2)); if (fail) throw new Error("x"); saves.push(t); } });
    return { a, saves, states }; };

  // 1) autoguardado tras 1 s sin escribir: solo la última versión, una vez
  let t = mk(); t.a.load(""); t.a.input("h"); t.a.input("ho"); t.a.input("hola");
  out.antes_del_segundo = t.saves.slice();
  await runTimers(); out.tras_un_segundo = t.saves.slice(); out.estados = t.states.slice();

  // 2) cerrar con Esc ANTES del autoguardado: flush guarda ya y el timer ya no duplica
  timers = []; t = mk(); t.a.load("vieja"); t.a.input("nueva antes de 1 s");
  await t.a.flush(); out.esc = t.saves.slice(); await runTimers(); out.esc_sin_duplicar = t.saves.slice();

  // 3) blur sin cambios no guarda; editar y blur guarda
  timers = []; t = mk(); t.a.load("igual"); await t.a.flush(); out.blur_sin_cambios = t.saves.slice();
  t.a.input("editada"); await t.a.flush(); out.blur_con_cambios = t.saves.slice();

  // 4) escribir mientras se guarda: al terminar vuelve a guardar la última versión
  timers = []; t = mk(); t.a.load(""); t.a.input("uno"); const p = t.a.flush(); t.a.input("uno dos"); await p;
  await t.a.flush(); out.durante = t.saves.slice();

  // 5) error al guardar: estado de error y sigue pendiente (no se pierde)
  timers = []; t = mk(true); t.a.load(""); t.a.input("no llega"); const ok = await t.a.flush();
  out.error = { ok, estados: t.states.slice(), pendiente: t.a.dirty(), valor: t.a.value() };
  process.stdout.write(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(NODE is None, reason="Node.js no está instalado")
def test_panel_autosave_logic():
    r = json.loads(subprocess.run([NODE, "-e", SCRIPT, str(NOTES_JS)], capture_output=True, text=True,
                                  encoding="utf-8", check=True).stdout)
    assert r["antes_del_segundo"] == [] and r["tras_un_segundo"] == ["hola"]
    assert r["estados"] == ["sin_cambios", "guardando", "guardado"]
    assert r["esc"] == ["nueva antes de 1 s"] and r["esc_sin_duplicar"] == ["nueva antes de 1 s"]
    assert r["blur_sin_cambios"] == [] and r["blur_con_cambios"] == ["editada"]
    assert r["durante"][-1] == "uno dos"
    assert r["error"] == {"ok": False, "estados": ["sin_cambios", "guardando", "error"], "pendiente": True, "valor": "no llega"}


def test_panel_wires_autosave_and_flush_before_closing():
    js = (NOTES_JS.parent / "panel.js").read_text(encoding="utf-8")
    html = (NOTES_JS.parent / "index.html").read_text(encoding="utf-8")
    assert html.index('src="notes.js"') < html.index('src="panel.js"')
    close = js[js.index("function closeDetail()"):js.index("async function moveDetail")]
    move = js[js.index("async function moveDetail"):js.index("function markedText")]
    assert "flushNote()" in close and "await flushNote()" in move
    assert 'ta.addEventListener("blur"' in js and "delay: 1000" in js
    assert "Se guarda solo en tu BD local. No escribas datos sensibles: la nota no se enmascara." in js
    assert "else if (typing) return;" in js  # ←/→ dentro de la nota no cambian de evento
