"""Lanzador del dashboard: siempre con la configuración de dashboard/.streamlit/config.toml.

    python dashboard/run.py

Alternativa a `streamlit run dashboard/app.py`: corre Streamlit DENTRO de dashboard/ y además fuerza
por línea de comandos las dos opciones de privacidad (solo localhost y sin telemetría), por si en
alguna versión de Streamlit no se encontrara .streamlit/config.toml.
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

if __name__ == "__main__":
    cmd = [sys.executable, "-m", "streamlit", "run", "app.py",
           "--server.address", "127.0.0.1", "--browser.gatherUsageStats", "false", *sys.argv[1:]]
    sys.exit(subprocess.call(cmd, cwd=HERE))
