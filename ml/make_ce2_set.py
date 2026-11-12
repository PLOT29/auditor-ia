"""Set de CE-2 (falsos positivos de bloqueo): 200 prompts NORMALES, sin datos sensibles reales.

    python ml/make_ce2_set.py      → ml/data/ce2_prompts_normales.csv  (id, text, origen)

Composición (determinista, semilla 42):
  - 100 de ml/data/topic_synthetic_v2.csv (estratificado: ~17 por categoría de tema)
  -  60 de ml/data/topic_probe.csv (casos de frontera de tema, todos)
  -  40 escritos a mano (Fase 5A.1): prompts técnicos y cotidianos que se PARECEN a lo que
     detectan las reglas nuevas (variables token/password en código, ${VARS}, URIs sin
     contraseña, menciones sueltas de calles y colonias, "#123", "lote 12", "tarjeta terminada
     en 4412"…). Se escribieron ANTES de medir y no se ajustaron después.
Ninguno debería bloquearse. Lo usan tests/test_ce2_false_positives.py y extension/test/ce2_fp.js.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
OUT = DATA / "ce2_prompts_normales.csv"

HAND = [
    "¿Cómo leo una variable de entorno en Python? Tengo API_KEY = os.environ.get('API_KEY') y me sale None.",
    "En mi docker-compose tengo POSTGRES_PASSWORD: ${POSTGRES_PASSWORD} ¿está bien así o debo usar secrets?",
    "Explícame qué hace el header Authorization: Bearer <token> en una API REST.",
    "Mi función get_token() regresa undefined: const token = await getToken(); console.log(token)",
    "¿Por qué mi tokenizer = AutoTokenizer.from_pretrained('bert-base-uncased') tarda tanto?",
    "Configura max_tokens = 4096 y temperature = 0.7 en la llamada a la API, ¿es mucho?",
    "Tengo un error 404 en https://api.github.com/repos/usuario/proyecto/issues/123, ¿qué significa?",
    "En el issue #482 del repositorio dicen que la versión 3.2 tiene un bug, ¿lo confirmas?",
    "La conexión es postgresql://localhost:5432/midb y no me deja entrar, ¿qué reviso?",
    "Usa la URL mongodb://usuario:<password>@cluster0.mongodb.net/test como plantilla, ¿qué parámetros le faltan?",
    "¿Qué diferencia hay entre una clave pública pk_test y una secreta en Stripe?",
    "Mi CSS tiene .password-field { width: 100% } y no se alinea, ayuda",
    "¿Cómo valido que password.length >= 12 en un formulario de React?",
    "Hice SELECT * FROM users WHERE token_expires_at < NOW(); ¿cómo lo optimizo?",
    "Quiero llegar al Ángel de la Independencia en Reforma desde Insurgentes, ¿qué metro me deja cerca?",
    "Vivo cerca de Av. Universidad, ¿qué gimnasios hay por la zona?",
    "La colonia Roma tiene muchos cafés, recomiéndame tres.",
    "El departamento de ventas necesita un reporte trimestral, ¿cómo lo estructuro?",
    "Mi pedido 20261112 no ha llegado, redacta un correo de reclamo a la tienda.",
    "Calcula el 16% de IVA de 12500 pesos.",
    "Escribe una función que valide un código postal mexicano de 5 dígitos.",
    "¿Qué es un JWT y por qué empieza con eyJ?",
    "Explica la diferencia entre access_token y refresh_token en OAuth 2.0.",
    "En Laravel, ¿dónde va APP_KEY y cómo se genera con php artisan key:generate?",
    "Tengo SECRET_KEY = config('SECRET_KEY') en Django, ¿así se lee del .env?",
    "¿Cómo genero una clave SSH con ssh-keygen -t ed25519 para GitHub?",
    "Mi código: password = input('Contraseña: ') ¿cómo oculto lo que se escribe?",
    "Traduce al inglés: 'Nos vemos en la calle Madero a las 5'.",
    "La tarjeta terminada en 4412 me cobró doble, redacta una queja para el banco.",
    "Redacta un aviso para los vecinos del edificio sobre el mantenimiento del elevador el lunes.",
    "Resume la historia de la Avenida de los Insurgentes en la Ciudad de México.",
    "¿Cuántos municipios tiene el Estado de México?",
    "Haz un script de bash que lea $HOME y liste los archivos .env del proyecto.",
    "¿Por qué git dice 'Permission denied (publickey)' al hacer push?",
    "En mi YAML: api_version: v2 y timeout: 30 ¿está bien la indentación?",
    "Dame un regex para validar RFC de persona moral.",
    "¿Qué es el NIE en España y cómo se tramita?",
    "¿Qué significa C.P. en una dirección postal?",
    "Mi lote 12 de producción salió con defectos, redacta un reporte de calidad.",
    "Corrige: const headers = { Authorization: `Bearer ${token}` }; fetch(url, { headers })",
]


def main() -> None:
    rng = random.Random(42)
    with (DATA / "topic_synthetic_v2.csv").open(encoding="utf-8") as f:
        synth = list(csv.DictReader(f))
    by_label: dict[str, list[dict]] = {}
    for r in synth:
        by_label.setdefault(r["label"], []).append(r)
    labels = sorted(by_label)
    quota = {lab: 100 // len(labels) for lab in labels}
    for lab in labels[: 100 - sum(quota.values())]:
        quota[lab] += 1
    rows = []
    for lab in labels:
        for r in rng.sample(by_label[lab], quota[lab]):
            rows.append({"text": r["text"], "origen": f"sintetico_v2:{lab}"})
    with (DATA / "topic_probe.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows.append({"text": r["text"], "origen": f"probe:{r['label']}"})
    rows += [{"text": t, "origen": "manual_5A1"} for t in HAND]
    assert len(rows) == 200, len(rows)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "text", "origen"])
        w.writeheader()
        for i, r in enumerate(rows, 1):
            w.writerow({"id": f"ce2-{i:03d}", **r})
    print(f"{OUT.relative_to(DATA.parent.parent)}: {len(rows)} prompts")


if __name__ == "__main__":
    main()
