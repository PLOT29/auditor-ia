/*
 * patterns.js — Centinela · detección LOCAL y SÍNCRONA (Capa 1).
 *
 * - CATÁLOGO de tipos (nombre, categoría, política por defecto, riesgo, marcador): igual que
 *   backend/detectors/catalog.py.
 * - Secretos: mismas regex que backend/detectors/secrets.py.
 * - PII (correo, teléfono MX, CURP, RFC, DNI, NIE y dirección heurística): mismas regex y misma
 *   heurística que backend/detectors/pii_patterns.py.
 * - Diccionario propio: términos confidenciales del usuario (solo en chrome.storage.local).
 * - Máscaras iguales a backend/masking.py y marcadores de censura ([CORREO_OCULTO]…).
 * Paridad verificada con:
 *   node extension/test/patterns.test.js
 *   python -m pytest backend/tests/test_patterns_parity.py
 *
 * Semántica ASCII explícita en ambos lados (Python con re.ASCII; aquí sin la bandera `u`). Las
 * letras acentuadas se escriben literalmente en las clases y NUNCA se usa la bandera `i` con ellas
 * (en JS `i` pliega Á/á y en Python con re.ASCII no). Índices start/end en unidades UTF-16.
 *
 * PRIVACIDAD: los valores en claro solo existen dentro de estas funciones y en las llamadas
 * que los piden explícitamente (censura local). Hacia afuera viajan {type, masked}. Nunca
 * hagas console.log del texto ni de los términos del diccionario.
 *
 * Se carga en content.js (window), en background.js (importScripts), en popup.html y en Node (tests).
 */
(function (root) {
  "use strict";

  const STARS = "****";

  // ------------------------------------------------------------ catálogo
  // categoria: secreto | dato_personal | termino_propio · politica: default del popup
  // riesgo: el que asigna el backend (risk.py) · local: false = solo backend (Presidio).
  // Orden = orden en el popup.
  const CATALOG = {
    private_key: { nombre: "Clave privada", art: "una clave privada", categoria: "secreto", politica: "bloquear", riesgo: "alto", marcador: "[CLAVE_PRIVADA_OCULTA]" },
    openai_key: { nombre: "API key de OpenAI", art: "una API key", categoria: "secreto", politica: "bloquear", riesgo: "alto", marcador: "[API_KEY_OCULTA]" },
    anthropic_key: { nombre: "API key de Anthropic", art: "una API key", categoria: "secreto", politica: "bloquear", riesgo: "alto", marcador: "[API_KEY_OCULTA]" },
    google_api_key: { nombre: "API key de Google", art: "una API key", categoria: "secreto", politica: "bloquear", riesgo: "alto", marcador: "[API_KEY_OCULTA]" },
    stripe_key: { nombre: "Clave secreta de Stripe", art: "una clave de Stripe", categoria: "secreto", politica: "bloquear", riesgo: "alto", marcador: "[STRIPE_KEY_OCULTA]" },
    stripe_webhook_secret: { nombre: "Secreto de webhook de Stripe", art: "un secreto de webhook", categoria: "secreto", politica: "bloquear", riesgo: "alto", marcador: "[WEBHOOK_SECRET_OCULTO]" },
    aws_access_key: { nombre: "Clave de acceso de AWS", art: "una clave de AWS", categoria: "secreto", politica: "bloquear", riesgo: "alto", marcador: "[CLAVE_AWS_OCULTA]" },
    aws_secret_key: { nombre: "Clave secreta de AWS", art: "una clave secreta de AWS", categoria: "secreto", politica: "bloquear", riesgo: "alto", marcador: "[CLAVE_AWS_OCULTA]" },
    github_token: { nombre: "Token de GitHub", art: "un token", categoria: "secreto", politica: "bloquear", riesgo: "alto", marcador: "[TOKEN_OCULTO]" },
    jwt: { nombre: "JWT", art: "un token", categoria: "secreto", politica: "bloquear", riesgo: "alto", marcador: "[TOKEN_OCULTO]" },
    bearer_token: { nombre: "Token Bearer", art: "un token Bearer", categoria: "secreto", politica: "bloquear", riesgo: "alto", marcador: "[TOKEN_OCULTO]" },
    uri_credentials: { nombre: "URI con credenciales", art: "una URI con contraseña", categoria: "secreto", politica: "bloquear", riesgo: "alto", marcador: "[URI_CON_CREDENCIALES]" },
    password: { nombre: "Contraseña", art: "una contraseña", categoria: "secreto", politica: "bloquear", riesgo: "alto", marcador: "[CONTRASENA_OCULTA]" },
    secret_assignment: { nombre: "Secreto en configuración", art: "un secreto de configuración", categoria: "secreto", politica: "bloquear", riesgo: "alto", marcador: "[SECRETO_OCULTO]" },
    credit_card: { nombre: "Tarjeta", art: "una tarjeta", categoria: "secreto", politica: "bloquear", riesgo: "alto", marcador: "[TARJETA_OCULTA]" },
    openai_org: { nombre: "ID de organización de OpenAI", art: "un ID de organización", categoria: "secreto", politica: "advertir", riesgo: "medio", marcador: "[ORG_ID_OCULTO]" },
    stripe_publishable_key: { nombre: "Clave pública de Stripe", art: "una clave pública de Stripe", categoria: "secreto", politica: "advertir", riesgo: "medio", marcador: "[STRIPE_PK_OCULTA]" },
    curp: { nombre: "CURP", art: "un CURP", categoria: "dato_personal", politica: "bloquear", riesgo: "alto", marcador: "[CURP_OCULTA]" },
    rfc: { nombre: "RFC", art: "un RFC", categoria: "dato_personal", politica: "bloquear", riesgo: "alto", marcador: "[RFC_OCULTO]" },
    dni: { nombre: "DNI", art: "un DNI", categoria: "dato_personal", politica: "bloquear", riesgo: "alto", marcador: "[DNI_OCULTO]" },
    nie: { nombre: "NIE", art: "un NIE", categoria: "dato_personal", politica: "bloquear", riesgo: "alto", marcador: "[NIE_OCULTO]" },
    address: { nombre: "Dirección", art: "una dirección", categoria: "dato_personal", politica: "bloquear", riesgo: "alto", marcador: "[DIRECCIÓN_OCULTA]" },
    email: { nombre: "Correo", art: "un correo", categoria: "dato_personal", politica: "advertir", riesgo: "medio", marcador: "[CORREO_OCULTO]" },
    phone_mx: { nombre: "Teléfono", art: "un teléfono", categoria: "dato_personal", politica: "advertir", riesgo: "medio", marcador: "[TELEFONO_OCULTO]" },
    person: { nombre: "Nombre", art: "un nombre", categoria: "dato_personal", politica: "advertir", riesgo: "medio", marcador: "[NOMBRE_OCULTO]", local: false },
    termino_propio: { nombre: "Término propio", art: "un término propio", categoria: "termino_propio", politica: "advertir", riesgo: "medio", marcador: "[TERMINO_OCULTO]" },
  };
  const CATEGORY_NAMES = { secreto: "secreto", dato_personal: "dato personal", termino_propio: "término propio", inyeccion: "inyección" };
  const info = (type) => CATALOG[type] || null;
  const typeName = (type) => (CATALOG[type] ? CATALOG[type].nombre : type);
  const riskOf = (type) => (CATALOG[type] ? CATALOG[type].riesgo : "medio");
  const categoryOf = (type) => (CATALOG[type] ? CATALOG[type].categoria : "dato_personal");
  const LOCAL_TYPES = Object.keys(CATALOG).filter((t) => CATALOG[t].local !== false);
  const DEFAULT_POLICIES = Object.fromEntries(LOCAL_TYPES.map((t) => [t, CATALOG[t].politica]));

  /** Etiqueta para el modal/escudo. DNI/NIE con letra de control incorrecta lo dicen. */
  function findingLabel(f) {
    if ((f.type === "dni" || f.type === "nie") && f.valido === false) {
      return `formato de ${typeName(f.type)} (letra de control no válida)`;
    }
    return typeName(f.type);
  }

  // ------------------------------------------------------------ validadores
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

  // Valores de relleno que NO son secretos (igual que secrets.is_placeholder).
  const PLACEHOLDER_WORDS = new Set([
    "changeme", "change_me", "change-me", "changeit", "example", "ejemplo", "placeholder", "redacted",
    "password", "contraseña", "secret", "secreto", "null", "none", "undefined", "dummy",
  ]);
  function isPlaceholder(value) {
    const v = value.toLowerCase();
    if (PLACEHOLDER_WORDS.has(v)) return true;
    if (/^[<[{(][\s\S]*[>\]})]$/.test(value)) return true; // <token>, [token], {token}
    if (/^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$/.test(value)) return true; // $VAR, ${VAR}
    if (/^%\([\s\S]*\)s$/.test(value)) return true; // %(var)s
    if (/^[xX*.#_-]+$/.test(value)) return true; // xxx, ***, ...
    if (v.includes("xxxx") || v.includes("****")) return true;
    if (v.includes("...") || v.includes("…")) return true; // sk_live_..., abc…
    if (/^(?:your|tu)[_-]/.test(v)) return true; // your_api_key, tu_token
    if (/^([\s\S])\1+$/.test(value)) return true; // aaaaaaaa
    return false;
  }
  const notPlaceholder = (v) => !isPlaceholder(v);

  // Contraseña "password: X": además del relleno, descarta código: llamadas (getpass()), variables
  // snake_case o camelCase (user_password_hash, newPassword) y atributos (self.password).
  const DOTTED = /^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+$/;
  const passwordOk = (v) =>
    !isPlaceholder(v) && !v.includes("(") && !DOTTED.test(v) &&
    !/^[a-z]+(?:_[a-z]+)+$/.test(v) && !/^[a-z]+(?:[A-Z][a-z]+)+$/.test(v);

  // Asignación genérica (.env/YAML/JSON/código): descarta relleno, variables y expresiones.
  function assignmentOk(v) {
    if (isPlaceholder(v)) return false;
    if (/^[A-Za-z_]+$/.test(v)) return false; // palabra o variable sin dígitos (user_password, authToken)
    if (/^[0-9]+$/.test(v)) return false; // número (TOKEN_TTL=86400000)
    if (DOTTED.test(v)) return false; // process.env.X
    return true;
  }

  // Letra de control de DNI/NIE (módulo 23).
  const DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE";
  function dniValid(value) {
    const s = value.replace(/[ -]/g, "").toUpperCase();
    let num = s.slice(0, -1);
    const first = "XYZ".indexOf(num[0]);
    if (first >= 0) num = String(first) + num.slice(1);
    return DNI_LETTERS[Number(num) % 23] === s.slice(-1);
  }

  // ------------------------------------------------------------ secretos
  // Orden = prioridad (igual que secrets.PATTERNS). `group` = grupo con el valor (flag d).
  const VAL = "[^ \\t\\n\\r\\f\\v\"'`,;<>(){}\\[\\]]"; // carácter de un valor de asignación
  const SECRET_WORD =
    "(?:SECRET|TOKEN|PASSWORD|PASSWD|PWD|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|CLIENT[_-]?SECRET|CONNECTION[_-]?STRING)";
  const URI_SCHEMES = "(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\\+srv)?|rediss?|amqps?|mssql|ftps?|smtps?|https?)";

  const PATTERNS = [
    {
      // Bloque completo hasta su END (multilínea, con sangría o dentro de ```). Sin END:
      // el encabezado y las líneas base64 que lo siguen.
      type: "private_key",
      re: /-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----(?:[\s\S]*?-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|(?:[ \t]*\r?\n[ \t]*[A-Za-z0-9+/=]{8,}[ \t]*(?![^\r\n]))*)/g,
    },
    { type: "anthropic_key", re: /\bsk-ant-[A-Za-z0-9_\-]{20,}/g },
    { type: "openai_key", re: /\bsk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}/g },
    { type: "stripe_key", re: /\b[rs]k_(?:live|test)_[A-Za-z0-9]{10,}/g },
    { type: "stripe_webhook_secret", re: /\bwhsec_[A-Za-z0-9+/=]{16,}/g },
    { type: "aws_access_key", re: /\b(?:AKIA|ASIA)[0-9A-Z]{16,}\b/g },
    {
      // Por contexto: AWS_SECRET_ACCESS_KEY / aws_secret_access_key / SecretAccessKey, con =, : o comillas.
      type: "aws_secret_key",
      re: /(?:aws_?secret_?access_?key|aws_?secret_?key|secret_?access_?key)["']?[ \t]*(?::=|=>|[:=])[ \t]*["']?([A-Za-z0-9/+=]{16,})/dgi,
      group: 1,
      validator: notPlaceholder,
    },
    { type: "github_token", re: /\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b/g },
    { type: "google_api_key", re: /\bAIza[0-9A-Za-z_\-]{35}(?![0-9A-Za-z_\-])/g },
    { type: "jwt", re: /\beyJ[A-Za-z0-9_\-]{5,}\.eyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}/g },
    {
      // esquema://usuario:contraseña@host — la contraseña llega hasta la ÚLTIMA @ antes del host
      // (puede contener @ o !) y tolera URIs mal formadas. Solo se enmascara la contraseña.
      type: "uri_credentials",
      re: new RegExp(`\\b${URI_SCHEMES}://[^ \\t\\n\\r\\f\\v:/@"'<>\`]*:([^ \\t\\n\\r\\f\\v"'<>\`/]+)@`, "dg"),
      group: 1,
      validator: notPlaceholder,
    },
    {
      // "Authorization: Bearer <token>" o "Bearer <token>" (código, cURL, JSON, headers). Solo el token.
      type: "bearer_token",
      re: /\b(?:[Bb]earer|BEARER)[ \t]+([A-Za-z0-9\-._~+/]{20,}=*)/dg,
      group: 1,
      validator: notPlaceholder,
    },
    {
      // \s ASCII explícito ([ \t\n\r\f\v]) para igualar re.ASCII de Python.
      type: "password",
      re: /\b(?:password|passwd|pwd|contrase[ñÑnN]a)\b[ \t\n\r\f\v]*[:=][ \t\n\r\f\v]*["']?([^ \t\n\r\f\v"',;]{4,})/dgi,
      group: 1,
      validator: passwordOk,
    },
    {
      // NOMBRE_CON_SECRET=valor · "client_secret": "valor" · dbPassword: valor (8+ caracteres).
      type: "secret_assignment",
      re: new RegExp(
        `(?<![A-Za-z0-9_.-])["']?[A-Za-z0-9_.-]*?${SECRET_WORD}(?:[_.-][A-Za-z0-9]+)*["']?[ \\t]*(?::=|=>|[:=])[ \\t]*["']?(${VAL}{8,})(?!${VAL}|[(\\[])`,
        "dgi"
      ),
      group: 1,
      validator: assignmentOk,
    },
    // (?<!\d[.,]) — no es la parte fraccionaria de un decimal (0.4111..., 3,1415...).
    { type: "credit_card", re: /(?<![\d-])(?<!\d[.,])\d(?:[ -]?\d){12,18}(?![\d-])/g, validator: cardOk },
    // Identificadores que solo ADVIERTEN.
    { type: "openai_org", re: /\borg-[A-Za-z0-9]{20,}\b/g },
    { type: "stripe_publishable_key", re: /\bpk_(?:live|test)_[A-Za-z0-9]{10,}/g },
  ];

  // ---------------------------------------------------------------- PII
  // MISMAS expresiones que backend/detectors/pii_patterns.py (re.ASCII | IGNORECASE).
  const STATES =
    "AS|BC|BS|CC|CL|CM|CS|CH|DF|DG|GT|GR|HG|JC|MC|MN|MS|NT|NL|OC|PL|QT|QR|SP|SL|SR|TC|TS|TL|VZ|YN|ZS|NE";
  const DATE = "[0-9]{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01])";
  const WORD = "A-Za-z0-9_";
  const LETTER_ES = "ÁÉÍÓÚÜÑáéíóúüñ";
  const SP = "[ \\t\\n\\r\\f\\v.\\-]";
  const DNI_CLASS = "[TRWAGMYFPDXBNJZSQVHLCKEtrwagmyfpdxbnjzsqvhlcke]";

  function phoneOk(value) {
    let digits = value.replace(/[^0-9]/g, "");
    if (value.startsWith("+") || digits.length > 10) {
      if (digits.startsWith("52")) digits = digits.slice(2);
      if (digits.length === 11 && digits.startsWith("1")) digits = digits.slice(1);
    }
    return digits.length === 10;
  }

  // --- Dirección de vivienda: heurística por señales (igual que pii_patterns.address_spans) ---
  const LET = `A-Za-z${LETTER_ES}`;
  const NB = `(?<![${LET}0-9_])`; // no precedido por letra/dígito
  const NA = `(?![${LET}0-9_])`; // no seguido por letra/dígito
  const UPW = `[A-ZÁÉÍÓÚÜÑ0-9][${LET}0-9]*\\.?`; // palabra con mayúscula inicial (o número)
  const NAME = `${UPW}(?:[ \\t]+(?:(?:de|del|la|las|los|el|y)[ \\t]+)?${UPW}){0,4}`;
  const ADDRESS_SIGNALS = [
    ["via", `${NB}(?:[Cc]alle|C\\.|[Aa]v\\.?|[Aa]venida|[Bb]lvd\\.?|[Bb]oulevard|[Cc]alz\\.?|[Cc]alzada|[Pp]rivada|[Pp]riv\\.|[Cc]errada|[Aa]ndador|[Cc]amino|[Cc]arretera)[ \\t]+${NAME}[ \\t]*,?[ \\t]*(?:(?:[Nn]o\\.|[Nn]úm\\.?|#)[ \\t]*)?[0-9]{1,5}(?:[ \\t]?[A-Za-z])?${NA}`],
    ["numero", `(?:${NB}[Nn]o\\.|${NB}[Nn]úm\\.?|#)[ \\t]*[0-9]{1,5}${NA}`],
    ["interior", `${NB}(?:[Ii]nt\\.|[Ee]xt\\.|[Ii]nterior|[Dd]epto\\.?|[Dd]pto\\.?|[Dd]epartamento)[ \\t]*(?:[0-9][A-Za-z0-9-]{0,5}|[A-Z])${NA}`],
    ["colonia", `${NB}(?:[Cc]ol\\.|[Cc]olonia|[Ff]racc\\.?|[Ff]raccionamiento|[Uu]nidad [Hh]abitacional|U\\.[ ]?H\\.)[ \\t]+${NAME}`],
    ["cp", `${NB}(?:C\\.[ ]?P\\.?|CP|[Cc]ódigo [Pp]ostal)[ \\t]*:?[ \\t]*[0-9]{5}${NA}`],
    ["municipio", `${NB}(?:[Aa]lcald[íi]a|[Dd]elegaci[óo]n|[Mm]unicipio(?:[ \\t]+de)?)[ \\t]+${NAME}`],
    ["manzana", `${NB}(?:[Mm]z\\.?|[Mm]za\\.?|[Mm]anzana|[Ll]t\\.?|[Ll]ote)[ \\t]*[0-9]{1,4}${NA}`],
  ].map(([kind, src]) => ({ kind, re: new RegExp(src, "g") }));
  const ADDRESS_MAX_GAP = 40;
  // "#12" o "lote 7" solos son ambiguos: un grupo necesita al menos una señal fuerte.
  const ADDRESS_WEAK = new Set(["numero", "manzana"]);

  /**
   * Spans de direcciones: agrupa señales cercanas (≤ 40 caracteres, sin línea en blanco) y
   * acepta el grupo con 2 o más señales de tipo distinto (C.P. + cualquier otra incluido), de las
   * que al menos una es fuerte (vía, interior, colonia, C.P., alcaldía). Una sola señal
   * ("Av. Reforma 222", "colonia Roma") o solo débiles ("issue #12 y lote 7") no bastan.
   */
  function addressSpans(text) {
    const sig = [];
    ADDRESS_SIGNALS.forEach(({ kind, re }, k) => {
      re.lastIndex = 0;
      let m;
      while ((m = re.exec(text)) !== null) {
        if (m[0].length === 0) {
          re.lastIndex++;
          continue;
        }
        sig.push({ kind, k, start: m.index, end: m.index + m[0].length });
      }
    });
    sig.sort((a, b) => a.start - b.start || b.end - a.end || a.k - b.k);
    const kept = [];
    for (const s of sig) if (!kept.length || s.start >= kept[kept.length - 1].end) kept.push(s);
    const out = [];
    let cur = null;
    const flush = () => {
      if (cur && cur.kinds.size >= 2 && [...cur.kinds].some((k) => !ADDRESS_WEAK.has(k))) {
        out.push({ start: cur.start, end: cur.end });
      }
    };
    for (const s of kept) {
      const gap = cur ? text.slice(cur.end, s.start) : "";
      if (cur && s.start - cur.end <= ADDRESS_MAX_GAP && !/\n[ \t\r]*\n/.test(gap)) {
        cur.end = s.end;
        cur.kinds.add(s.kind);
      } else {
        flush();
        cur = { start: s.start, end: s.end, kinds: new Set([s.kind]) };
      }
    }
    flush();
    return out;
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
    { type: "dni", re: new RegExp(`(?<![${WORD}])[0-9]{8}[ \\-]?${DNI_CLASS}(?![${WORD}])`, "g"), annotate: (v) => ({ valido: dniValid(v) }) },
    { type: "nie", re: new RegExp(`(?<![${WORD}])[XYZxyz][ \\-]?[0-9]{7}[ \\-]?${DNI_CLASS}(?![${WORD}])`, "g"), annotate: (v) => ({ valido: dniValid(v) }) },
    { type: "address", scan: addressSpans },
    {
      type: "phone_mx",
      re: new RegExp(`(?<![${WORD}+])(?:\\+?52${SP}?)?(?:1${SP}?)?(?:\\([0-9]{2,3}\\)|[0-9]{2,3})${SP}?[0-9]{3,4}${SP}?[0-9]{4}(?![0-9])`, "g"),
      validator: phoneOk,
    },
  ];
  const SECRET_TYPES = PATTERNS.map((p) => p.type);
  const PII_TYPES = PII_RULES.map((p) => p.type);

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
  const FIXED_PREFIX = {
    anthropic_key: "sk-ant-", openai_key: "sk-", google_api_key: "AIza", jwt: "eyJ",
    stripe_webhook_secret: "whsec_", openai_org: "org-",
  };
  const FULLY_HIDDEN = new Set(["password", "private_key", "person", "address", "uri_credentials", "secret_assignment", DICT_TYPE]);

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
    else if (type === "stripe_key" || type === "stripe_publishable_key") prefix = value.slice(0, value.indexOf("_", 3) + 1);
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
  const censorMarker = (type) => (CATALOG[type] ? CATALOG[type].marcador : "[DATO_OCULTO]");

  /** Texto con cada hallazgo reemplazado por su marcador de censura. */
  function censorText(text, findings) {
    let out = text;
    for (const f of [...findings].sort((a, b) => b.start - a.start)) {
      out = out.slice(0, f.start) + censorMarker(f.type) + out.slice(f.end);
    }
    return out;
  }

  // ----------------------------------------------------------- motor
  function* candidates(rule, text) {
    if (rule.scan) {
      for (const s of rule.scan(text)) yield [s.start, s.end];
      return;
    }
    rule.re.lastIndex = 0;
    let m;
    while ((m = rule.re.exec(text)) !== null) {
      if (m[0].length === 0) {
        rule.re.lastIndex++;
        continue;
      }
      yield rule.group ? m.indices[rule.group] : [m.index, m.index + m[0].length];
    }
  }

  function run(rules, text, taken, group) {
    const found = [];
    for (const rule of rules) {
      for (const [start, end] of candidates(rule, text)) {
        const value = text.slice(start, end);
        if (rule.validator && !rule.validator(value)) continue;
        if (taken.some((t) => t.start < end && start < t.end)) continue;
        const f = { type: rule.type, masked: maskValue(rule.type, value), start, end, source: group };
        if (rule.annotate) Object.assign(f, rule.annotate(value));
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

  const strip = ({ type, masked, start, end, valido }) =>
    valido === undefined ? { type, masked, start, end } : { type, masked, start, end, valido };

  /** Secretos (y los identificadores que advierten) en `text`. @returns {{type, masked, start, end}[]} */
  function detectSecrets(text) {
    if (!text) return [];
    return run(PATTERNS, text, [], "secret").map(strip);
  }

  /** Secretos + PII por regex (sin solapes). Igual que secrets + pii.detect_regex del backend. */
  function detectSensitive(text) {
    if (!text) return [];
    const taken = [];
    const secrets = run(PATTERNS, text, taken, "secret");
    const pii = run(PII_RULES, text, taken, "pii");
    return [...secrets, ...pii].sort((a, b) => a.start - b.start).map(strip);
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

  /** Acción más fuerte de una lista de hallazgos con `action`. */
  const RANK = { permitir: 0, advertir: 1, bloquear: 2 };
  const maxAction = (findings) => findings.reduce((a, f) => (RANK[f.action] > RANK[a] ? f.action : a), "permitir");

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
    dniValid,
    isPlaceholder,
    addressSpans,
    maxAction,
    typeName,
    findingLabel,
    riskOf,
    categoryOf,
    CATALOG,
    CATEGORY_NAMES,
    DEFAULT_POLICIES,
    LOCAL_TYPES,
    SECRET_TYPES,
    PII_TYPES,
    DICT_TYPE,
    DICT_MAX,
    PATTERN_TYPES: SECRET_TYPES,
    info,
  };
  root.AuditorPatterns = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
