/*
 * Centinela · autoguardado de la nota del evento (Fase 5D.1). Sin DOM: lo usa panel.js y lo prueba
 * backend/tests/test_panel_notes.py (con Node).
 *
 *   const a = CentinelaNotes.autosaver({ save: (texto) => Promise, delay: 1000, onState: (estado) => {} });
 *   a.load("texto guardado")   valor inicial (no cuenta como cambio)
 *   a.input("texto")           cada tecla: programa el guardado tras `delay` ms sin escribir
 *   a.flush()                  guarda YA si hay cambios (blur, Esc, flechas, cerrar); devuelve una promesa
 * Estados: "guardado" · "guardando" · "error" · "sin_cambios". Nunca guarda dos veces el mismo texto ni
 * pierde la última versión: si se escribe mientras se guarda, vuelve a guardar al terminar.
 */
(function (root) {
  "use strict";

  function autosaver({ save, delay = 1000, onState = () => {}, setTimer = setTimeout, clearTimer = clearTimeout }) {
    let saved = "";
    let current = "";
    let timer = null;
    let inflight = null;

    const dirty = () => current !== saved;

    async function flush() {
      if (timer) {
        clearTimer(timer);
        timer = null;
      }
      if (inflight) await inflight.catch(() => {});
      if (!dirty()) return true;
      const value = current;
      onState("guardando");
      inflight = Promise.resolve().then(() => save(value));
      try {
        await inflight;
        saved = value;
        onState(dirty() ? "guardando" : "guardado");
        if (dirty()) return flush(); // se escribió mientras se guardaba
        return true;
      } catch (_err) {
        onState("error");
        return false;
      } finally {
        inflight = null;
      }
    }

    return {
      load(value) {
        saved = current = value || "";
        onState("sin_cambios");
      },
      input(value) {
        current = value || "";
        if (timer) clearTimer(timer);
        timer = setTimer(() => {
          timer = null;
          flush();
        }, delay);
      },
      flush,
      dirty,
      value: () => current,
    };
  }

  const api = { autosaver };
  root.CentinelaNotes = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
