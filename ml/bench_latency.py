"""Latencia de /analyze por etapa (CE-7: p95 < 1 s).

    python ml/bench_latency.py            # 50 prompts
    python ml/bench_latency.py --n 100

- Levanta la app en proceso (TestClient, con su lifespan: los modelos se cargan una vez)
  sobre una BD TEMPORAL: no toca C:\\auditor-data\\auditor.db.
- Prompts de prueba: sintéticos de tema + ejemplos propios de inyección + PII/secretos
  FALSOS, de longitudes variadas. Se descartan 3 de calentamiento.
- Lee las etapas del log del backend (secretos, pii, embeddings, tema, inyeccion, total) y
  el tiempo de ida y vuelta HTTP. Escribe metrics.json["latency_backend_4b"].
"""
from __future__ import annotations

import argparse
import logging
import os
import random
import re
import sys
import tempfile
import time
from pathlib import Path

ML = Path(__file__).resolve().parent
sys.path.insert(0, str(ML.parent / "backend"))
sys.path.insert(0, str(ML))
sys.stdout.reconfigure(encoding="utf-8")

STAGES = ["secretos", "pii", "embeddings", "tema", "inyeccion", "total"]
FAKE_KEY = "sk-" + "proj-DEMO" + "FAKE" * 5 + "abcd"


def prompts(n: int, seed: int = 42) -> list[str]:
    import csv

    import injection_es

    rng = random.Random(seed)
    with (ML / "data" / "topic_synthetic_v1.csv").open(encoding="utf-8") as f:
        topic = [r["text"] for r in csv.DictReader(f)]
    pool = (rng.sample(topic, 20) + rng.sample(injection_es.POSITIVOS, 8) + rng.sample(injection_es.NEGATIVOS, 8) + [
        f"Corrige este código: client = OpenAI(api_key='{FAKE_KEY}')",
        "Hola, soy Juan Pérez, mi correo es demo.fake@gmail.com y vivo en Calle Reforma 123, Col. Centro",
        "Mi CURP es GOMJ800101HDFRRN09 y mi teléfono 55 1234 5678, llena el formulario",
    ])
    long_text = " ".join(rng.sample(topic, 40))  # ~2–3 mil caracteres
    pool += [long_text, long_text[:1500], " ".join(pool[:10])]
    out = []
    while len(out) < n:
        out += pool
    rng.shuffle(out)
    return out[:n]


class StageHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.rows = []

    def emit(self, record):
        msg = record.getMessage()
        if "etapas_ms=" in msg:
            self.rows.append({k: float(v) for k, v in re.findall(r"(\w+)=([\d.]+)", msg.split("etapas_ms=")[1])})


def pct(values, p):
    import numpy as np

    return round(float(np.percentile(values, p)), 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    args = ap.parse_args()

    os.environ["AUDITOR_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "bench.db")
    from fastapi.testclient import TestClient

    import main as backend
    import mlutils as U

    handler = StageHandler()
    log = logging.getLogger("auditor")
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    texts = prompts(args.n + 3)
    rtt = []
    with TestClient(backend.app) as client:
        st = backend.models_status()
        for i, t in enumerate(texts):
            t0 = time.perf_counter()
            r = client.post("/analyze", json={"text": t, "platform": "test"})
            r.raise_for_status()
            if i >= 3:  # calentamiento
                rtt.append((time.perf_counter() - t0) * 1000)
    rows = handler.rows[3:]

    import numpy as np

    table = {s: {"media": round(float(np.mean([r[s] for r in rows])), 2), "p50": pct([r[s] for r in rows], 50),
                 "p95": pct([r[s] for r in rows], 95), "max": round(max(r[s] for r in rows), 2)} for s in STAGES}
    table["http_ida_y_vuelta"] = {"media": round(float(np.mean(rtt)), 2), "p50": pct(rtt, 50),
                                  "p95": pct(rtt, 95), "max": round(max(rtt), 2)}
    loaded = {k: v["loaded"] for k, v in st["models"].items()}
    print(f"n={len(rows)} prompts · modelos cargados: {loaded}")
    print(f"{'etapa':<20}{'media':>9}{'p50':>9}{'p95':>9}{'máx':>9}   (ms)")
    for k, v in table.items():
        print(f"{k:<20}{v['media']:>9}{v['p50']:>9}{v['p95']:>9}{v['max']:>9}")
    ok = table["http_ida_y_vuelta"]["p95"] < 1000
    print(f"CE-7 (p95 < 1000 ms): {'cumple' if ok else 'NO cumple'}")
    U.update_metrics("latency_backend_4b", {
        "n": len(rows), "por_etapa_ms": table, "CE7_meta_ms": 1000, "CE7_cumple": ok,
        "modelos": {k: v["loaded"] for k, v in st["models"].items()},
        "nota": "TestClient en proceso, CPU, un prompt a la vez; incluye escritura en SQLite.",
    })


if __name__ == "__main__":
    main()
