"""Fase 5C.1 · rango de fechas del centro de control (backend/panel/range.js, con Node).

Los presets se recalculan respecto a "ahora" (cruzan la medianoche solos) y un rango manual es fijo y
muestra un aviso.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

RANGE_JS = Path(__file__).resolve().parents[1] / "panel" / "range.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js no está instalado")

SCRIPT = r"""
const R = require(process.argv[1]);
const at = (y, m, d, h, mi, s) => new Date(y, m - 1, d, h, mi, s);
const before = at(2026, 11, 17, 23, 59, 30), after = at(2026, 11, 18, 0, 0, 30);
const out = {
  p14_before: R.presetRange("14", before), p14_after: R.presetRange("14", after),
  p7_after: R.presetRange("7", after), p30_after: R.presetRange("30", after),
  todo_after: R.presetRange("todo", after, "2026-10-30"), todo_sin_datos: R.presetRange("todo", after, null),
  fin_de_mes: R.presetRange("7", at(2026, 12, 1, 0, 5, 0)), fin_de_anio: R.presetRange("14", at(2027, 1, 1, 0, 0, 1)),
  nota_preset: R.fixedRangeNote("14", "2026-11-04", "2026-11-17", after),
  nota_fija_vieja: R.fixedRangeNote(null, "2026-11-04", "2026-11-17", after),
  nota_fija_hoy: R.fixedRangeNote(null, "2026-11-10", "2026-11-18", after),
};
try { R.presetRange("90", after); out.error = null; } catch (e) { out.error = e.message; }
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def r():
    out = subprocess.run([NODE, "-e", SCRIPT, str(RANGE_JS)], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


def test_preset_crosses_midnight(r):
    assert r["p14_before"] == {"inicio": "2026-11-04", "fin": "2026-11-17"}
    assert r["p14_after"] == {"inicio": "2026-11-05", "fin": "2026-11-18"}  # un minuto después: avanza solo


def test_other_presets(r):
    assert r["p7_after"] == {"inicio": "2026-11-12", "fin": "2026-11-18"}
    assert r["p30_after"] == {"inicio": "2026-10-20", "fin": "2026-11-18"}
    assert r["todo_after"] == {"inicio": "2026-10-30", "fin": "2026-11-18"}
    assert r["todo_sin_datos"] == {"inicio": "2025-11-19", "fin": "2026-11-18"}
    assert r["fin_de_mes"] == {"inicio": "2026-11-25", "fin": "2026-12-01"}
    assert r["fin_de_anio"] == {"inicio": "2026-12-19", "fin": "2027-01-01"}
    assert "preset desconocido" in r["error"]


def test_fixed_range_shows_note(r):
    assert r["nota_preset"] is None
    assert "Rango fijo 2026-11-04 → 2026-11-17" in r["nota_fija_vieja"] and "no incluye hoy" in r["nota_fija_vieja"]
    assert "feed En vivo sí" in r["nota_fija_vieja"]
    assert "no avanza" in r["nota_fija_hoy"]


def test_panel_uses_range_module_and_recomputes_on_every_load():
    panel = RANGE_JS.parent
    html = (panel / "index.html").read_text(encoding="utf-8")
    js = (panel / "panel.js").read_text(encoding="utf-8")
    assert html.index('src="range.js"') < html.index('src="panel.js"')
    load = js[js.index("async function load()"):]
    assert load.index("applyRange();") < load.index("new URLSearchParams")  # recalcula antes de cada /stats
