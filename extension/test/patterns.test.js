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
const demo = JSON.parse(fs.readFileSync(path.join(__dirname, "demo_cases.json"), "utf8")).casos;

if (process.argv.includes("--json")) {
  const out = {
    secret_cases: data.secret_cases.map((c) => P.detectSecrets(c.text)),
    pii_cases: data.pii_cases.map((c) => P.detectSensitive(c.text)),
    demo_cases: demo.map((c) => P.detectSensitive(c.texto)),
    catalog: P.CATALOG,
    default_policies: P.DEFAULT_POLICIES,
    pii_types: P.PII_TYPES,
  };
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
}

const same = (got, exp) =>
  got.length === exp.length &&
  got.every((g, i) => g.type === exp[i].type && g.masked === exp[i].masked && g.valido === exp[i].valido);

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

// ------------------------------------------------- casos de demo (Fase 5A.1)
// Cada escenario produce EXACTAMENTE los hallazgos y la acción esperados con las políticas por defecto,
// y «Censurar» deja un texto sin nada que bloquear ni advertir.
const withPolicy = (fs) => fs.map((f) => ({ ...f, action: P.DEFAULT_POLICIES[f.type] || "advertir" }));
for (const c of demo) {
  const got = withPolicy(P.evaluate(c.texto));
  check(c.id, same(got, c.esperado),
    `esperado ${JSON.stringify(c.esperado.map((e) => e.type))}, obtenido ${JSON.stringify(got.map((g) => `${g.type}:${g.masked}`))}`);
  check(c.id, P.maxAction(got) === c.accion, `acción ${P.maxAction(got)} ≠ ${c.accion}`);
  const censored = P.censorText(c.texto, got.filter((f) => f.action !== "permitir"));
  for (const g of got) check(c.id, !censored.includes(c.texto.slice(g.start, g.end)), `censura dejó un ${g.type} en claro`);
  check(c.id, P.evaluate(censored).length === 0, `el texto censurado aún tiene hallazgos: ${P.evaluate(censored).map((f) => f.type)}`);
}
const dniBad = P.evaluate(demo.find((c) => c.id === "caso-5-stripe-pii").texto).find((f) => f.type === "dni");
check("modal-dni-invalido", P.findingLabel(dniBad) === "formato de DNI (letra de control no válida)", P.findingLabel(dniBad));
check("modal-dni-valido", P.findingLabel(P.evaluate("DNI 12345678Z")[0]) === "DNI");
const addr = P.evaluate(demo.find((c) => c.id === "caso-6a-direccion-completa").texto)[0];
check("direccion-completa-enmascarada", /^Calle Fresno 128, Int\. 4, Col\. Santa María la Ribera, Alcaldía Cuauhtémoc, C\.P\. 06400$/
  .test(demo.find((c) => c.id === "caso-6a-direccion-completa").texto.slice(addr.start, addr.end)), "el span no cubre la dirección completa");
check("marcadores", ["bearer_token", "stripe_key", "stripe_webhook_secret", "uri_credentials", "dni", "address", "private_key"]
  .map(P.censorMarker).join() === "[TOKEN_OCULTO],[STRIPE_KEY_OCULTA],[WEBHOOK_SECRET_OCULTO],[URI_CON_CREDENCIALES],[DNI_OCULTO],[DIRECCIÓN_OCULTA],[CLAVE_PRIVADA_OCULTA]");
check("politicas-default", ["curp", "rfc", "dni", "nie", "address", "stripe_key", "bearer_token"].every((t) => P.DEFAULT_POLICIES[t] === "bloquear") &&
  ["email", "phone_mx", "openai_org", "stripe_publishable_key", "termino_propio"].every((t) => P.DEFAULT_POLICIES[t] === "advertir") &&
  !("person" in P.DEFAULT_POLICIES));

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

// ---------------------------------------------------------------- Fase 5A
let extra = 0;
function ok(id, cond, detail = "") {
  extra++;
  check(id, cond, detail);
}

// Diccionario propio: sin mayúsculas ni acentos, palabra completa, nunca el término en la salida.
const DICT = ["Proyecto Fénix", "fénix", "ACME-Interno", "  ", "Fénix"];
ok("dict-normaliza", JSON.stringify(P.normalizeDictionary(DICT)) === JSON.stringify(["Proyecto Fénix", "fénix", "ACME-Interno"]));
ok("dict-max-50", P.normalizeDictionary(Array.from({ length: 80 }, (_, i) => `termino${i}`)).length === 50);
const dtext = "Revisa el PROYECTO FENIX y el fenix viejo; no fenixes ni ACME-interno2. Pero sí acme-interno.";
const df = P.evaluate(dtext, { dictionary: DICT }).filter((f) => f.type === P.DICT_TYPE);
ok("dict-encuentra", df.length === 3, JSON.stringify(df.map((f) => dtext.slice(f.start, f.end))));
ok("dict-palabra-completa", !df.some((f) => /fenixes|interno2/i.test(dtext.slice(f.start, f.end + 2))));
ok("dict-gana-el-mas-largo", dtext.slice(df[0].start, df[0].end) === "PROYECTO FENIX");
ok("dict-enmascarado", df.every((f) => f.masked === "****"));
ok("dict-sin-terminos", P.evaluate(dtext, { dictionary: [] }).every((f) => f.type !== P.DICT_TYPE));
ok("dict-no-pisa-secretos", (() => {
  const t = "key sk-proj-DEMOFAKEFAKEFAKEFAKEFAKEabcd";
  return P.evaluate(t, { dictionary: ["fakefake"] }).map((f) => f.type).join() === "openai_key";
})());

// evaluate = secretos + PII + diccionario, ordenados y con source.
const etext = "Mi CURP GOMJ800101HDFRRN09, key sk-proj-DEMOFAKEFAKEFAKEFAKEFAKEabcd y el proyecto fénix";
const ev = P.evaluate(etext, { dictionary: ["proyecto fénix"] });
ok("evaluate-tipos", ev.map((f) => `${f.source}:${f.type}`).join() === "pii:curp,secret:openai_key,dict:termino_propio",
  ev.map((f) => `${f.source}:${f.type}`).join());

// Censura: marcadores y ningún valor en claro.
const ct = P.censorText(etext, ev);
ok("censura-marcadores", ct === "Mi CURP [CURP_OCULTA], key [API_KEY_OCULTA] y el [TERMINO_OCULTO]", ct);
ok("censura-sin-valores", P.evaluate(ct, { dictionary: ["proyecto fénix"] }).length === 0);

// Escudo en vivo: evaluar 5 000 caracteres con 50 términos debe costar < 10 ms (p95), también con
// textos que ejercitan las reglas nuevas (casos de demo repetidos) y con textos ADVERSARIALES para la
// heurística de direcciones y las asignaciones (muchas palabras con mayúscula, tokens largos sin espacios).
const dict50 = Array.from({ length: 50 }, (_, i) => `confidencial${i}`);
const fill = (s) => s.repeat(Math.ceil(5000 / s.length)).slice(0, 5000);
const BENCH = {
  "prosa con PII": fill("Hola, revisa este texto con datos demo.fake@gmail.com y 55 1234 5678. "),
  "casos de demo": fill(demo.map((c) => c.texto).join("\n")),
  "adversarial: Calle Calle…": fill("Calle Avenida Colonia Col. Alcaldía Municipio de Privada No. Int. Mz. Lt. "),
  "adversarial: Mayúsculas": fill("Palabra Con Mayuscula Inicial Repetida Sin Numero "),
  "adversarial: token largo": "A".repeat(2500) + "_TOKEN_" + "a".repeat(2493),
  "adversarial: base64": fill("-----BEGIN RSA PRIVATE KEY-----\n" + "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=\n".repeat(20)),
  "adversarial: URIs": fill("postgres://u:p@p@p@p@p@p@p@p@p@p@p@p "),
};
for (const [name, t] of Object.entries(BENCH)) {
  const times = [];
  for (let i = 0; i < 300; i++) {
    const t0 = performance.now();
    P.evaluate(t, { dictionary: dict50 });
    times.push(performance.now() - t0);
  }
  times.sort((a, b) => a - b);
  const p95 = times[Math.floor(0.95 * times.length)];
  console.log(`Escudo en vivo · ${name.padEnd(26)} (${t.length} car, 50 términos): p50=${times[150].toFixed(2)}ms p95=${p95.toFixed(2)}ms máx=${times.at(-1).toFixed(2)}ms`);
  ok(`escudo-menos-de-10ms · ${name}`, p95 < 10, `p95=${p95.toFixed(2)}ms`);
}

const total = data.secret_cases.length + data.pii_cases.length + demo.length + extra;
console.log(failures ? `\n✗ ${failures} fallo(s)` : `\n✓ ${total}/${total} casos OK`);
process.exitCode = failures ? 1 : 0;
