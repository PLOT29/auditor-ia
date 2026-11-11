import pytest

import fakes as F
from detectors import secrets


def types(text):
    return [f.type for f in secrets.detect(text)]


@pytest.mark.parametrize(
    "value, expected",
    [
        (F.OPENAI_KEY, "openai_key"),
        (F.OPENAI_KEY_LEGACY, "openai_key"),
        (F.ANTHROPIC_KEY, "anthropic_key"),
        (F.AWS_KEY, "aws_access_key"),
        (F.GITHUB_TOKEN, "github_token"),
        (F.GOOGLE_KEY, "google_api_key"),
        (F.JWT, "jwt"),
        (F.PEM, "private_key"),
        (F.CARD, "credit_card"),
        (F.CARD.replace(" ", ""), "credit_card"),
        (F.CARD.replace(" ", "-"), "credit_card"),
    ],
)
def test_detects_each_secret_type(value, expected):
    assert types(f"Corrige esto: {value} gracias") == [expected]


def test_demo_prompt_from_spec():
    text = f"Corrige este código: client = OpenAI(api_key='{F.OPENAI_KEY}')"
    found = secrets.detect(text)
    assert [f.type for f in found] == ["openai_key"]
    assert found[0].value == F.OPENAI_KEY


def test_anthropic_key_not_reported_as_openai():
    assert types(F.ANTHROPIC_KEY) == ["anthropic_key"]


@pytest.mark.parametrize(
    "text",
    [
        f"password={F.PASSWORD_VALUE}",
        f"PASSWORD: '{F.PASSWORD_VALUE}'",
        f"mi contraseña: {F.PASSWORD_VALUE}",
        f'db_pwd = "{F.PASSWORD_VALUE}"'.replace("db_", "db "),
    ],
)
def test_password_only_captures_value(text):
    found = secrets.detect(text)
    assert [f.type for f in found] == ["password"]
    assert found[0].value == F.PASSWORD_VALUE


def test_pem_header_only_still_detected():
    assert types("-----BEGIN OPENSSH " + "PRIVATE KEY-----\nb3BlbnNz...") == ["private_key"]


def test_multiple_secrets():
    text = f"{F.AWS_KEY} y {F.GITHUB_TOKEN}"
    assert types(text) == ["aws_access_key", "github_token"]


@pytest.mark.parametrize(
    "text",
    [
        "Explícame qué es un firewall",
        "¿Cómo hago un ask-question-about-things-in-python-loop?",
        "mi password es muy segura, ¿qué opinas?",
        "La contraseña debe tener 12 caracteres",
        "Mi número de pedido es " + F.CARD_BAD_LUHN,
        "0000 0000 0000 0000",
        "El año 2026 tuvo 365 días y 8760 horas",
        "Usa el formato task-management-board-for-teams-2026",
        "AKIA es un prefijo de AWS",
    ],
)
def test_no_false_positives(text):
    assert types(text) == []


def test_luhn():
    assert secrets.luhn_ok("4111111111111111")
    assert not secrets.luhn_ok("4111111111111112")
    assert not secrets.luhn_ok("1234")
