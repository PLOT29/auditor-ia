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
 *
 * TOKEN DE SESIÓN (Fase 5C): /analyze y /stats exigen el header X-Centinela-Token. El backend genera
 * uno nuevo cada vez que arranca; aquí se pide a GET /auth/token (que solo responde a la extensión:
 * Chrome no manda Origin en estos GET, pero sí Sec-Fetch-Site: none) y se guarda SOLO en memoria. Si
 * /analyze o /stats responden 401/403 (backend reiniciado = token nuevo), se pide otro y se reintenta una
 * vez. El token nunca se loguea ni se guarda en storage.
 *
 * Fase 5C.1: mientras haya eventos en cola, una alarma (chrome.alarms, cada 30 s) intenta vaciarla sola;
 * el último motivo de fallo (p. ej. "origen no permitido", "backend no disponible") se guarda en
 * storage.local para que el popup lo muestre ("N en cola · último error: …"). Nunca se guarda texto.
 */
"use strict";

importScripts("patterns.js"); // → self.AuditorPatterns

const BACKEND = "http://localhost:8000";
const TIMEOUT_MS = 3000;
const QUEUE_KEY = "pendingEvents";
const QUEUE_MAX = 100;
const ERROR_KEY = "syncError";
const FLUSH_ALARM = "centinela-flush";
const FLUSH_MINUTES = 0.5; // 30 s (mínimo de chrome.alarms en MV3)

// Políticas por defecto: catálogo de patterns.js (Fase 5A.1). Secretos, CURP, RFC, DNI, NIE y dirección
// = bloquear; correo, teléfono, org- de OpenAI, pk_ de Stripe y diccionario propio = advertir.
const DEFAULT_POLICIES = self.AuditorPatterns.DEFAULT_POLICIES;
const POLICIES_VERSION = 2;
const DEFAULT_SETTINGS = {
  monitoring: true, blocking: true, policies: DEFAULT_POLICIES, dictionary: [], pasteThreshold: 3000,
  censorFallbackHosts: [], policiesVersion: POLICIES_VERSION,
};

/**
 * Migra las políticas guardadas por una versión anterior. v1 (Fase 5A) guardaba CURP y RFC = advertir
 * porque era el default; en la v2 (5A.1) su default es bloquear. Solo se cambia lo que seguía en el
 * default viejo: si el usuario lo había elegido distinto, se respeta. Los tipos nuevos toman su default.
 */
function migratePolicies(stored, version) {
  const out = { ...DEFAULT_POLICIES, ...(stored || {}) };
  if ((version || 1) < 2) {
    for (const t of ["curp", "rfc"]) if (out[t] === "advertir") out[t] = DEFAULT_POLICIES[t];
  }
  return out;
}

chrome.runtime.onInstalled.addListener(async () => {
  resumeQueue();
  const current = await chrome.storage.local.get(Object.keys(DEFAULT_SETTINGS));
  const policies = migratePolicies(current.policies, current.policiesVersion);
  await chrome.storage.local.set({ ...DEFAULT_SETTINGS, ...current, policies, policiesVersion: POLICIES_VERSION });
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

// ------------------------------------------------------------- token
let sessionToken = null;

/** Motivo corto que el backend da en un 401/403 ("token inválido", "origen no permitido"…). */
async function reasonOf(res) {
  try {
    const j = await res.clone().json();
    return typeof j.detail === "string" ? j.detail.slice(0, 60) : `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}`;
  }
}

async function setError(motivo) {
  await chrome.storage.local.set({ [ERROR_KEY]: { motivo, ts: new Date().toISOString() } });
}
async function clearError() {
  await chrome.storage.local.remove(ERROR_KEY);
}

async function getToken(force = false) {
  if (sessionToken && !force) return sessionToken;
  const res = await fetchWithTimeout(`${BACKEND}/auth/token`);
  if (!res.ok) {
    const motivo = await reasonOf(res);
    throw Object.assign(new Error(`no se obtuvo el token: ${motivo}`), { name: "TokenError", motivo });
  }
  sessionToken = (await res.json()).token;
  return sessionToken;
}

/** fetch al backend con el token; si responde 401/403 (backend reiniciado = token nuevo), pide otro y
 *  reintenta UNA vez. */
async function authFetch(path, options = {}) {
  let res;
  for (let attempt = 0; attempt < 2; attempt++) {
    const token = await getToken(attempt > 0);
    res = await fetchWithTimeout(`${BACKEND}${path}`, {
      ...options,
      headers: { ...(options.headers || {}), "X-Centinela-Token": token },
    });
    if (res.status !== 401 && res.status !== 403) return res;
  }
  return res;
}

/** POST /analyze. Devuelve el JSON o null si falla (red, timeout o HTTP ≠ 2xx). */
async function postAnalyze(payload) {
  try {
    const res = await authFetch("/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const motivo = await reasonOf(res);
      console.warn(`[auditor] /analyze respondió HTTP ${res.status}: ${motivo}`);
      await setError(motivo);
      return null;
    }
    await clearError();
    return await res.json();
  } catch (err) {
    const motivo = err.motivo || "backend no disponible";
    console.warn(`[auditor] ${motivo} (${err.name})`);
    await setError(motivo);
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
    await scheduleFlush(true);
  });
}

/** Alarma de reintento: existe solo mientras haya eventos pendientes. */
async function scheduleFlush(pending) {
  if (pending) chrome.alarms.create(FLUSH_ALARM, { periodInMinutes: FLUSH_MINUTES });
  else await chrome.alarms.clear(FLUSH_ALARM);
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
    await scheduleFlush(queue.length - sent > 0);
  });
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === FLUSH_ALARM) flushQueue();
});

// Al despertar el service worker: si quedó cola de una sesión anterior, se vuelve a programar la alarma.
async function resumeQueue() {
  const { [QUEUE_KEY]: queue = [] } = await chrome.storage.local.get(QUEUE_KEY);
  await scheduleFlush(queue.length > 0);
}
chrome.runtime.onStartup.addListener(resumeQueue);

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

/** Resumen de hoy (hora local) para el popup: prompts, advertencias, censurados y bloqueos. */
async function today() {
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  try {
    const res = await authFetch(`/stats?desde=${encodeURIComponent(midnight.toISOString())}`);
    if (!res.ok) return { ok: false };
    const s = await res.json();
    const d = s.por_decision || {};
    return { ok: true, prompts: s.total, advertencias: d.advertido || 0, censurados: d.censurado || 0,
             bloqueos: s.bloqueados };
  } catch {
    return { ok: false };
  }
}

async function labelRequest(path, body) {
  try {
    const res = await authFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await res.json().catch(() => ({}));
    return res.ok ? j : { error: typeof j.detail === "string" ? j.detail : `HTTP ${res.status}` };
  } catch (err) {
    return { error: err.motivo || "backend no disponible" };
  }
}

async function status() {
  const [h, stored] = await Promise.all([health(), chrome.storage.local.get([QUEUE_KEY, ERROR_KEY])]);
  const queue = stored[QUEUE_KEY] || [];
  if (h.ok && queue.length) await flushQueue();
  const after = await chrome.storage.local.get([QUEUE_KEY, ERROR_KEY]);
  const err = after[ERROR_KEY];
  return { ok: h.ok, pending: (after[QUEUE_KEY] || []).length, error: err ? err.motivo : (h.ok ? null : "backend no disponible") };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "analyze") {
    analyze(msg.payload).then(sendResponse);
    return true; // respuesta asíncrona
  }
  if (msg?.type === "today") {
    today().then(sendResponse);
    return true;
  }
  if (msg?.type === "health") {
    status().then(sendResponse);
    return true;
  }
  // Fase 5D · corrección desde la etiqueta del chat (solo crear o deshacer; nunca propagar ni reentrenar).
  if (msg?.type === "label") {
    labelRequest("/labels", { event_id: msg.event_id, tarea: msg.tarea, valor: msg.valor }).then(sendResponse);
    return true;
  }
  if (msg?.type === "unlabel") {
    labelRequest(`/labels/${Number(msg.id)}/deshacer`, { incluir_propagadas: false }).then(sendResponse);
    return true;
  }
  return false;
});
