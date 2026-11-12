/*
 * patterns.js — Centinela · detección LOCAL y SÍNCRONA (Capa 1).
 *
 * - Secretos: mismas regex que backend/detectors/secrets.py.
 * - PII (correo, teléfono MX, CURP, RFC): mismas regex que backend/detectors/pii_patterns.py.
 * - Diccionario propio: términos confidenciales del usuario (solo en chrome.storage.local).
 * - Máscaras iguales a backend/masking.py y marcadores de censura ([CORREO_OCULTO]…).
 * Paridad verificada con:
 *   node extension/test/patterns.test.js
 *   python -m pytest backend/tests/test_patterns_parity.py
 *
 * Semántica ASCII explícita en ambos lados (Python con re.ASCII). Índices start/end en
 * unidades UTF-16 (JavaScript).
 *
 * PRIVACIDAD: los valores en claro solo existen dentro de estas funciones y en las llamadas
 * que los piden explícitamente (censura local). Hacia afuera viajan {type, masked}. Nunca
 * hagas console.log del texto ni de los términos del diccionario.
 *
 * Se carga en content.js (window), en background.js (importScripts) y en Node (tests).
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

  // Orden = prioridad (igual que secrets.PATTERNS). `group` = grupo con el valor (flag d).
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
  const SECRET_TYPES = new Set(PATTERNS.map((p) => p.type));

  // ---------------------------------------------------------------- PII
  // MISMAS expresiones que backend/detectors/pii_patterns.py (re.ASCII | IGNORECASE).
  const STATES =
    "AS|BC|BS|CC|CL|CM|CS|CH|DF|DG|GT|GR|HG|JC|MC|MN|MS|NT|NL|OC|PL|QT|QR|SP|SL|SR|TC|TS|TL|VZ|YN|ZS|NE";
  const DATE = "[0-9]{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01])";
  const WORD = "A-Za-z0-9_";
  const LETTER_ES = "ÁÉÍÓÚÜÑáéíóúüñ";
  const SP = "[ \\t\\n\\r\\f\\v.\\-]";

  function phoneOk(value) {
    let digits = value.replace(/[^0-9]/g, "");
    if (value.startsWith("+") || digits.length > 10) {
      if (digits.startsWith("52")) digits = digits.slice(2);
      if (digits.length === 11 && digits.startsWith("1")) digits = digits.slice(1);
    }
    return digits.length === 10;
  }

  const PII_RULES = [
    {
      type: "email",
      re: new RegExp(`(?<![${WORD}.+\\-])[A-Za-z0-9._%+\\-]+@[A-Za-z0-9\\-]+(?:\\.[A-Za-z0-9\\-]+)*\\.[A-Za-z]{2,}(?![${WORD}])`, "g"),
    },
    {
      type: "curp",
      re: new RegExp(`(?<![${WORD}])[A-Z][AEIOUX][A-Z]{2}${DATE}[HM](?:${STATES})[B-DF-HJ-NP-TV-Z]{3}[A-Z0-9][0-9](?![${WORD}])`, "gi"),
    },
    { type: "rfc", re: new RegExp(`(?<![${WORD}${LETTER_ES}&])[A-ZÑñ&]{3,4}${DATE}[A-Z0-9]{2}[A0-9](?![${WORD}])`, "gi") },
    {
      type: "phone_mx",
      re: new RegExp(`(?<![${WORD}+])(?:\\+?52${SP}?)?(?:1${SP}?)?(?:\\([0-9]{2,3}\\)|[0-9]{2,3})${SP}?[0-9]{3,4}${SP}?[0-9]{4}(?![0-9])`, "g"),
      validator: phoneOk,
    },
  ];
  const PII_TYPES = new Set(PII_RULES.map((p) => p.type));

  // ------------------------------------------------------- diccionario propio
  const DICT_TYPE = "termino_propio";
  const DICT_MAX = 50;

  /** Pliega un carácter (minúsculas, sin acentos) conservando la longitud del texto. */
  function foldChar(c) {
    const f = c.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
    return f.length === 1 ? f : c.toLowerCase().length === 1 ? c.toLowerCase() : c;
  }

  function fold(text) {
    let out = "";
    for (const c of text) out += c.length === 1 ? foldChar(c) : c; // pares sustitutos: intactos
    return out;
  }

  function escapeRe(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  /** Normaliza la lista del usuario: sin vacíos ni repetidos, máx. 50. */
  function normalizeDictionary(terms) {
    const seen = new Set();
    const out = [];
    for (const raw of terms || []) {
      const t = String(raw).trim().replace(/\s+/g, " ");
      const k = fold(t);
      if (t.length < 2 || seen.has(k)) continue;
      seen.add(k);
      out.push(t);
      if (out.length >= DICT_MAX) break;
    }
    return out;
  }

  const dictCache = { key: null, re: null };

  function dictRegex(terms) {
    const list = normalizeDictionary(terms);
    const key = list.join("\u0000");
    if (dictCache.key !== key) {
      // Más largos primero, para que "proyecto fénix" gane sobre "fénix".
      const alts = list.map(fold).sort((a, b) => b.length - a.length).map(escapeRe);
      dictCache.key = key;
      dictCache.re = alts.length ? new RegExp(`(?<![\\p{L}\\p{N}_])(?:${alts.join("|")})(?![\\p{L}\\p{N}_])`, "gu") : null;
    }
    return dictCache.re;
  }

  // ------------------------------------------------------------ máscara
  const FIXED_PREFIX = { anthropic_key: "sk-ant-", openai_key: "sk-", google_api_key: "AIza", jwt: "eyJ" };
  const FULLY_HIDDEN = new Set(["password", "private_key", "person", "address", DICT_TYPE]);

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

  // Marcadores visibles de "Censurar" (lo que queda escrito en el campo del chat).
  const CENSOR_MARKERS = {
    openai_key: "[API_KEY_OCULTA]",
    anthropic_key: "[API_KEY_OCULTA]",
    google_api_key: "[API_KEY_OCULTA]",
    aws_access_key: "[CLAVE_AWS_OCULTA]",
    github_token: "[TOKEN_OCULTO]",
    jwt: "[TOKEN_OCULTO]",
    private_key: "[CLAVE_PRIVADA_OCULTA]",
    password: "[CONTRASENA_OCULTA]",
    credit_card: "[TARJETA_OCULTA]",
    email: "[CORREO_OCULTO]",
    phone_mx: "[TELEFONO_OCULTO]",
    curp: "[CURP_OCULTA]",
    rfc: "[RFC_OCULTO]",
    [DICT_TYPE]: "[TERMINO_OCULTO]",
  };
  const censorMarker = (type) => CENSOR_MARKERS[type] || "[DATO_OCULTO]";

  /** Texto con cada hallazgo reemplazado por su marcador de censura. */
  function censorText(text, findings) {
    let out = text;
    for (const f of [...findings].sort((a, b) => b.start - a.start)) {
      out = out.slice(0, f.start) + censorMarker(f.type) + out.slice(f.end);
    }
    return out;
  }

  // ----------------------------------------------------------- motor
  function run(rules, text, taken, group) {
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
        const f = { type: rule.type, masked: maskValue(rule.type, value), start, end, source: group };
        taken.push(f);
        found.push(f);
      }
    }
    return found.sort((a, b) => a.start - b.start);
  }

  function runDictionary(text, terms, taken) {
    const re = dictRegex(terms);
    if (!re) return [];
    const folded = fold(text);
    const found = [];
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(folded)) !== null) {
      const start = m.index;
      const end = start + m[0].length;
      if (taken.some((t) => t.start < end && start < t.end)) continue;
      const f = { type: DICT_TYPE, masked: STARS, start, end, source: "dict" }; // nunca el término
      taken.push(f);
      found.push(f);
    }
    return found;
  }

  const strip = ({ type, masked, start, end }) => ({ type, masked, start, end });

  /** Secretos en `text`. @returns {{type, masked, start, end}[]} */
  function detectSecrets(text) {
    if (!text) return [];
    return run(PATTERNS, text, [], "secret").map(strip);
  }

  /** Secretos + PII por regex (sin solapes), con `source`. Igual que secrets + pii.detect_regex. */
  function detectSensitive(text) {
    if (!text) return [];
    const taken = [];
    const secrets = run(PATTERNS, text, taken, "secret");
    const pii = run(PII_RULES, text, taken, "pii");
    return [...secrets, ...pii].sort((a, b) => a.start - b.start);
  }

  /**
   * Evaluación completa para el escudo en vivo y la decisión de envío:
   * secretos → PII → diccionario propio, sin solapes. `source`: secret | pii | dict.
   */
  function evaluate(text, opts = {}) {
    if (!text) return [];
    const taken = [];
    const secrets = run(PATTERNS, text, taken, "secret");
    const pii = run(PII_RULES, text, taken, "pii");
    const dict = runDictionary(text, opts.dictionary, taken);
    return [...secrets, ...pii, ...dict].sort((a, b) => a.start - b.start);
  }

  const api = {
    detectSecrets,
    detectSensitive,
    evaluate,
    maskValue,
    maskText,
    censorText,
    censorMarker,
    normalizeDictionary,
    fold,
    luhnOk,
    SECRET_TYPES: [...SECRET_TYPES],
    PII_TYPES: [...PII_TYPES],
    DICT_TYPE,
    DICT_MAX,
    PATTERN_TYPES: PATTERNS.map((p) => p.type),
  };
  root.AuditorPatterns = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
