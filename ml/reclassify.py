"""Reclasifica las filas ya guardadas con los modelos de la Fase 4B.

    python ml/reclassify.py --only-unclassified --dry-run   # solo conteos
    python ml/reclassify.py --only-unclassified             # aplica (una transacción)
    python ml/reclassify.py                                 # todas las filas

- Recalcula category, category_conf, injection_score y risk con el MISMO pipeline del
  backend (main.classify_text: un embedding → tema → inyección; risk.compute_risk).
- Usa `text_masked`: el texto original no existe por diseño. Los marcadores (<EMAIL>,
  <OPENAI_KEY>…) reemplazan los datos, así que la precisión puede ser un poco menor que
  con el texto original (SPEC §5.3).
- NO toca findings, blocked ni user_decision. El riesgo solo puede SUBIR: se toma el
  máximo entre el riesgo guardado (que ya refleja secretos/PII) y el recalculado.
- Carga los modelos una sola vez. Si tema o inyección no cargan, no reclasifica nada
  (para no sobrescribir categorías reales con "sin_clasificar").
- --dry-run: no escribe; muestra solo conteos. Sin --dry-run, todo va en UNA transacción.
- Nunca imprime texto.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))


class ModelsUnavailable(RuntimeError):
    pass


def run(only_unclassified: bool = False, dry_run: bool = False) -> dict:
    import db  # noqa: PLC0415 — después de fijar AUDITOR_DB_PATH
    import main as backend  # noqa: PLC0415
    import risk  # noqa: PLC0415

    logging.getLogger("auditor").setLevel(logging.WARNING)
    st = backend.load_models()
    loaded = {k: v["loaded"] for k, v in st["models"].items()}
    if not (loaded["topic"] and loaded["injection"]):
        raise ModelsUnavailable(f"modelos no disponibles {loaded}: no se reclasifica nada")

    rows = db.fetch_for_reclassify(only_unclassified)
    stats: Counter = Counter()
    cat_changes: Counter = Counter()
    risk_changes: Counter = Counter()
    new_cats: Counter = Counter()
    updates = []
    for r in rows:
        cls = backend.classify_text(r["text_masked"] or "")
        findings = json.loads(r["findings_json"] or "[]")
        recomputed = risk.compute_risk(findings, cls["injection_score"])
        new_risk = risk.max_risk(r["risk"], recomputed)  # nunca baja
        new_cats[cls["category"]] += 1
        if cls["category"] != r["category"]:
            stats["cambian_categoria"] += 1
            cat_changes[f"{r['category']} -> {cls['category']}"] += 1
        if new_risk != r["risk"]:
            stats["cambian_riesgo"] += 1
            risk_changes[f"{r['risk']} -> {new_risk}"] += 1
        if recomputed != new_risk:
            stats["riesgo_no_bajado"] += 1  # el modelo daría menos, pero se respeta lo guardado
        updates.append((r["id"], cls["category"], cls["category_conf"], cls["injection_score"], new_risk))

    if not dry_run and updates:
        with db.transaction():  # todo o nada
            for u in updates:
                db.update_classification(*u)

    return {
        "dry_run": dry_run,
        "solo_sin_clasificar": only_unclassified,
        "filas": len(rows),
        "cambian_categoria": stats["cambian_categoria"],
        "cambian_riesgo": stats["cambian_riesgo"],
        "riesgo_no_bajado": stats["riesgo_no_bajado"],
        "transiciones_categoria": dict(cat_changes.most_common()),
        "transiciones_riesgo": dict(risk_changes.most_common()),
        "categorias_resultantes": dict(new_cats.most_common()),
        "actualizadas": 0 if dry_run else len(updates),
    }


def main(argv: list[str] | None = None) -> dict:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="no escribe; solo conteos")
    ap.add_argument("--only-unclassified", action="store_true", help="solo filas con category_conf = 0 o null")
    ap.add_argument("--db", help="ruta de la BD (por defecto AUDITOR_DB_PATH o C:\\auditor-data\\auditor.db)")
    args = ap.parse_args(argv)
    if args.db:
        os.environ["AUDITOR_DB_PATH"] = args.db
    try:
        summary = run(args.only_unclassified, args.dry_run)
    except ModelsUnavailable as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    main()
