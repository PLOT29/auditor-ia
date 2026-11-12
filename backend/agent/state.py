"""Nivel de IA activo y configuración del agente (Fase 5C).

- `agent.json` en la carpeta de datos (la de la BD, fuera del repo; AUDITOR_DATA_DIR para cambiarla):
  nivel activo (1/2/3), modelo de Ollama y, para el nivel 3, URL base, modelo, dominio y si el usuario
  aceptó el aviso. NUNCA la API key.
- La API key del nivel 3 (BYOK) vive SOLO en el Administrador de credenciales de Windows (librería
  `keyring`, servicio "Centinela-IA"). No se guarda en el navegador, la BD, .env, archivos de config ni
  logs, y nunca vuelve al panel: solo `****abcd`.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

import db

KEYRING_SERVICE = "Centinela-IA"
KEYRING_USER = "byok-api-key"
DEFAULT_OLLAMA_MODEL = "qwen2.5:3b"
LEVEL_NAMES = {1: "Sin IA", 2: "IA local", 3: "IA en la nube"}

_lock = threading.Lock()


def data_dir() -> Path:
    env = os.environ.get("AUDITOR_DATA_DIR", "").strip()
    return Path(env) if env else db.db_path().parent


def _file() -> Path:
    return data_dir() / "agent.json"


def _defaults() -> dict:
    return {"nivel": 1,
            "ollama": {"modelo": os.environ.get("AUDITOR_OLLAMA_MODEL", "").strip() or DEFAULT_OLLAMA_MODEL,
                       "lista": False},
            "nube": {"base_url": None, "modelo": None, "dominio": None, "aceptado": False}}


def load() -> dict:
    with _lock:
        data = _defaults()
        try:
            saved = json.loads(_file().read_text(encoding="utf-8"))
            data["nivel"] = saved.get("nivel", 1) if saved.get("nivel") in (1, 2, 3) else 1
            data["ollama"].update({k: v for k, v in saved.get("ollama", {}).items() if k in data["ollama"]})
            data["nube"].update({k: v for k, v in saved.get("nube", {}).items() if k in data["nube"]})
        except (OSError, ValueError, AttributeError):
            pass
        return data


def save(data: dict) -> None:
    clean = {"nivel": data["nivel"], "ollama": data["ollama"], "nube": data["nube"]}
    assert "api_key" not in json.dumps(clean)  # la key nunca va a este archivo
    with _lock:
        _file().parent.mkdir(parents=True, exist_ok=True)
        tmp = _file().with_suffix(".tmp")
        tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_file())


def set_level(level: int) -> dict:
    data = load()
    data["nivel"] = level
    save(data)
    return data


# ------------------------------------------------------------------ API key (keyring)
def _keyring():
    import keyring  # noqa: PLC0415  (import tardío: los tests usan un almacén en memoria)

    return keyring


def set_key(key: str) -> None:
    _keyring().set_password(KEYRING_SERVICE, KEYRING_USER, key)


def get_key() -> str | None:
    """Solo para el cliente HTTP del nivel 3. Nunca se devuelve al panel ni se loguea."""
    try:
        return _keyring().get_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:  # noqa: BLE001
        return None


def delete_key() -> bool:
    try:
        _keyring().delete_password(KEYRING_SERVICE, KEYRING_USER)
        return True
    except Exception:  # noqa: BLE001  (no existía)
        return False


def key_hint() -> str | None:
    key = get_key()
    return f"****{key[-4:]}" if key and len(key) >= 8 else ("****" if key else None)


def domain_of(base_url: str) -> str:
    return (urlsplit(base_url).hostname or "").lower()


def validate_base_url(base_url: str) -> str:
    u = urlsplit(base_url.strip())
    host = (u.hostname or "").lower()
    if u.scheme not in ("https", "http") or not host:
        raise ValueError("la URL base debe empezar con https://")
    if u.scheme == "http" and host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("solo se permite https:// para proveedores externos (la key viajaría en claro)")
    if u.username or u.password or u.query or u.fragment:
        raise ValueError("la URL base no puede llevar usuario, contraseña, parámetros ni fragmento")
    return base_url.strip().rstrip("/")


def public(data: dict | None = None) -> dict:
    """Estado para el panel: sin la key (solo su pista)."""
    data = data or load()
    nube = dict(data["nube"])
    nube["clave"] = key_hint()
    return {"nivel": data["nivel"], "nombre": LEVEL_NAMES[data["nivel"]], "ollama": data["ollama"], "nube": nube}
