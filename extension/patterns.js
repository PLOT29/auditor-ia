/*
 * patterns.js — Capa 1 (DLP local, síncrono).
 *
 * Mismo set de regex que backend/detectors/secrets.py y misma máscara que
 * backend/masking.py. Si cambias uno, cambia el otro: el test
 * backend/tests/test_patterns_parity.py compara ambos con Node.
 *
 * Fase 2: solo se usa para llenar `client_findings`.
 * Fase 3: content.js lo usará para bloquear el envío.
 *
 * PRIVACIDAD: los valores en claro solo existen dentro de detectSecrets();
 * hacia afuera solo salen {type, masked}. Nunca hagas console.log del texto.
 */
(function (root) {
  "use strict";

  const STARS = "****";

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
    if (new Set(digits).size === 1) return false; // 0000..., 1111...
    return luhnOk(digits);
  }

  // Orden = prioridad (igual que en secrets.py). `group` = grupo con el valor.
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
      type: "password",
      re: /\b(?:password|passwd|pwd|contrase(?:ñ|n)a)\b\s*[:=]\s*["']?([^\s"',;]{4,})/gi,
      group: 1,
    },
    { type: "credit_card", re: /(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])/g, validator: cardOk },
  ];

  const FIXED_PREFIX = {
    anthropic_key: "sk-ant-",
    openai_key: "sk-",
    google_api_key: "AIza",
    jwt: "eyJ",
  };
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

  /**
   * Busca secretos en `text`.
   * @returns {{type: string, masked: string}[]} ordenados por posición.
   */
  function detectSecrets(text) {
    if (!text) return [];
    const taken = []; // [start, end]
    const found = [];
    for (const p of PATTERNS) {
      p.re.lastIndex = 0;
      let m;
      while ((m = p.re.exec(text)) !== null) {
        if (m[0].length === 0) {
          p.re.lastIndex++;
          continue;
        }
        const value = p.group ? m[p.group] : m[0];
        const start = p.group ? m.index + m[0].lastIndexOf(value) : m.index;
        const end = start + value.length;
        if (p.validator && !p.validator(value)) continue;
        if (taken.some(([s, e]) => s < end && start < e)) continue;
        taken.push([start, end]);
        found.push({ start, type: p.type, masked: maskValue(p.type, value) });
      }
    }
    found.sort((a, b) => a.start - b.start);
    return found.map(({ type, masked }) => ({ type, masked }));
  }

  const api = { detectSecrets, maskValue, luhnOk, PATTERN_TYPES: PATTERNS.map((p) => p.type) };
  root.AuditorPatterns = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
