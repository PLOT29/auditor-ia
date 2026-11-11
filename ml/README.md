# ml/ · Fase 4A — datos, entrenamiento y evaluación

Nada de esto está integrado al backend todavía (eso es la Fase 4B). La única excepción
es el importador de historial, que escribe en la BD usando el pipeline del backend.

## Cómo reproducir (en este orden)

```powershell
conda activate auditor
python ml/check_env.py                 # valida librerías, spaCy es y MiniLM
python ml/make_topic_synthetic.py      # data/topic_synthetic.csv (480 sintéticos)
python ml/export_for_labeling.py       # data/topic_to_label.csv (prompts reales enmascarados)
python ml/build_injection_dataset.py   # data/injection.csv (HF + 200 propios en español)
python ml/build_notebooks.py --run     # escribe y ejecuta los 3 notebooks (4A, pre-registrado)
python ml/select_injection.py          # 4A.1: selección limpia → models/injection_final.joblib
python ml/pii_eval.py                  # PII: regex vs. Presidio base vs. Presidio ajustado
```

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
- La inferencia de los 4 tipos de modelo vive en `injection_model.py`; la 4B debe usar ese
  código (o moverlo al backend tal cual).

## Presidio en español (Fase 4A.1)

`presidio_es.py` = `build_analyzer()` + `detect(analyzer, text, PostConfig())`. El post-proceso
fusiona los fragmentos de una dirección, filtra falsos LOCATION ("Col", "cel"…), aplica un
score mínimo configurable a las direcciones (`--min-location-score`, default 0.5) y da
prioridad a las entidades de patrón sobre las del NER.

## Importar historial de ChatGPT / Claude (CE-11)

```powershell
python ml/import_history.py --file "C:\ruta\fuera\del\repo\conversations.json" --dry-run
python ml/import_history.py --file "C:\ruta\fuera\del\repo\conversations.json"
```

- `conversations.json` viene dentro del ZIP que se descarga de *Settings → Data controls →
  Export* (ChatGPT) o *Settings → Privacy → Export data* (Claude). El formato se detecta solo.
- Solo se importan los mensajes **del usuario**, con su fecha. Pasan por `backend/main.py →
  process()`, el mismo pipeline que `/analyze`, y se guardan con `source = 'import'`.
- Deduplica por `text_sha256`, así que se puede volver a correr sin duplicar. Nunca imprime texto.
- El archivo debe estar **fuera del repo**; el script lo rechaza si está dentro.
- **Mejor córrelo después de la Fase 4B:** la BD solo guarda el texto enmascarado, así que
  el tema y el score de inyección se calculan al importar. Si importas antes, quedarán con
  los valores de los stubs actuales (`consulta_general` / 0.0).

## Test real de tema (CE-4)

1. `python ml/export_for_labeling.py`
2. Llena la columna `label` de `data/topic_to_label.csv` usando los criterios de
   `make_topic_synthetic.py` y guárdalo como `data/topic_test_real.csv`.
3. `python ml/build_notebooks.py --run`: `evaluate.ipynb` usa ese archivo automáticamente.

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
| `presidio_es.py` | Presidio en español + post-proceso de direcciones |
| `import_history.py` | Importador de historial de ChatGPT / Claude |
| `models/*.joblib` | `topic_clf`, `injection_clf`, baselines `*_tfidf` y **`injection_final`** (el que se integra en la 4B) — no se suben al repo |
| `reports/metrics.json` | Todas las métricas |
| `reports/*.png` | Figuras para el reporte |
| `reports/error_analysis.md` | Hipótesis sobre los errores |
