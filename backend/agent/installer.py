"""Nivel 2 «IA local con Ollama» — instalación y arranque automáticos (Fase 5C).

Este es el ÚNICO módulo del backend que ejecuta comandos del sistema. Reglas:
- Lista blanca FIJA en el código (`COMMANDS`): instalar Ollama con winget (solo el paquete
  `Ollama.Ollama`), verificar la firma Authenticode del ejecutable y arrancar `ollama serve`. Nada se
  construye con texto del usuario; los argumentos son constantes y las rutas salen de ubicaciones fijas.
- `subprocess` sin intérprete de comandos (shell=False), con timeouts y sin ventana. Nunca se descargan ejecutables de otras URL.
- Antes de ejecutar ollama.exe se exige firma Authenticode «Valid» de un certificado de Ollama.
- Solo se invoca desde las rutas /ia/local/* (token de sesión + Origin local, ver security.py) y la
  instalación requiere la confirmación explícita del usuario en el panel.

Flujo (`Setup`): a) ¿responde 127.0.0.1:11434? → d · b) instalado pero apagado → firma + `ollama serve`
· c) no instalado → confirmación → winget → firma → arranque · d) modelo (pull con progreso real)
· e) prueba con una pregunta que obliga a llamar a una herramienta → activa el nivel 2.
Si un paso falla, se detiene y explica la causa con instrucciones manuales; el chat sigue en el nivel 1.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import httpx

from agent import llm, state

log = logging.getLogger("auditor")

OLLAMA = "http://127.0.0.1:11434"
DOWNLOAD_PAGE = "https://ollama.com/download/windows"
WINGET_PACKAGE = "Ollama.Ollama"
WINGET_ARGS = ("install", "--id", WINGET_PACKAGE, "-e", "--silent",
               "--accept-package-agreements", "--accept-source-agreements")
# La ruta del ejecutable se pasa por variable de entorno, no concatenada en el comando.
SIGNATURE_SCRIPT = ('$s = Get-AuthenticodeSignature -LiteralPath $env:CENTINELA_FIRMA; '
                    'Write-Output ("{0}|{1}" -f $s.Status, $s.SignerCertificate.Subject)')
MIN_FREE_GB = 4
TIMEOUTS = {"winget_install": 20 * 60, "firma": 60}
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

MANUAL_STEPS = [
    f"Abre la página oficial de descarga: {DOWNLOAD_PAGE}",
    "Descarga «Download for Windows» (OllamaSetup.exe) y ejecútalo; no necesita permisos de administrador.",
    "Cuando termine, Ollama queda corriendo en la bandeja del sistema (icono de llama).",
    f"En una terminal, descarga el modelo: ollama pull {state.DEFAULT_OLLAMA_MODEL}",
    "Vuelve al centro de control → IA → «Activar IA local».",
]


# ------------------------------------------------------------------ lista blanca de comandos
def winget_path() -> str | None:
    return shutil.which("winget")


def powershell_path() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def ollama_exe() -> str | None:
    """Solo ubicaciones fijas: la instalación por usuario de winget/OllamaSetup y el PATH."""
    local = os.environ.get("LOCALAPPDATA")
    candidates = [Path(local) / "Programs" / "Ollama" / "ollama.exe"] if local else []
    found = shutil.which("ollama")
    if found and Path(found).name.lower() == "ollama.exe":
        candidates.append(Path(found))
    return next((str(p) for p in candidates if p.is_file()), None)


def build(name: str) -> tuple[list[str], dict | None]:
    """argv (y entorno extra) de un comando de la lista blanca. Cualquier otro nombre → ValueError."""
    if name == "winget_install":
        exe = winget_path()
        if not exe:
            raise FileNotFoundError("winget")
        return [exe, *WINGET_ARGS], None
    if name == "firma":
        exe = ollama_exe()
        if not exe:
            raise FileNotFoundError("ollama.exe")
        return [powershell_path(), "-NoProfile", "-NonInteractive", "-Command", SIGNATURE_SCRIPT], {"CENTINELA_FIRMA": exe}
    if name == "ollama_serve":
        exe = ollama_exe()
        if not exe:
            raise FileNotFoundError("ollama.exe")
        return [exe, "serve"], None
    raise ValueError(f"comando no permitido: {name}")


COMMANDS = ("winget_install", "firma", "ollama_serve")


def _exec(argv: list[str], timeout: int, env: dict | None) -> tuple[int, str]:  # pragma: no cover (los tests lo simulan)
    full_env = {**os.environ, **(env or {})}
    p = subprocess.run(argv, shell=False, capture_output=True, timeout=timeout, env=full_env,
                       creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)
    out = (p.stdout or b"") + b"\n" + (p.stderr or b"")
    return p.returncode, out.decode("utf-8", errors="replace")


def _spawn(argv: list[str]) -> None:  # pragma: no cover
    subprocess.Popen(argv, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS, close_fds=True)


def run_command(name: str) -> tuple[int, str]:
    if name not in COMMANDS or name == "ollama_serve":
        raise ValueError(f"comando no permitido: {name}")
    argv, env = build(name)
    return _exec(argv, TIMEOUTS[name], env)


def spawn_command(name: str) -> None:
    if name != "ollama_serve":
        raise ValueError(f"comando no permitido: {name}")
    argv, _ = build(name)
    _spawn(argv)


# ------------------------------------------------------------------ API local de Ollama
def ollama_get(path: str, timeout: float = 3.0) -> dict | None:
    try:
        r = httpx.get(f"{OLLAMA}{path}", timeout=timeout)
        return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


def ollama_up() -> bool:
    return ollama_get("/api/version") is not None


def model_present(model: str) -> bool:
    tags = ollama_get("/api/tags") or {}
    names = {m.get("name") for m in tags.get("models", [])} | {m.get("model") for m in tags.get("models", [])}
    return model in names or f"{model}:latest" in names


def ollama_pull(model: str, on_progress) -> None:
    """POST /api/pull con stream: progreso real (bytes completados / total) de cada capa."""
    with httpx.stream("POST", f"{OLLAMA}/api/pull", json={"model": model, "stream": True}, timeout=None) as r:
        if r.status_code != 200:
            r.read()
            raise RuntimeError(f"Ollama respondió HTTP {r.status_code} al descargar el modelo")
        for line in r.iter_lines():
            if not line.strip():
                continue
            msg = json.loads(line)
            if msg.get("error"):
                raise RuntimeError(msg["error"])
            on_progress(msg)


def test_model(model: str) -> list[dict]:
    """Pregunta corta que obliga a llamar a una herramienta. Devuelve las llamadas hechas."""
    provider = llm.Provider(base_url=llm.OLLAMA_BASE, model=model, timeout=180, name="ollama")
    events = list(llm.run("¿Cuántos prompts hubo en los últimos 7 días? Usa la herramienta resumen_periodo.", provider))
    return [e for e in events if e["type"] == "tool"]


# ------------------------------------------------------------------ explicación de errores
def explain(exc: BaseException | None = None, code: int | None = None, output: str = "") -> str:
    win = getattr(exc, "winerror", None)
    text = (output or "").lower()
    if isinstance(exc, FileNotFoundError) and "winget" in str(exc):
        return ("No se encontró winget (Instalador de aplicaciones de Windows). Instálalo o actualízalo desde "
                "Microsoft Store, o instala Ollama manualmente.")
    if win == 4551 or "smart app control" in text or "control inteligente de aplicaciones" in text:
        return ("Smart App Control (Control inteligente de aplicaciones) de Windows bloqueó el programa. "
                "Instala Ollama manualmente desde la página oficial o revisa Seguridad de Windows → Control de "
                "aplicaciones y navegador.")
    if win in (1260, 5) or "group policy" in text or "directiva" in text:
        return "Una directiva del sistema (o permisos) bloqueó la ejecución del instalador o de Ollama."
    if isinstance(exc, subprocess.TimeoutExpired):
        return "El paso tardó demasiado y se canceló (¿conexión lenta?)."
    if "0x8a15" in text or "internet" in text or "network" in text or "conexi" in text:
        return "No se pudo descargar el paquete: revisa la conexión a internet."
    if "disk" in text or "espacio" in text or "no space" in text:
        return "No hay espacio suficiente en el disco."
    if code is not None:
        return f"El instalador terminó con el código {code}."
    return f"Error inesperado ({type(exc).__name__ if exc else 'desconocido'})."


def _summary(output: str, n: int = 6) -> list[str]:
    lines = [ln.strip() for ln in output.replace("\r", "\n").split("\n")]
    lines = [ln for ln in lines if ln and not set(ln) <= set("-\\|/█▒░ ") and len(ln) > 2]
    return [ln[:160] for ln in lines[-n:]]


# ------------------------------------------------------------------ flujo de activación
STEPS = [
    ("servicio", "¿Ollama responde en 127.0.0.1:11434?"),
    ("instalacion", "Instalar Ollama (winget, paquete oficial)"),
    ("firma", "Verificar la firma digital de Ollama"),
    ("arranque", "Arrancar Ollama en segundo plano"),
    ("modelo", "Descargar el modelo"),
    ("prueba", "Prueba: una pregunta que use una herramienta"),
]


class Setup:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._confirm = threading.Event()
        self._accepted = False
        self.reset()

    def reset(self) -> None:
        self.estado = "inactivo"  # inactivo | en_curso | esperando_confirmacion | listo | error | cancelado
        self.pasos = {k: {"id": k, "titulo": t, "estado": "pendiente", "detalle": ""} for k, t in STEPS}
        self.progreso = None
        self.salida: list[str] = []
        self.error = None
        self.thread = None
        self._confirm.clear()
        self._accepted = False

    def snapshot(self) -> dict:
        with self._lock:
            model = state.load()["ollama"]["modelo"]
            pasos = [dict(p) for p in self.pasos.values()]
            pasos[4]["titulo"] = f"Descargar el modelo {model}"
            return {"estado": self.estado, "pasos": pasos, "progreso": self.progreso, "salida": list(self.salida),
                    "error": self.error, "modelo": model,
                    "confirmacion": ("Se instalará Ollama desde el paquete oficial (winget, Ollama.Ollama), ~1 GB. "
                                     "¿Continuar?") if self.estado == "esperando_confirmacion" else None}

    def _set(self, step: str, estado: str, detalle: str = "") -> None:
        with self._lock:
            self.pasos[step].update(estado=estado, detalle=detalle)

    def _fail(self, step: str, causa: str) -> None:
        with self._lock:
            self.pasos[step].update(estado="error", detalle=causa)
            self.estado = "error"
            self.error = {"paso": step, "causa": causa, "instrucciones": MANUAL_STEPS, "enlace": DOWNLOAD_PAGE}
        log.warning("IA local: falló el paso %s", step)

    # ---------------------------------------------------------------- API
    def start(self) -> bool:
        with self._lock:
            if self.thread and self.thread.is_alive():
                return False
        self.reset()
        self.estado = "en_curso"
        self._confirm.clear()
        self.thread = threading.Thread(target=self._run, name="centinela-ia-local", daemon=True)
        self.thread.start()
        return True

    def confirm(self, accept: bool) -> bool:
        if self.estado != "esperando_confirmacion":
            return False
        self._accepted = accept
        self._confirm.set()
        return True

    # ---------------------------------------------------------------- pasos
    def _run(self) -> None:
        try:
            self._flow()
        except Exception as e:  # noqa: BLE001
            current = next((k for k, p in self.pasos.items() if p["estado"] == "en_curso"), "servicio")
            self._fail(current, explain(e))

    def _verify_signature(self) -> bool:
        self._set("firma", "en_curso")
        code, out = run_command("firma")
        status, _, subject = (out.strip().splitlines() or ["|"])[0].partition("|")
        if code != 0 or status.strip() != "Valid":
            self._fail("firma", f"La firma de ollama.exe no es válida (estado: {status.strip() or 'desconocido'}). "
                                "Por seguridad no se ejecutará.")
            return False
        if "ollama" not in subject.lower():
            self._fail("firma", "ollama.exe está firmado, pero no por Ollama. Por seguridad no se ejecutará.")
            return False
        self._set("firma", "ok", subject.strip()[:120])
        return True

    def _start_server(self) -> bool:
        self._set("arranque", "en_curso", "ollama serve")
        try:
            spawn_command("ollama_serve")
        except OSError as e:
            self._fail("arranque", explain(e))
            return False
        for _ in range(60):
            if ollama_up():
                self._set("arranque", "ok", "Ollama responde en 127.0.0.1:11434")
                return True
            time.sleep(0.5)
        self._fail("arranque", "Ollama no respondió en 30 s después de arrancarlo.")
        return False

    def _install(self) -> bool:
        if not winget_path():
            self._fail("instalacion", explain(FileNotFoundError("winget")))
            return False
        with self._lock:
            self.estado = "esperando_confirmacion"
            self.pasos["instalacion"].update(estado="esperando", detalle="Esperando tu confirmación en el panel")
        self._confirm.wait(timeout=600)
        if not self._accepted:
            with self._lock:
                self.estado = "cancelado"
                self.pasos["instalacion"].update(estado="cancelado", detalle="Instalación cancelada por el usuario")
            return False
        with self._lock:
            self.estado = "en_curso"
        self._set("instalacion", "en_curso", "winget install --id Ollama.Ollama -e --silent …")
        try:
            code, out = run_command("winget_install")
        except (OSError, subprocess.TimeoutExpired) as e:
            self._fail("instalacion", explain(e))
            return False
        with self._lock:
            self.salida = _summary(out)
        if code != 0 and not ollama_exe():
            self._fail("instalacion", explain(code=code, output=out))
            return False
        for _ in range(20):  # el instalador puede tardar un momento en dejar el ejecutable
            if ollama_exe():
                break
            time.sleep(0.5)
        if not ollama_exe():
            self._fail("instalacion", "winget terminó, pero no se encontró ollama.exe en la ubicación esperada.")
            return False
        self._set("instalacion", "ok", "Ollama instalado")
        return True

    def _pull(self, model: str) -> bool:
        if model_present(model):
            self._set("modelo", "omitido", f"{model} ya estaba descargado")
            return True
        home = Path(os.environ.get("USERPROFILE", str(Path.home())))
        free_gb = shutil.disk_usage(home.anchor or str(home)).free / 1024 ** 3
        if free_gb < MIN_FREE_GB:
            self._fail("modelo", f"No hay espacio suficiente: quedan {free_gb:.1f} GB y el modelo necesita ~{MIN_FREE_GB} GB.")
            return False
        self._set("modelo", "en_curso", f"Descargando {model}…")

        def progress(msg: dict) -> None:
            with self._lock:
                if msg.get("total"):
                    self.progreso = {"completado": int(msg.get("completed", 0)), "total": int(msg["total"]),
                                     "estado": str(msg.get("status", ""))[:60]}
                else:
                    self.pasos["modelo"]["detalle"] = str(msg.get("status", ""))[:80]
        try:
            ollama_pull(model, progress)
        except (httpx.HTTPError, RuntimeError, ValueError) as e:
            causa = str(e) if isinstance(e, RuntimeError) else explain(e)
            if isinstance(e, httpx.HTTPError) or "dial tcp" in causa or "lookup" in causa:
                causa = "No se pudo descargar el modelo: revisa la conexión a internet."
            self._fail("modelo", causa)
            return False
        if not model_present(model):
            self._fail("modelo", "La descarga terminó, pero el modelo no aparece en Ollama.")
            return False
        self._set("modelo", "ok", f"{model} listo")
        return True

    def _flow(self) -> None:
        model = state.load()["ollama"]["modelo"]
        self._set("servicio", "en_curso")
        if ollama_up():
            self._set("servicio", "ok", "Ollama ya está corriendo")
            for k in ("instalacion", "firma", "arranque"):
                self._set(k, "omitido")
        else:
            self._set("servicio", "ok", "No responde: se revisa la instalación")
            if ollama_exe():
                self._set("instalacion", "omitido", "Ollama ya está instalado")
            elif not self._install():
                return
            if not self._verify_signature():
                return
            if ollama_up():  # el instalador oficial lo deja corriendo
                self._set("arranque", "omitido", "El instalador ya lo dejó corriendo")
            elif not self._start_server():
                return
        if not self._pull(model):
            return
        self._set("prueba", "en_curso", "Preguntando al modelo…")
        try:
            calls = test_model(model)
        except llm.ProviderError as e:
            self._fail("prueba", f"El modelo no respondió: {e}")
            return
        if not any(c.get("ok") for c in calls):
            self._fail("prueba", "El modelo respondió, pero no llamó a ninguna herramienta; prueba con otro modelo "
                                 "(AUDITOR_OLLAMA_MODEL) o usa el nivel 1.")
            return
        self._set("prueba", "ok", f"Llamó a {calls[0]['nombre']}")
        data = state.load()
        data["ollama"]["lista"] = True
        data["nivel"] = 2
        state.save(data)
        with self._lock:
            self.estado = "listo"
            self.progreso = None
        log.info("IA local activada (modelo %s)", model)


setup = Setup()
