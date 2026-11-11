// Popup: interruptores guardados en chrome.storage.local (RF-08).
"use strict";

const KEYS = ["monitoring", "blocking"];
const DEFAULTS = { monitoring: true, blocking: true };

(async () => {
  const stored = await chrome.storage.local.get(KEYS);
  for (const key of KEYS) {
    const box = document.getElementById(key);
    box.checked = stored[key] ?? DEFAULTS[key];
    box.addEventListener("change", () => chrome.storage.local.set({ [key]: box.checked }));
  }

  const dot = document.getElementById("dot");
  const label = document.getElementById("backend");
  const resp = await chrome.runtime.sendMessage({ type: "health" }).catch(() => null);
  if (resp && resp.ok) {
    dot.className = "dot ok";
    label.textContent = "Backend conectado (localhost:8000)";
  } else {
    dot.className = "dot off";
    label.textContent = "Backend no disponible";
  }
})();
