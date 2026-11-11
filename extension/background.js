/*
 * background.js — service worker (Manifest V3).
 *
 * ÚNICO lugar donde se hace fetch al backend (SPEC §5.1 y §12 regla 4):
 * desde aquí, gracias a host_permissions, no hay problemas de CORS ni de
 * Private Network Access.
 *
 * Cola de reintento (RNF-05): si un evento BLOQUEADO (cancelado / enviado) no
 * llega al backend, se guarda en chrome.storage.local (máx. 100) y se reintenta
 * tras el siguiente POST exitoso. Antes de guardarlo, el texto se ENMASCARA aquí
 * mismo con patterns.js (secretos + PII) — nunca se guarda texto en claro.
 *
 * PRIVACIDAD: nunca loguear `payload.text`. Solo metadatos (latencia, estado).
 */
"use strict";

importScripts("patterns.js"); // → self.AuditorPatterns

const BACKEND = "http://localhost:8000";
const TIMEOUT_MS = 3000;
const QUEUE_KEY = "pendingEvents";
const QUEUE_MAX = 100;

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

/** POST /analyze. Devuelve el JSON o null si falla (red, timeout o HTTP ≠ 2xx). */
async function postAnalyze(payload) {
  try {
    const res = await fetchWithTimeout(`${BACKEND}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      console.warn(`[auditor] /analyze respondió HTTP ${res.status}`);
      return null;
    }
    return await res.json();
  } catch (err) {
    console.warn(`[auditor] backend no disponible (${err.name})`);
    return null;
  }
}

// ------------------------------------------------------------- cola
// Serializa el acceso a la cola (varias pestañas pueden escribir a la vez).
let queueLock = Promise.resolve();
function withQueue(fn) {
  const run = queueLock.then(fn, fn);
  queueLock = run.catch(() => {});
  return run;
}

/** Copia del payload apta para guardarse en disco: texto enmascarado en el cliente. */
function sanitizeForQueue(payload) {
  const P = self.AuditorPatterns;
  const found = P.detectSensitive(payload.text || "");
  const byKey = new Map();
  for (const f of [...(payload.client_findings || []), ...found]) {
    byKey.set(`${f.type}|${f.masked}`, { type: f.type, masked: f.masked });
  }
  return {
    text: P.maskText(payload.text || "", found),
    platform: payload.platform,
    timestamp: payload.timestamp,
    client_findings: [...byKey.values()],
    blocked: payload.blocked,
    user_decision: payload.user_decision,
    client_latency_ms: payload.client_latency_ms ?? null,
  };
}

function enqueue(payload) {
  return withQueue(async () => {
    const { [QUEUE_KEY]: queue = [] } = await chrome.storage.local.get(QUEUE_KEY);
    queue.push(sanitizeForQueue(payload));
    const dropped = Math.max(0, queue.length - QUEUE_MAX);
    await chrome.storage.local.set({ [QUEUE_KEY]: queue.slice(dropped) });
    console.info(`[auditor] evento en cola (pendientes=${queue.length - dropped}${dropped ? `, descartados=${dropped}` : ""})`);
  });
}

function flushQueue() {
  return withQueue(async () => {
    const { [QUEUE_KEY]: queue = [] } = await chrome.storage.local.get(QUEUE_KEY);
    if (!queue.length) return;
    let sent = 0;
    for (const item of queue) {
      if (!(await postAnalyze(item))) break; // backend cayó otra vez: conservar el resto
      sent++;
    }
    await chrome.storage.local.set({ [QUEUE_KEY]: queue.slice(sent) });
    console.info(`[auditor] cola: reenviados=${sent} pendientes=${queue.length - sent}`);
  });
}

// --------------------------------------------------------- mensajes
async function analyze(payload) {
  const t0 = performance.now();
  const data = await postAnalyze(payload);
  if (data) {
    const rtt = Math.round(performance.now() - t0);
    console.debug(
      `[auditor] id=${data.id} risk=${data.risk} rtt=${rtt}ms server=${data.latency_ms}ms ` +
        `cliente=${payload.client_latency_ms ?? "-"}ms`
    );
    flushQueue(); // "reintenta al siguiente envío exitoso" (sin bloquear la respuesta)
    return { ...data, rtt_ms: rtt };
  }
  if (payload.blocked) {
    await enqueue(payload);
    return { error: "backend_unavailable", queued: true };
  }
  return { error: "backend_unavailable" };
}

async function health() {
  try {
    const res = await fetchWithTimeout(`${BACKEND}/health`);
    return { ok: res.ok && (await res.json()).ok === true };
  } catch {
    return { ok: false };
  }
}

async function status() {
  const [h, { [QUEUE_KEY]: queue = [] }] = await Promise.all([health(), chrome.storage.local.get(QUEUE_KEY)]);
  if (h.ok && queue.length) flushQueue();
  return { ok: h.ok, pending: queue.length };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "analyze") {
    analyze(msg.payload).then(sendResponse);
    return true; // respuesta asíncrona
  }
  if (msg?.type === "health") {
    status().then(sendResponse);
    return true;
  }
  return false;
});
