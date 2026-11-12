"""CE-2 · falsos positivos de bloqueo ≤ 3 % en los 200 prompts normales (ml/data/ce2_prompts_normales.csv,
generado por ml/make_ce2_set.py). Se aplica la política POR DEFECTO de cada tipo (catalog.py) sobre las
mismas regex que usa la extensión (secretos + PII por regex: la decisión de bloqueo es local).
La medición antes/después y el detalle por caso están en extension/test/ce2_fp.js.
"""
import csv
from pathlib import Path

from detectors import catalog, pii, secrets

REPO = Path(__file__).resolve().parents[2]
CE2 = REPO / "ml" / "data" / "ce2_prompts_normales.csv"


def blocked(text: str) -> list[str]:
    found = secrets.detect(text)
    found += pii.detect_regex(text, taken=found)
    return [f.type for f in found if catalog.CATALOG.get(f.type, {}).get("politica") == "bloquear"]


def test_ce2_set_has_200_prompts():
    with CE2.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 200 and len({r["id"] for r in rows}) == 200


def test_ce2_false_positive_rate_at_most_3_percent():
    with CE2.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    fps = {r["id"]: blocked(r["text"]) for r in rows}
    fps = {k: v for k, v in fps.items() if v}
    rate = 100 * len(fps) / len(rows)
    assert rate <= 3.0, f"{rate:.1f} % bloqueados por error: {fps}"  # solo ids y tipos
