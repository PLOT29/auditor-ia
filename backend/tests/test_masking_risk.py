import fakes as F
import masking
import risk
from main import analyze_text


def test_mask_value_formats_from_spec():
    assert masking.mask_value("openai_key", F.OPENAI_KEY) == "sk-****abcd"
    assert masking.mask_value("email", F.EMAIL) == "o****@gmail.com"
    assert masking.mask_value("anthropic_key", F.ANTHROPIC_KEY) == "sk-ant-****1234"
    assert masking.mask_value("aws_access_key", F.AWS_KEY) == "AKIA****MPLE"
    assert masking.mask_value("github_token", F.GITHUB_TOKEN) == "ghp_****9876"
    assert masking.mask_value("credit_card", F.CARD) == "****1111"
    assert masking.mask_value("phone_mx", F.PHONE) == "****5678"
    assert masking.mask_value("password", F.PASSWORD_VALUE) == "****"
    assert masking.mask_value("private_key", F.PEM) == "****"


def test_mask_text_replaces_with_placeholders():
    text = f"key {F.OPENAI_KEY} y correo {F.EMAIL} y password={F.PASSWORD_VALUE}"
    found, masked = analyze_text(text)
    assert masked == "key <OPENAI_KEY> y correo <EMAIL> y password=<PASSWORD>"
    for f in found:
        assert f.value not in masked


def test_masked_values_never_contain_full_secret():
    text = " ".join([F.OPENAI_KEY, F.ANTHROPIC_KEY, F.AWS_KEY, F.GITHUB_TOKEN,
                     F.GOOGLE_KEY, F.JWT, F.CARD, F.EMAIL, F.CURP])
    found, _ = analyze_text(text)
    assert len(found) == 9
    for f in found:
        assert f.value not in f.masked
        assert masking.STARS in f.masked


def test_risk_rules():
    # umbrales fijos de SPEC §5.4 original; los de producción se prueban en test_models.py
    secret = [{"type": "openai_key", "masked": "x", "source": "secret"}]
    client = [{"type": "openai_key", "masked": "x", "source": "client"}]
    pii_ = [{"type": "email", "masked": "x", "source": "pii"}]
    assert risk.compute_risk_fixed([], 0.0) == "bajo"
    assert risk.compute_risk_fixed([], 0.49) == "bajo"
    assert risk.compute_risk_fixed([], 0.50) == "medio"
    assert risk.compute_risk_fixed([], 0.79) == "medio"
    assert risk.compute_risk_fixed([], 0.80) == "alto"
    assert risk.compute_risk_fixed(pii_, 0.0) == "medio"
    assert risk.compute_risk_fixed(secret, 0.0) == "alto"
    assert risk.compute_risk_fixed(client, 0.0) == "alto"
    assert risk.compute_risk_fixed(pii_ + secret, 0.0) == "alto"


def _fixed(findings, inj):
    return risk.compute_risk(findings, inj, high=0.80, medium=0.50)


risk.compute_risk_fixed = _fixed


def test_none_injection_adds_no_risk():
    assert risk.compute_risk([], None, high=0.8, medium=0.5) == "bajo"
    assert risk.compute_risk([{"source": "pii"}], None, high=0.8, medium=0.5) == "medio"


def test_max_risk_never_lowers():
    assert risk.max_risk("alto", "bajo") == "alto"
    assert risk.max_risk("bajo", "medio") == "medio"


# ---------------------------------------------------------------- Fase 5A.1
def test_catalog_motives():
    from detectors import catalog

    assert catalog.motives(["openai_key", "email"], "alto") == ["secreto", "dato personal"]
    assert catalog.motives(["termino_propio"], "medio") == ["término propio"]
    assert catalog.motives([], "alto") == ["inyección"]  # riesgo sin hallazgos = inyección
    assert catalog.motives([], "bajo") == []


def test_risk_by_type():
    import risk

    r = lambda *fs: risk.compute_risk([{"type": t, "source": s} for t, s in fs], None, high=0.8, medium=0.5)
    assert r(("stripe_key", "secret")) == "alto"
    assert r(("stripe_publishable_key", "secret")) == "medio"  # pk_ es pública: solo advierte
    assert r(("openai_org", "secret")) == "medio"
    assert r(("dni", "pii")) == "alto" and r(("address", "pii")) == "alto"
    assert r(("address", "pii_ner")) == "medio"  # dirección solo de Presidio
    assert r(("person", "pii"), ("email", "pii")) == "medio"
    assert r(("tipo_nuevo", "client")) == "alto" and r(("tipo_nuevo", "pii")) == "medio"


def test_new_masks():
    assert masking.mask_value("stripe_key", "sk_" + "live_FAKEFAKEFAKE1234abcd") == "sk_live_****abcd"
    assert masking.mask_value("stripe_publishable_key", "pk_test_FAKE1234FAKE") == "pk_test_****FAKE"
    assert masking.mask_value("stripe_webhook_secret", "wh" + "sec_FAKEfakeFAKEfake1234") == "whsec_****1234"
    assert masking.mask_value("openai_org", "org-FAKEfake1234FAKEfake5678") == "org-****5678"
    for t in ("uri_credentials", "secret_assignment", "address", "termino_propio"):
        assert masking.mask_value(t, "FakePass2026!") == "****"
