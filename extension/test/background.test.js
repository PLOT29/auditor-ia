#!/usr/bin/env node
/*
 * Prueba de la cola de reintento de background.js (RNF-05) con chrome/fetch simulados.
 *   node extension/test/background.test.js
 * Valores falsos. Solo imprime nombres de prueba.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");

const EXT = path.join(__dirname, "..");
const KEY = "sk-" + "proj-DEMO" + "FAKE".repeat(5) + "abcd";
const EMAIL = "demo.fake@gmail.com";

function load() {
  const store = {};
  const posted = [];
  let messageListener = null;
  let installedListener = null;
  let alarmListener = null;
  const alarms = new Map();
  const state = { backendUp: false, token: "tok-1", tokenRequests: 0, headers: [], tokenStatus: 200, authStatus: 403 };

  const chrome = {
    runtime: {
      onInstalled: { addListener: (fn) => (installedListener = fn) },
      onStartup: { addListener() {} },
      onMessage: { addListener: (fn) => (messageListener = fn) },
    },
    alarms: {
      create: (name, info) => alarms.set(name, info),
      clear: async (name) => alarms.delete(name),
      onAlarm: { addListener: (fn) => (alarmListener = fn) },
    },
    storage: {
      local: {
        get: async (keys) => {
          const out = {};
          for (const k of [].concat(keys)) if (k in store) out[k] = structuredClone(store[k]);
          return out;
        },
        set: async (obj) => Object.assign(store, structuredClone(obj)),
        remove: async (k) => { for (const x of [].concat(k)) delete store[x]; },
      },
    },
  };
  const fetch = async (url, opts = {}) => {
    if (!state.backendUp) throw Object.assign(new Error("down"), { name: "TypeError" });
    if (url.endsWith("/health")) return { ok: true, json: async () => ({ ok: true }) };
    const res = (status, body) => {
      const r = { ok: status === 200, status, json: async () => body };
      r.clone = () => r;
      return r;
    };
    if (url.endsWith("/auth/token")) {
      state.tokenRequests++;
      return state.tokenStatus === 200 ? res(200, { token: state.token }) : res(state.tokenStatus, { detail: "origen no permitido" });
    }
    const sent = (opts.headers || {})["X-Centinela-Token"];
    state.headers.push(sent);
    if (sent !== state.token) return res(state.authStatus, { detail: "token inválido" });
    const body = JSON.parse(opts.body);
    posted.push(body);
    return { ok: true, status: 200, json: async () => ({ id: posted.length, risk: "alto", latency_ms: 1 }) };
  };
  const ctx = {
    chrome, fetch, console: { info() {}, debug() {}, warn() {} },
    AbortController, setTimeout, clearTimeout, performance, structuredClone,
  };
  ctx.self = ctx;
  ctx.importScripts = (file) => vm.runInContext(fs.readFileSync(path.join(EXT, file), "utf8"), ctx);
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(EXT, "background.js"), "utf8"), ctx);

  // JSON ida y vuelta: los objetos del contexto vm tienen otro prototipo (otro "realm").
  const send = (msg) => new Promise((resolve) => messageListener(msg, {}, (r) => resolve(JSON.parse(JSON.stringify(r)))));
  const fireAlarm = async (name) => {
    alarmListener({ name });
    await new Promise((r) => setTimeout(r, 30));
  };
  return { store, posted, state, send, alarms, fireAlarm, install: () => installedListener() };
}

const payload = (over = {}) => ({
  text: `Corrige: api_key='${KEY}' y escribe a ${EMAIL}`,
  platform: "chatgpt",
  timestamp: "2026-11-11T10:00:00.000Z",
  client_findings: [{ type: "openai_key", masked: "sk-****abcd" }],
  blocked: true,
  user_decision: "cancelado",
  client_latency_ms: 0.4,
  ...over,
});
const settle = () => new Promise((r) => setTimeout(r, 20));

const tests = {
  async "401 por token también renueva el token y reintenta una vez"() {
    const bg = load();
    bg.state.backendUp = true;
    bg.state.authStatus = 401;
    await bg.send({ type: "analyze", payload: payload({ blocked: false, user_decision: "no_aplica", text: "uno" }) });
    bg.state.token = "tok-9"; // reinicio del backend sin recargar la extensión
    const resp = await bg.send({ type: "analyze", payload: payload({ blocked: false, user_decision: "no_aplica", text: "dos" }) });
    assert.equal(resp.id, 2);
    assert.deepEqual(bg.state.headers, ["tok-1", "tok-1", "tok-9"]);
  },

  async "sin token (403 en /auth/token): el evento queda en cola y el popup ve el motivo"() {
    const bg = load();
    bg.state.backendUp = true;
    bg.state.tokenStatus = 403;
    const resp = await bg.send({ type: "analyze", payload: payload() });
    assert.deepEqual(resp, { error: "backend_unavailable", queued: true });
    assert.equal(bg.store.syncError.motivo, "origen no permitido");
    assert.ok(bg.alarms.has("centinela-flush"), "alarma de reintento programada");
    const st = await bg.send({ type: "health" });
    assert.deepEqual(st, { ok: true, pending: 1, error: "origen no permitido" });
    assert.ok(!JSON.stringify(bg.store).includes(KEY));
  },

  async "la alarma vacía la cola sola al reconectar y luego se apaga"() {
    const bg = load();
    await bg.send({ type: "analyze", payload: payload({ user_decision: "cancelado" }) });
    await bg.send({ type: "analyze", payload: payload({ user_decision: "censurado" }) });
    assert.equal(bg.store.pendingEvents.length, 2);
    assert.equal(bg.alarms.get("centinela-flush").periodInMinutes, 0.5);
    assert.equal(bg.store.syncError.motivo, "backend no disponible");
    await bg.fireAlarm("centinela-flush"); // sigue caído: nada se pierde
    assert.equal(bg.store.pendingEvents.length, 2);
    assert.ok(bg.alarms.has("centinela-flush"));
    bg.state.backendUp = true; // el backend volvió (con token nuevo)
    bg.state.token = "tok-2";
    await bg.fireAlarm("centinela-flush");
    assert.deepEqual(bg.store.pendingEvents, []);
    assert.deepEqual(bg.posted.map((p) => p.user_decision), ["cancelado", "censurado"]);
    assert.ok(!bg.alarms.has("centinela-flush"), "sin pendientes no queda alarma");
    assert.equal(bg.store.syncError, undefined, "el error se limpia al reconectar");
    const st = await bg.send({ type: "health" });
    assert.deepEqual(st, { ok: true, pending: 0, error: null });
  },

  async "manda el token de sesión y lo renueva si el backend se reinició (403)"() {
    const bg = load();
    bg.state.backendUp = true;
    let resp = await bg.send({ type: "analyze", payload: payload({ blocked: false, user_decision: "no_aplica", text: "hola" }) });
    assert.equal(resp.id, 1);
    assert.equal(bg.state.tokenRequests, 1);
    assert.deepEqual(bg.state.headers, ["tok-1"]);
    bg.state.token = "tok-2"; // reinicio del backend: token nuevo
    resp = await bg.send({ type: "analyze", payload: payload({ blocked: false, user_decision: "no_aplica", text: "otra" }) });
    assert.equal(resp.id, 2);
    assert.equal(bg.state.tokenRequests, 2);
    assert.deepEqual(bg.state.headers, ["tok-1", "tok-1", "tok-2"]);
    assert.ok(!JSON.stringify(bg.store).includes("tok-"), "el token nunca se guarda en storage");
  },

  async "instalación nueva: políticas por defecto de la 5A.1"() {
    const bg = load();
    await bg.install();
    const p = bg.store.policies;
    for (const t of ["openai_key", "stripe_key", "uri_credentials", "curp", "rfc", "dni", "nie", "address"]) assert.equal(p[t], "bloquear", t);
    for (const t of ["email", "phone_mx", "openai_org", "stripe_publishable_key", "termino_propio"]) assert.equal(p[t], "advertir", t);
    assert.equal(p.person, undefined, "person solo lo detecta el backend: no tiene política local");
    assert.equal(bg.store.policiesVersion, 2);
  },

  async "actualización desde la 5A: CURP/RFC en el default viejo pasan a bloquear; lo elegido se respeta"() {
    const bg = load();
    Object.assign(bg.store, { policies: { curp: "advertir", rfc: "permitir", email: "bloquear", openai_key: "advertir" } });
    await bg.install();
    const p = bg.store.policies;
    assert.equal(p.curp, "bloquear"); // seguía en el default viejo
    assert.equal(p.rfc, "permitir"); // elección del usuario
    assert.equal(p.email, "bloquear");
    assert.equal(p.openai_key, "advertir");
    assert.equal(p.dni, "bloquear"); // tipo nuevo → su default
    await bg.install(); // idempotente: una segunda actualización no vuelve a tocar nada
    assert.equal(bg.store.policies.rfc, "permitir");
  },

  async "backend caído: evento bloqueado se encola ENMASCARADO"() {
    const bg = load();
    const resp = await bg.send({ type: "analyze", payload: payload() });
    assert.deepEqual(resp, { error: "backend_unavailable", queued: true });
    const q = bg.store.pendingEvents;
    assert.equal(q.length, 1);
    const raw = JSON.stringify(bg.store);
    assert.ok(!raw.includes(KEY), "la key quedó en claro en storage");
    assert.ok(!raw.includes(EMAIL), "el correo quedó en claro en storage");
    assert.equal(q[0].text, "Corrige: api_key='<OPENAI_KEY>' y escribe a <EMAIL>");
    assert.deepEqual(q[0].client_findings.map((f) => f.type).sort(), ["email", "openai_key"]);
    assert.equal(q[0].user_decision, "cancelado");
    assert.equal(q[0].timestamp, "2026-11-11T10:00:00.000Z");
  },

  async "backend caído: evento NO bloqueado no se encola"() {
    const bg = load();
    const resp = await bg.send({ type: "analyze", payload: payload({ blocked: false, user_decision: "no_aplica" }) });
    assert.deepEqual(resp, { error: "backend_unavailable" });
    assert.equal(bg.store.pendingEvents, undefined);
  },

  async "la cola se limita a 100 (descarta los más viejos)"() {
    const bg = load();
    for (let i = 0; i < 105; i++) {
      await bg.send({ type: "analyze", payload: payload({ timestamp: `2026-11-11T10:00:${String(i % 60).padStart(2, "0")}.${i}Z` }) });
    }
    assert.equal(bg.store.pendingEvents.length, 100);
    assert.ok(bg.store.pendingEvents[0].timestamp.endsWith(".5Z"));
  },

  async "siguiente envío exitoso reenvía la cola en orden y la vacía"() {
    const bg = load();
    await bg.send({ type: "analyze", payload: payload({ user_decision: "cancelado" }) });
    await bg.send({ type: "analyze", payload: payload({ user_decision: "enviado" }) });
    bg.state.backendUp = true;
    const resp = await bg.send({ type: "analyze", payload: payload({ blocked: false, user_decision: "no_aplica", text: "hola" }) });
    assert.equal(resp.id, 1);
    await settle();
    assert.deepEqual(bg.posted.map((p) => p.user_decision), ["no_aplica", "cancelado", "enviado"]);
    assert.ok(!JSON.stringify(bg.posted.slice(1)).includes(KEY));
    assert.deepEqual(bg.store.pendingEvents, []);
  },

  async "health informa pendientes y vacía la cola si el backend volvió"() {
    const bg = load();
    await bg.send({ type: "analyze", payload: payload() });
    let st = await bg.send({ type: "health" });
    assert.deepEqual(st, { ok: false, pending: 1, error: "backend no disponible" });
    bg.state.backendUp = true;
    st = await bg.send({ type: "health" });
    assert.deepEqual(st, { ok: true, pending: 0, error: null }); // health ya vacía la cola antes de responder
    await settle();
    assert.deepEqual(bg.store.pendingEvents, []);
  },
};

(async () => {
  let failed = 0;
  for (const [name, fn] of Object.entries(tests)) {
    try {
      await fn();
      console.log(`  ✓ ${name}`);
    } catch (err) {
      failed++;
      console.log(`  ✗ ${name}\n    ${err.message}`);
    }
  }
  const total = Object.keys(tests).length;
  console.log(failed ? `\n✗ ${failed}/${total} fallaron` : `\n✓ ${total}/${total} OK`);
  process.exitCode = failed ? 1 : 0;
})();
