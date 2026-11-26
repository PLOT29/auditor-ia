"""Aprendizaje asistido en el backend (Fase 5D): reentrenar en segundo plano, adoptar, descartar y volver a
la versión anterior. La lógica de entrenamiento vive en ml/retrain_from_feedback.py (la misma del script).

- Solo lo disparan las rutas /learning/* desde el PANEL (token + origen del panel; nunca la extensión ni el
  agente) o el script.
- Un candidato nunca se adopta solo: lo adopta una persona (botón «Adoptar») si pasó la evaluación.
- Tras adoptar o volver atrás: se recarga el modelo en memoria y se reclasifica la BD en segundo plano con
  ml/reclassify.py, que actualiza las PREDICCIONES; las etiquetas humanas (tabla labels) no se tocan y siguen
  mandando sobre la predicción.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import config
import feedback
import registry

log = logging.getLogger("auditor")


def _ml():
    config.enable_ml_imports()
    import retrain_from_feedback as R  # noqa: PLC0415

    return R


class Job:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.estado = "inactivo"  # inactivo | entrenando | listo | error | reclasificando
        self.pasos: list[dict] = []
        self.resultado = None
        self.error = None
        self.inicio = None
        self.thread = None

    def busy(self) -> bool:
        return self.estado in ("entrenando", "reclasificando")

    def snapshot(self) -> dict:
        with self._lock:
            return {"estado": self.estado, "pasos": list(self.pasos), "resultado": self.resultado, "error": self.error,
                    "segundos": round(time.time() - self.inicio, 1) if self.inicio else None}

    def _step(self, paso: str, msg: str) -> None:
        with self._lock:
            self.pasos.append({"paso": paso, "mensaje": msg, "t": round(time.time() - (self.inicio or time.time()), 1)})

    def _run(self, fn, estado: str) -> bool:
        if self.busy():
            return False
        self.reset()
        self.estado, self.inicio = estado, time.time()
        self.thread = threading.Thread(target=self._wrap, args=(fn,), name=f"centinela-{estado}", daemon=True)
        self.thread.start()
        return True

    def _wrap(self, fn) -> None:
        try:
            self.resultado = fn()
            self.estado = "listo"
        except Exception as e:  # noqa: BLE001
            self.error = str(e)[:300]
            self.estado = "error"
            log.warning("aprendizaje: %s (%s)", type(e).__name__, self.error)

    # ---------------------------------------------------------------- acciones
    def retrain(self) -> bool:
        return self._run(lambda: _ml().run(progress=self._step), "entrenando")

    def apply(self, tarea: str, action) -> bool:
        """Adoptar / volver atrás: cambia el registro, recarga el modelo y reclasifica en segundo plano."""
        def work():
            entry = action()
            self._step("recarga", f"Cargando {tarea} {entry['version']}…")
            reload_model(tarea)
            self._step("reclasificar", "Reclasificando la BD (las etiquetas humanas no se tocan)…")
            summary = reclassify()
            self._step("listo", f"{summary.get('actualizadas', 0)} eventos reclasificados")
            return {"tarea": tarea, "version": entry["version"], "reclasificacion": summary}
        return self._run(work, "reclasificando")


job = Job()


def reload_model(tarea: str) -> None:
    from detectors import injection, topic  # noqa: PLC0415

    (topic if tarea == "tema" else injection).load()


def reclassify() -> dict:
    config.enable_ml_imports()
    import reclassify as RC  # noqa: PLC0415

    try:
        return RC.run()
    except RC.ModelsUnavailable as e:
        return {"actualizadas": 0, "motivo": str(e)}


def adopt(tarea: str, version: str) -> bool:
    R = _ml()
    reg = registry.load()  # validación previa, fuera del hilo (error inmediato para el panel)
    v = registry.find(reg, tarea, version)
    if not v or v["estado"] != "candidato":
        raise ValueError("solo se adopta un candidato que pasó la evaluación")
    return job.apply(tarea, lambda: R.adopt(tarea, version))


def discard(tarea: str, version: str) -> dict:
    reg = registry.load()
    v = registry.find(reg, tarea, version)
    if not v or v["estado"] != "candidato":
        raise ValueError("no hay un candidato con esa versión")
    v.update(estado="descartado", descartado_en=registry.now())
    registry.save(reg)
    try:
        (config.models_dir() / v["archivo"]).unlink(missing_ok=True)
    except OSError:
        pass
    return v


def rollback(tarea: str) -> bool:
    reg = registry.load()
    cur = registry.active(tarea, reg)
    if not cur.get("anterior") or not registry.find(reg, tarea, cur["anterior"]):
        raise ValueError("no hay una versión anterior a la que volver")
    return job.apply(tarea, lambda: _ml().rollback(tarea))


def status() -> dict:
    reg = registry.load()
    st = feedback.stats(registry.max_label_ids(reg))
    minimo = int(os.environ.get("AUDITOR_RETRAIN_MIN", "").strip() or 20)
    nuevas = sum(st["pendientes"].values())
    return {
        "estadisticas": st,
        "minimo": minimo,
        "puede_reentrenar": nuevas >= minimo and not job.busy(),
        "faltan": max(0, minimo - nuevas),
        "activo": {t: registry.active(t, reg) for t in registry.TASKS},
        "versiones": list(reversed(reg["versiones"])),
        "trabajo": job.snapshot(),
        "umbral_similitud": feedback.similarity_threshold(),
    }
