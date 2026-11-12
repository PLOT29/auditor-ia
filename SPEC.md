# SPEC.md — Auditor de Prompts en Tiempo Real (DLP para IA)

> Proyecto final · Seguridad de la Información · IBERO
> Entrega: 2 semanas (≈ 25 nov 2026)
> **Instrucción para el agente de IA (Claude Code / Cursor):** lee este archivo completo antes de cada tarea. No cambies la arquitectura, el contrato de la API ni el esquema de la BD sin que se te pida explícitamente. Trabaja solo en la fase indicada.

---

## 1. Resumen

Una extensión de Chrome que **captura el prompt exacto** que el usuario envía a ChatGPT, Claude o Gemini, lo **clasifica en tiempo real** (tema + riesgo de seguridad), muestra el resultado **en la misma página** y, si detecta un secreto, **bloquea el envío antes de que salga de la computadora** (DLP). Todo se registra en una BD local y se visualiza en un dashboard con estadísticas de uso.

**Problema:** las personas pegan datos sensibles (API keys, contraseñas, datos personales) en asistentes de IA sin darse cuenta; una vez enviados, salen del control del usuario. Además no existe visibilidad de *cómo* se usa la IA.

**Objetivo:** demostrar un sistema de prevención de pérdida de datos (DLP) y auditoría de uso de IA que funcione en vivo, con métricas de desempeño medibles.

---

## 2. Alcance

### Dentro
- Extensión Chrome (Manifest V3) para `chatgpt.com`, `claude.ai`, `gemini.google.com`.
- Detección local y síncrona de secretos (regex) + modo bloqueo.
- Backend local (FastAPI) con detección de PII, clasificación de tema y de jailbreak/inyección.
- Almacenamiento local en SQLite con datos **enmascarados**.
- Dashboard en vivo (Streamlit).
- Evaluación cuantitativa de cada detector.

### Fuera (trabajo futuro, justificado)
| Excluido | Razón |
|---|---|
| Proxy de red / inspección TLS (mitmproxy, LiteLLM) | Requiere instalar certificados raíz; complejidad y riesgo no justificados para el MVP |
| Captura del prompt de sistema de IAs comerciales | Se agrega del lado del servidor; no viaja por el navegador. Extraerlo violaría los TyC |
| Multiusuario, autenticación, despliegue en la nube | El sistema es local y personal por diseño (privacidad) |
| Otros navegadores (Firefox, Safari) | Tiempo |
| Análisis de archivos adjuntos / imágenes | Tiempo |

### Principio ético
El sistema opera **solo en el navegador de quien lo instala y con su consentimiento explícito**. Nunca se instala de forma oculta ni se envían datos fuera de `localhost`.

---

## 3. Arquitectura

```
[Usuario escribe en chatgpt.com / claude.ai / gemini]
        │  Enter / clic en "Enviar"
        ▼
┌─────────────────────────── EXTENSIÓN ───────────────────────────┐
│ content.js                                                       │
│  1. Listener en FASE DE CAPTURA (keydown Enter sin Shift, click)  │
│  2. Lee texto del textarea / contenteditable activo (genérico)    │
│  3. Capa 1 — DLP local SÍNCRONO (regex de secretos)               │
│       └─ si hay secreto → preventDefault + stopImmediatePropagation│
│          → modal "¿Enviar de todos modos?"                         │
│  4. Envía {text, platform, ...} a background.js (runtime message) │
│ background.js (service worker)                                    │
│  5. fetch POST http://localhost:8000/analyze                      │
│  6. Devuelve resultado a content.js → etiqueta flotante           │
└──────────────────────────────────────────────────────────────────┘
        ▼
┌─────────────────────────── BACKEND (FastAPI) ────────────────────┐
│ Capa 2 — Análisis completo:                                       │
│   detectors/secrets.py   (regex, mismo set que la extensión)      │
│   detectors/pii.py       (Presidio + spaCy es_core_news_md)       │
│   detectors/topic.py     (embeddings + LogisticRegression)        │
│   detectors/injection.py (embeddings + clasificador)              │
│   risk.py                (reglas de agregación → bajo/medio/alto) │
│ db.py → SQLite (texto ENMASCARADO)                                │
└──────────────────────────────────────────────────────────────────┘
        ▼
[dashboard/app.py — Streamlit, refresco cada 3 s]
```

**Decisión clave (explicar en el reporte):** el bloqueo **no puede esperar al backend**, porque el evento de envío debe cancelarse de forma síncrona. Por eso hay **dos capas**: regex local inmediata (bloqueo) + análisis ML en backend (clasificación y estadística).

---

## 4. Estructura del repositorio

**Entorno de desarrollo:**
- El repo vive en `C:\dev\auditor-ia` (fuera de OneDrive u otras carpetas sincronizadas).
- Entorno Python: conda `auditor` (Python 3.11) → `conda activate auditor`.
- El servidor se levanta desde `backend/` con `python -m uvicorn main:app --reload`.
- La BD vive fuera del repo (ver §5.5).

```
auditor-ia/
├── SPEC.md
├── README.md
├── extension/
│   ├── manifest.json
│   ├── content.js
│   ├── background.js
│   ├── patterns.js        # regex de secretos (fuente única para la extensión)
│   ├── overlay.css
│   ├── popup.html / popup.js  # interruptores ON/OFF (RF-08)
│   └── test/test.html     # página de prueba local (solo desarrollo)
├── backend/
│   ├── main.py
│   ├── db.py
│   ├── risk.py
│   ├── masking.py
│   ├── detectors/
│   │   ├── secrets.py
│   │   ├── pii.py
│   │   ├── topic.py
│   │   └── injection.py
│   └── tests/
├── ml/
│   ├── data/              # datasets (no subir datos reales sensibles)
│   ├── train_topic.ipynb
│   ├── train_injection.ipynb
│   ├── evaluate.ipynb
│   └── models/            # .joblib
├── dashboard/
│   └── app.py
└── docs/
    ├── arquitectura.png
    └── capturas/
```

---

## 5. Componentes

### 5.1 Extensión (Manifest V3)
- `host_permissions`: `https://chatgpt.com/*`, `https://claude.ai/*`, `https://gemini.google.com/*`, `http://localhost:8000/*`.
- **El fetch al backend se hace SOLO desde `background.js`** (evita bloqueos CORS / Private Network Access).
- Captura de texto genérica: `textarea` **o** elemento `[contenteditable="true"]` con foco / más cercano al botón de envío. No depender de clases CSS específicas.
- Detección de plataforma por `location.hostname`.
- **Etiqueta flotante** (esquina inferior derecha, 5 s, apilable):
  - `✅ Sin riesgo · <categoría> · <plataforma>`
  - `⚠️ Riesgo medio · contiene <tipo PII> · <categoría>`
  - `⛔ Riesgo alto · contiene <tipo secreto> · <categoría>`
- **Modal de bloqueo:** muestra el tipo de secreto y el valor enmascarado. Botones: «Cancelar» (default) y «Enviar de todos modos». Si el usuario confirma, se reenvía el mensaje y se registra `user_decision = "enviado"`.
- Popup de la extensión: interruptor ON/OFF del monitoreo y del modo bloqueo.

### 5.2 Contrato de la API

`POST /analyze`
```json
{
  "text": "string",
  "platform": "chatgpt | claude | gemini",
  "timestamp": "ISO-8601",
  "client_findings": [{"type": "openai_key", "masked": "sk-****abcd"}],
  "blocked": true,
  "user_decision": "enviado | cancelado | no_aplica",
  "client_latency_ms": 0.4
}
```
- `client_latency_ms` (opcional, Fase 3): latencia de la decisión de bloqueo en `content.js` (CE-6). Solo se loguea; no se guarda en la BD.
- `platform` acepta además `"test"` para `extension/test/test.html` (solo desarrollo).

Respuesta:
```json
{
  "id": 123,
  "risk": "bajo | medio | alto",
  "category": "programacion",
  "category_confidence": 0.91,
  "injection_score": 0.07,
  "findings": [{"type": "email", "masked": "o****@gmail.com", "source": "pii"}],
  "latency_ms": 180
}
```
`GET /stats` → agregados para el dashboard. `GET /health` → `{"ok": true, "models": {...}, "thresholds": {...}}` (qué modelos cargaron y umbrales vigentes; la extensión solo lee `ok`).

**Fase 4B — modo degradado:** si un modelo no carga, el backend sigue funcionando: `category = "sin_clasificar"` (con `category_confidence = 0.0`), `injection_score = null` y PII solo con regex. Por eso `injection_score` admite `null`.

**Fase 4C — campo opcional `category_uncertain: bool | null`** (cambio aditivo; los clientes que no lo lean no se ven afectados): `true` si `category_confidence` < umbral de confianza del tema (`metrics.json → topic_v2.umbral_confianza.umbral`), `false` si no, y `null` sin clasificador de tema. La BD no cambia: sigue guardando la categoría más probable y su confianza. La extensión muestra "tema incierto (¿categoría?)" cuando es `true`.

### 5.3 Detectores
| Detector | Método | Tipos |
|---|---|---|
| Secretos | Regex | API keys (OpenAI `sk-`, Anthropic `sk-ant-`, AWS `AKIA…`, GitHub `ghp_`, Google `AIza…`), JWT, claves privadas PEM, `password=`/`contraseña:` , tarjetas (con validación Luhn) |
| PII | Presidio (es) con spaCy `es_core_news_md` + recognizers propios (`backend/detectors/presidio_es.py`, compartido con `ml/pii_eval.py`) | Correo, teléfono MX, CURP, RFC, nombre de persona (≥ 2 palabras), dirección (con calle, número, Col. o C.P.; una ciudad sola no cuenta). Fusión de fragmentos de dirección y lista negra de falsos LOCATION |
| Tema | `paraphrase-multilingual-MiniLM-L12-v2` + LogisticRegression (`ml/models/topic_clf.joblib`, **v2** desde la Fase 4C, entrenado con `ml/data/topic_synthetic_v2.csv`; el v1 queda en `topic_clf_v1.joblib`) | `programacion`, `academico`, `redaccion`, `ciberseguridad`, `consulta_general`, `personal` (definiciones abajo) |
| Inyección/jailbreak | **Ensamble**: promedio de TF-IDF (palabras + caracteres) + LR y MiniLM + LR (`ml/models/injection_final.joblib`, inferencia de `ml/injection_model.py`) | score 0–1 |

#### Definición de las categorías de tema (Fase 4C · guía completa en `ml/LABELING.md`)

Se elige una sola etiqueta: la del **tema central**. **Desempate, en orden:** (1) pedir producir o transformar un texto que no es código → `redaccion`; (2) el tema central es seguridad → `ciberseguridad`; (3) hay código concreto o una tarea de desarrollo → `programacion`; (4) contexto escolar o concepto formal de una materia → `academico`; (5) la propia vida → `personal`; (6) lo demás → `consulta_general`.

| Categoría | Definición | Positivo | Frontera (→ etiqueta, regla) |
|---|---|---|---|
| `ciberseguridad` | **Solo** si el tema central es seguridad: ataques, vulnerabilidades, malware, cifrado, autenticación, privacidad, hardening, forense. **Hardware, redes o software en general NO** | "¿Cómo detecto un correo de phishing?" | "¿Qué es una tarjeta gráfica?" → `consulta_general` (6) |
| `programacion` | Escribir, depurar o explicar **código concreto** (errores, comandos, librerías, SQL) | "Mi Python da `KeyError` al leer el JSON" | "¿Qué es Python?" → `consulta_general` (6) |
| `academico` | Tareas, exámenes, conceptos de una materia, investigación | "Explícame la 2.ª ley de Newton con un ejercicio" | "¿Qué hace la ALU? Es para mi clase" → `academico` (4) |
| `consulta_general` | Dudas generales, **incluida la tecnología cotidiana** (hardware, apps, redes, cómo funciona algo) sin contexto escolar | "¿Qué laptop me recomiendas para editar video?" | "¿Es seguro el wifi del aeropuerto?" → `ciberseguridad` (2) |
| `redaccion` | Escribir, corregir o reformular textos | "Redacta un correo para pedir una prórroga" | "Escribe un ensayo sobre phishing" → `redaccion` (1) |
| `personal` | Vida personal, salud, finanzas personales, relaciones, planes | "Ayúdame con un presupuesto con mi sueldo" | "Me hackearon el Instagram" → `ciberseguridad` (2) |

**Incertidumbre (Fase 4C):** si la confianza del clasificador es menor que el umbral de `metrics.json → topic_v2.umbral_confianza`, `/analyze` responde `category_uncertain: true` y la etiqueta muestra "tema incierto (¿categoría?)". En la BD se guarda igual la categoría más probable y su confianza.

Los embeddings se calculan **una sola vez por prompt** y los reutilizan tema e inyección. Los modelos se cargan una vez al arrancar (rutas configurables: `AUDITOR_MODELS_DIR`, `AUDITOR_METRICS_PATH`). El backend no sale a internet: sentence-transformers usa la caché local (`HF_HUB_OFFLINE=1`).

**Reclasificación (`ml/reclassify.py`):** recalcula tema, inyección y riesgo de filas ya guardadas usando `text_masked`, porque el texto original no existe por diseño. Los marcadores (`<EMAIL>`, `<OPENAI_KEY>`…) sustituyen los datos, así que la precisión puede ser **un poco menor** que con el texto original. No toca `findings`, `blocked` ni `user_decision`, y el riesgo solo puede subir.

### 5.4 Reglas de riesgo (`risk.py`)
- **Alto:** cualquier secreto **o** `injection_score ≥ umbral alto`.
- **Medio:** cualquier PII **o** `umbral medio ≤ injection_score < umbral alto`.
- **Bajo:** lo demás. `injection_score = null` no suma riesgo.

Umbrales finales (Fase 4B):
- **Alto = 0.514**, el umbral del detector elegido. Se lee en tiempo de ejecución de `ml/reports/metrics.json → injection_4A1.final.umbral` (nunca está hardcodeado); se eligió con predicciones out-of-fold del train para recall ≥ 0.90.
- **Medio = 0.50**, configurable con `AUDITOR_INJECTION_MEDIUM` (nunca mayor que el alto). Con el detector calibrado la franja "medio" por inyección es estrecha (0.50–0.514); en la práctica, "medio" viene sobre todo de la PII.

### 5.5 Base de datos (SQLite) — tabla `prompts`
| Campo | Tipo | Nota |
|---|---|---|
| id | INTEGER PK | |
| ts | TEXT | ISO-8601 |
| platform | TEXT | |
| text_masked | TEXT | secretos y PII reemplazados: `<OPENAI_KEY>`, `<EMAIL>`… |
| text_sha256 | TEXT | para deduplicar sin guardar el original |
| length | INTEGER | |
| category | TEXT | |
| category_conf | REAL | |
| injection_score | REAL | |
| risk | TEXT | |
| findings_json | TEXT | solo tipos y valores enmascarados |
| blocked | INTEGER | 0/1 |
| user_decision | TEXT | |
| latency_ms | INTEGER | |
| source | TEXT | `extension` (capturado en vivo) \| `import` (historial importado con `ml/import_history.py`). Default `extension` |

**Migraciones:** `db.init_db()` agrega las columnas nuevas de forma idempotente (`ALTER TABLE … ADD COLUMN` solo si no existen); las filas anteriores quedan con el valor por defecto.

**Ubicación:** la ruta de la BD se lee de la variable de entorno `AUDITOR_DB_PATH` (default: `C:\auditor-data\auditor.db`). Nunca dentro del repo; la carpeta se crea si no existe.

**Regla de oro:** el texto original **nunca** se escribe en disco ni en logs.

### 5.6 Dashboard (Streamlit)
- KPIs arriba: total de prompts · % riesgo alto · envíos bloqueados · plataforma más usada.
- Gráficas: prompts por plataforma · temas más frecuentes · riesgo por plataforma · mapa de calor día × hora · tendencia diaria.
- Tabla de alertas recientes (texto enmascarado).
- Refresco automático cada 3 s.

---

## 6. Requisitos

### Funcionales
- **RF-01** Capturar el texto exacto enviado en las 3 plataformas.
- **RF-02** Bloquear el envío cuando se detecte un secreto y pedir confirmación.
- **RF-03** Mostrar una etiqueta con riesgo, hallazgos y categoría en la página.
- **RF-04** Clasificar cada prompt por tema.
- **RF-05** Detectar PII y calcular score de inyección/jailbreak.
- **RF-06** Persistir cada evento enmascarado en SQLite.
- **RF-07** Mostrar estadísticas en un dashboard que se actualice en vivo.
- **RF-08** Permitir activar/desactivar monitoreo y bloqueo desde el popup.

### No funcionales
- **RNF-01** Bloqueo local en < 50 ms (imperceptible).
- **RNF-02** Etiqueta visible en < 1 s tras el envío (p95).
- **RNF-03** Ningún dato sale de `localhost`.
- **RNF-04** Ningún secreto ni PII en claro en disco o logs.
- **RNF-05** Si el backend está caído, la extensión sigue bloqueando secretos (degradación elegante) y muestra `Backend no disponible`.
- **RNF-06** El uso normal del chat no se rompe (no duplicar mensajes, no perder texto).

---

## 7. Criterios de éxito (medibles)

| # | Criterio | Meta | Cómo se mide |
|---|---|---|---|
| CE-1 | Recall de secretos | ≥ 0.95 | Set de prueba de 100 prompts con secretos falsos |
| CE-2 | Falsos positivos de bloqueo | ≤ 3 % | 200 prompts normales; % bloqueados por error |
| CE-3 | F1 de PII (es) | ≥ 0.85 | Set etiquetado de 100 prompts |
| CE-4 | F1 macro de tema | ≥ 0.80 | Test REAL de **~100 prompts etiquetados a mano** (antes 150), muestreados de la BD de forma estratificada por plataforma. Se reduce porque el export de Claude solo trajo texto desde el 2026-10-31. Se reporta F1 macro con IC 95 % por bootstrap (1000 remuestreos), F1 por plataforma y matriz de confusión. El entrenamiento usa solo sintéticos |
| CE-5 | Recall de inyección/jailbreak | ≥ 0.85 (con precision ≥ 0.80) | Dataset público + prompts propios en español |
| CE-6 | Latencia bloqueo local | < 50 ms | `performance.now()` en content.js |
| CE-7 | Latencia etiqueta p95 | < 1 s | `latency_ms` en BD |
| CE-8 | Cobertura de plataformas | 3/3 | Prueba manual documentada con capturas |
| CE-9 | Privacidad | 0 secretos en claro | `grep` de los secretos de prueba sobre la BD y logs |
| CE-10 | Robustez | Funciona con backend apagado | Prueba manual |
| CE-11 | Uso real | ≥ 150 prompts propios registrados | Conteo en BD antes de la presentación. Los prompts importados del historial (`source = 'import'`) cuentan como uso real, pero se reportan **por separado** de los capturados con la extensión (`/stats → por_origen`) |

**Baselines para comparar en el reporte:** solo regex vs. regex + Presidio (PII); TF-IDF vs. embeddings (tema e inyección).

### Resultados obtenidos (Fase 4B · detalle en `ml/reports/metrics.json`)

| # | Resultado | ¿Cumple? |
|---|---|---|
| CE-1 / CE-2 | Paridad JS ↔ Python en 52 casos de secretos (0 falsos positivos en los 24 negativos); falta medirlo con los sets de 100 y 200 prompts | ⏳ |
| CE-3 | F1 micro PII **0.99** (P 0.98 / R 1.00) con Presidio en la configuración de producción; solo regex: 0.76. Sobre 100 prompts **sintéticos**: es optimista para las regex, que reconocen exactamente los formatos generados | ✅ (sintético) |
| CE-4 | **Pendiente** del test real (~100 prompts). Provisionales, sin contar para CE-4: modelo **v2** (Fase 4C), F1 macro **0.86** en CV agrupada sobre sintéticos v2 y **0.83** en el probe set de 60 casos de frontera (v1: 0.68). Umbral de confianza 0.61 (cobertura 85 %) | ⏳ |
| CE-5 | Ensamble con umbral 0.514, **test (n = 431): P 0.978 / R 0.872 / F1 0.922**; en español (n = 40): **P 0.826 / R 0.950** | ✅ (ver nota) |
| CE-6 | Decisión de bloqueo en `content.js`: p95 0.1 ms (prompts del set), 0.4 ms (10 000 caracteres) | ✅ |
| CE-7 | `/analyze` en CPU con 50 prompts: p95 del pipeline **104 ms**, de ida y vuelta HTTP **121 ms** (PII 57 ms y embeddings 36 ms en p95) | ✅ |

> **Nota metodológica (CE-5, Fase 4A.1):** la regla pre-registrada (umbral con recall OOF = 0.85, sin margen) **no cumplía** en el test: recall 0.81. El procedimiento se ajustó **después** de esa primera evaluación: objetivo de recall 0.90 en CV, comparación de 4 candidatos solo en CV y una única evaluación final en el test. Como el test ya se había mirado, el resultado de CE-5 debe leerse como **optimista**; la validación definitiva es el uso real (CE-11). Detalle en `metrics.json → injection_4A1.nota_metodologica`.

---

## 8. Guion de la demo (≈ 5 min)

**Preparación:** backend corriendo, dashboard abierto en segunda pantalla, extensión activa, BD con ≥ 150 prompts reales de las 2 semanas. Video de respaldo grabado por si falla la red.

**Golpe 1 — "Te estoy leyendo" (1 min)**
1. Abrir ChatGPT frente a la audiencia.
2. Escribir: *"Explícame qué es un firewall"* → Enter.
3. Aparece `✅ Sin riesgo · académico · ChatGPT`.
4. Señalar el dashboard: el contador sube en vivo.
5. Frase: *"Todo lo que le escribes a una IA puede ser capturado y clasificado."*

**Golpe 2 — "Te salvé" (1.5 min)**
1. Pegar: *"Corrige este código: `client = OpenAI(api_key='sk-proj-DEMO…')`"* (key falsa preparada).
2. Enter → **el mensaje NO se envía**. Aparece el modal rojo: `⛔ Detectada API key de OpenAI (sk-****DEMO). ¿Enviar de todos modos?`
3. Cancelar. Mostrar en el dashboard el evento `bloqueado / cancelado`, con la key enmascarada.
4. Frase: *"El dato nunca salió de mi computadora. Esto es DLP."*
5. Repetir rápido en Claude con un correo + CURP falsos → `⚠️ Riesgo medio`.

**Golpe 3 — "Así usas la IA" (1.5 min)**
1. Dashboard con los datos reales de 2 semanas: plataforma más usada, temas, mapa de calor, % de prompts con datos sensibles.
2. Frase con el hallazgo real: *"El X % de mis prompts contenían datos personales y no me había dado cuenta."*

**Remate — "Ahora usted" (1 min)**
1. Ofrecer el teclado al profesor para que escriba lo que quiera (incluido un intento de jailbreak).
2. Su prompt aparece clasificado en la página y en el dashboard.

**Cierre:** mapeo a OWASP Top 10 for LLM Applications (LLM01 Prompt Injection, LLM02 Sensitive Information Disclosure) y limitaciones.

---

## 9. Plan por fases

| Fase | Días | Entregable | Criterio para avanzar |
|---|---|---|---|
| 1 | 1–2 | Backend `/analyze` con regex de secretos + PII básica, SQLite enmascarada, tests | `pytest` verde; `curl` devuelve JSON correcto |
| 2 | 3–4 | Extensión: captura en 3 plataformas + etiqueta flotante | CE-8 cumplido |
| 3 | 5 | Modo bloqueo / DLP local | CE-6, CE-10, RNF-06 |
| 4 | 6–9 | Clasificadores de tema e inyección + Presidio es | CE-3, CE-4, CE-5 |
| 5 | 10–11 | Dashboard en vivo | RF-07 |
| 6 | 12–14 | Evaluación, capturas, reporte, ensayo de demo, video de respaldo | Todos los CE medidos |

**Desde el día 3:** usar la extensión en el día a día para acumular datos reales (CE-11).

---

## 10. Mapeo a marcos de seguridad
| Componente | Marco |
|---|---|
| Detección de secretos y PII + bloqueo | OWASP LLM02 — Sensitive Information Disclosure · concepto DLP |
| Detector de inyección/jailbreak | OWASP LLM01 — Prompt Injection · MITRE ATLAS (LLM Prompt Injection) |
| Enmascarado y almacenamiento local | Minimización de datos (LFPDPPP, principios de privacidad) |
| Auditoría y estadísticas | NIST AI RMF — función *Measure* |

> Verificar versiones vigentes de OWASP Top 10 for LLM y MITRE ATLAS antes de citarlas.

---

## 11. Riesgos y mitigaciones
| Riesgo | Mitigación |
|---|---|
| Las plataformas cambian su HTML | Captura genérica (textarea / contenteditable); probar cada 2–3 días |
| El reenvío tras "Enviar de todos modos" duplica o pierde el mensaje | Bandera `bypassOnce` y re-disparar el clic original; probar en las 3 plataformas |
| Pocos datos en español para inyección | Traducir/parafrasear ejemplos y crear 50–100 propios |
| Presidio débil en español | Recognizers regex propios para CURP, RFC, teléfono MX |
| Falla en vivo | Video de respaldo + BD precargada |

### Limitaciones
- **Tamaño del test real de tema (~100 prompts):** da intervalos de confianza amplios. Con n ≈ 100 y 6 clases, el IC 95 % del F1 macro puede tener ±0.08 o más, y las clases con pocos ejemplos (≈ 10–20) tienen F1 por clase muy inestables. Por eso CE-4 se reporta con su intervalo, no solo con el valor puntual.
- **Historial incompleto:** el export de Claude no trae texto antes del 2026-10-31, así que el uso real disponible para el test y para CE-11 es más corto de lo previsto.
- **Test de inyección no del todo ciego:** el umbral se ajustó después de la primera evaluación (ver `ml/reports/metrics.json → injection_4A1.nota_metodologica`).

---

## 12. Reglas para el agente de IA (vibe coding)
1. Lee este SPEC antes de cada tarea; trabaja **solo en la fase indicada**.
2. No cambies el contrato de la API, el esquema de BD ni la estructura de carpetas sin pedirlo.
3. Nunca escribas el texto original del prompt en disco ni en logs.
4. El fetch al backend va **solo** en `background.js`.
5. Toda función de detección lleva tests en `backend/tests/`.
6. Usa únicamente secretos **falsos** en tests y ejemplos.
7. Al terminar, resume qué archivos cambiaste y cómo probarlo manualmente.
8. La BD y el entorno nunca viven dentro del repo ni en carpetas sincronizadas con la nube.
