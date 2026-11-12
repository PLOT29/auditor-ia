# ml/ · Fases 4A–4B — datos, entrenamiento, evaluación e integración

Los modelos de `ml/models/` ya están **integrados al backend** (Fase 4B): `topic_clf.joblib`
e `injection_final.joblib`, con el umbral leído de `reports/metrics.json`. El backend importa
`mlutils.py` e `injection_model.py` de esta carpeta (no duplica su lógica).

## Cómo reproducir (en este orden)

```powershell
conda activate auditor
python ml/check_env.py                 # valida librerías, spaCy es y MiniLM
python ml/make_topic_synthetic.py      # data/topic_synthetic_v1.csv (480 sintéticos, Fase 4A)
python ml/export_for_labeling.py       # data/topic_to_label.csv (prompts reales enmascarados)
python ml/build_injection_dataset.py   # data/injection.csv (HF + 200 propios en español)
python ml/build_notebooks.py --run     # escribe y ejecuta los 3 notebooks (4A, pre-registrado)
python ml/select_injection.py          # 4A.1: selección limpia → models/injection_final.joblib
python ml/pii_eval.py                  # PII: regex vs. Presidio base / 4A.1 / producción (4B)
python ml/bench_latency.py             # 4B: latencia de /analyze por etapa (CE-7), BD temporal
python ml/probe_eval.py --model models/topic_clf_v1.joblib --tag v1   # 4C: línea base en el probe
python ml/make_topic_synthetic_v2.py   # 4C: data/topic_synthetic_v2.csv (900, 150 por clase)
python ml/train_topic_v2.py            # 4C: reentrena el tema, umbral de confianza, v1 vs. v2
```

**Siempre con `conda activate auditor`.** Si lanzas `python.exe` del entorno sin activarlo, en
esta máquina numpy puede morir con `Windows fatal exception 0xc06d007f` (conflicto de DLL de
BLAS con otras instalaciones que están en el PATH).

`build_notebooks.py` es la fuente de los notebooks. `--run` los ejecuta **dentro del mismo
proceso** con IPython, porque en esta máquina el kernel de Jupyter no arranca (ZeroMQ falla
con `Bad file descriptor`). Con `--nbconvert` se usa el kernel normal.
`select_injection.py` usa los `C` que eligió `train_injection.ipynb`, así que va después.

## Detector de inyección final (Fase 4A.1)

- **Regla:** el umbral se elige solo con predicciones out-of-fold (5-fold) del train, con
  recall objetivo **0.90**. Se comparan en CV cuatro candidatos: TF-IDF, embeddings,
  embeddings con ventanas deslizantes (score = máximo de las ventanas) y un ensamble
  (promedio de TF-IDF y embeddings).
- **Selección:** el de mayor F1 OOF entre los que tienen recall en español ≥ 0.85 dentro de la CV.
- **Elegido:** ensamble, umbral 0.514. Se evaluó en el test **una sola vez**.
- `metrics.json["injection_4A1"]` guarda `pre_registrado`, `cv_comparacion`, `final` y
  `nota_metodologica` (el procedimiento se ajustó después de la primera evaluación; ver ahí).
- La inferencia de los 4 tipos de modelo vive en `injection_model.py`; el backend la usa tal
  cual (`backend/detectors/injection.py`), pasándole los embeddings ya calculados.

## Tema v2 (Fase 4C)

- **Motivo:** en vivo, "explícame qué es una tarjeta gráfica" salió como `ciberseguridad`.
  Los sintéticos v1 ponían casi todo lo técnico en seguridad (ver `reports/error_analysis.md`).
- **Guía:** [`LABELING.md`](LABELING.md) define cada categoría, con ejemplos de frontera y reglas
  de desempate. Es la guía para etiquetar el test real.
- **Probe set** (`data/topic_probe.csv`): 60 casos de frontera escritos a mano. Su SHA-256 está en
  `metrics.json → topic_probe.sha256` y `probe_eval.py` se niega a evaluar si el archivo cambia.
  **Nunca** se usa para entrenar ni para elegir hiperparámetros o umbrales, y no sustituye a CE-4.
- **Datos v2:** las bases v1 revisadas, más bases nuevas y plantillas de frontera (tecnología
  cotidiana en `consulta_general`, la misma con contexto escolar en `academico` y dentro de
  código en `programacion`), más variantes de registro. Sin casi duplicados (coseno > 0.95) y
  sin nada parecido al probe (> 0.90).
- **Modelos:** `models/topic_clf.joblib` es el v2 (lo carga el backend) y `topic_clf_v1.joblib`
  es el anterior. Umbral de confianza en `metrics.json → topic_v2.umbral_confianza` (0.61, elegido
  con OOF). El backend responde `category_uncertain` y la extensión muestra "tema incierto".

## Presidio en español (Fases 4A.1 y 4B)

Vive en **`backend/detectors/presidio_es.py`**, compartido por el backend y `pii_eval.py`:
`build_analyzer()` + `detect(analyzer, text, PostConfig())`. El post-proceso fusiona los
fragmentos de una dirección, filtra falsos LOCATION ("Col", "cel"…), aplica un score mínimo
configurable a las direcciones (`--min-location-score`, default 0.5) y da prioridad a las
entidades de patrón. En la 4B (producción) además exige ≥ 2 palabras para una PERSON y calle,
número, Col. o C.P. para una dirección (una ciudad sola no cuenta).
**Privacidad:** el logger `presidio-analyzer` queda fijo en WARNING: en DEBUG registra los
tokens del texto analizado.

## Reclasificar filas existentes (Fase 4B)

```powershell
python ml/reclassify.py --only-unclassified --dry-run   # solo conteos
python ml/reclassify.py --only-unclassified             # aplica, en una transacción
```

Recalcula tema, inyección y riesgo con el mismo pipeline del backend, sobre `text_masked`
(puede ser un poco menos preciso que con el texto original). No toca `findings`, `blocked` ni
`user_decision`; el riesgo solo sube. Si los modelos no cargan, no hace nada.
**Haz una copia de la BD antes** (`copy C:uditor-datauditor.db C:uditor-datauditor.antes-reclasificar.db`):
no hay "deshacer".

## Importar historial de ChatGPT / Claude / Gemini (CE-11)

```powershell
python ml/import_history.py --file "C:\auditor-data\exports\claude\conversations.json" --dry-run
python ml/import_history.py --file "C:\auditor-data\exports\gemini\MiActividad.json" --dry-run
python ml/import_history.py --file "...\conversations.json"          # importación real
```

- `conversations.json` viene dentro del ZIP que se descarga de *Settings → Data controls →
  Export* (ChatGPT) o *Settings → Privacy → Export data* (Claude). Para Gemini:
  *Google Takeout → Mi actividad → Apps de Gemini*, en formato **JSON**
  (`MiActividad.json` / `My Activity.json`). El formato se detecta solo.
- Gemini: solo se importan las actividades cuyo `title` empieza con un prefijo de prompt
  ("Prompted ", "Instrucción: "…); las respuestas en HTML y las actividades de Gems o de
  configuración se ignoran y se cuentan aparte. Si tu cuenta usa otro prefijo, agrégalo
  con `--gemini-prefix "Prefijo: "`. Las fechas se guardan en hora de la Ciudad de México.
- Toda la importación es **una transacción**: si falla a la mitad, no queda nada a medias.
- Solo se importan los mensajes **del usuario**, con su fecha. Pasan por `backend/main.py →
  process()`, el mismo pipeline que `/analyze`, y se guardan con `source = 'import'`.
- Deduplica por `text_sha256`, así que se puede volver a correr sin duplicar. Nunca imprime texto.
- El archivo debe estar **fuera del repo**; el script lo rechaza si está dentro.
- **Mejor córrelo después de la Fase 4B:** la BD solo guarda el texto enmascarado, así que
  el tema y el score de inyección se calculan al importar. Si importas antes, quedarán con
  los valores de los stubs actuales (`consulta_general` / 0.0).

## Test real de tema (CE-4) · ~100 prompts

1. `python ml/export_for_labeling.py` (default `--n 100`): muestreo **estratificado por
   plataforma** (lo más parejo posible entre chatgpt, claude y gemini), sin duplicados y
   con seed fija. Columnas `id, platform, text, label`. Se puede volver a correr cuando haya
   más prompts: **nunca sobrescribe ni quita las filas ya etiquetadas**, solo agrega nuevas.
2. Llena la columna `label` usando los criterios de `make_topic_synthetic.py` y guarda el
   archivo como `data/topic_test_real.csv`.
3. `python ml/build_notebooks.py --run --only evaluate.ipynb`: evalúa con ese archivo sin
   reentrenar. Reporta F1 macro con **IC 95 % por bootstrap (1000 remuestreos)**, F1 por
   plataforma y matriz de confusión. Con ~100 prompts el intervalo es amplio (±0.08 o más).

Ambos CSV están en `.gitignore`. **Antes de hacer commit de `evaluate.ipynb` o de
`reports/metrics.json` después de usar el test real**, revisa que no lleven texto de tus
prompts (aunque estén enmascarados), o limpia las salidas.

## Archivos

| Archivo | Qué es |
|---|---|
| `mlutils.py` | Semillas, embeddings con caché, pipelines, splits y métricas |
| `injection_es.py` | 100 positivos + 100 negativos propios en español |
| `injection_model.py` | Inferencia de los 4 tipos de detector de inyección (ventanas, ensamble…) |
| `select_injection.py` | Selección limpia del modelo y umbral (4A.1) |
| `reclassify.py` | Reclasificación de filas existentes con los modelos de la 4B |
| `LABELING.md` | Definiciones de las categorías de tema y reglas de desempate (4C) |
| `probe_eval.py` | Evalúa un modelo de tema en el probe set (4C) |
| `make_topic_synthetic_v2.py` / `train_topic_v2.py` | Sintéticos v2 y reentrenamiento del tema (4C) |
| `bench_latency.py` | Latencia de `/analyze` por etapa (CE-7) |
| `import_history.py` | Importador de historial de ChatGPT / Claude |
| `models/*.joblib` | `topic_clf`, `injection_clf`, baselines `*_tfidf` y **`injection_final`** (el que se integra en la 4B) — no se suben al repo |
| `reports/metrics.json` | Todas las métricas |
| `reports/*.png` | Figuras para el reporte |
| `reports/error_analysis.md` | Hipótesis sobre los errores |
