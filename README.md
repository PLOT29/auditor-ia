# Auditor de Prompts en Tiempo Real (DLP para IA)

Proyecto final · Seguridad de la Información · IBERO. Ver [SPEC.md](SPEC.md).

## Estado

| Fase | Estado |
|---|---|
| 1 · Backend `/analyze` + secretos + PII básica + SQLite enmascarada + tests | ✅ |
| 2 · Extensión: captura + etiqueta | pendiente |
| 3 · Modo bloqueo / DLP local | pendiente |
| 4 · Clasificadores de tema e inyección + Presidio | pendiente (`topic.py` e `injection.py` son stubs) |
| 5 · Dashboard | pendiente |
| 6 · Evaluación y demo | pendiente |

## Backend (Fase 1)

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

pytest -q                                              # tests
uvicorn main:app --host 127.0.0.1 --port 8000          # servidor
```

> Si `python -m venv` falla con *"No such file or directory … Long Path"*, la
> carpeta del proyecto tiene una ruta demasiado larga: muévela a algo como
> `C:\dev\auditor-ia` o activa el soporte de rutas largas de Windows.

La BD se crea en `backend/auditor.db` (o en la ruta de `AUDITOR_DB_PATH`).

### Probar con curl

```bash
curl -s http://127.0.0.1:8000/health

curl -s -X POST http://127.0.0.1:8000/analyze -H "Content-Type: application/json" \
  -d '{"text":"Mi correo es demo.fake@gmail.com y CURP GOMJ800101HDFRRN09","platform":"claude"}'

curl -s http://127.0.0.1:8000/stats
```

En Windows, si el texto lleva acentos, la terminal puede no mandarlo en UTF-8
(error `There was an error parsing the body`). Guarda el JSON en un archivo
UTF-8 y usa `--data-binary @body.json`. La extensión no tiene este problema.

### Qué detecta hoy

- **Secretos (regex):** `openai_key`, `anthropic_key`, `aws_access_key`,
  `github_token`, `google_api_key`, `jwt`, `private_key` (PEM), `password`
  (`password=` / `contraseña:` …, solo el valor), `credit_card` (con Luhn).
- **PII (regex):** `email`, `phone_mx`, `curp`, `rfc`.
  Nombre de persona y dirección llegan en Fase 4 con Presidio.

### Privacidad

- El texto original solo vive en memoria durante la petición.
- En BD: `text_masked` (`<OPENAI_KEY>`, `<EMAIL>`…), `text_sha256` y hallazgos
  con valores enmascarados (`sk-****abcd`, `o****@gmail.com`).
- Los logs solo tienen metadatos (id, plataforma, riesgo, nº de hallazgos, latencia).
- Los errores de validación (422) no repiten el cuerpo recibido.
- `tests/test_api.py::test_privacy_no_plaintext_in_db_or_logs` verifica CE-9.
