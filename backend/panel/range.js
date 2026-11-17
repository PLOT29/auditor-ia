/*
 * Centinela · rango de fechas del centro de control (Fase 5C.1). Funciones puras: las usa panel.js y
 * las prueba backend/tests/test_panel_range.py (con Node).
 *
 * Los presets (7 d, 14 d, 30 d, Todo) son RELATIVOS a "ahora": se recalculan en cada refresco, así que al
 * cruzar la medianoche el rango avanza solo. Un rango elegido a mano es FIJO (no avanza).
 */
(function (root) {
  "use strict";

  const PRESETS = ["7", "14", "30", "todo"];

  /** AAAA-MM-DD del día LOCAL de `d`. */
  function isoDay(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function addDays(d, n) {
    const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    x.setDate(x.getDate() + n);
    return x;
  }

  /**
   * Rango {inicio, fin} de un preset respecto a `now`. "todo" empieza en el primer día con datos
   * (`firstDataDay`, AAAA-MM-DD) o, si no se conoce, un año atrás.
   */
  function presetRange(preset, now = new Date(), firstDataDay = null) {
    if (!PRESETS.includes(String(preset))) throw new Error(`preset desconocido: ${preset}`);
    const fin = isoDay(now);
    if (preset === "todo") return { inicio: firstDataDay && firstDataDay <= fin ? firstDataDay : isoDay(addDays(now, -364)), fin };
    return { inicio: isoDay(addDays(now, -(Number(preset) - 1))), fin };
  }

  /** Aviso para un rango manual (fijo): null si es un preset. */
  function fixedRangeNote(preset, inicio, fin, now = new Date()) {
    if (preset) return null;
    const today = isoDay(now);
    return fin < today
      ? `Rango fijo ${inicio} → ${fin}: no incluye hoy; los KPIs y gráficas no cambian con los eventos nuevos (el feed En vivo sí).`
      : `Rango fijo ${inicio} → ${fin}: no avanza con el tiempo; usa 7 d / 14 d / 30 d / Todo para un rango que se actualice solo.`;
  }

  const api = { PRESETS, isoDay, addDays, presetRange, fixedRangeNote };
  root.CentinelaRange = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
