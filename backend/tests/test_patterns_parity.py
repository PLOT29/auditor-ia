"""extension/patterns.js debe detectar y enmascarar igual que el backend.

Ejecuta patterns.js con Node (si no está instalado, el test se omite).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import fakes as F
import masking
from detectors import secrets

PATTERNS_JS = Path(__file__).resolve().parents[2] / "extension" / "patterns.js"
NODE = shutil.which("node")

SAMPLES = [
    f"Corrige: client = OpenAI(api_key='{F.OPENAI_KEY}')",
    F.OPENAI_KEY_LEGACY,
    F.ANTHROPIC_KEY,
    f"{F.AWS_KEY} y {F.GITHUB_TOKEN}",
    "token github_pat_" + "F" * 30,
    F.GOOGLE_KEY,
    F.JWT,
    F.PEM,
    "-----BEGIN OPENSSH " + "PRIVATE KEY-----\nb3BlbnNz",
    f"password={F.PASSWORD_VALUE}",
    f"PASSWORD: '{F.PASSWORD_VALUE}'",
    f"mi contraseña: {F.PASSWORD_VALUE}",
    F.CARD,
    F.CARD.replace(" ", "-"),
    F.CARD_BAD_LUHN,
    "0000 0000 0000 0000",
    "Explícame qué es un firewall",
    "mi password es muy segura",
    "Usa el formato task-management-board-for-teams-2026",
    "AKIA es un prefijo de AWS",
    f"todo junto {F.OPENAI_KEY} {F.JWT} {F.CARD} password={F.PASSWORD_VALUE}",
]

JS = """
const P = require(process.argv[1]);
const samples = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(samples.map(s => P.detectSecrets(s))));
"""


def python_side(text):
    return [{"type": f.type, "masked": masking.mask_value(f.type, f.value)} for f in secrets.detect(text)]


@pytest.mark.skipif(NODE is None, reason="Node.js no está instalado")
def test_patterns_js_matches_backend():
    out = subprocess.run(
        [NODE, "-e", JS, str(PATTERNS_JS)],
        input=json.dumps(SAMPLES),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    js_results = json.loads(out.stdout)
    for text, js in zip(SAMPLES, js_results):
        assert js == python_side(text), f"diferencia en muestra que empieza con {text[:12]!r}"


@pytest.mark.skipif(NODE is None, reason="Node.js no está instalado")
def test_same_types_in_same_order():
    out = subprocess.run(
        [NODE, "-e", "process.stdout.write(JSON.stringify(require(process.argv[1]).PATTERN_TYPES))",
         str(PATTERNS_JS)],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(out.stdout) == [p.type for p in secrets.PATTERNS]
