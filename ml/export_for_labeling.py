"""Exporta prompts REALES (ya enmascarados) de la BD para etiquetar el tema a mano.

    python ml/export_for_labeling.py            # hasta 150 prompts
    python ml/export_for_labeling.py --limit 200

Salida: ml/data/topic_to_label.csv con columnas id,text,label (label vacía).
Etiqueta la columna `label` con una de:
    programacion, academico, redaccion, ciberseguridad, consulta_general, personal
(criterios en ml/make_topic_synthetic.py) y guarda el archivo como
ml/data/topic_test_real.csv. Ese archivo es el ÚNICO test de tema (CE-4).

- Solo se lee `text_masked` (la BD nunca tiene el texto original).
- Se excluye la plataforma "test" (test.html) y los duplicados (text_sha256).
- Si ya existe topic_test_real.csv, no se vuelven a exportar los ids ya etiquetados.
- Ambos CSV están en .gitignore: son prompts reales.
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
OUT = DATA / "topic_to_label.csv"
LABELED = DATA / "topic_test_real.csv"
DEFAULT_DB = r"C:\auditor-data\auditor.db"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--db", default=os.environ.get("AUDITOR_DB_PATH") or DEFAULT_DB)
    args = ap.parse_args()

    done: set[str] = set()
    if LABELED.exists():
        with LABELED.open(encoding="utf-8") as f:
            done = {r["id"] for r in csv.DictReader(f)}

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)  # solo lectura
    rows = conn.execute(
        """
        SELECT MIN(id) AS id, text_masked
        FROM prompts
        WHERE platform != 'test' AND length(trim(text_masked)) > 0
        GROUP BY text_sha256
        ORDER BY id DESC
        """
    ).fetchall()
    conn.close()

    rows = [(str(i), t) for i, t in rows if str(i) not in done][: args.limit]
    DATA.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "text", "label"])
        for i, t in rows:
            w.writerow([i, t, ""])
    print(f"{len(rows)} prompts exportados -> {OUT}  (ya etiquetados antes: {len(done)})")
    if len(rows) < args.limit:
        print("Aviso: hay menos prompts reales de los pedidos; sigue usando la extensión (CE-11).")


if __name__ == "__main__":
    main()
