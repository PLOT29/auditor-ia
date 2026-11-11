# Auditor de Prompts en Tiempo Real (DLP para IA)

Proyecto final · Seguridad de la Información · IBERO. Ver [SPEC.md](SPEC.md).

## Estado

| Fase | Estado |
|---|---|
| 1 · Backend `/analyze` + secretos + PII básica + SQLite enmascarada + tests | ✅ |
| 2 · Extensión: captura + etiqueta flotante | ✅ (falta prueba manual en las 3 plataformas, CE-8) |
| 3 · Modo bloqueo / DLP local | pendiente (`shouldBlock()` es stub) |
| 4 · Clasificadores de tema e inyección + Presidio | pendiente (`topic.py` e `injection.py` son stubs) |
| 5 · Dashboard | pendiente |
| 6 · Evaluación y demo | pendiente |

## Entorno

- Repo: `C:\dev\auditor-ia` (fuera de OneDrive).
- Python: conda `auditor` (3.11).
- BD: `C:\auditor-data\auditor.db` por defecto, o la ruta de `AUDITOR_DB_PATH`.
  Nunca dentro del repo (SPEC §12, regla 8).

## Backend

```powershell
conda activate auditor
cd backend
pip install -r requirements.txt     # solo la primera vez
python -m pytest -q                 # tests
python -m uvicorn main:app --reload # servidor en http://127.0.0.1:8000
```

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

## Extensión (Chrome, Manifest V3)

1. Abre `chrome://extensions` y activa **Modo de desarrollador**.
2. **Cargar extensión sin empaquetar** → elige la carpeta `extension/`.
3. Tras cambiar código: botón ⟳ de la extensión **y** refrescar la pestaña del chat.

Página de prueba (solo desarrollo):

```powershell
cd extension\test
python -m http.server 5500
# abrir http://localhost:5500/test.html
```

> **Antes de la entrega:** quitar de `manifest.json` el segundo bloque de
> `content_scripts` (`localhost:5500` / `127.0.0.1:5500`), esas dos URLs de
> `web_accessible_resources` y el `version_name` "-dev". JSON no admite
> comentarios, por eso la marca está en `version_name` (visible en `chrome://extensions`).

Archivos:

| Archivo | Papel |
|---|---|
| `manifest.json` | Permisos: `storage` + hosts de las 3 plataformas y `localhost:8000` |
| `patterns.js` | Mismas regex y máscara que el backend (`test_patterns_parity.py` las compara con Node) |
| `content.js` | Captura en fase de captura (Enter / clic en Enviar), deduplicación, etiqueta en Shadow DOM |
| `background.js` | **Único** lugar con `fetch` al backend (timeout 3 s) |
| `overlay.css` | Estilos de la etiqueta (dentro del Shadow DOM) |
| `popup.html/js` | Interruptor de monitoreo (y de bloqueo, deshabilitado hasta Fase 3) |

## Qué detecta hoy

- **Secretos (regex):** `openai_key`, `anthropic_key`, `aws_access_key`,
  `github_token`, `google_api_key`, `jwt`, `private_key` (PEM), `password`
  (`password=` / `contraseña:` …, solo el valor), `credit_card` (con Luhn).
- **PII (regex):** `email`, `phone_mx`, `curp`, `rfc`.
  Nombre de persona y dirección llegan en Fase 4 con Presidio.

## Privacidad

- El texto original solo vive en memoria (en la extensión y durante la petición).
- En BD: `text_masked` (`<OPENAI_KEY>`, `<EMAIL>`…), `text_sha256` y hallazgos
  con valores enmascarados (`sk-****abcd`, `o****@gmail.com`).
- Logs y consola: solo metadatos (id, plataforma, riesgo, nº de hallazgos, latencia).
- Los errores de validación (422) no repiten el cuerpo recibido.
- Sin CORS a propósito: ninguna página web puede leer `/stats` ni mandar JSON a `/analyze`.
- `tests/test_api.py::test_privacy_no_plaintext_in_db_or_logs` verifica CE-9.
