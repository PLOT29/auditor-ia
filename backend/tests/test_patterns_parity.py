"""Paridad extension/patterns.js ↔ backend (secrets.py + masking.py).

Usa los casos de extension/test/secret_cases.json:
  1. El backend da exactamente los secretos esperados (tipo + máscara).
  2. patterns.js (vía Node) da EXACTAMENTE lo mismo que el backend, incluidas
     las posiciones start/end (convertidas a UTF-16, que es como cuenta JS).
  3. La PII que patterns.js usa para enmascarar la cola coincide con pii.py (incluidos DNI/NIE con
     `valido` y la dirección heurística).
  4. Los casos de demo (demo_cases.json) dan lo mismo en ambos lados.
  5. El catálogo de tipos (nombre, categoría, política, riesgo, marcador) es el mismo.
  6. extension/test/patterns.test.js y background.test.js pasan.
Si Node no está instalado, 2–4 se omiten.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import masking
from detectors import catalog, pii, pii_patterns, secrets

REPO = Path(__file__).resolve().parents[2]
CASES_FILE = REPO / "extension" / "test" / "secret_cases.json"
NODE_TEST = REPO / "extension" / "test" / "patterns.test.js"
NODE = shutil.which("node")

CASES = json.loads(CASES_FILE.read_text(encoding="utf-8"))
DEMO = json.loads((REPO / "extension" / "test" / "demo_cases.json").read_text(encoding="utf-8"))["casos"]
SECRET_CASES = CASES["secret_cases"]
PII_CASES = CASES["pii_cases"]

needs_node = pytest.mark.skipif(NODE is None, reason="Node.js no está instalado")


def utf16(text: str, i: int) -> int:
    """Índice de código Python → índice UTF-16 (JavaScript)."""
    return len(text[:i].encode("utf-16-le")) // 2


def py_secrets(text):
    return [
        {"type": f.type, "masked": masking.mask_value(f.type, f.value),
         "start": utf16(text, f.start), "end": utf16(text, f.end)}
        for f in secrets.detect(text)
    ]


def py_sensitive(text):
    found = secrets.detect(text)
    found += pii.detect_regex(text, taken=found)  # regex puras: lo que replica patterns.js (sin Presidio)
    out = []
    for f in sorted(found, key=lambda f: f.start):
        d = {"type": f.type, "masked": masking.mask_value(f.type, f.value),
             "start": utf16(text, f.start), "end": utf16(text, f.end)}
        if f.valido is not None:
            d["valido"] = f.valido
        out.append(d)
    return out


def public(found):
    return [{k: v for k, v in f.items() if k not in ("start", "end")} for f in found]


@pytest.fixture(scope="module")
def js():
    out = subprocess.run([NODE, str(NODE_TEST), "--json"], capture_output=True,
                         text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


def test_at_least_30_secret_cases():
    assert len(SECRET_CASES) >= 30
    assert sum(1 for c in SECRET_CASES if c["expected"]) >= 10
    assert sum(1 for c in SECRET_CASES if not c["expected"]) >= 10


@pytest.mark.parametrize("case", SECRET_CASES, ids=[c["id"] for c in SECRET_CASES])
def test_backend_matches_expected(case):
    got = [{"type": f["type"], "masked": f["masked"]} for f in py_secrets(case["text"])]
    assert got == case["expected"]


@needs_node
@pytest.mark.parametrize("i", range(len(SECRET_CASES)), ids=[c["id"] for c in SECRET_CASES])
def test_js_equals_backend_secrets(js, i):
    assert js["secret_cases"][i] == py_secrets(SECRET_CASES[i]["text"])


@needs_node
@pytest.mark.parametrize("i", range(len(PII_CASES)), ids=[c["id"] for c in PII_CASES])
def test_js_equals_backend_pii(js, i):
    text = PII_CASES[i]["text"]
    assert js["pii_cases"][i] == py_sensitive(text)
    assert public(py_sensitive(text)) == PII_CASES[i]["expected"]


@pytest.mark.parametrize("case", PII_CASES, ids=[c["id"] for c in PII_CASES])
def test_backend_pii_matches_expected(case):
    """Sin Node también: el backend da la PII esperada (con `valido` en DNI/NIE)."""
    assert public(py_sensitive(case["text"])) == case["expected"]


@needs_node
@pytest.mark.parametrize("i", range(len(DEMO)), ids=[c["id"] for c in DEMO])
def test_js_equals_backend_demo_cases(js, i):
    assert js["demo_cases"][i] == py_sensitive(DEMO[i]["texto"])


@needs_node
def test_same_catalog_and_default_policies(js):
    assert js["catalog"].keys() == catalog.CATALOG.keys()
    assert list(js["catalog"]) == list(catalog.CATALOG)  # mismo orden (el del popup)
    for t, info in catalog.CATALOG.items():
        for k in ("nombre", "categoria", "politica", "riesgo", "marcador"):
            assert js["catalog"][t][k] == info[k], (t, k)
        assert js["catalog"][t].get("local", True) == info.get("local", True), t
    assert js["default_policies"] == {t: v["politica"] for t, v in catalog.CATALOG.items() if v.get("local", True)}


@needs_node
def test_same_pii_rule_order(js):
    assert js["pii_types"] == [r[0] for r in pii_patterns.RULES]


def test_every_detected_type_is_in_catalog():
    types = [p.type for p in secrets.PATTERNS] + [r[0] for r in pii_patterns.RULES]
    assert set(types) <= set(catalog.CATALOG)


@needs_node
def test_same_pattern_types_in_same_order():
    out = subprocess.run(
        [NODE, "-e", "process.stdout.write(JSON.stringify(require(process.argv[1]).PATTERN_TYPES))",
         str(REPO / "extension" / "patterns.js")],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(out.stdout) == [p.type for p in secrets.PATTERNS]


@needs_node
@pytest.mark.parametrize("script", ["patterns.test.js", "background.test.js"])
def test_node_suites_pass(script):
    out = subprocess.run([NODE, str(REPO / "extension" / "test" / script)],
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stdout + out.stderr
