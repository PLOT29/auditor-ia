# Auditor de Prompts en Tiempo Real (DLP para IA)

Proyecto final · Seguridad de la Información · IBERO. Ver [SPEC.md](SPEC.md).

## Estado

| Fase | Estado |
|---|---|
| 1 · Backend `/analyze` + secretos + PII básica + SQLite enmascarada + tests | ✅ |
| 2 · Extensión: captura + etiqueta flotante | ✅ |
| 3 · Modo bloqueo / DLP local | ✅ en test.html (falta validar el reenvío en ChatGPT / Claude / Gemini) |
| 4 · Clasificadores de tema e inyección + Presidio | ✅ integrados al backend (4B); CE-4 pendiente del test real etiquetado. Ver [ml/README.md](ml/README.md) |
| 5 · Dashboard | ✅ Streamlit en vivo (ver abajo) |
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

## Extensión «Centinela» (Chrome, Manifest V3)

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
| `patterns.js` | Catálogo de tipos (nombre, categoría, política por defecto, riesgo, marcador), secretos + PII (correo, teléfono, CURP, RFC, DNI, NIE, dirección heurística) con las mismas regex que el backend, diccionario propio y censura (`test_patterns_parity.py` las compara con Node) |
| `content.js` | Escudo en vivo, políticas por tipo (bloquear / advertir / permitir), modal y banner, censura en el campo, pegado grande |
| `ui.js` | Componentes de Centinela (escudo, banner, modal, tarjetas) en un Shadow DOM cerrado, sin recursos externos |
| `icons/` | Escudo de Centinela (PNG para la barra de Chrome) |
| `background.js` | **Único** lugar con `fetch` al backend (timeout 3 s) |
| `overlay.css` | Estilos de Centinela (tokens oscuros, `prefers-reduced-motion`) |
| `popup.html/js` | Estado, resumen de hoy, interruptores, políticas por tipo, diccionario propio, umbral de pegado y acceso al centro de control |

### Modo bloqueo (Fase 3)

- Si el prompt contiene algo cuya política es **bloquear** (por defecto: secretos, CURP, RFC, DNI,
  NIE y dirección de vivienda), el envío se cancela en la fase de captura (antes que cualquier
  listener de la página) y se abre un modal. Correo, teléfono, `org-` de OpenAI, `pk_` de Stripe y
  el diccionario propio solo **advierten** (banner).
- «Cancelar» (default, también Escape) deja el texto en el input y registra
  `blocked:true, user_decision:"cancelado"`.
- «Enviar de todos modos» reenvía con `button.click()` → si la página no lo acepta,
  `KeyboardEvent` Enter → si tampoco, pide al usuario presionar Enviar otra vez
  (ventana de 10 s). Solo se registra `"enviado"` cuando la página realmente envió.
- Backend apagado: el bloqueo sigue funcionando; los eventos bloqueados se guardan
  **enmascarados** en `chrome.storage.local` (máx. 100) y se reenvían tras el
  siguiente envío exitoso. El popup muestra cuántos hay en cola.
- Limitación: un evento reenviado desde la cola llega con el texto ya enmascarado,
  así que en la BD su `length` y `text_sha256` son los del texto enmascarado.

### Pruebas de la extensión (Node)

```bash
node extension/test/patterns.test.js     # 163 casos + 7 de demo + latencia CE-6 y escudo (< 10 ms)
node extension/test/background.test.js   # cola de reintento + migración de políticas
node extension/test/ce2_fp.js            # CE-2: falsos positivos de bloqueo en 200 prompts normales
```

`ce2_fp.js --patterns <otra/patterns.js>` mide otra versión (antes/después) y
`--db C:uditor-datauditor.db` lo repite sobre el historial real (`text_masked`, solo conteos).

**Casos de demo (Fase 5A.1):** `extension/test/demo_cases.json` (6 escenarios simulados, valores
falsos). En `test.html`, el selector «Casos de demo» inserta cada uno en el último campo enfocado y
muestra los hallazgos y la acción esperados.

```bash
python ml/make_ce2_set.py                # regenera ml/data/ce2_prompts_normales.csv
```

Ambas corren también dentro de `python -m pytest` (`test_patterns_parity.py`).

## Dashboard (Fase 5)

```powershell
conda activate auditor
streamlit run dashboard/app.py
```

Abre http://127.0.0.1:8501. Lee la BD de `AUDITOR_DB_PATH` (default `C:uditor-datauditor.db`)
**en solo lectura** y se refresca cada 3 s. Para proyectar, deja activado el **Modo presentación**
(viene así por defecto): oculta todo texto de prompts.
Streamlit toma `dashboard/.streamlit/config.toml` (solo localhost, sin telemetría). Si alguna vez
no lo encuentra, el dashboard se niega a mostrar datos; en ese caso usa `python dashboard/run.py`,
que fuerza esas opciones.

## Qué detecta hoy

La lista completa (con política por defecto, riesgo y marcador) está en `backend/detectors/catalog.py`
(= `CATALOG` de `extension/patterns.js`).

- **Secretos (regex, bloquean):** `openai_key` (`sk-`, `sk-proj-`, `sk-svcacct-`, `sk-admin-`),
  `anthropic_key`, `google_api_key`, `stripe_key` (`sk_`/`rk_` live/test), `stripe_webhook_secret`
  (`whsec_`), `aws_access_key` (AKIA/ASIA + 16 o más), `aws_secret_key` (por contexto),
  `github_token`, `jwt`, `bearer_token`, `uri_credentials` (solo la contraseña de
  `esquema://usuario:contraseña@host`), `password`, `secret_assignment` (`*_SECRET=`, `api_key:`,
  `dbPassword = "…"`…; ignora placeholders), `private_key` (bloque PEM completo), `credit_card` (Luhn).
- **Identificadores (advierten):** `openai_org` (`org-…`), `stripe_publishable_key` (`pk_…`, públicas).
- **Datos personales:** bloquean `curp`, `rfc`, `dni`, `nie` (con letra de control: `valido`) y
  `address` (heurística local de señales: vía + número, Col., C.P., Alcaldía, Int., Mz./Lt.; hacen
  falta 2 señales). Advierten `email`, `phone_mx` y `person` (este último solo en el backend, con Presidio).

## Privacidad

- El texto original solo vive en memoria (en la extensión y durante la petición).
- En BD: `text_masked` (`<OPENAI_KEY>`, `<EMAIL>`…), `text_sha256` y hallazgos
  con valores enmascarados (`sk-****abcd`, `o****@gmail.com`).
- Logs y consola: solo metadatos (id, plataforma, riesgo, nº de hallazgos, latencia).
- Los errores de validación (422) no repiten el cuerpo recibido.
- Sin CORS a propósito: ninguna página web puede leer `/stats` ni mandar JSON a `/analyze`.
- `tests/test_api.py::test_privacy_no_plaintext_in_db_or_logs` verifica CE-9.
