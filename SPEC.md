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
│ analytics.py → consultas de solo lectura (panel y dashboard)      │
│ events_bus.py → GET /events (SSE, resúmenes SIN texto)            │
└──────────────────────────────────────────────────────────────────┘
        ▼
[GET /panel — Centro de control (Fase 5B), en vivo por SSE]
[dashboard/app.py — Streamlit, respaldo, refresco cada 3 s]
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
│   ├── analytics.py       # consultas de estadística (solo lectura), compartidas panel ↔ dashboard (5B)
│   ├── events_bus.py      # bus en memoria para GET /events (SSE) (5B)
│   ├── panel/             # centro de control: index.html, panel.css, panel.js, vendor/echarts (5B)
│   ├── detectors/
│   │   ├── catalog.py     # catálogo de tipos (5A.1)
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
│   ├── app.py             # respaldo en Streamlit
│   └── queries.py         # shim: reexporta backend/analytics.py
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
- **Identidad (Fase 5A): "Centinela".** Escudo propio (SVG original), estilo oscuro. Tokens: fondo `#0B1020`, superficie `#131A2E`, borde `#24304D`, texto `#E6EAF2`, acento `#22D3EE`, seguro `#34D399`, advertencia `#FBBF24`, peligro `#F87171`. Fuentes del sistema (Segoe UI; Cascadia Code/Consolas para datos). Toda la UI vive en **un Shadow DOM cerrado** (`extension/ui.js` + `overlay.css`), sin recursos externos (RNF-03); respeta `prefers-reduced-motion`, con foco visible y navegable por teclado.
- **Escudo en vivo:** indicador junto al campo activo (esquina inferior derecha del campo, posicionado con `getBoundingClientRect`; si no hay campo, la de la ventana). Al escribir o pegar evalúa el texto **localmente** con debounce de 150 ms: verde (nada), ámbar (solo hallazgos que advierten), rojo (algo que se bloquearía). Al pasar el cursor o enfocarlo muestra los tipos con valores enmascarados y la acción de cada uno. Sin llamadas al backend al teclear. Costo medido (5A.1, con las reglas nuevas): p95 ≤ 2.1 ms con 5 000 caracteres y 50 términos, también con textos adversariales para la heurística de direcciones y las asignaciones (meta < 10 ms; `patterns.test.js`).
- **Detección local (`patterns.js`):** secretos + correo, teléfono MX, CURP, RFC, DNI, NIE y dirección de vivienda (heurística) con las MISMAS regex que el backend (paridad verificada en `test_patterns_parity.py` y `patterns.test.js`) + diccionario propio. Los nombres de persona solo los detecta el backend (Presidio); no se intenta localmente.
- **Catálogo de tipos (Fase 5A.1):** `CATALOG` en `patterns.js` = `backend/detectors/catalog.py` (paridad verificada): nombre, categoría/motivo (`secreto` · `dato_personal` · `termino_propio`), política por defecto, riesgo y marcador de censura de cada tipo. Popup, escudo, modal, banner, censura, backend y dashboard leen de ahí.
- **Políticas por tipo** (popup → `chrome.storage.local`, leídas en memoria): `bloquear` · `advertir` · `permitir`. **Por defecto (5A.1):** secretos, CURP, RFC, DNI, NIE y dirección = bloquear; correo, teléfono, ID de organización de OpenAI (`org-…`), clave pública de Stripe (`pk_…`) y diccionario propio = advertir. El interruptor "Aplicar políticas" apagado equivale a "permitir" para todo (solo monitoreo). Migración: al actualizar desde la 5A, CURP y RFC que seguían en su default viejo ("advertir") pasan a "bloquear"; si el usuario los había cambiado, se respeta (`policiesVersion: 2`).
- **Advertir (flujo que no pierde ni duplica mensajes, RNF-06):** un banner no modal sobre el campo ("Tu mensaje incluye un CURP") con «Censurar», «Enviar así» y «Editar».
  - Si el banner ya estaba visible para esos mismos hallazgos, un Enter o clic real **envía** (el usuario ya fue avisado) y se registra `advertido`; no hay reenvío nuestro, así que no puede duplicarse.
  - Si el usuario intenta enviar antes de verlo (p. ej. Enter antes de los 150 ms), se detiene **ese** envío una sola vez y aparece el banner con el foco en «Censurar». El texto queda intacto.
  - «Censurar» reemplaza los datos por marcadores en el campo y **no envía**; «Editar» (o Escape) cierra el banner y selecciona el dato; «Enviar así» reenvía con la estrategia verificada del modal.
- **Modal de bloqueo:** muestra el fragmento alrededor de cada hallazgo con el valor **enmascarado y resaltado** (los demás hallazgos de la ventana también enmascarados). Un DNI/NIE cuya letra de control no cuadra se bloquea igual y se rotula "formato de DNI (letra de control no válida)". Acciones: «Cancelar» (default, también Escape) · «Censurar y enviar» · «Enviar de todos modos» → `cancelado` / `censurado` / `enviado`.
- **Censura en el campo:** cada hallazgo se reemplaza por un marcador (`[API_KEY_OCULTA]`, `[TOKEN_OCULTO]`, `[STRIPE_KEY_OCULTA]`, `[WEBHOOK_SECRET_OCULTO]`, `[URI_CON_CREDENCIALES]`, `[SECRETO_OCULTO]`, `[CLAVE_PRIVADA_OCULTA]`, `[CORREO_OCULTO]`, `[CURP_OCULTA]`, `[DNI_OCULTO]`, `[DIRECCIÓN_OCULTA]`, `[TERMINO_OCULTO]`…; lista completa en el catálogo). En las URIs solo se reemplaza la contraseña; la clave privada y la dirección, completas. Estrategia: seleccionar el valor (textarea: `setSelectionRange`; contenteditable: un `Range` sobre los nodos de texto) y `document.execCommand("insertText")`, que genera `beforeinput`/`input` reales que procesan los frameworks (ProseMirror, Quill…). En textarea, si falla, `setRangeText` + `input`. **Siempre se verifica** después que el campo ya no contiene ningún valor detectado; solo entonces se envía. Si falla, no se envía nada: se copia la versión censurada al portapapeles y, en ese sitio, el botón pasa a «Copiar versión censurada» (se recuerda en `chrome.storage.local`). Verificado en `test.html` (textarea y contenteditable); en ChatGPT, Claude y Gemini queda pendiente de la prueba manual.
- **Diccionario propio:** hasta 50 términos confidenciales (popup), sin distinguir mayúsculas ni acentos, palabra completa; solo en `chrome.storage.local`. Al backend llega el tipo `termino_propio` (`masked: "****"`) y el texto con el término reemplazado por `<TERMINO_PROPIO>`: **el término nunca sale de la extensión**.
- **Pegado grande:** al pegar más de 3 000 caracteres (umbral configurable en el popup) aparece un aviso discreto con el tamaño y los tipos detectados. No bloquea.
- **Etiqueta flotante:** tarjeta con franja de color por riesgo, chips por hallazgo (valor enmascarado), categoría, plataforma y latencia; animación breve de entrada y salida.
- **Popup:** estado del backend, resumen de hoy (prompts, advertencias, censurados y bloqueos; `background.js` lo pide a `GET /stats?desde=`), interruptores, políticas por tipo, diccionario, umbral de pegado y botón "Abrir centro de control" (`http://localhost:8000/panel`, Fase 5B).

### 5.2 Contrato de la API

`POST /analyze`
```json
{
  "text": "string",
  "platform": "chatgpt | claude | gemini",
  "timestamp": "ISO-8601",
  "client_findings": [{"type": "openai_key", "masked": "sk-****abcd"}],
  "blocked": true,
  "user_decision": "enviado | cancelado | no_aplica | censurado | advertido",
  "client_latency_ms": 0.4
}
```
- `client_latency_ms` (opcional, Fase 3): latencia de la decisión de bloqueo en `content.js` (CE-6). Solo se loguea; no se guarda en la BD.
- `platform` acepta además `"test"` para `extension/test/test.html` (solo desarrollo).
- **Fase 5A (aditivo):** `user_decision` acepta `"censurado"` (se reemplazaron los hallazgos por marcadores y se envió el texto censurado: es el que recibe el backend) y `"advertido"` (política "advertir" y el usuario envió igual). La columna es TEXT: sin cambio de esquema. Los hallazgos que solo ve el cliente se marcan `source: "client"` (secretos e identificadores) o `source: "client_pii"` (datos personales y `termino_propio`); su riesgo sale del tipo (§5.4).
- **Fase 5A.1 (aditivo):** cada hallazgo de DNI/NIE trae `valido: true|false` (letra de control); en los demás tipos el campo no aparece. Nueva fuente `pii_ner`: dirección que solo encontró Presidio (riesgo medio, ver §5.4). Tipos nuevos en `findings`: `stripe_key`, `stripe_webhook_secret`, `stripe_publishable_key`, `aws_secret_key`, `bearer_token`, `uri_credentials`, `secret_assignment`, `openai_org`, `dni`, `nie`, `address` (heurística). `GET /stats?desde=<ISO-8601>` (opcional) limita los agregados a partir de ese instante.

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

**Fase 5B (aditivo):**
- `GET /stats?detalle=1&inicio=AAAA-MM-DD&fin=AAAA-MM-DD&plataformas=chatgpt,claude&origen=ambos|extension|import` agrega `panel` (sin tocar los campos de siempre): `kpis` (actual, anterior y delta, con `evitados` y `censurados`), `exposicion`, `sankey`, `mapa_calor`, `tendencia` (por riesgo), `tipos_hallazgo`, `motivos`, `hallazgo` (frase), `recientes` (últimos 30 eventos), `rango_datos` y `nombres_tipos`. Días de la Ciudad de México; por defecto los últimos 14. `inicio > fin` → 422. Nunca incluye texto de prompts.
- `GET /events` (Server-Sent Events): `retry: 3000`, un `event: hola` al conectar, un `event: prompt` por cada evento que guarda `/analyze` y `: ping` cada 15 s. Cada `prompt` lleva solo `id, ts, hora, plataforma, riesgo, motivo, tipos, categoria, decision, bloqueado, latencia_ms, simulado, publicado_ms` — **sin texto ni valores, ni siquiera enmascarados**. Cola en memoria por cliente (100; un cliente lento pierde lo más viejo y `/analyze` nunca se bloquea). `?limit=N` cierra tras N eventos (tests, curl).
- `GET /panel` → `/panel/` (estáticos de `backend/panel/`). `/panel` y `/events` responden **403** a cualquier cliente que no sea local (además de que uvicorn escucha en 127.0.0.1), y el panel se sirve con `Content-Security-Policy: default-src 'self'; connect-src 'self'…`.

**Fase 4B — modo degradado:** si un modelo no carga, el backend sigue funcionando: `category = "sin_clasificar"` (con `category_confidence = 0.0`), `injection_score = null` y PII solo con regex. Por eso `injection_score` admite `null`.

**Fase 4C — campo opcional `category_uncertain: bool | null`** (cambio aditivo; los clientes que no lo lean no se ven afectados): `true` si `category_confidence` < umbral de confianza del tema (`metrics.json → topic_v2.umbral_confianza.umbral`), `false` si no, y `null` sin clasificador de tema. La BD no cambia: sigue guardando la categoría más probable y su confianza. La extensión muestra "tema incierto (¿categoría?)" cuando es `true`.

### 5.3 Detectores
| Detector | Método | Tipos |
|---|---|---|
| Secretos | Regex (`secrets.py` = `PATTERNS` de `patterns.js`) | **Bloquean:** OpenAI (`sk-`, `sk-proj-`, `sk-svcacct-`, `sk-admin-`), Anthropic `sk-ant-`, Google `AIza…`, Stripe (`sk_`/`rk_` `live`/`test`) y webhook `whsec_`, AWS (`AKIA`/`ASIA` + 16 o más; secret key **por contexto** tras `AWS_SECRET_ACCESS_KEY`/`aws_secret_access_key`/`SecretAccessKey`, sin exigir 40), GitHub, JWT, **token Bearer** (solo el token, 20+), **URI con credenciales** (`postgres(ql)`, `mysql`, `mariadb`, `mongodb(+srv)`, `redis(s)`, `amqp(s)`, `mssql`, `ftp(s)`, `smtp(s)`, `http(s)`; la contraseña llega hasta la ÚLTIMA `@`, tolera URIs mal formadas y solo ella se enmascara), `password=`/`contraseña:`, **asignaciones** `.env`/YAML/JSON/código cuyo nombre contiene SECRET, TOKEN, PASSWORD, PASSWD, PWD, API_KEY, ACCESS_KEY, PRIVATE_KEY, CLIENT_SECRET o CONNECTION_STRING (valor 8+), clave privada (bloque `BEGIN … PRIVATE KEY` completo hasta su END, multilínea, dentro de bloques de código o con sangría), tarjetas (Luhn). **Advierten:** ID de organización de OpenAI `org-…`, clave pública de Stripe `pk_…`. **Placeholders ignorados:** `xxx`, `***`, `<…>`, `${VAR}`, `$VAR`, `changeme`, `your_*`, `tu_*`, `example` aislado, `…`/`...`; en contraseñas y asignaciones, también variables y expresiones de código (`getpass()`, `user_password_hash`, `process.env.X`) |
| PII | Regex locales (`pii_patterns.py` = `PII_RULES`) + Presidio (es) con spaCy `es_core_news_md` y recognizers propios (`backend/detectors/presidio_es.py`, compartido con `ml/pii_eval.py`) | Correo, teléfono MX, CURP, RFC, **DNI y NIE** (con letra de control, `valido`), **dirección por heurística** (abajo), nombre de persona (≥ 2 palabras, solo Presidio). Presidio sigue aportando direcciones (fusión de fragmentos y lista negra de falsos LOCATION), pero las que solo él ve llevan fuente `pii_ner` y riesgo medio. "Tarjeta terminada en 4412" no es un hallazgo |
| Tema | `paraphrase-multilingual-MiniLM-L12-v2` + LogisticRegression (`ml/models/topic_clf.joblib`, **v2** desde la Fase 4C, entrenado con `ml/data/topic_synthetic_v2.csv`; el v1 queda en `topic_clf_v1.joblib`) | `programacion`, `academico`, `redaccion`, `ciberseguridad`, `consulta_general`, `personal` (definiciones abajo) |
| Inyección/jailbreak | **Ensamble**: promedio de TF-IDF (palabras + caracteres) + LR y MiniLM + LR (`ml/models/injection_final.joblib`, inferencia de `ml/injection_model.py`) | score 0–1 |

#### Dirección de vivienda — heurística local (Fase 5A.1)
Síncrona, sin Presidio, igual en `patterns.js` y `pii_patterns.address_spans`. Señales: **vía** (Calle, C., Av., Avenida, Blvd., Calz., Privada, Cerrada, Andador, Camino, Carretera + nombre + número) · **número** ("No."/"#"/"Núm." + número) · **interior** (Int./Ext./Depto. + identificador) · **colonia** (Col./Colonia/Fracc./Unidad Habitacional + nombre) · **C.P.** (C.P./CP + 5 dígitos) · **municipio** (Alcaldía/Delegación/Municipio + nombre) · **manzana** (Mz./Lt.). Se agrupan las señales a ≤ 40 caracteres entre sí (sin línea en blanco); un grupo es dirección con **2 o más señales distintas** (C.P. + cualquier otra incluido) y al menos una fuerte (no basta "#12 y lote 7"). Una sola señal no bloquea ("nos vemos en Reforma", "Av. Reforma 222", "colonia Roma"). Se enmascara el grupo completo como `[DIRECCIÓN_OCULTA]`.

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
- **Alto:** cualquier hallazgo de riesgo alto **o** `injection_score ≥ umbral alto`.
- **Medio:** cualquier otro hallazgo **o** `umbral medio ≤ injection_score < umbral alto`.
- **Bajo:** lo demás. `injection_score = null` no suma riesgo.

**Riesgo de cada hallazgo (Fase 5A.1, `catalog.py`):** el de su tipo, alineado con la política por defecto de la extensión: lo que **bloquea** (secretos, CURP, RFC, DNI, NIE, dirección heurística) es alto; lo que **advierte** (correo, teléfono, nombre, término propio, `org-`, `pk_`) es medio. Una dirección que solo vio Presidio (`pii_ner`) es **medio**: el riesgo alto por dirección solo lo da la heurística, para no heredar los falsos positivos de Presidio. Tipos desconocidos: por su fuente (`secret`/`client` → alto). *Cambio frente a la 5A:* un CURP/RFC pasaba como medio; ahora es alto.

**Motivos** (alertas del dashboard, `catalog.motives`): secreto · dato personal · término propio · inyección (riesgo medio/alto sin hallazgos).

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

### 5.6 Centro de control (Fase 5B) y dashboard de respaldo (Fase 5)

#### Centro de control — pantalla principal de la demo
Abrir **http://localhost:8000/panel** con el backend corriendo (`python -m uvicorn main:app` desde `backend/`). Es una página propia (`backend/panel/`: `index.html`, `panel.css`, `panel.js`) servida por el backend, pensada para proyector a 1080p: sin scroll en la vista principal, tipografía grande, alto contraste y los tokens de Centinela (§5.1), cifras en monoespaciada con dígitos tabulares.
- **Datos:** `GET /stats?detalle=1` (mismo SQL que el dashboard, `backend/analytics.py`, conexión de solo lectura), `GET /health` cada 10 s y `GET /events` (SSE). El panel **nunca escribe en la BD** ni recibe texto de prompts (lo verifica `tests/test_panel.py`).
- **Gráficas:** Apache ECharts 6.0.0 **copiada al repo** (`backend/panel/vendor/echarts/`, Apache-2.0, con `LICENSE`, `NOTICE` y las licencias BSD de zrender y d3; SHA-256 en su `README.md`). Sin CDN ni peticiones externas: la demo funciona sin internet, y la CSP del panel (`default-src 'self'`) bloquearía cualquier origen externo aunque se agregara por error.
- **Cabecera:** escudo, estado del backend, de los modelos y del feed, reloj, rango de fechas (7 d / 14 d / 30 d / todo o fechas libres) y **Modo presentación** (activo por defecto; tipografía más grande y sin detalles técnicos como ids y latencias). El panel no muestra texto de prompts en ningún modo: para revisar texto enmascarado está el dashboard de respaldo con su modo presentación apagado.
- **KPIs** (contadores animados, con la variación frente al periodo anterior): prompts · % con datos sensibles (riesgo medio + alto) · bloqueos evitados (`cancelado` + `censurado`) · censurados · plataforma principal (y tema top).
- **Índice de exposición (0–100), en un medidor:** `IE = 100 × (0.5 × eventos de riesgo medio + 1 × eventos de riesgo alto) ÷ total de eventos del periodo`. 0 = ningún prompt con riesgo; 50 = todos de riesgo medio (o la mitad de riesgo alto); 100 = todos de riesgo alto. Bandas: 0–20 baja (verde), 20–50 moderada (ámbar), 50–100 alta (rojo). La fórmula y los conteos aparecen en el tooltip «i».
- **Feed en vivo** (columna derecha): cada evento entra con animación en cuanto llega por SSE, con franja de color **e icono y texto** por riesgo (● bajo, ◆ medio, ▲ alto), plataforma, hora, tema, motivo, chips de hallazgo y decisión. Un bloqueo produce un destello rojo breve en el borde de la pantalla. Si se cae el SSE, el indicador pasa a «Feed reconectando…» y vuelve solo (sin recargar). Los eventos de `test.html` (`platform: "test"`) aparecen en el feed como «Prueba» pero nunca cuentan en las estadísticas. Latencia medida de `/analyze` a feed: 76–150 ms de punta a punta (el pipeline toma 32–109 ms); de la publicación SSE al feed, 1–5 ms.
- **Gráficas:** Sankey plataforma → tema → riesgo · mapa de calor día × hora (CDMX) · tendencia diaria apilada por riesgo · barras de tipos de hallazgo (rojo = secreto, ámbar = dato personal) · dona de motivo del riesgo (secreto / dato personal (PII) / inyección / término propio; un evento puede contar en varios).
- **Frase de hallazgo** (la de la Fase 5), con las cifras resaltadas. Estados vacíos («Sin datos en este periodo») y de error (aviso si falla `/stats`, reintento cada 10 s; si ECharts no cargara, KPIs y feed siguen funcionando).
- **Accesibilidad:** contraste AA sobre los tokens oscuros, foco visible, controles con teclado, riesgo con icono y texto además del color, `prefers-reduced-motion` (sin contadores animados, entradas ni destello animado).
- **Modo demo:** con `?demo=1` aparece «Simular», que inyecta 5 eventos **falsos** al feed (bloqueo de API key, correo + teléfono, uno sin riesgo, AWS censurado, inyección), marcados «SIMULADO» con borde punteado. No llama al backend ni escribe en la BD; sirve para ensayar o como respaldo si falla la red.

#### Dashboard de respaldo (Streamlit) — Fase 5
Cómo levantarlo: `conda activate auditor` y luego `streamlit run dashboard/app.py` (alternativa: `python dashboard/run.py`). Se abre en http://127.0.0.1:8501.

- **Estructura:** `backend/analytics.py` concentra TODO el SQL de estadística (antes `dashboard/queries.py`, que ahora es un *shim* que lo reexporta), como funciones puras (filtros → DataFrames) sobre una conexión SQLite de **solo lectura** (`mode=ro`); lo comparten el dashboard y `/stats?detalle=1`. `dashboard/app.py` es solo interfaz. `dashboard/.streamlit/config.toml` fija `server.address = 127.0.0.1` y `browser.gatherUsageStats = false` (RNF-03), además del tema claro para proyector.
- **Privacidad:** Streamlit toma `dashboard/.streamlit/config.toml` (comprobado con Streamlit 1.67 lanzando desde la raíz del repo). Como defensa adicional, si la telemetría estuviera activa o el servidor no escuchara solo en localhost, `app.py` **no muestra datos** y explica cómo lanzarlo; `run.py` fuerza ambas opciones por línea de comandos.
- **Filtros (barra lateral, no se refrescan):** rango de fechas (días de la Ciudad de México), plataforma (ChatGPT/Claude/Gemini; `test` nunca aparece), origen (extensión / importado / ambos) y **Modo presentación**, activado por defecto, que oculta TODO texto de prompts.
- **KPIs, con la variación frente al periodo anterior de la misma duración:** total de prompts · % con datos sensibles (riesgo medio + alto, que incluye detecciones de inyección) · envíos bloqueados (cancelados vs. enviados de todos modos) · plataforma más usada · tema más frecuente.
- **Gráficas (plotly):** prompts por plataforma · temas (los de confianza < umbral de `metrics.json → topic_v2.umbral_confianza` van en la barra "incierto") · riesgo por plataforma (apiladas) · mapa de calor día × hora (Ciudad de México) · tendencia diaria (todos vs. riesgo medio o alto) · tipos de hallazgo (de `findings_json`). Colores fijos, validados para daltonismo: ChatGPT `#009E73`, Claude `#AA3377`, Gemini `#0072B2`; riesgo bajo `#56B4E9`, medio `#E69F00`, alto `#D55E00`, siempre con etiquetas.
- **Alertas recientes:** los últimos 20 eventos de riesgo medio o alto (hora, plataforma, riesgo, tipos de hallazgo, bloqueo/decisión y tema). El texto enmascarado (≤ 100 caracteres) solo aparece con el modo presentación **apagado**; con el modo activado, la columna ni siquiera se lee de la BD.
- **Tiempo real:** `st.fragment(run_every=3)` refresca KPIs, gráficas y tabla cada 3 s, sin recargar los filtros. Muestra "Última actualización hh:mm:ss" y un punto verde/rojo según `GET /health` del backend.
- **Hallazgo (golpe 3 de la demo):** una frase generada solo con cifras y nombres, por ejemplo: "El X % de tus prompts contenían datos personales; la plataforma con más riesgo fue Y (Z % …)".

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
- **RF-09** (5A) Escudo en vivo: evaluación local al escribir/pegar, sin backend.
- **RF-10** (5A) Políticas por tipo de hallazgo: bloquear / advertir / permitir.
- **RF-11** (5A) Censurar: reemplazar los hallazgos por marcadores en el campo antes de enviar, con verificación.
- **RF-12** (5A) Diccionario propio de términos confidenciales que nunca salen de la extensión.
- **RF-13** (5A) Aviso al pegar textos grandes (umbral configurable).
- **RF-15** (5B) Centro de control en `/panel` con feed en vivo (SSE), KPIs, índice de exposición y gráficas, sin recursos externos.
- **RF-14** (5A.1) Detección local ampliada: Stripe, AWS por contexto, Bearer, URIs con credenciales, asignaciones `.env`/YAML/JSON, DNI/NIE y dirección de vivienda, con los mismos tipos en el backend.

### No funcionales
- **RNF-01** Bloqueo local en < 50 ms (imperceptible).
- **RNF-02** Etiqueta visible en < 1 s tras el envío (p95).
- **RNF-03** Ningún dato sale de `localhost`.
- **RNF-04** Ningún secreto ni PII en claro en disco o logs.
- **RNF-05** Si el backend está caído, la extensión sigue bloqueando secretos (degradación elegante) y muestra `Backend no disponible`.
- **RNF-06** El uso normal del chat no se rompe (no duplicar mensajes, no perder texto).
- **RNF-07** (5A) Evaluación en vivo < 10 ms con textos de 5 000 caracteres.
- **RNF-08** (5A) La UI en la página no carga recursos externos (CDN, fuentes remotas) y es accesible: foco visible, teclado, `prefers-reduced-motion`.

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
| CE-1 / CE-2 | **5A.1:** paridad JS ↔ Python al 100 % en 118 casos de secretos, 45 de PII y los 7 escenarios de demo. **CE-2: 0/200 bloqueados (0.0 %)** en `ml/data/ce2_prompts_normales.csv` con las políticas por defecto (antes de la 5A.1: 1/200, 0.5 %, por `password = input(…)`); `test_ce2_false_positives.py` exige ≤ 3 %. En el historial real (49 prompts, `text_masked`) la 5A.1 bloquearía 2, ambos con los valores falsos de los casos de demo 2 y 3 (verdaderos positivos). CE-1 (100 prompts con secretos) sigue pendiente | ⏳ CE-1 · ✅ CE-2 |
| CE-3 | F1 micro PII **0.99** (P 0.98 / R 1.00) con Presidio en la configuración de producción; solo regex: 0.76. Sobre 100 prompts **sintéticos**: es optimista para las regex, que reconocen exactamente los formatos generados | ✅ (sintético) |
| CE-4 | **Pendiente** del test real (~100 prompts). Provisionales, sin contar para CE-4: modelo **v2** (Fase 4C), F1 macro **0.86** en CV agrupada sobre sintéticos v2 y **0.83** en el probe set de 60 casos de frontera (v1: 0.68). Umbral de confianza 0.61 (cobertura 85 %) | ⏳ |
| CE-5 | Ensamble con umbral 0.514, **test (n = 431): P 0.978 / R 0.872 / F1 0.922**; en español (n = 40): **P 0.826 / R 0.950** | ✅ (ver nota) |
| CE-6 | Decisión de bloqueo en `content.js`: p95 0.1 ms (prompts del set), 0.4 ms (10 000 caracteres) | ✅ |
| CE-7 | `/analyze` en CPU con 50 prompts: p95 del pipeline **104 ms**, de ida y vuelta HTTP **121 ms** (PII 57 ms y embeddings 36 ms en p95) | ✅ |

> **Nota metodológica (CE-5, Fase 4A.1):** la regla pre-registrada (umbral con recall OOF = 0.85, sin margen) **no cumplía** en el test: recall 0.81. El procedimiento se ajustó **después** de esa primera evaluación: objetivo de recall 0.90 en CV, comparación de 4 candidatos solo en CV y una única evaluación final en el test. Como el test ya se había mirado, el resultado de CE-5 debe leerse como **optimista**; la validación definitiva es el uso real (CE-11). Detalle en `metrics.json → injection_4A1.nota_metodologica`.

---

## 8. Guion de la demo (≈ 5 min)

**Preparación:** backend corriendo, **centro de control (http://localhost:8000/panel) en la segunda pantalla** con el modo presentación activo (el dashboard de Streamlit queda como respaldo), extensión activa, BD con ≥ 150 prompts reales de las 2 semanas. Video de respaldo grabado por si falla la red; si falla en vivo, abrir `http://localhost:8000/panel?demo=1` y usar «Simular».

**Golpe 1 — "Te estoy leyendo" (1 min)**
1. Abrir ChatGPT frente a la audiencia.
2. Escribir: *"Explícame qué es un firewall"* → Enter.
3. Aparece `✅ Sin riesgo · académico · ChatGPT`.
4. Señalar el centro de control: el evento entra en el feed en vivo (≈ 0.1 s) y el contador de prompts sube.
5. Frase: *"Todo lo que le escribes a una IA puede ser capturado y clasificado."*

**Golpe 2 — "Te salvé" (1.5 min)**
1. Pegar: *"Corrige este código: `client = OpenAI(api_key='sk-proj-DEMO…')`"* (key falsa preparada). **El escudo de Centinela se pone rojo antes de enviar**; al pasar el cursor muestra `API key de OpenAI sk-****abcd · bloquear`.
2. Enter → **el mensaje NO se envía**. Aparece el modal "Posible fuga de información" con el fragmento y la key **enmascarada y resaltada**.
3. «Censurar y enviar» → en el campo queda `[API_KEY_OCULTA]` y se envía esa versión. (Alternativa: «Cancelar» y mostrar el evento `bloqueado / cancelado` en el dashboard.)
4. Frase: *"El dato nunca salió de mi computadora. Esto es DLP."*
5. Repetir rápido en Claude con un correo + teléfono falsos → escudo ámbar y banner "Tu mensaje incluye un correo y un teléfono" → «Enviar así» → `⚠️ Riesgo medio`. (Desde la 5A.1 un CURP **bloquea**: sirve para mostrar el modal con un dato personal.)
5b. (Opcional, 5A.1) Con el selector «Casos de demo» de `test.html` (o copiando de `extension/test/demo_cases.json`): AWS en `.env` (caso 2), URI de base de datos con contraseña (caso 3), Stripe + DNI con letra inválida (caso 5) y dirección completa (caso 6a) → modal; "Nos vemos en Reforma" (caso 6b) → pasa sin aviso.
6. (Opcional) Agregar "Proyecto Fénix" al diccionario en el popup y escribirlo: banner "un término propio"; en la BD aparece `<TERMINO_PROPIO>`.

**Golpe 3 — "Así usas la IA" (1.5 min)**
1. Centro de control con los datos reales de 2 semanas: KPIs, índice de exposición (explicar la fórmula con el tooltip), Sankey plataforma → tema → riesgo, mapa de calor y % de prompts con datos sensibles. (En el golpe 2, el bloqueo ya habrá hecho destellar el borde en rojo y sumado un «bloqueo evitado».)
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
