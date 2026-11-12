#!/usr/bin/env node
/*
 * CE-2 · falsos positivos de bloqueo sobre los 200 prompts normales (ml/data/ce2_prompts_normales.csv).
 *   node extension/test/ce2_fp.js                         (patterns.js actual)
 *   node extension/test/ce2_fp.js --patterns <ruta.js>    (otra versión, p. ej. la anterior: antes/después)
 *   node extension/test/ce2_fp.js --max 3                 (sale con código 1 si % bloqueados > 3)
 * Aplica las políticas POR DEFECTO de esa versión (las de la 5A si no exporta DEFAULT_POLICIES).
 * Solo imprime ids de caso y tipos, nunca el texto.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const arg = (name) => {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : null;
};
const P = require(path.resolve(arg("--patterns") || path.join(__dirname, "..", "patterns.js")));
const CSV = path.join(__dirname, "..", "..", "ml", "data", "ce2_prompts_normales.csv");

/** CSV mínimo (RFC 4180: comillas dobles, "" escapadas, saltos de línea dentro de comillas). */
function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') q = false;
      else field += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); rows.push(row); row = []; field = "";
    } else field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  const [head, ...body] = rows.filter((r) => r.length > 1);
  return body.map((r) => Object.fromEntries(head.map((h, i) => [h, r[i]])));
}

const policies = P.DEFAULT_POLICIES || Object.fromEntries([
  ...P.SECRET_TYPES.map((t) => [t, "bloquear"]),
  ...P.PII_TYPES.map((t) => [t, "advertir"]),
]);
const RANK = { permitir: 0, advertir: 1, bloquear: 2 };

/*
 * --db <auditor.db>: complemento con el HISTORIAL REAL (columna text_masked, BD en solo lectura).
 * Ojo: es texto ya enmascarado (los secretos y la PII que el backend vio son marcadores <TIPO>), así
 * que mide sobre todo falsos positivos NUEVOS en uso real. Solo imprime conteos agregados por tipo.
 */
const dbPath = arg("--db");
if (dbPath) {
  const { DatabaseSync } = require("node:sqlite");
  const db = new DatabaseSync(dbPath, { readOnly: true });
  const texts = db.prepare("SELECT text_masked FROM prompts WHERE platform != 'test'").all().map((r) => r.text_masked || "");
  db.close();
  let b = 0;
  let w = 0;
  const byType = {};
  for (const t of texts) {
    const fs_ = P.evaluate(t).map((f) => ({ ...f, action: policies[f.type] || "advertir" }));
    const action = fs_.reduce((a, f) => (RANK[f.action] > RANK[a] ? f.action : a), "permitir");
    if (action === "bloquear") b++;
    if (action === "advertir") w++;
    for (const f of fs_) byType[`${f.type}:${f.action}`] = (byType[`${f.type}:${f.action}`] || 0) + 1;
  }
  console.log(`Historial real (text_masked) · ${texts.length} prompts · bloquearía ${b} (${((100 * b) / texts.length).toFixed(1)} %) · advertiría ${w}`);
  console.log(`  por tipo: ${JSON.stringify(byType)}`);
  process.exit(0);
}

const rows = parseCsv(fs.readFileSync(CSV, "utf8"));
let blocked = 0;
let warned = 0;
const detail = [];
for (const r of rows) {
  const fs_ = P.evaluate(r.text).map((f) => ({ ...f, action: policies[f.type] || "advertir" }));
  const action = fs_.reduce((a, f) => (RANK[f.action] > RANK[a] ? f.action : a), "permitir");
  if (action === "bloquear") blocked++;
  if (action === "advertir") warned++;
  if (action !== "permitir") detail.push(`  ${r.id} [${r.origen}] ${action}: ${fs_.map((f) => f.type).join(", ")}`);
}
const pct = (n) => ((100 * n) / rows.length).toFixed(1);
console.log(`CE-2 · ${rows.length} prompts normales · bloqueados ${blocked} (${pct(blocked)} %) · advertidos ${warned} (${pct(warned)} %)`);
if (detail.length) console.log(detail.join("\n"));
if (process.argv.includes("--json")) {
  process.stdout.write(`\n${JSON.stringify({ total: rows.length, bloqueados: blocked, advertidos: warned })}\n`);
}
const max = arg("--max");
if (max != null && (100 * blocked) / rows.length > Number(max)) process.exitCode = 1;
