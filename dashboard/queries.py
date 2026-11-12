"""Shim de compatibilidad (Fase 5B): las consultas viven ahora en backend/analytics.py, compartidas
con el centro de control (/panel). dashboard/app.py (respaldo en Streamlit) sigue importando
`queries` como siempre; aquí solo se reexporta el módulo del backend, sin duplicar SQL.
"""
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import analytics as _analytics  # noqa: E402

globals().update({k: v for k, v in vars(_analytics).items() if not k.startswith("__")})
