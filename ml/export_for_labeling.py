"""Exporta prompts REALES (ya enmascarados) de la BD para etiquetar el tema a mano (CE-4).

    python ml/export_for_labeling.py            # ~100 prompts (default)
    python ml/export_for_labeling.py --n 120

Salida: ml/data/topic_to_label.csv con columnas id,platform,text,label (label vacía).
Etiqueta `label` con una de:
    programacion, academico, redaccion, ciberseguridad, consulta_general, personal
(criterios en ml/make_topic_synthetic.py) y guarda el archivo como
ml/data/topic_test_real.csv. Ese archivo es el ÚNICO test de tema.

- Muestreo ESTRATIFICADO por plataforma: lo más parejo posible entre chatgpt, claude y
  gemini según lo disponible (si una plataforma tiene pocos, el resto se reparte entre
  las demás). Seed fija (42): con la misma BD, mismo resultado.
- Solo se lee `text_masked` (la BD nunca tiene el texto original). Se excluye la
  plataforma "test" (test.html) y los duplicados por text_sha256.
- RESPETA el CSV existente: las filas que ya tienen etiqueta nunca se sobrescriben ni se
  quitan; las filas sin etiqueta que ya estaban se conservan (el muestreo es estable) y
  solo se agregan filas nuevas hasta llegar a --n. Tampoco repite ids que ya estén en
  topic_test_real.csv.
- Ambos CSV están en .gitignore: son prompts reales.
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

SEED = 42
DATA = Path(__file__).resolve().parent / "data"
OUT = DATA / "topic_to_label.csv"
LABELED = DATA / "topic_test_real.csv"
DEFAULT_DB = r"C:\auditor-data\auditor.db"
COLUMNS = ["id", "platform", "text", "label"]


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def load_candidates(db: str) -> list[dict]:
    """Un prompt por text_sha256 (el de menor id), sin la plataforma de prueba."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)  # solo lectura
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT p.id, p.platform, p.text_masked, p.text_sha256
        FROM prompts p
        JOIN (SELECT MIN(id) AS id FROM prompts
              WHERE platform != 'test' AND length(trim(text_masked)) > 0
              GROUP BY text_sha256) u ON u.id = p.id
        ORDER BY p.id
        """
    ).fetchall()
    conn.close()
    return [{"id": str(r["id"]), "platform": r["platform"], "text": r["text_masked"], "sha": r["text_sha256"]}
            for r in rows]


def stratified_pick(pool: dict[str, list[dict]], n: int, start: Counter, rng: random.Random) -> list[dict]:
    """Toma `n` filas de `pool` repartiendo lo más parejo posible por plataforma.

    En cada paso elige la plataforma con MENOS filas (contando las que ya había, `start`)
    que todavía tenga disponibles; empates en orden alfabético. Determinista con la seed.
    """
    for rows in pool.values():
        rng.shuffle(rows)
    counts = Counter(start)
    picked = []
    while len(picked) < n:
        avail = [p for p in sorted(pool) if pool[p]]
        if not avail:
            break
        p = min(avail, key=lambda k: counts[k])
        picked.append(pool[p].pop())
        counts[p] += 1
    return picked


def main(argv: list[str] | None = None) -> dict:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", "--limit", type=int, default=100, dest="n", help="tamaño total del CSV (default 100)")
    ap.add_argument("--db", default=os.environ.get("AUDITOR_DB_PATH") or DEFAULT_DB)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--labeled", default=str(LABELED))
    args = ap.parse_args(argv)
    out = Path(args.out)

    candidates = load_candidates(args.db)
    by_id = {c["id"]: c for c in candidates}
    final_ids = {r["id"] for r in read_csv(Path(args.labeled))}

    # 1) Conservar el CSV existente: etiquetadas siempre; sin etiqueta mientras quepan.
    existing = read_csv(out)
    labeled = [r for r in existing if (r.get("label") or "").strip()]
    unlabeled = [r for r in existing if not (r.get("label") or "").strip()]
    keep = labeled + unlabeled[: max(0, args.n - len(labeled))]
    for r in keep:  # CSV anterior sin columna platform → se completa desde la BD
        if not r.get("platform"):
            r["platform"] = by_id.get(r["id"], {}).get("platform", "desconocida")
    kept_ids = {r["id"] for r in keep}
    kept_sha = {by_id[i]["sha"] for i in kept_ids if i in by_id}

    # 2) Completar hasta --n con muestreo estratificado (sin repetir ids ni huellas).
    pool: dict[str, list[dict]] = defaultdict(list)
    for c in candidates:
        if c["id"] in kept_ids or c["id"] in final_ids or c["sha"] in kept_sha:
            continue
        pool[c["platform"]].append(c)
    new = stratified_pick(pool, max(0, args.n - len(keep)), Counter(r["platform"] for r in keep),
                          random.Random(SEED))

    rows = keep + [{"id": c["id"], "platform": c["platform"], "text": c["text"], "label": ""} for c in new]
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, out)  # escritura atómica: nunca queda un CSV a medias

    summary = {
        "total": len(rows),
        "ya_etiquetadas_conservadas": len(labeled),
        "sin_etiqueta_conservadas": len(keep) - len(labeled),
        "nuevas": len(new),
        "por_plataforma": dict(sorted(Counter(r["platform"] for r in rows).items())),
        "disponibles_en_bd": dict(sorted(Counter(c["platform"] for c in candidates).items())),
    }
    print(f"{len(rows)} filas -> {out}")
    print(summary)
    if len(rows) < args.n:
        print(f"Aviso: solo hay {len(rows)} prompts reales distintos; sigue usando la extensión (CE-11).")
    return summary


if __name__ == "__main__":
    main()
