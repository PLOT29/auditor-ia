// Popup: interruptores guardados en chrome.storage.local (RF-08).
// Monitoreo OFF ⇒ el bloqueo también queda inactivo (content.js aplica la misma regla).
"use strict";

const DEFAULTS = { monitoring: true, blocking: true };

const monitoring = document.getElementById("monitoring");
const blocking = document.getElementById("blocking");
const blockingRow = document.getElementById("row-blocking");
const blockingHint = document.getElementById("blocking-hint");

function render() {
  blocking.disabled = !monitoring.checked;
  blockingRow.classList.toggle("off", !monitoring.checked);
  blockingHint.textContent = monitoring.checked
    ? "Detiene el envío si hay un secreto"
    : "Inactivo: requiere el monitoreo encendido";
}

(async () => {
  const stored = await chrome.storage.local.get(Object.keys(DEFAULTS));
  monitoring.checked = stored.monitoring ?? DEFAULTS.monitoring;
  blocking.checked = stored.blocking ?? DEFAULTS.blocking;
  render();

  monitoring.addEventListener("change", () => {
    chrome.storage.local.set({ monitoring: monitoring.checked });
    render();
  });
  blocking.addEventListener("change", () => chrome.storage.local.set({ blocking: blocking.checked }));

  const dot = document.getElementById("dot");
  const label = document.getElementById("backend");
  const resp = await chrome.runtime.sendMessage({ type: "health" }).catch(() => null);
  const pending = resp && resp.pending ? ` · ${resp.pending} evento(s) en cola` : "";
  if (resp && resp.ok) {
    dot.className = "dot ok";
    label.textContent = `Backend conectado (localhost:8000)${pending}`;
  } else {
    dot.className = "dot off";
    label.textContent = `Backend no disponible${pending}`;
  }
})();
