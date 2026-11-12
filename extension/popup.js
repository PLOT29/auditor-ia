// Popup de Centinela (Fase 5A). Todo se guarda en chrome.storage.local; content.js lo lee en memoria.
"use strict";

const P = globalThis.AuditorPatterns;
// Nombres y políticas por defecto: catálogo único de patterns.js (Fase 5A.1).
const NAMES = Object.fromEntries(Object.keys(P.CATALOG).map((t) => [t, P.CATALOG[t].nombre]));
NAMES[P.DICT_TYPE] = "Diccionario propio";
const DEFAULT_POLICIES = P.DEFAULT_POLICIES;
const DEFAULTS = { monitoring: true, blocking: true, policies: DEFAULT_POLICIES, dictionary: [], pasteThreshold: 3000 };
const $ = (id) => document.getElementById(id);

function policyTable(table, types, policies) {
  table.replaceChildren();
  for (const t of types) {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = NAMES[t] || t;
    const cell = document.createElement("td");
    const sel = document.createElement("select");
    sel.setAttribute("aria-label", `Política para ${NAMES[t] || t}`);
    for (const a of ["bloquear", "advertir", "permitir"]) {
      const o = document.createElement("option");
      o.value = a;
      o.textContent = a;
      sel.append(o);
    }
    sel.value = policies[t] || DEFAULT_POLICIES[t];
    sel.className = sel.value;
    sel.addEventListener("change", async () => {
      sel.className = sel.value;
      const { policies: cur = {} } = await chrome.storage.local.get("policies");
      await chrome.storage.local.set({ policies: { ...DEFAULT_POLICIES, ...cur, [t]: sel.value } });
    });
    cell.append(sel);
    tr.append(name, cell);
    table.append(tr);
  }
}

function render(monitoring) {
  $("blocking").disabled = !monitoring;
  $("row-blocking").classList.toggle("off", !monitoring);
  $("blocking-hint").textContent = monitoring ? "Bloquear y advertir según cada tipo" : "Inactivo: requiere el monitoreo";
}

(async () => {
  const s = { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) };
  const policies = { ...DEFAULT_POLICIES, ...(s.policies || {}) };
  $("monitoring").checked = s.monitoring !== false;
  $("blocking").checked = s.blocking !== false;
  render($("monitoring").checked);
  $("monitoring").addEventListener("change", () => {
    chrome.storage.local.set({ monitoring: $("monitoring").checked });
    render($("monitoring").checked);
  });
  $("blocking").addEventListener("change", () => chrome.storage.local.set({ blocking: $("blocking").checked }));

  const local = P.LOCAL_TYPES;
  policyTable($("pol-pii"), local.filter((t) => P.categoryOf(t) !== "secreto"), policies);
  policyTable($("pol-secret"), local.filter((t) => P.categoryOf(t) === "secreto"), policies);

  // Diccionario: se normaliza (sin vacíos ni repetidos, máx. 50) al guardar.
  const dict = $("dictionary");
  dict.value = (s.dictionary || []).join("\n");
  const count = () => {
    const n = P.normalizeDictionary(dict.value.split("\n")).length;
    $("dict-count").textContent = `${n}/${P.DICT_MAX}`;
  };
  count();
  let t = null;
  dict.addEventListener("input", () => {
    count();
    clearTimeout(t);
    t = setTimeout(() => chrome.storage.local.set({ dictionary: P.normalizeDictionary(dict.value.split("\n")) }), 400);
  });
  dict.addEventListener("blur", () => {
    const list = P.normalizeDictionary(dict.value.split("\n"));
    dict.value = list.join("\n");
    chrome.storage.local.set({ dictionary: list });
    count();
  });

  const thr = $("pasteThreshold");
  thr.value = s.pasteThreshold;
  thr.addEventListener("change", () => {
    const v = Math.max(200, Math.min(100000, Number(thr.value) || 3000));
    thr.value = v;
    chrome.storage.local.set({ pasteThreshold: v });
  });

  $("panel").addEventListener("click", () => chrome.tabs.create({ url: "http://localhost:8000/panel" }));

  // Estado del backend y resumen de hoy (lo pide background.js; el popup nunca hace fetch).
  const [health, today] = await Promise.all([
    chrome.runtime.sendMessage({ type: "health" }).catch(() => null),
    chrome.runtime.sendMessage({ type: "today" }).catch(() => null),
  ]);
  const ok = health && health.ok;
  $("dot").className = `dot ${ok ? "ok" : "off"}`;
  $("backend").textContent = ok ? "Backend conectado" : "Backend no disponible";
  if (health && health.pending) $("backend").textContent += ` · ${health.pending} en cola`;
  if (today && today.ok) {
    $("t-prompts").textContent = today.prompts;
    $("t-adv").textContent = today.advertencias;
    $("t-cen").textContent = today.censurados;
    $("t-blk").textContent = today.bloqueos;
  }
})();
