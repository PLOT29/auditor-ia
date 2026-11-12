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

### Modo bloqueo (Fase 3)

- Si el prompt contiene un **secreto**, el envío se cancela en la fase de captura
  (antes que cualquier listener de la página) y se abre un modal. La PII no bloquea.
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
node extension/test/patterns.test.js     # 59 casos + latencia CE-6
node extension/test/background.test.js   # cola de reintento
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
