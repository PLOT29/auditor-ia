# ml/ · Fase 4A — datos, entrenamiento y evaluación

Nada de esto está integrado al backend todavía (eso es la Fase 4B).

## Cómo reproducir

```powershell
conda activate auditor
python ml/check_env.py                 # valida librerías, spaCy es y MiniLM
python ml/make_topic_synthetic.py      # data/topic_synthetic.csv (480 sintéticos)
python ml/export_for_labeling.py       # data/topic_to_label.csv (prompts reales enmascarados)
python ml/build_injection_dataset.py   # data/injection.csv (HF + 200 propios en español)
python ml/build_notebooks.py --run     # escribe y ejecuta los 3 notebooks
python ml/pii_eval.py                  # PII: regex vs. regex + Presidio
```

`build_notebooks.py` es la fuente de los notebooks. `--run` los ejecuta **dentro del mismo
proceso** con IPython, porque en esta máquina el kernel de Jupyter no arranca (ZeroMQ falla
con `Bad file descriptor`). Con `--nbconvert` se usa el kernel normal.

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
| `models/*.joblib` | `topic_clf`, `injection_clf` (embeddings + LR, con el nombre del modelo de embeddings y el umbral adentro) y los baselines `*_tfidf` — no se suben al repo |
| `reports/metrics.json` | Todas las métricas |
| `reports/*.png` | Figuras para el reporte |
| `reports/error_analysis.md` | Hipótesis sobre los errores |
