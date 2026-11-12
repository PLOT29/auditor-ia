"""Catálogo de tipos de hallazgo (Fase 5A.1) — MISMO contenido que `CATALOG` en extension/patterns.js
(paridad verificada en tests/test_patterns_parity.py).

Para cada tipo: nombre para mostrar, categoría (motivo), política por defecto en la extensión,
riesgo que asigna el backend y marcador de censura.

- categoria: "secreto" | "dato_personal" | "termino_propio"   (+ "inyeccion" como motivo sin tipo)
- politica:  "bloquear" | "advertir"                           (default del popup)
- riesgo:    "alto" | "medio"                                  (risk.py)
- local:     False = solo lo detecta el backend (Presidio)

Sin dependencias: lo importan el backend (analytics, risk, agente) y ml/
"""
from __future__ import annotations

CATALOG: dict[str, dict] = {
    "private_key": {"nombre": "Clave privada", "categoria": "secreto", "politica": "bloquear", "riesgo": "alto", "marcador": "[CLAVE_PRIVADA_OCULTA]"},
    "openai_key": {"nombre": "API key de OpenAI", "categoria": "secreto", "politica": "bloquear", "riesgo": "alto", "marcador": "[API_KEY_OCULTA]"},
    "anthropic_key": {"nombre": "API key de Anthropic", "categoria": "secreto", "politica": "bloquear", "riesgo": "alto", "marcador": "[API_KEY_OCULTA]"},
    "google_api_key": {"nombre": "API key de Google", "categoria": "secreto", "politica": "bloquear", "riesgo": "alto", "marcador": "[API_KEY_OCULTA]"},
    "stripe_key": {"nombre": "Clave secreta de Stripe", "categoria": "secreto", "politica": "bloquear", "riesgo": "alto", "marcador": "[STRIPE_KEY_OCULTA]"},
    "stripe_webhook_secret": {"nombre": "Secreto de webhook de Stripe", "categoria": "secreto", "politica": "bloquear", "riesgo": "alto", "marcador": "[WEBHOOK_SECRET_OCULTO]"},
    "aws_access_key": {"nombre": "Clave de acceso de AWS", "categoria": "secreto", "politica": "bloquear", "riesgo": "alto", "marcador": "[CLAVE_AWS_OCULTA]"},
    "aws_secret_key": {"nombre": "Clave secreta de AWS", "categoria": "secreto", "politica": "bloquear", "riesgo": "alto", "marcador": "[CLAVE_AWS_OCULTA]"},
    "github_token": {"nombre": "Token de GitHub", "categoria": "secreto", "politica": "bloquear", "riesgo": "alto", "marcador": "[TOKEN_OCULTO]"},
    "jwt": {"nombre": "JWT", "categoria": "secreto", "politica": "bloquear", "riesgo": "alto", "marcador": "[TOKEN_OCULTO]"},
    "bearer_token": {"nombre": "Token Bearer", "categoria": "secreto", "politica": "bloquear", "riesgo": "alto", "marcador": "[TOKEN_OCULTO]"},
    "uri_credentials": {"nombre": "URI con credenciales", "categoria": "secreto", "politica": "bloquear", "riesgo": "alto", "marcador": "[URI_CON_CREDENCIALES]"},
    "password": {"nombre": "Contraseña", "categoria": "secreto", "politica": "bloquear", "riesgo": "alto", "marcador": "[CONTRASENA_OCULTA]"},
    "secret_assignment": {"nombre": "Secreto en configuración", "categoria": "secreto", "politica": "bloquear", "riesgo": "alto", "marcador": "[SECRETO_OCULTO]"},
    "credit_card": {"nombre": "Tarjeta", "categoria": "secreto", "politica": "bloquear", "riesgo": "alto", "marcador": "[TARJETA_OCULTA]"},
    "openai_org": {"nombre": "ID de organización de OpenAI", "categoria": "secreto", "politica": "advertir", "riesgo": "medio", "marcador": "[ORG_ID_OCULTO]"},
    "stripe_publishable_key": {"nombre": "Clave pública de Stripe", "categoria": "secreto", "politica": "advertir", "riesgo": "medio", "marcador": "[STRIPE_PK_OCULTA]"},
    "curp": {"nombre": "CURP", "categoria": "dato_personal", "politica": "bloquear", "riesgo": "alto", "marcador": "[CURP_OCULTA]"},
    "rfc": {"nombre": "RFC", "categoria": "dato_personal", "politica": "bloquear", "riesgo": "alto", "marcador": "[RFC_OCULTO]"},
    "dni": {"nombre": "DNI", "categoria": "dato_personal", "politica": "bloquear", "riesgo": "alto", "marcador": "[DNI_OCULTO]"},
    "nie": {"nombre": "NIE", "categoria": "dato_personal", "politica": "bloquear", "riesgo": "alto", "marcador": "[NIE_OCULTO]"},
    "address": {"nombre": "Dirección", "categoria": "dato_personal", "politica": "bloquear", "riesgo": "alto", "marcador": "[DIRECCIÓN_OCULTA]"},
    "email": {"nombre": "Correo", "categoria": "dato_personal", "politica": "advertir", "riesgo": "medio", "marcador": "[CORREO_OCULTO]"},
    "phone_mx": {"nombre": "Teléfono", "categoria": "dato_personal", "politica": "advertir", "riesgo": "medio", "marcador": "[TELEFONO_OCULTO]"},
    "person": {"nombre": "Nombre", "categoria": "dato_personal", "politica": "advertir", "riesgo": "medio", "marcador": "[NOMBRE_OCULTO]", "local": False},
    "termino_propio": {"nombre": "Término propio", "categoria": "termino_propio", "politica": "advertir", "riesgo": "medio", "marcador": "[TERMINO_OCULTO]"},
}

CATEGORY_NAMES = {"secreto": "secreto", "dato_personal": "dato personal", "termino_propio": "término propio",
                  "inyeccion": "inyección"}

# Fuente de un hallazgo de dirección que SOLO vio Presidio (NER), sin la heurística local:
# cuenta como dato personal, pero con riesgo medio (no hereda sus falsos positivos como "alto").
NER_SOURCE = "pii_ner"


def name(type_: str) -> str:
    return CATALOG.get(type_, {}).get("nombre", type_)


def category(type_: str) -> str | None:
    return CATALOG.get(type_, {}).get("categoria")


def finding_risk(type_: str, source: str | None) -> str:
    """Riesgo de UN hallazgo. Tipos desconocidos: por su fuente (secret/client → alto; pii → medio)."""
    if source == NER_SOURCE:
        return "medio"
    if type_ in CATALOG:
        return CATALOG[type_]["riesgo"]
    return "alto" if source in ("secret", "client") else "medio"


def is_personal(type_: str) -> bool:
    """¿Dato personal o término propio? (para la fuente client_pii de los hallazgos del cliente)."""
    return category(type_) in ("dato_personal", "termino_propio")


def motives(types, risk: str | None = None) -> list[str]:
    """Motivos legibles de un evento: secreto / dato personal / término propio / inyección.

    "inyección" aparece cuando el riesgo es medio o alto y ningún hallazgo lo explica.
    """
    cats = []
    for t in types:
        c = category(t) or "dato_personal"
        if c not in cats:
            cats.append(c)
    order = ["secreto", "dato_personal", "termino_propio"]
    out = [CATEGORY_NAMES[c] for c in order if c in cats]
    if not out and risk in ("medio", "alto"):
        out.append(CATEGORY_NAMES["inyeccion"])
    return out
