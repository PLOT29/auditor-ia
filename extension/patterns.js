/*
 * patterns.js — Capa 1 (DLP local, SÍNCRONO).
 *
 * Mismo set de regex que backend/detectors/secrets.py y misma máscara que
 * backend/masking.py. Si cambias uno, cambia el otro. La paridad se verifica con
 *   node extension/test/patterns.test.js
 *   python -m pytest backend/tests/test_patterns_parity.py
 * usando los casos de extension/test/secret_cases.json.
 *
 * Semántica: todas las regex son ASCII (`\b`, `\d`, `\s`), igual que el backend
 * con re.ASCII. Los índices start/end son de JavaScript (unidades UTF-16).
 *
 * PRIVACIDAD: los valores en claro solo existen dentro de estas funciones;
 * hacia afuera salen {type, masked, start, end}. Nunca hagas console.log del texto.
 *
 * Se carga en content.js (window) y en background.js (importScripts) y en Node.
 */
(function (root) {
  "use strict";

  const STARS = "****";

  // ------------------------------------------------------------ secretos
  function luhnOk(number) {
    const digits = number.replace(/\D/g, "");
    if (digits.length < 13 || digits.length > 19) return false;
    let total = 0;
    for (let i = 0; i < digits.length; i++) {
      let d = digits.charCodeAt(digits.length - 1 - i) - 48;
      if (i % 2 === 1) {
        d *= 2;
        if (d > 9) d -= 9;
      }
      total += d;
    }
    return total % 10 === 0;
  }

  function cardOk(value) {
    const digits = value.replace(/\D/g, "");
    if (new Set(digits).size === 1) return false; // 0000..., 1111... pasan Luhn
    return luhnOk(digits);
  }

  // Orden = prioridad (igual que secrets.PATTERNS). `group` = grupo con el valor.
  // Flag `d` donde hay grupo: da los índices exactos del grupo.
  const PATTERNS = [
    {
      type: "private_key",
      re: /-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----(?:[\s\S]*?-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----)?/g,
    },
    { type: "anthropic_key", re: /\bsk-ant-[A-Za-z0-9_\-]{20,}/g },
    { type: "openai_key", re: /\bsk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}/g },
    { type: "aws_access_key", re: /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g },
    { type: "github_token", re: /\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b/g },
    { type: "google_api_key", re: /\bAIza[0-9A-Za-z_\-]{35}(?![0-9A-Za-z_\-])/g },
    { type: "jwt", re: /\beyJ[A-Za-z0-9_\-]{5,}\.eyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}/g },
    {
      // \s ASCII explícito ([ \t\n\r\f\v]) para igualar re.ASCII de Python.
      type: "password",
      re: /\b(?:password|passwd|pwd|contrase[ñÑnN]a)\b[ \t\n\r\f\v]*[:=][ \t\n\r\f\v]*["']?([^ \t\n\r\f\v"',;]{4,})/dgi,
      group: 1,
    },
    // (?<!\d[.,]) — no es la parte fraccionaria de un decimal (0.4111..., 3,1415...).
    { type: "credit_card", re: /(?<![\d-])(?<!\d[.,])\d(?:[ -]?\d){12,18}(?![\d-])/g, validator: cardOk },
  ];

  // ---------------------------------------------------------------- PII
  // Solo para ENMASCARAR la cola offline (background.js) — la PII no bloquea.
  // Aproximación de backend/detectors/pii.py (el backend sigue siendo la referencia).
  const STATES =
    "AS|BC|BS|CC|CL|CM|CS|CH|DF|DG|GT|GR|HG|JC|MC|MN|MS|NT|NL|OC|PL|QT|QR|SP|SL|SR|TC|TS|TL|VZ|YN|ZS|NE";
  const DATE = "\\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\\d|3[01])";
  const PII_RULES = [
    { type: "email", re: /(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b/g },
    {
      type: "curp",
      re: new RegExp(`\\b[A-Z][AEIOUX][A-Z]{2}${DATE}[HM](?:${STATES})[B-DF-HJ-NP-TV-Z]{3}[A-Z\\d]\\d\\b`, "gi"),
    },
    { type: "rfc", re: new RegExp(`(?<![\\wÑñ&])[A-ZÑ&]{3,4}${DATE}[A-Z\\d]{2}[A\\d]\\b`, "gi") },
    {
      type: "phone_mx",
      re: /(?<![\w+])(?:\+?52[\s.-]?)?(?:1[\s.-]?)?(?:\(\d{2,3}\)|\d{2,3})[\s.-]?\d{3,4}[\s.-]?\d{4}(?!\d)/g,
      validator: phoneOk,
    },
  ];

  function phoneOk(value) {
    let digits = value.replace(/\D/g, "");
    if (value.trimStart().startsWith("+") || digits.length > 10) {
      if (digits.startsWith("52")) digits = digits.slice(2);
      if (digits.length === 11 && digits.startsWith("1")) digits = digits.slice(1);
    }
    return digits.length === 10;
  }

  // ------------------------------------------------------------ máscara
  const FIXED_PREFIX = { anthropic_key: "sk-ant-", openai_key: "sk-", google_api_key: "AIza", jwt: "eyJ" };
  const FULLY_HIDDEN = new Set(["password", "private_key"]);

  /** Igual que masking.mask_value() del backend. */
  function maskValue(type, value) {
    if (FULLY_HIDDEN.has(type)) return STARS;
    if (type === "email") {
      const at = value.indexOf("@");
      return value.slice(0, 1) + STARS + value.slice(at);
    }
    if (type === "credit_card" || type === "phone_mx") {
      return STARS + value.replace(/\D/g, "").slice(-4);
    }
    let prefix;
    if (type === "aws_access_key") prefix = value.slice(0, 4);
    else if (type === "github_token") prefix = value.startsWith("github_pat_") ? "github_pat_" : value.slice(0, 4);
    else prefix = FIXED_PREFIX[type] || "";

    const tailLen = value.length >= 12 ? 4 : 2;
    if (value.length <= prefix.length + tailLen) return prefix + STARS;
    return prefix + STARS + value.slice(-tailLen);
  }

  /** Igual que masking.mask_text(): reemplaza cada hallazgo por <TIPO>. */
  function maskText(text, findings) {
    let out = text;
    for (const f of [...findings].sort((a, b) => b.start - a.start)) {
      out = out.slice(0, f.start) + `<${f.type.toUpperCase()}>` + out.slice(f.end);
    }
    return out;
  }

  // ----------------------------------------------------------- motor
  function run(rules, text, taken, source) {
    const found = [];
    for (const rule of rules) {
      rule.re.lastIndex = 0;
      let m;
      while ((m = rule.re.exec(text)) !== null) {
        if (m[0].length === 0) {
          rule.re.lastIndex++;
          continue;
        }
        const [start, end] = rule.group ? m.indices[rule.group] : [m.index, m.index + m[0].length];
        const value = text.slice(start, end);
        if (rule.validator && !rule.validator(value)) continue;
        if (taken.some((t) => t.start < end && start < t.end)) continue;
        const f = { type: rule.type, masked: maskValue(rule.type, value), start, end, source };
        taken.push(f);
        found.push(f);
      }
    }
    return found.sort((a, b) => a.start - b.start);
  }

  /**
   * Secretos en `text`, síncrono.
   * @returns {{type: string, masked: string, start: number, end: number}[]}
   */
  function detectSecrets(text) {
    if (!text) return [];
    return run(PATTERNS, text, [], "secret").map(({ type, masked, start, end }) => ({ type, masked, start, end }));
  }

  /** Secretos + PII (sin solapes), con `source`. Para enmascarar la cola offline. */
  function detectSensitive(text) {
    if (!text) return [];
    const taken = [];
    const secrets = run(PATTERNS, text, taken, "secret");
    const pii = run(PII_RULES, text, taken, "pii");
    return [...secrets, ...pii].sort((a, b) => a.start - b.start);
  }

  const api = {
    detectSecrets,
    detectSensitive,
    maskValue,
    maskText,
    luhnOk,
    PATTERN_TYPES: PATTERNS.map((p) => p.type),
  };
  root.AuditorPatterns = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
