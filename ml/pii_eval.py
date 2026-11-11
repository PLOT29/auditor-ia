"""Evaluación de PII (CE-3): solo regex vs. regex + Presidio (español).

    python ml/pii_eval.py

1. Genera 100 prompts sintéticos con PII FALSA etiquetada (tipo + posición) →
   ml/data/pii_synthetic.jsonl (seed fija). ~15 % no tienen PII (miden falsos positivos).
2. "solo regex"      = backend/detectors/pii.py tal cual (email, phone_mx, curp, rfc).
   "regex+Presidio"  = Presidio con spaCy es_core_news_md (PERSON, LOCATION) + Email +
                       recognizers propios CURP, RFC y teléfono MX (mismas regex del backend).
3. Mide precision / recall / F1 por tipo y micro-promedio. Coincidencia por SOLAPE:
   una predicción es correcta si se solapa con una entidad real del mismo tipo.
   (La dirección completa rara vez coincide exacta con lo que marca el NER.)
4. Guarda ml/reports/metrics.json["pii_eval"] y ml/reports/pii_f1_por_tipo.png.

Solo lee backend/ (no lo modifica).
"""
from __future__ import annotations

import json
import random
import re
import sys
import time
from pathlib import Path

ML = Path(__file__).resolve().parent
sys.path.insert(0, str(ML))
sys.path.insert(0, str(ML.parent / "backend"))
sys.stdout.reconfigure(encoding="utf-8")

import mlutils as U  # noqa: E402
from detectors import pii as backend_pii  # noqa: E402

TYPES = ["email", "phone_mx", "curp", "rfc", "person", "address"]
REGEX_TYPES = {"email", "phone_mx", "curp", "rfc"}
OUT = U.DATA / "pii_synthetic.jsonl"

# ------------------------------------------------------------------ generador
NOMBRES = ["Juan", "María", "José Luis", "Ana Sofía", "Carlos", "Fernanda", "Diego", "Valeria", "Ricardo",
           "Guadalupe", "Alejandro", "Ximena", "Miguel Ángel", "Daniela", "Jorge", "Paola", "Emilio", "Regina"]
APELLIDOS = ["Hernández", "García", "Martínez", "López", "González", "Pérez", "Rodríguez", "Sánchez", "Ramírez",
             "Torres", "Flores", "Rivera", "Gómez", "Díaz", "Cruz", "Morales", "Ortiz", "Castillo"]
CALLES = ["Av. Insurgentes Sur", "Calle Reforma", "Av. Universidad", "Calle Morelos", "Av. Juárez",
          "Calle Hidalgo", "Prolongación Paseo de la Reforma", "Calle 5 de Mayo", "Av. Vallarta", "Calle Zaragoza"]
COLONIAS = ["Santa Fe", "Del Valle", "Centro", "Roma Norte", "Narvarte", "Lomas de Chapultepec", "Coyoacán", "Providencia"]
CIUDADES = ["Ciudad de México", "Guadalajara, Jalisco", "Monterrey, Nuevo León", "Puebla", "Querétaro", "Mérida, Yucatán"]
DOMINIOS = ["gmail.com", "hotmail.com", "outlook.com", "correo.ibero.mx", "empresa.com.mx", "yahoo.com.mx"]
ESTADOS = ["DF", "JC", "NL", "PL", "QT", "YN", "MC", "GT", "VZ"]
CONS = "BCDFGHJKLMNPQRSTVWXZ"
VOC = "AEIOU"
LETR = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

TEMPLATES = [
    "Hola, soy {person}, mi correo es {email} por si me quieren contactar",
    "Llena el formulario con mi CURP {curp} y mi RFC {rfc}",
    "Mi teléfono es {phone_mx}, márcame después de las 5",
    "Escribe una carta de recomendación para {person}",
    "Envíale la factura a {email} con el RFC {rfc}",
    "Vivo en {address}, ¿qué gimnasio me queda cerca?",
    "Redacta un correo para {person} ({email}) avisando que la junta se movió",
    "Mis datos: {person}, CURP {curp}, tel. {phone_mx}",
    "¿Puedes revisar si este CURP está bien escrito? {curp}",
    "Manda el paquete a {address} a nombre de {person}",
    "El contacto del proveedor es {person}, cel {phone_mx}",
    "Necesito una constancia de situación fiscal, mi RFC es {rfc}",
    "Corrige este texto: 'atentamente, {person}, {email}'",
    "Agenda una cita con el doctor y avisa a {phone_mx}",
    "Mi dirección fiscal es {address} y mi RFC {rfc}",
    "Haz un CV para {person}, correo {email}, teléfono {phone_mx}, domicilio {address}",
    "mi correo es {email} y mi cel {phone_mx} porfa guardalo",
    "Ayúdame a responderle a {person}, que me escribió desde {email}",
    "La entrega es en {address}",
    "Registra a {person} con CURP {curp} en la lista del curso",
]
SIN_PII = [
    "Explícame qué es un firewall",
    "El Banco de México subió la tasa de interés, ¿cómo me afecta?",
    "Mi pedido 4589123 llegó incompleto, redacta una queja",
    "¿Cuál es el código postal más común en la Ciudad de México?",
    "Resume el capítulo 3 de Cien años de soledad",
    "Escribe un poema sobre Guadalajara",
    "¿Cuántos habitantes tiene Monterrey?",
    "La reunión es el 15 de marzo a las 10:30 en la sala B",
    "¿Qué es la CURP y para qué sirve?",
    "¿Cómo se calcula el RFC de una persona moral?",
    "Dame una receta de pozole para 12 personas",
    "Traduce 'Avenida Reforma' al inglés",
    "Mi contraseña tiene 16 caracteres, ¿es segura?",
    "¿Qué hizo Benito Juárez por México?",
    "El examen vale 30 % y la tarea 20 %",
]


def fake_person(rng: random.Random) -> str:
    n = f"{rng.choice(NOMBRES)} {rng.choice(APELLIDOS)}"
    if rng.random() < 0.4:
        n += f" {rng.choice(APELLIDOS)}"
    return n.lower() if rng.random() < 0.15 else n  # usuarios reales a veces escriben en minúsculas


def strip_acc(s: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def fake_email(rng: random.Random) -> str:
    a = strip_acc(rng.choice(NOMBRES).split()[0]).lower()
    b = strip_acc(rng.choice(APELLIDOS)).lower()
    sep = rng.choice([".", "_", ""])
    return f"{a}{sep}{b}{rng.choice(['', str(rng.randint(1, 99))])}.fake@{rng.choice(DOMINIOS)}"


def fake_phone(rng: random.Random) -> str:
    lada = rng.choice(["55", "33", "81", "22", "44"])
    a, b = rng.randint(1000, 9999), rng.randint(1000, 9999)
    fmt = rng.choice(["{l} {a} {b}", "{l}{a}{b}", "+52 {l} {a} {b}", "({l}) {a}-{b}", "{l}-{a}-{b}"])
    return fmt.format(l=lada, a=a, b=b)


def _date(rng):
    return f"{rng.randint(60, 99):02d}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}"


def fake_curp(rng: random.Random) -> str:
    return (rng.choice(LETR) + rng.choice(VOC) + rng.choice(LETR) + rng.choice(LETR) + _date(rng)
            + rng.choice("HM") + rng.choice(ESTADOS) + "".join(rng.choice(CONS) for _ in range(3))
            + rng.choice(LETR + "0123456789") + str(rng.randint(0, 9)))


def fake_rfc(rng: random.Random) -> str:
    return ("".join(rng.choice(LETR) for _ in range(4)) + _date(rng)
            + rng.choice(LETR) + rng.choice("0123456789") + rng.choice("A0123456789"))


def fake_address(rng: random.Random) -> str:
    parts = f"{rng.choice(CALLES)} {rng.randint(1, 3999)}"
    if rng.random() < 0.7:
        parts += f", Col. {rng.choice(COLONIAS)}"
    if rng.random() < 0.6:
        parts += f", C.P. {rng.randint(1000, 99999):05d}"
    if rng.random() < 0.7:
        parts += f", {rng.choice(CIUDADES)}"
    return parts


GEN = {"person": fake_person, "email": fake_email, "phone_mx": fake_phone, "curp": fake_curp,
       "rfc": fake_rfc, "address": fake_address}


def render(template: str, rng: random.Random) -> dict:
    text, spans = "", []
    for piece in re.split(r"(\{\w+\})", template):
        m = re.fullmatch(r"\{(\w+)\}", piece)
        if m:
            value = GEN[m.group(1)](rng)
            spans.append({"type": m.group(1), "start": len(text), "end": len(text) + len(value)})
            text += value
        else:
            text += piece
    return {"text": text, "entities": spans}


def generate(n: int = 100) -> list[dict]:
    rng = random.Random(U.SEED)
    rows = [{"text": t, "entities": []} for t in SIN_PII]
    while len(rows) < n:
        rows.append(render(rng.choice(TEMPLATES), rng))
    rng.shuffle(rows)
    return rows


# ------------------------------------------------------------------ sistemas
def predict_regex(text: str) -> list[dict]:
    return [{"type": f.type, "start": f.start, "end": f.end} for f in backend_pii.detect(text)]


def build_presidio():
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
        supported_entity="PHONE_MX", supported_language="es", context=["tel", "teléfono", "cel", "celular", "márcame"],
        patterns=[Pattern("phone_mx", backend_pii.PHONE_RE.pattern, 0.7)]))
    return AnalyzerEngine(nlp_engine=nlp, registry=registry, supported_languages=["es"])


PRESIDIO_MAP = {"EMAIL_ADDRESS": "email", "PHONE_MX": "phone_mx", "CURP": "curp", "RFC": "rfc",
                "PERSON": "person", "LOCATION": "address"}


def predict_presidio(analyzer, text: str) -> list[dict]:
    res = analyzer.analyze(text=text, language="es", score_threshold=0.4)
    out = []
    for r in sorted(res, key=lambda r: (-r.score, r.start)):
        t = PRESIDIO_MAP.get(r.entity_type)
        if not t:
            continue
        # Las entidades de patrón ganan sobre el NER si se solapan (p. ej. "CURP" marcado como PERSON).
        if any(o["start"] < r.end and r.start < o["end"] for o in out):
            continue
        out.append({"type": t, "start": r.start, "end": r.end})
    return sorted(out, key=lambda e: e["start"])


# ------------------------------------------------------------------ métrica
def score(rows: list[dict], preds: list[list[dict]]) -> dict:
    stats = {t: {"tp": 0, "fp": 0, "fn": 0} for t in TYPES}
    for row, pred in zip(rows, preds):
        gold = row["entities"]
        matched = set()
        for p in pred:
            hit = [i for i, g in enumerate(gold)
                   if g["type"] == p["type"] and g["start"] < p["end"] and p["start"] < g["end"]]
            if not hit:
                stats[p["type"]]["fp"] += 1
            elif not (set(hit) & matched):
                stats[p["type"]]["tp"] += 1
                matched.update(hit)
            # una 2.ª predicción sobre la misma entidad (dirección partida en dos) no suma ni resta
        for i, g in enumerate(gold):
            if i not in matched:
                stats[g["type"]]["fn"] += 1

    def prf(s):
        p = s["tp"] / (s["tp"] + s["fp"]) if s["tp"] + s["fp"] else 0.0
        r = s["tp"] / (s["tp"] + s["fn"]) if s["tp"] + s["fn"] else 0.0
        f = 2 * p * r / (p + r) if p + r else 0.0
        return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4), **s}

    out = {t: prf(s) for t, s in stats.items()}
    total = {k: sum(stats[t][k] for t in TYPES) for k in ("tp", "fp", "fn")}
    regex_only = {k: sum(stats[t][k] for t in REGEX_TYPES) for k in ("tp", "fp", "fn")}
    out["micro_todos_los_tipos"] = prf(total)
    out["micro_tipos_regex"] = prf(regex_only)
    return out


def main() -> None:
    U.set_seeds()
    rows = generate()
    with OUT.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_ent = sum(len(r["entities"]) for r in rows)
    print(f"{len(rows)} prompts, {n_ent} entidades -> {OUT.name}")

    regex_preds = [predict_regex(r["text"]) for r in rows]
    analyzer = build_presidio()
    predict_presidio(analyzer, "calentamiento")
    t0 = time.perf_counter()
    pres_preds = [predict_presidio(analyzer, r["text"]) for r in rows]
    ms = (time.perf_counter() - t0) * 1000 / len(rows)

    res = {"regex": score(rows, regex_preds), "regex+presidio": score(rows, pres_preds)}
    support = {t: sum(e["type"] == t for r in rows for e in r["entities"]) for t in TYPES}

    print(f"\n{'tipo':<24}{'n':>4}  {'regex P/R/F1':>22}  {'regex+Presidio P/R/F1':>24}")
    for t in TYPES + ["micro_tipos_regex", "micro_todos_los_tipos"]:
        a, b = res["regex"][t], res["regex+presidio"][t]
        n = support.get(t, "")
        print(f"{t:<24}{n!s:>4}  {a['precision']:.2f}/{a['recall']:.2f}/{a['f1']:.2f}{'':>8}"
              f"{b['precision']:.2f}/{b['recall']:.2f}/{b['f1']:.2f}")
    print(f"\nlatencia Presidio: {ms:.1f} ms/prompt")

    # errores de Presidio (para el análisis)
    errors = []
    for r, p in zip(rows, pres_preds):
        gold = {(e["type"], r["text"][e["start"]:e["end"]]) for e in r["entities"]}
        pred = {(e["type"], r["text"][e["start"]:e["end"]]) for e in p}
        if gold != pred:
            errors.append({"texto": U.mask_preview(r["text"], 160),
                           "faltó": sorted(gold - pred), "sobró": sorted(pred - gold)})

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    labels = TYPES + ["micro (todos)"]
    keys = TYPES + ["micro_todos_los_tipos"]
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.bar(x - 0.2, [res["regex"][k]["f1"] for k in keys], 0.4, label="solo regex")
    ax.bar(x + 0.2, [res["regex+presidio"][k]["f1"] for k in keys], 0.4, label="regex + Presidio")
    ax.axhline(0.85, ls="--", c="gray", lw=1, label="CE-3 (0.85)")
    ax.set_xticks(x, labels)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("F1")
    ax.set_title("PII en español · F1 por tipo (100 prompts sintéticos)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(U.REPORTS / "pii_f1_por_tipo.png", dpi=150)

    U.update_metrics("pii_eval", {
        "n_prompts": len(rows), "n_entidades": n_ent, "soporte": support, "coincidencia": "solape + mismo tipo",
        "CE3_meta": 0.85, "resultados": res,
        "CE3_cumple": {"regex": res["regex"]["micro_todos_los_tipos"]["f1"] >= 0.85,
                       "regex+presidio": res["regex+presidio"]["micro_todos_los_tipos"]["f1"] >= 0.85},
        "latencia_presidio_ms_por_prompt": round(ms, 1),
        "errores_presidio_muestra": errors[:15], "n_prompts_con_error_presidio": len(errors),
    })
    print(f"prompts con algún error (Presidio): {len(errors)}")


if __name__ == "__main__":
    main()
