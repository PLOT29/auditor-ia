import pytest

import fakes as F
from detectors import pii, secrets


def types(text):
    return [f.type for f in pii.detect(text)]


@pytest.mark.parametrize(
    "value, expected",
    [
        (F.EMAIL, "email"),
        (F.CURP, "curp"),
        (F.CURP.lower(), "curp"),
        (F.RFC, "rfc"),
        (F.PHONE, "phone_mx"),
        (F.PHONE_INTL, "phone_mx"),
        ("(55) 1234-5678", "phone_mx"),
        ("5512345678", "phone_mx"),
        ("+525512345678", "phone_mx"),
    ],
)
def test_detects_each_pii_type(value, expected):
    assert types(f"Mis datos: {value}.") == [expected]


def test_curp_not_also_reported_as_rfc():
    assert types(f"CURP {F.CURP}") == ["curp"]


def test_demo_prompt_email_and_curp():
    text = f"Llena el formulario con {F.EMAIL} y CURP {F.CURP}"
    assert types(text) == ["email", "curp"]


def test_skips_spans_taken_by_secrets():
    text = f"Tarjeta {F.CARD}"
    taken = secrets.detect(text)
    assert pii.detect(text, taken=taken) == []


@pytest.mark.parametrize(
    "text",
    [
        "Explícame qué es un firewall",
        "Tengo 3 tareas y 12 exámenes",
        "El código postal es 03100",
        "123456789",          # 9 dígitos
        "12345678901234",     # 14 dígitos
        "Escribe a soporte en la página",
    ],
)
def test_no_false_positives(text):
    assert types(text) == []
