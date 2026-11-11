#!/usr/bin/env node
/*
 * Prueba de patterns.js con los casos de secret_cases.json.
 *   node extension/test/patterns.test.js          (desde la raíz del repo)
 *   node extension/test/patterns.test.js --json   (salida cruda para test_patterns_parity.py)
 *
 * Comprueba:
 *   1. detectSecrets() devuelve exactamente los secretos esperados (tipo + máscara).
 *   2. start/end apuntan al valor real: maskValue(texto[start:end]) == masked.
 *   3. detectSensitive() (cola offline) encuentra la PII esperada.
 *   4. CE-6: tiempo de detectSecrets() (p50 / p95 / máx).
 * Solo imprime ids de caso, nunca el texto.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const P = require(path.join(__dirname, "..", "patterns.js"));

const data = JSON.parse(fs.readFileSync(path.join(__dirname, "secret_cases.json"), "utf8"));

if (process.argv.includes("--json")) {
  const out = {
    secret_cases: data.secret_cases.map((c) => P.detectSecrets(c.text)),
    pii_cases: data.pii_cases.map((c) =>
      P.detectSensitive(c.text).map(({ type, masked, start, end }) => ({ type, masked, start, end }))
    ),
  };
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
}

const same = (got, exp) =>
  got.length === exp.length && got.every((g, i) => g.type === exp[i].type && g.masked === exp[i].masked);

let failures = 0;
function check(id, ok, detail) {
  if (ok) return;
  failures++;
  console.log(`  ✗ ${id}  ${detail}`);
}

console.log(`patterns.js — ${data.secret_cases.length} casos de secretos, ${data.pii_cases.length} de PII`);

for (const c of data.secret_cases) {
  const got = P.detectSecrets(c.text);
  check(c.id, same(got, c.expected),
    `esperado ${JSON.stringify(c.expected.map((e) => e.type))}, obtenido ${JSON.stringify(got.map((g) => `${g.type}:${g.masked}`))}`);
  for (const g of got) {
    check(c.id, P.maskValue(g.type, c.text.slice(g.start, g.end)) === g.masked, `start/end no corresponden a ${g.type}`);
  }
}

for (const c of data.pii_cases) {
  const got = P.detectSensitive(c.text);
  check(c.id, same(got, c.expected),
    `esperado ${JSON.stringify(c.expected.map((e) => e.type))}, obtenido ${JSON.stringify(got.map((g) => `${g.type}:${g.masked}`))}`);
  const masked = P.maskText(c.text, got);
  for (const g of got) check(c.id, !masked.includes(c.text.slice(g.start, g.end)), "maskText dejó un valor en claro");
}

// CE-6: latencia de la detección (la parte síncrona de la decisión de bloqueo).
const texts = data.secret_cases.map((c) => c.text);
const long = texts.join("\n").repeat(Math.ceil(10000 / texts.join("\n").length)).slice(0, 10000);
function bench(samples, reps) {
  const t = [];
  for (let r = 0; r < reps; r++) for (const s of samples) {
    const t0 = performance.now();
    P.detectSecrets(s);
    t.push(performance.now() - t0);
  }
  t.sort((a, b) => a - b);
  const q = (p) => t[Math.min(t.length - 1, Math.floor(p * t.length))];
  return { n: t.length, p50: q(0.5), p95: q(0.95), max: t[t.length - 1] };
}
const fmt = (b) => `p50=${b.p50.toFixed(3)}ms p95=${b.p95.toFixed(3)}ms máx=${b.max.toFixed(3)}ms (n=${b.n})`;
console.log(`CE-6 prompts del set:      ${fmt(bench(texts, 200))}`);
console.log(`CE-6 prompt de 10 000 car: ${fmt(bench([long], 200))}`);

const total = data.secret_cases.length + data.pii_cases.length;
console.log(failures ? `\n✗ ${failures} fallo(s)` : `\n✓ ${total}/${total} casos OK`);
process.exitCode = failures ? 1 : 0;
