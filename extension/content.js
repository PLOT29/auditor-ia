/*
 * content.js — se inyecta en chatgpt.com, claude.ai, gemini.google.com
 * (y en localhost:5500 para test.html, solo en desarrollo).
 *
 * Flujo (SPEC §3):
 *   1. Listeners en FASE DE CAPTURA sobre window (corren antes que los de la página).
 *   2. Lee el texto del textarea / contenteditable ANTES de que la página lo limpie.
 *   3. Capa 1: detectSecrets() local y SÍNCRONO.
 *        └─ shouldBlock() → preventDefault + stopImmediatePropagation + stopPropagation
 *           → modal «Cancelar» / «Enviar de todos modos». El texto queda en el input.
 *   4. Manda el payload a background.js → backend → etiqueta flotante.
 *
 * PRIVACIDAD: el texto y los valores reales de los secretos NUNCA se imprimen en
 * consola ni se guardan. Solo se loguean métricas (ms, nº de hallazgos).
 *
 * ---------------------------------------------------------------------------
 * REENVÍO tras «Enviar de todos modos» (SPEC §11, RNF-06)
 *   Estrategia A: button.click() sobre el botón de enviar (el clicado, o el más
 *                 cercano al editor). Evento isTrusted=false.
 *   Estrategia B: si no hay botón o A no envió, KeyboardEvent keydown Enter
 *                 despachado en el editor (isTrusted=false).
 *   Tras cada intento se verifica que la página haya vaciado el editor (≤ 1.5 s);
 *   solo entonces se registra user_decision="enviado". Si ninguna funciona, se
 *   arma un bypass MANUAL de 10 s para el mismo texto y se pide al usuario que
 *   presione Enviar otra vez (evento real → siempre funciona).
 *
 *   Resultado por plataforma (actualizar tras la prueba manual):
 *     test.html  → A (button.click) ✔ verificado. B ✔ verificado. Con «Ignorar
 *                  eventos no confiables» activo: A y B fallan → bypass manual ✔.
 *     ChatGPT    → A esperado (botón React data-testid="send-button"). PENDIENTE.
 *     Claude     → A esperado (botón React aria-label "Send message"). PENDIENTE.
 *     Gemini     → A esperado (botón Angular aria-label "Send message"). PENDIENTE.
 *   La consola muestra "[auditor] reenviado con <estrategia>" para documentarlo.
 * ---------------------------------------------------------------------------
 */
(() => {
  "use strict";

  if (window.__auditorIaLoaded) return; // evita doble inyección
  window.__auditorIaLoaded = true;

  const P = globalThis.AuditorPatterns;

  // ---------------------------------------------------------------- config
  const DEDUP_MS = 500;
  const LABEL_MS = 5000;
  const MAX_LABELS = 5;
  const BYPASS_MS = 2000; // bypassOnce expira si no se usa
  const MANUAL_BYPASS_MS = 10000; // ventana para que el usuario reintente a mano
  const VERIFY_MS = 1500; // espera a que la página vacíe el editor tras reenviar
  const EDITOR_SELECTOR =
    'textarea, [contenteditable="true"], [contenteditable=""], [contenteditable="plaintext-only"]';
  const BUTTON_SELECTOR = 'button, [role="button"], input[type="submit"]';
  // Se busca en aria-label / title / data-testid / texto del botón (sin clases CSS).
  const SEND_HINT = /send|enviar|submit/i;
  const OWN_HOSTS = "auditor-ia-overlay, auditor-ia-modal";

  const PLATFORM = detectPlatform(location.hostname);

  // Ajustes en MEMORIA: la decisión de bloqueo es síncrona y no puede esperar a
  // storage.get(). Hasta que llega el valor guardado rige el default seguro (ON).
  const DEFAULT_SETTINGS = { monitoring: true, blocking: true };
  const settings = { ...DEFAULT_SETTINGS };
  chrome.storage.local.get(Object.keys(DEFAULT_SETTINGS)).then((s) => Object.assign(settings, s));
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    for (const key of Object.keys(DEFAULT_SETTINGS)) {
      if (key in changes) settings[key] = changes[key].newValue ?? DEFAULT_SETTINGS[key];
    }
  });
  const monitoringOn = () => settings.monitoring !== false;
  const blockingOn = () => monitoringOn() && settings.blocking !== false; // monitoreo OFF ⇒ bloqueo OFF

  function detectPlatform(host) {
    if (host === "chatgpt.com" || host.endsWith(".chatgpt.com")) return "chatgpt";
    if (host === "claude.ai" || host.endsWith(".claude.ai")) return "claude";
    if (host === "gemini.google.com") return "gemini";
    if (host === "localhost" || host === "127.0.0.1") return "test";
    return "desconocida";
  }

  // ---------------------------------------------------------- editores
  /** Devuelve el editor (textarea o raíz contenteditable) que contiene `node`. */
  function editorFrom(node) {
    if (!(node instanceof Element)) return null;
    if (node.tagName === "TEXTAREA") return node;
    if (node.isContentEditable) {
      let host = node;
      while (host.parentElement && host.parentElement.isContentEditable) host = host.parentElement;
      return host;
    }
    return null;
  }

  function isVisible(el) {
    if (!el.isConnected || el.getClientRects().length === 0) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none";
  }

  function readText(editor) {
    const raw = editor.tagName === "TEXTAREA" ? editor.value : editor.innerText;
    return (raw || "").replace(/ /g, " ").trim();
  }

  // Último editor con foco: al hacer clic en "Enviar" el foco pasa al botón.
  let lastEditor = null;
  document.addEventListener(
    "focusin",
    (e) => {
      const ed = editorFrom(e.composedPath()[0]);
      if (ed) lastEditor = ed;
    },
    true
  );

  /** Sube por los ancestros de `start` y devuelve el primer conjunto no vacío. */
  function nearest(start, selector, map) {
    let node = start.parentElement;
    for (let depth = 0; node && depth < 10; depth++, node = node.parentElement) {
      const hits = [...node.querySelectorAll(selector)].map(map).filter((x) => x && isVisible(x));
      if (hits.length) return hits;
    }
    return [];
  }

  /** Editor más cercano al botón; si hay varios, el último con foco. */
  function findEditorNear(button) {
    const editors = nearest(button, EDITOR_SELECTOR, editorFrom);
    if (editors.length) return lastEditor && editors.includes(lastEditor) ? lastEditor : editors[0];
    return lastEditor && isVisible(lastEditor) ? lastEditor : null;
  }

  /**
   * Botón de enviar del MISMO composer que `editor` (para reenviar tras un bloqueo
   * por Enter). Se descarta cualquier botón cuyo editor más cercano sea otro: si
   * no, se podría disparar el envío de un composer distinto.
   */
  function findSendButtonNear(editor) {
    const candidates = nearest(editor, BUTTON_SELECTOR, sendButtonFrom);
    return candidates.find((btn) => nearest(btn, EDITOR_SELECTOR, editorFrom).includes(editor)) || null;
  }

  /** ¿`target` es (o está dentro de) un botón de "Enviar" habilitado? */
  function sendButtonFrom(target) {
    if (!(target instanceof Element)) return null;
    if (target.closest(OWN_HOSTS)) return null;
    const btn = target.closest(BUTTON_SELECTOR);
    if (!btn) return null;
    if (btn.disabled || btn.getAttribute("aria-disabled") === "true") return null;
    const label = [
      btn.getAttribute("aria-label"),
      btn.getAttribute("title"),
      btn.getAttribute("data-testid"),
      btn.value,
      (btn.textContent || "").trim().slice(0, 40),
    ]
      .filter(Boolean)
      .join(" ");
    if (SEND_HINT.test(label)) return btn;
    if (btn.type === "submit" && btn.form && btn.form.querySelector(EDITOR_SELECTOR)) return btn;
    return null;
  }

  // ------------------------------------------------------------ captura
  window.addEventListener("keydown", onKeyDown, true);
  window.addEventListener("click", onClick, true);

  function cancelEvent(e) {
    e.preventDefault();
    e.stopImmediatePropagation();
    e.stopPropagation();
  }

  function onKeyDown(e) {
    if (modal.isOpen()) {
      if (e.key === "Escape") {
        cancelEvent(e);
        modal.cancel();
        return;
      }
      // Mientras el modal está abierto se ignoran nuevos Enter de envío en el editor.
      if (e.key === "Enter" && editorFrom(e.composedPath()[0])) cancelEvent(e);
      return;
    }
    if (!monitoringOn()) return;
    if (e.key !== "Enter" || e.shiftKey || e.altKey) return;
    if (e.isComposing || e.keyCode === 229) return; // composición IME
    const editor = editorFrom(e.composedPath()[0]);
    if (!editor) return;
    intercept(e, editor, null, "enter");
  }

  function onClick(e) {
    if (e.button !== 0) return;
    const btn = sendButtonFrom(e.composedPath()[0]);
    if (!btn) return;
    if (modal.isOpen()) {
      cancelEvent(e); // el backdrop lo cubre todo, pero por si acaso
      return;
    }
    if (!monitoringOn()) return;
    const editor = findEditorNear(btn);
    if (!editor) return;
    intercept(e, editor, btn, "click");
  }

  // ---------------------------------------------------- modo bloqueo
  /** Decisión síncrona: ¿hay que cancelar este envío? */
  function shouldBlock(text, findings = P ? P.detectSecrets(text) : []) {
    return blockingOn() && findings.length > 0;
  }

  /**
   * bypassOnce: deja pasar UN envío con exactamente este texto (el reenvío tras
   * «Enviar de todos modos»). Se consume en el siguiente envío y expira solo.
   */
  let bypass = null; // { text, until, manual, ctx }

  function armBypass(text, ms, manual = false, ctx = null) {
    const b = { text, until: performance.now() + ms, manual, ctx };
    bypass = b;
    if (manual) {
      setTimeout(() => {
        if (bypass !== b) return; // ya se usó
        bypass = null;
        report(buildPayload(ctx, true, "cancelado"), { blocked: true });
        showLabel("alto", `⛔ Envío bloqueado · ${typesText(ctx.findings)} · reenvío expirado`);
      }, ms + 50);
    }
  }

  function takeBypass(text) {
    const b = bypass;
    if (!b || b.text !== text) return null;
    bypass = null;
    return performance.now() <= b.until ? b : null;
  }

  // Deduplicación Enter + clic del mismo envío (solo envíos NO bloqueados).
  let lastSend = null; // { text, t } — solo en memoria

  function intercept(e, editor, button, trigger) {
    const t0 = performance.now();
    const text = readText(editor);
    if (!text) return;

    const b = takeBypass(text);
    if (b) {
      // Reenvío autorizado: se deja pasar a la página sin volver a bloquear.
      if (b.manual) finishSendAnyway(b.ctx, `manual (${trigger}, isTrusted=${e.isTrusted})`);
      return;
    }

    // Capa 1 — decisión síncrona (CE-6: < 50 ms).
    const findings = P ? P.detectSecrets(text) : [];
    const block = shouldBlock(text, findings);
    const decisionMs = performance.now() - t0;

    if (block) {
      cancelEvent(e);
      console.info(
        `[auditor] BLOQUEADO ${trigger} platform=${PLATFORM} secretos=${findings.length} ` +
          `decision=${decisionMs.toFixed(2)}ms`
      );
      modal.open({ editor, button, text, findings, decisionMs, trigger });
      return;
    }

    if (lastSend && lastSend.text === text && t0 - lastSend.t < DEDUP_MS) return;
    const mark = { text, t: t0 };
    lastSend = mark;
    setTimeout(() => {
      if (lastSend === mark) lastSend = null; // no retener el texto
    }, DEDUP_MS * 2);

    console.debug(
      `[auditor] ${trigger} platform=${PLATFORM} len=${text.length} ` +
        `secretos_locales=${findings.length} decision=${decisionMs.toFixed(2)}ms`
    );
    report(buildPayload({ text, findings, decisionMs }, false, "no_aplica"), { findings });
  }

  function buildPayload(ctx, blocked, decision) {
    return {
      text: ctx.text,
      platform: PLATFORM,
      timestamp: new Date().toISOString(),
      client_findings: ctx.findings.map(({ type, masked }) => ({ type, masked })),
      blocked,
      user_decision: decision,
      client_latency_ms: Math.round(ctx.decisionMs * 100) / 100,
    };
  }

  // -------------------------------------------- decisiones del modal
  function onCancelled(ctx) {
    focusEnd(ctx.editor); // el texto sigue intacto en el input
    report(buildPayload(ctx, true, "cancelado"), { blocked: true });
    showLabel("alto", `⛔ Envío bloqueado · ${typesText(ctx.findings)}`);
  }

  async function onSendAnyway(ctx) {
    const strategies = [];
    const btn =
      ctx.button && ctx.button.isConnected && sendButtonFrom(ctx.button) ? ctx.button : findSendButtonNear(ctx.editor);
    if (btn) strategies.push(["button.click", () => btn.click()]);
    strategies.push(["enter", () => dispatchEnter(ctx.editor)]);

    for (const [name, fire] of strategies) {
      if (!ctx.editor.isConnected || readText(ctx.editor) !== ctx.text) break;
      armBypass(ctx.text, BYPASS_MS);
      focusEnd(ctx.editor);
      fire();
      if (await editorCleared(ctx)) {
        bypass = null;
        finishSendAnyway(ctx, `${name} (isTrusted=false)`);
        return;
      }
    }
    if (!ctx.editor.isConnected || readText(ctx.editor) !== ctx.text) {
      // El editor cambió por otra causa: no arriesgar un envío doble.
      bypass = null;
      finishSendAnyway(ctx, "desconocida");
      return;
    }
    // Ninguna estrategia programática funcionó: el usuario reintenta con un evento real.
    armBypass(ctx.text, MANUAL_BYPASS_MS, true, ctx);
    focusEnd(ctx.editor);
    showLabel("medio", "⚠️ La página no aceptó el reenvío automático · presiona Enviar otra vez (10 s)");
  }

  function finishSendAnyway(ctx, strategy) {
    console.info(`[auditor] reenviado con ${strategy} platform=${PLATFORM}`);
    report(buildPayload(ctx, true, "enviado"), { blocked: true });
    showLabel("medio", `⚠️ Enviado con secreto · ${typesText(ctx.findings)}`);
  }

  function dispatchEnter(editor) {
    const init = { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true, composed: true };
    editor.dispatchEvent(new KeyboardEvent("keydown", init));
    editor.dispatchEvent(new KeyboardEvent("keyup", init));
  }

  /** true si la página vació (o reemplazó) el editor: señal de que sí envió. */
  function editorCleared(ctx) {
    return new Promise((resolve) => {
      const t0 = performance.now();
      (function poll() {
        if (!ctx.editor.isConnected || readText(ctx.editor) !== ctx.text) return resolve(true);
        if (performance.now() - t0 > VERIFY_MS) return resolve(false);
        setTimeout(poll, 50);
      })();
    });
  }

  function focusEnd(editor) {
    if (!editor.isConnected) return;
    editor.focus({ preventScroll: true });
    if (editor.tagName !== "TEXTAREA") {
      const sel = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(editor);
      range.collapse(false);
      sel.removeAllRanges();
      sel.addRange(range);
    }
  }

  // ------------------------------------------------------- backend
  function report(payload, { blocked = false, findings = [] } = {}) {
    try {
      chrome.runtime.sendMessage({ type: "analyze", payload }, (resp) => {
        const failed = chrome.runtime.lastError || !resp || resp.error;
        if (blocked) {
          // La etiqueta del bloqueo ya se mostró; solo avisar si el backend no está.
          if (failed) {
            showLabel("gris", resp && resp.queued
              ? "⚪ Backend no disponible · evento guardado para reintento"
              : "⚪ Backend no disponible");
          }
          return;
        }
        if (failed) showUnavailable(findings);
        else showResult(resp);
      });
    } catch (_err) {
      // "Extension context invalidated": se recargó la extensión sin refrescar la página.
      showLabel("gris", "⚪ Extensión recargada · refresca la página");
    }
  }

  // ------------------------------------------------------- textos
  const CATEGORY_NAMES = {
    programacion: "programación",
    academico: "académico",
    redaccion: "redacción",
    ciberseguridad: "ciberseguridad",
    consulta_general: "consulta general",
    personal: "personal",
  };
  const PLATFORM_NAMES = { chatgpt: "ChatGPT", claude: "Claude", gemini: "Gemini", test: "Prueba" };
  const TYPE_NAMES = {
    openai_key: "API key de OpenAI",
    anthropic_key: "API key de Anthropic",
    aws_access_key: "clave de AWS",
    github_token: "token de GitHub",
    google_api_key: "API key de Google",
    jwt: "JWT",
    private_key: "clave privada",
    password: "contraseña",
    credit_card: "tarjeta",
    email: "correo",
    phone_mx: "teléfono",
    curp: "CURP",
    rfc: "RFC",
    person: "nombre",
    address: "dirección",
  };
  const typeName = (t) => TYPE_NAMES[t] || t;
  const typesText = (findings) => [...new Set(findings.map((f) => typeName(f.type)))].join(", ");

  function categoryName(resp) {
    // Mientras el ML no exista (Fase 4) la confianza es 0 → "sin clasificar".
    if (!resp.category || !resp.category_confidence) return "sin clasificar";
    return CATEGORY_NAMES[resp.category] || resp.category;
  }

  function findingsText(findings) {
    const ordered = [...findings].sort((a, b) => (a.source === "pii") - (b.source === "pii"));
    const parts = ordered.slice(0, 3).map((f) => `${typeName(f.type)} (${f.masked})`);
    if (ordered.length > 3) parts.push(`+${ordered.length - 3} más`);
    return parts.join(", ");
  }

  function showResult(resp) {
    const cat = categoryName(resp);
    const findings = resp.findings || [];
    const inj = Number(resp.injection_score || 0);
    const what = findings.length ? `contiene ${findingsText(findings)}` : `posible inyección (${inj.toFixed(2)})`;
    if (resp.risk === "alto") showLabel("alto", `⛔ Riesgo alto · ${what} · ${cat}`);
    else if (resp.risk === "medio") showLabel("medio", `⚠️ Riesgo medio · ${what} · ${cat}`);
    else showLabel("bajo", `✅ Sin riesgo · ${cat} · ${PLATFORM_NAMES[PLATFORM] || PLATFORM}`);
  }

  function showUnavailable(clientFindings) {
    let msg = "⚪ Backend no disponible";
    if (clientFindings && clientFindings.length) msg += ` · detectado localmente: ${findingsText(clientFindings)}`;
    showLabel("gris", msg);
  }

  // ------------------------------------------------------- estilos
  // Una sola hoja compartida por los dos Shadow DOM (etiquetas y modal).
  const sheet = new CSSStyleSheet();
  let cssReady = false;
  const cssUrl = chrome.runtime.getURL("overlay.css");
  fetch(cssUrl)
    .then((r) => r.text())
    .then((css) => {
      sheet.replaceSync(css);
      cssReady = true;
    })
    .catch(() => {});

  function makeShadowHost(tag, hostCss) {
    const host = document.createElement(tag);
    host.style.cssText = `all:initial;position:fixed;z-index:2147483647;${hostCss}`;
    const shadow = host.attachShadow({ mode: "closed" });
    shadow.adoptedStyleSheets = [sheet];
    if (!cssReady) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = cssUrl;
      shadow.append(link);
    }
    return { host, shadow };
  }

  // ------------------------------------------------------- etiqueta
  let labelHost = null;
  let stack = null;

  function ensureLabels() {
    if (!labelHost) {
      const { host, shadow } = makeShadowHost("auditor-ia-overlay", "right:16px;bottom:16px;pointer-events:none;");
      labelHost = host;
      stack = document.createElement("div");
      stack.className = "stack";
      stack.setAttribute("role", "status");
      stack.setAttribute("aria-live", "polite");
      shadow.append(stack);
    }
    // Las SPA a veces reemplazan el <body>; colgarlo de <html> es más estable.
    if (!labelHost.isConnected || labelHost.nextSibling) document.documentElement.appendChild(labelHost);
  }

  function showLabel(level, message) {
    ensureLabels();
    const el = document.createElement("div");
    el.className = `label ${level}`;
    el.textContent = message; // textContent: nunca innerHTML con datos externos
    el.title = "Auditor de Prompts · clic para cerrar";
    el.addEventListener("click", () => dismiss(el));
    stack.appendChild(el);
    while (stack.children.length > MAX_LABELS) stack.firstElementChild.remove();
    setTimeout(() => dismiss(el), LABEL_MS);
  }

  function dismiss(el) {
    if (!el.isConnected || el.classList.contains("leaving")) return;
    el.classList.add("leaving");
    setTimeout(() => el.remove(), 250);
  }

  // --------------------------------------------------------- modal
  const modal = (() => {
    let host = null;
    let ctx = null;
    let cancelBtn = null;

    function el(tag, cls, text) {
      const n = document.createElement(tag);
      if (cls) n.className = cls;
      if (text != null) n.textContent = text;
      return n;
    }

    function open(context) {
      ctx = context;
      const made = makeShadowHost("auditor-ia-modal", "inset:0;");
      host = made.host;

      const backdrop = el("div", "backdrop");
      const box = el("div", "modal");
      box.setAttribute("role", "alertdialog");
      box.setAttribute("aria-modal", "true");
      box.setAttribute("aria-labelledby", "aia-title");
      box.setAttribute("aria-describedby", "aia-desc");

      const title = el("h2", "title", "⛔ Posible fuga de información");
      title.id = "aia-title";
      const desc = el("p", "desc", "Tu mensaje NO se envió. Contiene datos que parecen secretos:");
      desc.id = "aia-desc";

      const list = el("ul", "findings");
      for (const f of ctx.findings) {
        const li = el("li");
        li.append(el("span", "ftype", typeName(f.type)), el("code", "fmask", f.masked)); // solo enmascarado
        list.append(li);
      }

      const note = el(
        "p",
        "note",
        `Detectado localmente en ${ctx.decisionMs.toFixed(1)} ms. El dato no ha salido de tu computadora.`
      );

      const actions = el("div", "actions");
      const sendBtn = el("button", "btn danger", "Enviar de todos modos");
      sendBtn.type = "button";
      cancelBtn = el("button", "btn primary", "Cancelar");
      cancelBtn.type = "button";
      sendBtn.addEventListener("click", sendAnyway);
      cancelBtn.addEventListener("click", cancel);
      actions.append(sendBtn, cancelBtn);

      // Teclas dentro del modal: no llegan a los listeners (burbuja) de la página.
      // Trampa de foco: Tab no sale del modal. (Escape lo maneja onKeyDown.)
      backdrop.addEventListener("keydown", (e) => {
        e.stopPropagation();
        if (e.key !== "Tab") return;
        e.preventDefault();
        const order = [sendBtn, cancelBtn];
        const i = order.indexOf(made.shadow.activeElement);
        order[(i + (e.shiftKey ? -1 : 1) + order.length) % order.length].focus();
      });

      box.append(title, desc, list, note, actions);
      backdrop.append(box);
      made.shadow.append(backdrop);
      document.documentElement.appendChild(host);
      cancelBtn.focus(); // «Cancelar» es el default (Enter/Espacio lo activan)
    }

    function close() {
      const c = ctx;
      if (host) host.remove();
      host = null;
      ctx = null;
      cancelBtn = null;
      return c;
    }

    function cancel() {
      const c = close();
      if (c) onCancelled(c);
    }

    function sendAnyway() {
      const c = close();
      if (c) onSendAnyway(c);
    }

    return { open, cancel, isOpen: () => host !== null };
  })();

  ensureLabels();
})();
