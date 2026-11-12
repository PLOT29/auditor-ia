"""Fase 5A.1 · escenarios SIMULADOS de la demo (extension/test/demo_cases.json). Valores FALSOS.

1. Con las regex (lo mismo que ve la extensión) cada caso da EXACTAMENTE los hallazgos esperados.
2. El riesgo del backend es coherente con la acción esperada: bloquear → alto; permitir → bajo.
3. Con Presidio cargado (si está disponible): los hallazgos esperados siguen ahí; Presidio solo puede
   AGREGAR nombres de persona o direcciones `pii_ner` (advertencia, riesgo medio), y la calle suelta del
   caso 6b nunca da riesgo alto.
4. Censurar (marcadores de catalog.py) deja un texto sin hallazgos.
"""
import json
from pathlib import Path

import pytest

import masking
import risk
from detectors import catalog, pii, secrets

REPO = Path(__file__).resolve().parents[2]
DEMO = json.loads((REPO / "extension" / "test" / "demo_cases.json").read_text(encoding="utf-8"))["casos"]
ACTION_RISK = {"bloquear": "alto", "advertir": "medio", "permitir": "bajo"}


def regex_findings(text):
    found = secrets.detect(text)
    found += pii.detect_regex(text, taken=found)
    masking.apply_masks(found)
    return sorted(found, key=lambda f: f.start)


def public(found):
    return [{"type": f.type, "masked": f.masked, **({"valido": f.valido} if f.valido is not None else {})}
            for f in found]


def test_six_scenarios_present():
    ids = [c["id"] for c in DEMO]
    assert len(ids) == 7 and len(set(ids)) == 7
    assert {c["accion"] for c in DEMO} == {"bloquear", "permitir"}


@pytest.mark.parametrize("case", DEMO, ids=[c["id"] for c in DEMO])
def test_exact_findings_and_risk(case):
    found = regex_findings(case["texto"])
    assert public(found) == case["esperado"]
    pub = [f.to_public() for f in found]
    assert risk.compute_risk(pub, 0.0, high=0.8, medium=0.5) == ACTION_RISK[case["accion"]]
    # La acción esperada es la más fuerte de las políticas por defecto de sus hallazgos.
    rank = {"permitir": 0, "advertir": 1, "bloquear": 2}
    action = max((catalog.CATALOG[f.type]["politica"] for f in found), key=rank.get, default="permitir")
    assert action == case["accion"]


@pytest.mark.parametrize("case", DEMO, ids=[c["id"] for c in DEMO])
def test_censoring_leaves_nothing(case):
    found = regex_findings(case["texto"])
    out = case["texto"]
    for f in sorted(found, key=lambda f: f.start, reverse=True):
        out = out[: f.start] + catalog.CATALOG[f.type]["marcador"] + out[f.end:]
    assert regex_findings(out) == []
    for f in found:
        assert f.value not in out


def test_masked_text_never_contains_values():
    for case in DEMO:
        found = regex_findings(case["texto"])
        masked = masking.mask_text(case["texto"], found)
        for f in found:
            assert f.value not in masked


@pytest.fixture(scope="module")
def presidio():
    if not pii.load():
        pytest.skip(f"Presidio no disponible: {pii.status()['error']}")
    yield
    pii.reset()


@pytest.mark.parametrize("case", DEMO, ids=[c["id"] for c in DEMO])
def test_with_presidio_only_adds_warnings(presidio, case):
    text = case["texto"]
    found = secrets.detect(text)
    found += pii.detect(text, taken=found)
    masking.apply_masks(found)
    got = public(sorted(found, key=lambda f: f.start))
    expected = case["esperado"]
    assert [g for g in got if g in expected] == expected  # nada esperado se pierde
    extra = [f for f in found if public([f])[0] not in expected]
    assert all(f.type in ("person", "address") for f in extra)
    assert all(catalog.finding_risk(f.type, f.source) == "medio" for f in extra)  # solo advierten
    level = risk.compute_risk([f.to_public() for f in found], 0.0, high=0.8, medium=0.5)
    if case["accion"] == "bloquear":
        assert level == "alto"
    else:
        assert level in ("bajo", "medio")  # 6b: "Reforma" nunca es riesgo alto
