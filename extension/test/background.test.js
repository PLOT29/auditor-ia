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
  const state = { backendUp: false };

  const chrome = {
    runtime: {
      onInstalled: { addListener() {} },
      onMessage: { addListener: (fn) => (messageListener = fn) },
    },
    storage: {
      local: {
        get: async (keys) => {
          const out = {};
          for (const k of [].concat(keys)) if (k in store) out[k] = structuredClone(store[k]);
          return out;
        },
        set: async (obj) => Object.assign(store, structuredClone(obj)),
      },
    },
  };
  const fetch = async (url, opts = {}) => {
    if (!state.backendUp) throw Object.assign(new Error("down"), { name: "TypeError" });
    if (url.endsWith("/health")) return { ok: true, json: async () => ({ ok: true }) };
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
  return { store, posted, state, send };
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
    assert.deepEqual(st, { ok: false, pending: 1 });
    bg.state.backendUp = true;
    st = await bg.send({ type: "health" });
    assert.deepEqual(st, { ok: true, pending: 1 });
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
