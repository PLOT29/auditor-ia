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
`GET /stats` → agregados para el dashboard. `GET /health` → `{"ok": true}`.

### 5.3 Detectores
| Detector | Método | Tipos |
|---|---|---|
| Secretos | Regex | API keys (OpenAI `sk-`, Anthropic `sk-ant-`, AWS `AKIA…`, GitHub `ghp_`, Google `AIza…`), JWT, claves privadas PEM, `password=`/`contraseña:` , tarjetas (con validación Luhn) |
| PII | Presidio (es) + recognizers propios | Correo, teléfono MX, CURP, RFC, nombre de persona, dirección |
| Tema | `paraphrase-multilingual-MiniLM-L12-v2` + LogisticRegression | `programacion`, `academico`, `redaccion`, `ciberseguridad`, `consulta_general`, `personal` |
| Inyección/jailbreak | Mismos embeddings + clasificador binario | score 0–1 |

### 5.4 Reglas de riesgo (`risk.py`)
- **Alto:** cualquier secreto **o** `injection_score ≥ 0.80`.
- **Medio:** cualquier PII **o** `0.50 ≤ injection_score < 0.80`.
- **Bajo:** lo demás.

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
| CE-4 | F1 macro de tema | ≥ 0.80 | Hold-out 20 %, matriz de confusión |
| CE-5 | Recall de inyección/jailbreak | ≥ 0.85 (con precision ≥ 0.80) | Dataset público + prompts propios en español |
| CE-6 | Latencia bloqueo local | < 50 ms | `performance.now()` en content.js |
| CE-7 | Latencia etiqueta p95 | < 1 s | `latency_ms` en BD |
| CE-8 | Cobertura de plataformas | 3/3 | Prueba manual documentada con capturas |
| CE-9 | Privacidad | 0 secretos en claro | `grep` de los secretos de prueba sobre la BD y logs |
| CE-10 | Robustez | Funciona con backend apagado | Prueba manual |
| CE-11 | Uso real | ≥ 150 prompts propios registrados | Conteo en BD antes de la presentación. Los prompts importados del historial (`source = 'import'`) cuentan como uso real, pero se reportan **por separado** de los capturados con la extensión (`/stats → por_origen`) |

**Baselines para comparar en el reporte:** solo regex vs. regex + Presidio (PII); TF-IDF vs. embeddings (tema e inyección).

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
