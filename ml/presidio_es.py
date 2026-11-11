"""Presidio en español + post-proceso de direcciones (Fase 4A.1).

    analyzer = build_analyzer()
    ents = detect(analyzer, text, PostConfig())          # con ajustes
    ents = detect(analyzer, text, PostConfig.base())     # sin ajustes (como en la Fase 4A)

Recognizers: spaCy es_core_news_md (PERSON, LOCATION), Email y propios de CURP, RFC y
teléfono MX con las MISMAS regex/validador de backend/detectors/pii.py.

Post-proceso (`PostConfig`):
  1. Una PERSON que empieza con "Av.", "Calle", "Blvd."… se reclasifica como dirección.
  2. Fusión de fragmentos de dirección contiguos ("Calle Zaragoza" + "3068, Col." + "Puebla")
     cuando entre ellos solo hay separadores, números, "Col.", "C.P.", "No.", "Int."…,
     y extensión sobre el número exterior / C.P. que queden pegados al final.
  3. Lista negra de falsos LOCATION ("Col", "cel", "C.P."…) y fragmentos de < 3 letras.
  4. Score mínimo configurable para LOCATION/dirección (una sola palabra en minúsculas
     baja su score ×0.6 antes de comparar).
  5. Si una entidad de patrón (email, teléfono, CURP, RFC) se solapa con una del NER, gana
     la de patrón.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from detectors import pii as backend_pii  # noqa: E402

TYPE_MAP = {"EMAIL_ADDRESS": "email", "PHONE_MX": "phone_mx", "CURP": "curp", "RFC": "rfc",
            "PERSON": "person", "LOCATION": "address"}
PATTERN_TYPES = {"email", "phone_mx", "curp", "rfc"}

LOCATION_BLACKLIST = {
    "col", "col.", "colonia", "cel", "cel.", "celular", "tel", "tel.", "c.p", "c.p.", "cp", "no", "no.",
    "num", "núm", "int", "ext", "av", "av.", "calle", "mx", "méx", "cdmx.", "b", "sala", "planta",
}
ADDRESS_PREFIX = re.compile(r"^(?:av(?:enida)?\.?|calle|blvd\.?|boulevard|prol(?:ongación)?\.?|calz(?:ada)?\.?|"
                            r"privada|cerrada|circuito|carretera)\s", re.IGNORECASE)
_SEP = r"[\s,.;#º°\-]*"
_TOKEN = r"(?:col(?:onia)?\.?|c\.?\s?p\.?|no\.?|n[uú]m\.?|int\.?|ext\.?|\d{1,5}[a-z]?)"
GAP_RE = re.compile(rf"^{_SEP}(?:{_TOKEN}{_SEP})*$", re.IGNORECASE)
TAIL_RE = re.compile(r"^(?:[\s,]*(?:#|no\.?|n[uú]m\.?)?\s*\d{1,5}[a-z]?\b)?(?:[\s,]*c\.?\s?p\.?\s*\d{4,5}\b)?",
                     re.IGNORECASE)


@dataclass
class PostConfig:
    adjust: bool = True
    min_location_score: float = 0.5
    blacklist: set[str] = field(default_factory=lambda: set(LOCATION_BLACKLIST))
    max_gap: int = 40
    score_threshold: float = 0.4  # umbral general de Presidio

    @classmethod
    def base(cls) -> "PostConfig":
        return cls(adjust=False)


def build_analyzer():
    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_analyzer.predefined_recognizers import EmailRecognizer, SpacyRecognizer

    class PhoneMxRecognizer(PatternRecognizer):
        def validate_result(self, pattern_text: str):  # mismo validador que el backend
            return backend_pii._phone_ok(pattern_text)

    nlp = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "es", "model_name": "es_core_news_md"}],
    }).create_engine()
    registry = RecognizerRegistry(supported_languages=["es"])
    registry.add_recognizer(SpacyRecognizer(supported_language="es", supported_entities=["PERSON", "LOCATION"]))
    registry.add_recognizer(EmailRecognizer(supported_language="es"))
    registry.add_recognizer(PatternRecognizer(
        supported_entity="CURP", supported_language="es", context=["curp"],
        patterns=[Pattern("curp", backend_pii.CURP_RE.pattern, 0.9)]))
    registry.add_recognizer(PatternRecognizer(
        supported_entity="RFC", supported_language="es", context=["rfc", "fiscal"],
        patterns=[Pattern("rfc", backend_pii.RFC_RE.pattern, 0.8)]))
    registry.add_recognizer(PhoneMxRecognizer(
        supported_entity="PHONE_MX", supported_language="es", context=["tel", "teléfono", "cel", "celular"],
        patterns=[Pattern("phone_mx", backend_pii.PHONE_RE.pattern, 0.7)]))
    return AnalyzerEngine(nlp_engine=nlp, registry=registry, supported_languages=["es"])


def _overlaps(a: dict, b: dict) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"]


def _merge_addresses(text: str, ents: list[dict], cfg: PostConfig) -> list[dict]:
    addr = sorted((e for e in ents if e["type"] == "address"), key=lambda e: e["start"])
    others = [e for e in ents if e["type"] != "address"]
    merged: list[dict] = []
    for e in addr:
        if merged:
            last = merged[-1]
            gap = text[last["end"]:e["start"]]
            if e["start"] <= last["end"] or (len(gap) <= cfg.max_gap and GAP_RE.match(gap)):
                last["end"] = max(last["end"], e["end"])
                last["score"] = max(last["score"], e["score"])
                last["parts"] += 1
                continue
        merged.append({**e, "parts": 1})
    for e in merged:  # número exterior / C.P. pegados al final
        m = TAIL_RE.match(text[e["end"]:])
        if m and m.end():
            e["end"] += m.end()
    return others + merged


def detect(analyzer, text: str, cfg: PostConfig | None = None) -> list[dict]:
    """Entidades {type, start, end, score} ordenadas por posición."""
    cfg = cfg or PostConfig()
    raw = analyzer.analyze(text=text, language="es", score_threshold=cfg.score_threshold)
    ents = []
    for r in raw:
        t = TYPE_MAP.get(r.entity_type)
        if not t:
            continue
        span = text[r.start:r.end]
        if cfg.adjust and t == "person" and ADDRESS_PREFIX.match(span):
            t = "address"
        ents.append({"type": t, "start": r.start, "end": r.end, "score": float(r.score)})

    if cfg.adjust:
        ents = _merge_addresses(text, ents, cfg)
        kept = []
        for e in ents:
            if e["type"] == "address":
                span = text[e["start"]:e["end"]].strip()
                if span.lower() in cfg.blacklist or len(re.sub(r"\W", "", span)) < 3:
                    continue
                score = e["score"] * (0.6 if (span.islower() and " " not in span) else 1.0)
                if score < cfg.min_location_score:
                    continue
            kept.append(e)
        ents = kept

    # Resolución de solapes: patrón > NER; dentro del mismo grupo, mayor score.
    ents.sort(key=lambda e: (e["type"] not in PATTERN_TYPES if cfg.adjust else 0, -e["score"], e["start"]))
    out: list[dict] = []
    for e in ents:
        if not any(_overlaps(e, o) for o in out):
            out.append({k: e[k] for k in ("type", "start", "end", "score")})
    return sorted(out, key=lambda e: e["start"])
