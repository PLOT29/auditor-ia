"""Evaluación de PII (CE-3): solo regex vs. regex + Presidio (español).

    python ml/pii_eval.py

1. Genera 100 prompts sintéticos con PII FALSA etiquetada (tipo + posición) →
   ml/data/pii_synthetic.jsonl (seed fija). ~15 % no tienen PII (miden falsos positivos).
2. "solo regex"               = backend/detectors/pii.py tal cual (email, phone_mx, curp, rfc).
   "regex+presidio" (base)     = Presidio con spaCy es_core_news_md (PERSON, LOCATION) + Email +
                                 recognizers propios CURP, RFC y teléfono MX (backend/detectors/presidio_es.py).
   "regex+presidio_ajustado"   = lo mismo + post-proceso de la Fase 4A.1: lista negra de falsos
                                 LOCATION, score mínimo para direcciones y fusión de fragmentos.
                                 `--min-location-score` cambia el score mínimo (default 0.5).
3. Mide precision / recall / F1 por tipo y micro-promedio. Coincidencia por SOLAPE:
   una predicción es correcta si se solapa con una entidad real del mismo tipo.
   (La dirección completa rara vez coincide exacta con lo que marca el NER.)
   Además, para direcciones: coincidencia EXACTA (la dirección completa como un solo span).
4. Guarda ml/reports/metrics.json["pii_eval"] y ml/reports/pii_f1_por_tipo.png.

Solo lee backend/ (no lo modifica).
"""
from __future__ import annotations

import argparse
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
from detectors import presidio_es  # noqa: E402

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
    return [{"type": f.type, "start": f.start, "end": f.end} for f in backend_pii.detect_regex(text)]


def address_exact(rows: list[dict], preds: list[list[dict]]) -> dict:
    """Direcciones reales cubiertas por UN solo span predicho (ignora espacios/comas en los bordes)."""
    strip = " ,."
    hit = total = 0
    for row, pred in zip(rows, preds):
        spans = {row["text"][p["start"]:p["end"]].strip(strip) for p in pred if p["type"] == "address"}
        for g in row["entities"]:
            if g["type"] == "address":
                total += 1
                hit += row["text"][g["start"]:g["end"]].strip(strip) in spans
    return {"exactas": hit, "total": total, "tasa": round(hit / total, 4) if total else 0.0}


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-location-score", type=float, default=0.5)
    args = ap.parse_args()

    U.set_seeds()
    rows = generate()
    with OUT.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_ent = sum(len(r["entities"]) for r in rows)
    print(f"{len(rows)} prompts, {n_ent} entidades -> {OUT.name}")

    analyzer = presidio_es.build_analyzer()
    presidio_es.detect(analyzer, "calentamiento")
    configs = {"regex+presidio": presidio_es.PostConfig.base(),
               "regex+presidio_ajustado": presidio_es.PostConfig.v4a1(),
               "regex+presidio_4b": presidio_es.PostConfig(min_location_score=args.min_location_score)}
    preds = {"regex": [predict_regex(r["text"]) for r in rows]}
    ms = {}
    for name, cfg in configs.items():
        t0 = time.perf_counter()
        preds[name] = [presidio_es.detect(analyzer, r["text"], cfg) for r in rows]
        ms[name] = round((time.perf_counter() - t0) * 1000 / len(rows), 1)

    res = {name: score(rows, p) for name, p in preds.items()}
    exact = {name: address_exact(rows, p) for name, p in preds.items()}
    support = {t: sum(e["type"] == t for r in rows for e in r["entities"]) for t in TYPES}

    names = list(res)
    print(f"\n{'tipo':<24}{'n':>4}  " + "  ".join(f"{n:>24}" for n in names))
    for t in TYPES + ["micro_tipos_regex", "micro_todos_los_tipos"]:
        cells = [f"{res[n][t]['precision']:.2f}/{res[n][t]['recall']:.2f}/{res[n][t]['f1']:.2f}" for n in names]
        print(f"{t:<24}{support.get(t, '')!s:>4}  " + "  ".join(f"{c:>24}" for c in cells))
    print("\ndirecciones completas en un solo span: " + " · ".join(f"{n}: {exact[n]['exactas']}/{exact[n]['total']}" for n in names))
    print("latencia Presidio (ms/prompt): " + " · ".join(f"{n}: {v}" for n, v in ms.items()))

    def errors_of(name):
        out = []
        for r, p in zip(rows, preds[name]):
            gold = {(e["type"], r["text"][e["start"]:e["end"]]) for e in r["entities"]}
            pred = {(e["type"], r["text"][e["start"]:e["end"]]) for e in p}
            if gold != pred:
                out.append({"texto": U.mask_preview(r["text"], 160),
                            "faltó": sorted(gold - pred), "sobró": sorted(pred - gold)})
        return out

    err_base, err_adj = errors_of("regex+presidio"), errors_of("regex+presidio_ajustado")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    labels = TYPES + ["micro (todos)"]
    keys = TYPES + ["micro_todos_los_tipos"]
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(11, 4.4))
    labs = ["solo regex", "Presidio base (4A)", "Presidio ajustado (4A.1)", "Presidio producción (4B)"]
    for i, (n, lab) in enumerate(zip(names, labs)):
        ax.bar(x + (i - 1.5) * 0.2, [res[n][k]["f1"] for k in keys], 0.2, label=lab)
    ax.axhline(0.85, ls="--", c="gray", lw=1, label="CE-3 (0.85)")
    ax.set_xticks(x, labels)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("F1")
    ax.set_title("PII en español · F1 por tipo (100 prompts sintéticos)")
    ax.legend(loc="lower left")
    fig.tight_layout()
    fig.savefig(U.REPORTS / "pii_f1_por_tipo.png", dpi=150)

    U.update_metrics("pii_eval", {
        "n_prompts": len(rows), "n_entidades": n_ent, "soporte": support, "coincidencia": "solape + mismo tipo",
        "CE3_meta": 0.85, "resultados": res, "direccion_exacta": exact,
        "config_ajustada": {"min_location_score": args.min_location_score,
                            "lista_negra": sorted(presidio_es.LOCATION_BLACKLIST)},
        "config_4b": {"min_person_tokens": 2, "require_street_for_address": True,
                      "nota": "configuración que usa el backend (detectors/pii.py)"},
        "CE3_cumple": {n: res[n]["micro_todos_los_tipos"]["f1"] >= 0.85 for n in names},
        "address_antes_despues": {
            "antes": {k: res["regex+presidio"]["address"][k] for k in ("precision", "recall", "f1")},
            "despues": {k: res["regex+presidio_ajustado"]["address"][k] for k in ("precision", "recall", "f1")},
            "exacta_antes": exact["regex+presidio"], "exacta_despues": exact["regex+presidio_ajustado"]},
        "latencia_presidio_ms_por_prompt": ms,
        "errores_presidio_muestra": err_adj[:15],
        "n_prompts_con_error_presidio": {"base": len(err_base), "ajustado": len(err_adj)},
    })
    print(f"prompts con algún error · base: {len(err_base)} · ajustado: {len(err_adj)}")


if __name__ == "__main__":
    main()
