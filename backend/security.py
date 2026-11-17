"""Seguridad de la API local (Fases 5C–5C.1). ÚNICO módulo que decide si una petición pasa.

Un servicio en localhost NO está protegido solo por escuchar en 127.0.0.1: cualquier página web abierta
en el navegador del usuario puede mandarle peticiones (CSRF) y, con DNS rebinding, incluso leer sus
respuestas como si fuera del mismo origen. `check()` aplica, en este orden:

1. **Host** local (127.0.0.1, localhost o ::1, cualquier puerto) en TODAS las rutas → si no, "host no
   permitido". Un ataque de DNS rebinding llega con `Host: sitio-del-atacante.com`.
2. **Origen** (`origin_reason`). Se acepta si se cumple ALGUNA de estas:
   a) `Origin: chrome-extension://<id de Centinela>` (config.extension_id(), que sale de la "key" del
      manifest; ninguna otra extensión);
   b) `Origin` == el propio backend (mismo esquema, host local y puerto que el Host de la petición): el panel;
   c) SIN `Origin` y `Sec-Fetch-Site` ∈ {"none", "same-origin"}: el service worker de la extensión (Chrome no
      manda Origin en sus GET), algo que el usuario abrió a mano, o un GET del propio panel;
   d) SIN `Origin` y SIN `Sec-Fetch-Site`: no es un navegador (curl, scripts locales, tests).
   Se rechaza siempre: cualquier otro Origin (http(s) externo, "null", otra extensión, otro puerto local
   como test.html en :5500), y `Sec-Fetch-Site: cross-site` o `same-site` sin un Origin de la lista.
   **Por qué Sec-Fetch-Site es confiable:** es un header *prohibido* (forbidden header name) de Fetch
   Metadata: lo pone el navegador y ni JavaScript ni un formulario de una página pueden fijarlo ni
   cambiarlo; una petición que una página web dispara hacia localhost siempre llega como "cross-site" o
   "same-site", nunca como "none". Un **proceso local malicioso** sí puede mandar cualquier header (o
   ninguno), pero queda fuera del modelo de amenazas: ya tiene acceso a la máquina, a la BD y a todo lo
   demás.
3. **Token de sesión** aleatorio (`SESSION_TOKEN`, 256 bits, solo en memoria, generado al arrancar) en
   `X-Centinela-Token` para las rutas que cambian estado, exponen detalle o usan el agente/instalador
   (`TOKEN_PREFIXES`) → si no, "token inválido". EventSource no puede mandar headers: /events lo acepta en
   `?token=`. El panel lo recibe embebido en el HTML de /panel; la extensión lo pide en /auth/token, que
   además exige (a) o (c) con "none" (`token_issuance_reason`).
4. **CORS cerrado:** no hay CORSMiddleware ni `Access-Control-Allow-Origin` (ni comodines).

Los motivos de rechazo son cortos y no sensibles; main.py los devuelve en el 403 y los registra en el log
(junto con el origen y Sec-Fetch-Site, nunca el token ni texto). `AUDITOR_ALLOWED_HOSTS` (separados por
coma) agrega nombres de host permitidos; solo lo usan los tests ("testserver").
"""
from __future__ import annotations

import hmac
import os
import secrets
from urllib.parse import urlsplit

import config

SESSION_TOKEN = secrets.token_urlsafe(32)
TOKEN_HEADER = "X-Centinela-Token"
TOKEN_QUERY = "token"  # solo para /events (EventSource no admite headers)

LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
TOKEN_PREFIXES = ("/analyze", "/stats", "/events", "/agent", "/ia")
NO_ORIGIN_OK_SITES = {"none", "same-origin"}

HOST_DENIED = "host no permitido"
ORIGIN_DENIED = "origen no permitido"
TOKEN_DENIED = "token inválido"
EXTENSION_ONLY = "solo la extensión"


def allowed_hostnames() -> set[str]:
    extra = {h.strip().lower() for h in os.environ.get("AUDITOR_ALLOWED_HOSTS", "").split(",") if h.strip()}
    return LOCAL_HOSTNAMES | extra


def _split_host(host_header: str | None) -> tuple[str | None, int | None]:
    if not host_header:
        return None, None
    try:
        u = urlsplit(f"//{host_header}")
        return (u.hostname or "").lower() or None, u.port
    except ValueError:
        return None, None


def host_ok(host_header: str | None) -> bool:
    """Defensa contra DNS rebinding: el Host tiene que ser local."""
    h, _ = _split_host(host_header)
    return h is not None and h in allowed_hostnames()


def is_extension_origin(origin: str | None) -> bool:
    """Solo la extensión Centinela (id de config.extension_id()), no cualquier extensión."""
    allowed = config.extension_id()
    return bool(origin and allowed) and origin.rstrip("/") == f"chrome-extension://{allowed}"


def is_panel_origin(origin: str | None, host_header: str | None, scheme: str = "http") -> bool:
    """Mismo origen que el backend: <el esquema de esta petición>://<host local>:<su puerto>."""
    if not origin:
        return False
    try:
        u = urlsplit(origin)
        port = u.port
    except ValueError:
        return False
    h, p = _split_host(host_header)
    default = {"http": 80, "https": 443}.get(u.scheme)
    return (u.scheme == scheme and not u.path.strip("/") and (u.hostname or "").lower() in allowed_hostnames()
            and h is not None and (u.hostname or "").lower() == h and (port or default) == (p or default))


def origin_reason(origin: str | None, sec_fetch_site: str | None, host_header: str | None,
                  scheme: str = "http") -> str | None:
    """None si el origen es aceptable; si no, el motivo corto del rechazo."""
    site = (sec_fetch_site or "").strip().lower() or None
    if origin is not None:
        if is_extension_origin(origin):
            return None
        if is_panel_origin(origin, host_header, scheme) and site in (None, "same-origin", "none"):
            return None
        return ORIGIN_DENIED
    if site is None or site in NO_ORIGIN_OK_SITES:
        return None
    return ORIGIN_DENIED  # cross-site / same-site sin Origin de la lista


def requires_token(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in TOKEN_PREFIXES)


def token_ok(header_value: str | None, query_value: str | None = None, allow_query: bool = False) -> bool:
    candidate = header_value or (query_value if allow_query else None)
    return bool(candidate) and hmac.compare_digest(candidate.encode(), SESSION_TOKEN.encode())


def check(path: str, headers, query, scheme: str = "http") -> str | None:
    """Decisión de TODO el backend para una petición: None (pasa) o el motivo del 403."""
    host = headers.get("host")
    if not host_ok(host):
        return HOST_DENIED
    reason = origin_reason(headers.get("origin"), headers.get("sec-fetch-site"), host, scheme)
    if reason:
        return reason
    if requires_token(path) and not token_ok(headers.get(TOKEN_HEADER), query.get(TOKEN_QUERY),
                                             allow_query=(path == "/events")):
        return TOKEN_DENIED
    return None


def token_issuance_reason(headers) -> str | None:
    """/auth/token: solo la extensión — Origin de Centinela, o sin Origin con Sec-Fetch-Site "none"
    (el service worker de la extensión; una página web nunca produce "none")."""
    origin = headers.get("origin")
    site = (headers.get("sec-fetch-site") or "").strip().lower()
    if is_extension_origin(origin) or (origin is None and site == "none"):
        return None
    return EXTENSION_ONLY


def describe(headers) -> str:
    """Contexto NO sensible para el log de un rechazo: esquema://host del Origin y Sec-Fetch-Site."""
    origin = headers.get("origin")
    if origin:
        try:
            u = urlsplit(origin)
            origin = f"{u.scheme}://{u.hostname or ''}{':' + str(u.port) if u.port else ''}"[:80]
        except ValueError:
            origin = "inválido"
    return f"origin={origin or '-'} sec-fetch-site={(headers.get('sec-fetch-site') or '-')[:20]}"
