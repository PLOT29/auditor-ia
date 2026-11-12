"""Seguridad de la API local (Fase 5C).

Un servicio en localhost NO está protegido solo por escuchar en 127.0.0.1: cualquier página web abierta
en el navegador del usuario puede mandarle peticiones (CSRF) y, con DNS rebinding, incluso leer sus
respuestas como si fuera del mismo origen. Por eso, en TODAS las rutas:

1. **Host** debe ser 127.0.0.1, localhost o ::1 (con cualquier puerto). Un ataque de DNS rebinding llega
   con `Host: sitio-del-atacante.com` y se rechaza (403).
2. **Origin**, si viene, debe ser el propio backend (http://127.0.0.1:…, http://localhost:…) o la
   extensión (`chrome-extension://…`; se puede fijar el id con AUDITOR_EXTENSION_ID). Cualquier otro
   (https://evil.example, "null") → 403. Sin Origin (curl, scripts locales) se permite, pero las rutas
   sensibles exigen además el token.
3. **Token de sesión** aleatorio, generado al arrancar (`SESSION_TOKEN`, 256 bits, solo en memoria), en el
   header `X-Centinela-Token` para las rutas que cambian estado, leen detalle o usan el agente/instalador
   (`requires_token`). El panel lo recibe embebido en el HTML de /panel (que una página ajena no puede leer:
   no hay CORS y el Host se valida); la extensión lo pide en /auth/token, que solo responde a un Origin
   `chrome-extension://`. EventSource no puede mandar headers: /events lo acepta en `?token=`.
4. **CORS cerrado:** no hay CORSMiddleware ni `Access-Control-Allow-Origin` (ni comodines). La extensión no
   lo necesita (host_permissions) y el panel es del mismo origen.

`AUDITOR_ALLOWED_HOSTS` (separados por coma) agrega nombres de host permitidos; solo lo usan los tests
("testserver" del TestClient). Nunca se loguea el token.
"""
from __future__ import annotations

import hmac
import os
import secrets
from urllib.parse import urlsplit

SESSION_TOKEN = secrets.token_urlsafe(32)
TOKEN_HEADER = "X-Centinela-Token"
TOKEN_QUERY = "token"  # solo para /events (EventSource no admite headers)

LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}

# Rutas que exigen token (prefijos). /health, /panel (estáticos) y /auth/token no lo exigen.
TOKEN_PREFIXES = ("/analyze", "/stats", "/events", "/agent", "/ia")


def allowed_hostnames() -> set[str]:
    extra = {h.strip().lower() for h in os.environ.get("AUDITOR_ALLOWED_HOSTS", "").split(",") if h.strip()}
    return LOCAL_HOSTNAMES | extra


def _hostname(host_header: str | None) -> str | None:
    if not host_header:
        return None
    try:
        return (urlsplit(f"//{host_header}").hostname or "").lower() or None
    except ValueError:
        return None


def host_ok(host_header: str | None) -> bool:
    """Defensa contra DNS rebinding: el Host tiene que ser local."""
    h = _hostname(host_header)
    return h is not None and h in allowed_hostnames()


def is_extension_origin(origin: str | None) -> bool:
    if not origin or not origin.startswith("chrome-extension://"):
        return False
    ext_id = origin[len("chrome-extension://"):].rstrip("/")
    pinned = os.environ.get("AUDITOR_EXTENSION_ID", "").strip()
    return bool(ext_id) and ext_id.isalnum() and (not pinned or ext_id == pinned)


def origin_ok(origin: str | None) -> bool:
    """Sin Origin → no es un navegador cruzando orígenes (curl, scripts): se permite (el token decide).
    Con Origin → solo el propio backend local o la extensión."""
    if origin is None:
        return True
    if is_extension_origin(origin):
        return True
    try:
        u = urlsplit(origin)
    except ValueError:
        return False
    return u.scheme in ("http", "https") and (u.hostname or "").lower() in allowed_hostnames()


def requires_token(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") or path.startswith(p + "?") for p in TOKEN_PREFIXES)


def token_ok(header_value: str | None, query_value: str | None = None, allow_query: bool = False) -> bool:
    candidate = header_value or (query_value if allow_query else None)
    return bool(candidate) and hmac.compare_digest(candidate.encode(), SESSION_TOKEN.encode())
