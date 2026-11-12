"""Fase 5C · nivel 2 (instalador de Ollama) y nivel 3 (BYOK). TODO simulado: pytest no instala nada,
no ejecuta comandos reales, no llama a Ollama ni a ningún proveedor y no toca el Administrador de
credenciales real (conftest usa un almacén en memoria). Valores FALSOS.
"""
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import fakes as F

AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_DB_PATH", str(tmp_path / "t.db"))
    from main import app

    with TestClient(app, headers=F.auth()) as c:
        yield c


# ------------------------------------------------------------------ lista blanca
def test_only_whitelisted_commands():
    from agent import installer

    with pytest.raises(ValueError):
        installer.build("powershell -c calc")
    with pytest.raises(ValueError):
        installer.run_command("ollama_serve")  # serve solo con spawn
    with pytest.raises(ValueError):
        installer.spawn_command("winget_install")
    assert installer.WINGET_ARGS == ("install", "--id", "Ollama.Ollama", "-e", "--silent",
                                     "--accept-package-agreements", "--accept-source-agreements")


def test_installer_is_the_only_module_running_commands():
    backend = Path(__file__).resolve().parents[1]
    offenders = []
    for py in backend.rglob("*.py"):
        if "tests" in py.parts or py.name == "installer.py":
            continue
        src = py.read_text(encoding="utf-8")
        if "subprocess" in src or "os.system" in src or "Popen" in src:
            offenders.append(py.name)
    assert offenders == []
    src = (AGENT_DIR / "installer.py").read_text(encoding="utf-8")
    assert "shell=True" not in src and "shell=False" in src and "timeout=" in src


class Sim:
    """Simula el sistema: Ollama apagado/instalado, winget, firma, pull y la prueba final."""

    def __init__(self, monkeypatch, *, installed=False, up=False, winget=True, signature="Valid|CN=Ollama Inc.",
                 winget_code=0, model=False, tool_ok=True):
        from agent import installer

        self.calls = []
        self.state = {"installed": installed, "up": up, "model": model}
        monkeypatch.setattr(installer, "winget_path", lambda: r"C:\fake\winget.exe" if winget else None)
        monkeypatch.setattr(installer, "ollama_exe", lambda: r"C:\fake\Ollama\ollama.exe" if self.state["installed"] else None)
        monkeypatch.setattr(installer, "ollama_up", lambda: self.state["up"])
        monkeypatch.setattr(installer, "model_present", lambda m: self.state["model"])
        monkeypatch.setattr(installer.time, "sleep", lambda s: None)

        def _exec(argv, timeout, env):
            self.calls.append(("exec", argv, env))
            if "winget" in argv[0]:
                self.state["installed"] = winget_code == 0
                return winget_code, "Encontrado Ollama [Ollama.Ollama]\n██████ 100%\nInstalado correctamente"
            return 0, signature

        def _spawn(argv):
            self.calls.append(("spawn", argv, None))
            self.state["up"] = True

        def _pull(model, cb):
            for done in (0, 500_000_000, 1_900_000_000):
                cb({"status": "pulling", "completed": done, "total": 1_900_000_000})
            self.state["model"] = True

        monkeypatch.setattr(installer, "_exec", _exec)
        monkeypatch.setattr(installer, "_spawn", _spawn)
        monkeypatch.setattr(installer, "ollama_pull", _pull)
        monkeypatch.setattr(installer, "test_model", lambda m: [{"type": "tool", "nombre": "resumen_periodo", "ok": tool_ok}])
        installer.setup.reset()
        self.setup = installer.setup


def _run(sim, accept=None):
    import threading

    t = threading.Thread(target=sim.setup._run)
    sim.setup.estado = "en_curso"
    t.start()
    if accept is not None:
        for _ in range(200):
            if sim.setup.estado == "esperando_confirmacion":
                break
            t.join(0.01)
        sim.setup.confirm(accept)
    t.join(5)
    return sim.setup.snapshot()


def test_full_flow_install_sign_start_pull_test(monkeypatch, tmp_path):
    from agent import state

    sim = Sim(monkeypatch)
    snap = _run(sim, accept=True)
    assert snap["estado"] == "listo", snap
    kinds = [(k, Path(a[0]).name) for k, a, _ in sim.calls]
    assert kinds == [("exec", "winget.exe"), ("exec", "powershell.exe"), ("spawn", "ollama.exe")]
    winget_argv = sim.calls[0][1]
    assert winget_argv[1:] == ["install", "--id", "Ollama.Ollama", "-e", "--silent",
                               "--accept-package-agreements", "--accept-source-agreements"]
    assert sim.calls[1][2] == {"CENTINELA_FIRMA": r"C:\fake\Ollama\ollama.exe"}  # ruta por entorno, no en el comando
    assert sim.calls[2][1][1:] == ["serve"]
    assert state.load()["nivel"] == 2 and state.load()["ollama"]["lista"] is True
    assert snap["salida"] and all(len(x) <= 160 for x in snap["salida"])


def test_running_ollama_skips_install_and_commands(monkeypatch):
    sim = Sim(monkeypatch, up=True, model=True)
    snap = _run(sim)
    assert snap["estado"] == "listo" and sim.calls == []
    estados = {p["id"]: p["estado"] for p in snap["pasos"]}
    assert estados["instalacion"] == estados["arranque"] == "omitido" and estados["modelo"] == "omitido"


def test_installed_but_stopped_verifies_signature_then_starts(monkeypatch):
    sim = Sim(monkeypatch, installed=True, model=True)
    snap = _run(sim)
    assert snap["estado"] == "listo"
    assert [k for k, *_ in sim.calls] == ["exec", "spawn"]


def test_declined_confirmation_installs_nothing(monkeypatch):
    from agent import state

    sim = Sim(monkeypatch)
    snap = _run(sim, accept=False)
    assert snap["estado"] == "cancelado" and sim.calls == []
    assert state.load()["nivel"] == 1


def test_invalid_signature_never_executes_ollama(monkeypatch):
    sim = Sim(monkeypatch, installed=True, signature="NotSigned|")
    snap = _run(sim)
    assert snap["estado"] == "error" and snap["error"]["paso"] == "firma"
    assert not any(k == "spawn" for k, *_ in sim.calls)


def test_signature_from_other_publisher_rejected(monkeypatch):
    sim = Sim(monkeypatch, installed=True, signature="Valid|CN=Otra Empresa")
    snap = _run(sim)
    assert snap["error"]["paso"] == "firma" and not any(k == "spawn" for k, *_ in sim.calls)


def test_no_winget_explains_and_gives_manual_steps(monkeypatch):
    sim = Sim(monkeypatch, winget=False)
    snap = _run(sim)
    assert snap["estado"] == "error" and snap["error"]["paso"] == "instalacion"
    assert "winget" in snap["error"]["causa"]
    assert snap["error"]["enlace"] == "https://ollama.com/download/windows"
    assert any("ollama pull" in s for s in snap["error"]["instrucciones"])
    assert sim.calls == []


def test_winget_failure_reported(monkeypatch):
    sim = Sim(monkeypatch, winget_code=-1978335212)
    snap = _run(sim, accept=True)
    assert snap["estado"] == "error" and snap["error"]["paso"] == "instalacion"
    assert not any(k == "spawn" for k, *_ in sim.calls)


def test_smart_app_control_explained():
    from agent import installer

    e = OSError("blocked")
    e.winerror = 4551
    assert "Smart App Control" in installer.explain(e)


def test_model_without_tool_calls_does_not_activate(monkeypatch):
    from agent import state

    sim = Sim(monkeypatch, up=True, model=True, tool_ok=False)
    snap = _run(sim)
    assert snap["error"]["paso"] == "prueba" and state.load()["nivel"] == 1


def test_installer_endpoints_require_token_and_local_origin(tmp_path, monkeypatch):
    from agent import installer

    started = []
    monkeypatch.setattr(installer.setup, "start", lambda: started.append(1) or True)
    monkeypatch.setenv("AUDITOR_DB_PATH", str(tmp_path / "t.db"))
    from main import app

    with TestClient(app) as anon:
        assert anon.post("/ia/local/activar").status_code == 403
    with TestClient(app, headers=F.auth()) as c:
        assert c.post("/ia/local/activar", headers={"Origin": "https://evil.example"}).status_code == 403
        assert started == []
        assert c.post("/ia/local/activar", headers={"Origin": "http://127.0.0.1:8000"}).status_code == 200
        assert started == [1]
        assert c.post("/ia/local/confirmar", json={"aceptar": True}).status_code == 409  # nada esperando


# ------------------------------------------------------------------ nivel 3 · BYOK
def _configure(c, **over):
    body = {"base_url": "https://api.proveedor-falso.example/v1", "modelo": "modelo-falso", "api_key": F.BYOK_KEY,
            "acepto_envio": True, "dominio_aceptado": "api.proveedor-falso.example", **over}
    return c.post("/ia/nube/configurar", json=body)


def test_byok_requires_explicit_acceptance_of_domain(client):
    assert _configure(client, acepto_envio=False).status_code == 422
    assert _configure(client, dominio_aceptado="otro.example").status_code == 422
    assert _configure(client, base_url="http://api.proveedor-falso.example/v1").status_code == 422  # sin https
    assert client.get("/ia/estado").json()["nivel"] == 1


def test_byok_key_never_leaves_the_keyring(client, caplog, _local_test_env, tmp_path):
    caplog.set_level(logging.DEBUG)
    r = _configure(client)
    assert r.status_code == 200 and F.BYOK_KEY not in r.text
    est = client.get("/ia/estado")
    assert est.json()["nivel"] == 3 and est.json()["nube"]["clave"] == "****zq9X"
    assert F.BYOK_KEY not in est.text
    assert _local_test_env.store[("Centinela-IA", "byok-api-key")] == F.BYOK_KEY  # solo en el almacén
    for f in (tmp_path / "datos").rglob("*"):
        if f.is_file():
            assert F.BYOK_KEY.encode() not in f.read_bytes(), f.name
    assert F.BYOK_KEY.encode() not in (tmp_path / "t.db").read_bytes() if (tmp_path / "t.db").exists() else True
    assert F.BYOK_KEY not in caplog.text


def test_byok_test_connection_does_not_echo_key(client, monkeypatch, caplog):
    import httpx

    _configure(client)
    sent = {}

    def fake_post(url, **kw):
        sent.update(url=url, auth=kw["headers"]["Authorization"])
        return httpx.Response(401, json={"error": f"Incorrect API key provided: {F.BYOK_KEY}"})

    monkeypatch.setattr(httpx, "post", fake_post)
    caplog.set_level(logging.DEBUG)
    r = client.post("/ia/nube/probar")
    assert r.json()["ok"] is False and "401" in r.json()["detalle"]
    assert F.BYOK_KEY not in r.text and F.BYOK_KEY not in caplog.text
    assert sent["url"].startswith("https://api.proveedor-falso.example/") and sent["auth"] == f"Bearer {F.BYOK_KEY}"


def test_byok_delete_removes_key(client, _local_test_env):
    _configure(client)
    r = client.post("/ia/nube/borrar")
    assert r.status_code == 200 and r.json()["nivel"] == 1 and r.json()["nube"]["clave"] is None
    assert ("Centinela-IA", "byok-api-key") not in _local_test_env.store
    assert client.post("/ia/nivel", json={"nivel": 3}).status_code == 409


def test_level_switch_requires_ready_levels(client):
    assert client.post("/ia/nivel", json={"nivel": 2}).status_code == 409
    assert client.post("/ia/nivel", json={"nivel": 1}).json()["nivel"] == 1


def test_cloud_payload_has_no_prompt_text(tmp_path, monkeypatch):
    """Lo que viaja al proveedor: system prompt, la pregunta y resultados de herramientas. Nunca text_masked."""
    from datetime import timedelta

    import db
    from agent import llm, tools

    monkeypatch.setenv("AUDITOR_DB_PATH", str(tmp_path / "s.db"))
    db.init_db()
    db.insert_prompt({"ts": f"{tools.today().isoformat()}T18:00:00+00:00", "platform": "claude",
                      "text_masked": "TEXTO_QUE_NUNCA_DEBE_SALIR <EMAIL>", "text_sha256": "0" * 64, "length": 10,
                      "category": "redaccion", "category_conf": 0.9, "injection_score": 0.1, "risk": "medio",
                      "findings": [{"type": "email", "masked": "d****@x.com", "source": "pii"}], "blocked": False,
                      "user_decision": "advertido", "latency_ms": 30, "source": "extension"})
    bodies = []

    class Resp:
        status_code = 200

        def __init__(self, lines):
            self.lines = lines

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b""

        def iter_lines(self):
            return iter(self.lines)

    rounds = [
        ['data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"a","function":{"name":"resumen_periodo","arguments":"{}"}}]}}]}', "data: [DONE]"],
        ['data: {"choices":[{"delta":{"content":"1 prompt."}}]}', "data: [DONE]"],
    ]

    def fake_stream(method, url, json=None, headers=None, timeout=None):
        bodies.append({"url": url, "json": json, "auth": headers.get("Authorization")})
        return Resp(rounds.pop(0))

    import httpx

    monkeypatch.setattr(httpx, "stream", fake_stream)
    p = llm.Provider(base_url="https://api.proveedor-falso.example/v1", model="m", api_key=F.BYOK_KEY)
    events = list(llm.run("¿Cuántos prompts hice?", p))
    assert [e["type"] for e in events] == ["tool", "delta"] and events[0]["ok"]
    sent = json.dumps([b["json"] for b in bodies], ensure_ascii=False)
    assert "TEXTO_QUE_NUNCA_DEBE_SALIR" not in sent and "<EMAIL>" not in sent and "d****@x.com" not in sent
    tool_msg = next(m for m in bodies[1]["json"]["messages"] if m["role"] == "tool")
    assert json.loads(tool_msg["content"])["prompts"] == 1  # solo el agregado
    assert F.BYOK_KEY not in sent  # la key va solo en el header Authorization
    assert all(b["auth"] == f"Bearer {F.BYOK_KEY}" for b in bodies)
    assert timedelta  # usado
