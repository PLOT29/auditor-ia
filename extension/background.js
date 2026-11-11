/*
 * background.js — service worker (Manifest V3).
 *
 * ÚNICO lugar donde se hace fetch al backend (SPEC §5.1 y §12 regla 4):
 * desde aquí, gracias a host_permissions, no hay problemas de CORS ni de
 * Private Network Access.
 *
 * PRIVACIDAD: nunca loguear `payload.text`. Solo metadatos (latencia, estado).
 */
"use strict";

const BACKEND = "http://localhost:8000";
const TIMEOUT_MS = 3000;

const DEFAULT_SETTINGS = { monitoring: true, blocking: true };

chrome.runtime.onInstalled.addListener(async () => {
  const current = await chrome.storage.local.get(Object.keys(DEFAULT_SETTINGS));
  await chrome.storage.local.set({ ...DEFAULT_SETTINGS, ...current });
});

async function fetchWithTimeout(url, options = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    return await fetch(url, { ...options, signal: ctrl.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function analyze(payload) {
  const t0 = performance.now();
  try {
    const res = await fetchWithTimeout(`${BACKEND}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      console.warn(`[auditor] /analyze respondió HTTP ${res.status}`);
      return { error: "backend_unavailable" };
    }
    const data = await res.json();
    const rtt = Math.round(performance.now() - t0);
    console.debug(`[auditor] id=${data.id} risk=${data.risk} rtt=${rtt}ms server=${data.latency_ms}ms`);
    return { ...data, rtt_ms: rtt };
  } catch (err) {
    console.warn(`[auditor] backend no disponible (${err.name})`);
    return { error: "backend_unavailable" };
  }
}

async function health() {
  try {
    const res = await fetchWithTimeout(`${BACKEND}/health`);
    return { ok: res.ok && (await res.json()).ok === true };
  } catch {
    return { ok: false };
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "analyze") {
    analyze(msg.payload).then(sendResponse);
    return true; // respuesta asíncrona
  }
  if (msg?.type === "health") {
    health().then(sendResponse);
    return true;
  }
  return false;
});
