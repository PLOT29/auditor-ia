"""Secretos y PII FALSOS para pruebas (SPEC §12.6). Ninguno es real.

Se construyen por concatenación para que los escáneres de secretos de GitHub
no los marquen al subir el repo.
"""
OPENAI_KEY = "sk-" + "proj-" + "DEMO" + "FAKE" * 5 + "abcd"
OPENAI_KEY_LEGACY = "sk-" + "FAKE" * 6 + "wxyz"
ANTHROPIC_KEY = "sk-" + "ant-" + "api03-" + "FAKE" * 6 + "1234"
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"  # clave de ejemplo oficial de la doc. de AWS
GITHUB_TOKEN = "ghp_" + "F" * 32 + "9876"
GOOGLE_KEY = "AIza" + "Sy" + "F" * 29 + "5555"
JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0In0" + ".FAKEsignatureFAKE"
PEM = (
    "-----BEGIN RSA " + "PRIVATE KEY-----\n"
    "MIIEFAKEFAKEFAKEFAKEFAKEFAKEFAKE\n"
    "-----END RSA " + "PRIVATE KEY-----"
)
PASSWORD_VALUE = "Hunter2Fake!"
CARD = "4111 1111 1111 1111"  # tarjeta de prueba Visa (Luhn válido)
CARD_BAD_LUHN = "4111 1111 1111 1112"

EMAIL = "octavio.fake@gmail.com"
CURP = "GOMJ800101HDFRRN09"
RFC = "GOMJ800101AB3"
PHONE = "55 1234 5678"
PHONE_INTL = "+52 55 1234 5678"

# API key FALSA para el nivel 3 (BYOK) de la Fase 5C.
BYOK_KEY = "sk-" + "byok-" + "FAKE" * 6 + "zq9X"


def auth() -> dict:
    """Header con el token de sesión del backend (Fase 5C) para los TestClient."""
    import security

    return {security.TOKEN_HEADER: security.SESSION_TOKEN}
