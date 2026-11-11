/*
 * content.js — se inyecta en chatgpt.com, claude.ai, gemini.google.com
 * (y en localhost:5500 para test.html, solo en desarrollo).
 *
 * Flujo (SPEC §3):
 *   1. Listeners en FASE DE CAPTURA (keydown Enter / click en "Enviar").
 *   2. Lee el texto del textarea / contenteditable ANTES de que la página lo limpie.
 *   3. Capa 1: detectSecrets() local y síncrono → client_findings.
 *      (Fase 3: si shouldBlock() → cancelar envío y mostrar modal.)
 *   4. Manda el payload a background.js → backend → etiqueta flotante.
 *
 * PRIVACIDAD: el texto del prompt NUNCA se imprime en consola ni se guarda.
 * Solo se loguean métricas (ms, nº de hallazgos).
 *
 * RNF-06: en Fase 2 no se llama preventDefault/stopPropagation: la página
 * sigue funcionando exactamente igual.
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
  const EDITOR_SELECTOR =
    'textarea, [contenteditable="true"], [contenteditable=""], [contenteditable="plaintext-only"]';
  // Se busca en aria-label / title / data-testid / texto del botón (sin clases CSS).
  const SEND_HINT = /send|enviar|submit/i;

  const PLATFORM = detectPlatform(location.hostname);

  let settings = { monitoring: true, blocking: true };
  chrome.storage.local.get(["monitoring", "blocking"]).then((s) => {
    settings = { ...settings, ...s };
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    for (const [k, { newValue }] of Object.entries(changes)) settings[k] = newValue;
  });

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

  /** Editor más cercano al botón (subiendo por los ancestros); si hay varios, el último con foco. */
  function findEditorNear(button) {
    let node = button.parentElement;
    for (let depth = 0; node && depth < 10; depth++, node = node.parentElement) {
      const editors = [...node.querySelectorAll(EDITOR_SELECTOR)]
        .map(editorFrom)
        .filter((ed) => ed && isVisible(ed));
      if (editors.length === 0) continue;
      if (lastEditor && editors.includes(lastEditor)) return lastEditor;
      return editors[0];
    }
    return lastEditor && isVisible(lastEditor) ? lastEditor : null;
  }

  /** ¿El clic fue en un botón de "Enviar"? Devuelve el botón o null. */
  function sendButtonFrom(target) {
    if (!(target instanceof Element)) return null;
    if (overlayHost && overlayHost.contains(target)) return null;
    const btn = target.closest('button, [role="button"], input[type="submit"]');
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

  function onKeyDown(e) {
    if (!settings.monitoring) return;
    if (e.key !== "Enter" || e.shiftKey || e.altKey) return;
    if (e.isComposing || e.keyCode === 229) return; // composición IME
    const editor = editorFrom(e.composedPath()[0]);
    if (!editor) return;
    handleSend(editor, "enter", e);
  }

  function onClick(e) {
    if (!settings.monitoring) return;
    if (e.button !== 0) return;
    const btn = sendButtonFrom(e.composedPath()[0]);
    if (!btn) return;
    const editor = findEditorNear(btn);
    if (!editor) return;
    handleSend(editor, "click", e);
  }

  // Deduplicación Enter + clic del mismo envío. Solo en memoria.
  let lastSend = null; // { text, t }

  function isDuplicate(text, now) {
    return lastSend && lastSend.text === text && now - lastSend.t < DEDUP_MS;
  }

  // ---------------------------------------------- Fase 3: modo bloqueo
  /**
   * TODO FASE 3: devolver true si `findings` contiene un secreto y el modo
   * bloqueo está activo. Entonces handleSend() hará:
   *   event.preventDefault(); event.stopImmediatePropagation();
   *   mostrar modal (tipo + valor enmascarado) con «Cancelar» (default) y
   *   «Enviar de todos modos». Si confirma → bypassOnce = true y re-disparar
   *   el Enter/clic original; registrar blocked:true + user_decision.
   */
  function shouldBlock(_text, _findings) {
    return false;
  }
  // TODO FASE 3: cuando sea true, el siguiente envío no se intercepta (re-disparo).
  // eslint-disable-next-line no-unused-vars
  let bypassOnce = false;

  // ------------------------------------------------------------ envío
  function handleSend(editor, trigger, event) {
    const text = readText(editor);
    if (!text) return;

    const now = performance.now();
    if (isDuplicate(text, now)) return;
    lastSend = { text, t: now };
    setTimeout(() => {
      if (lastSend && lastSend.t === now) lastSend = null; // no retener el texto
    }, DEDUP_MS * 2);

    // Capa 1 — detección local síncrona (CE-6: debe ser < 50 ms).
    const t0 = performance.now();
    const clientFindings = P ? P.detectSecrets(text) : [];
    const block = settings.blocking && shouldBlock(text, clientFindings);
    const localMs = performance.now() - t0;
    console.debug(
      `[auditor] ${trigger} platform=${PLATFORM} len=${text.length} ` +
        `secretos_locales=${clientFindings.length} local=${localMs.toFixed(2)}ms`
    );

    if (block) {
      // TODO FASE 3: interceptar `event` y mostrar el modal aquí.
      void event;
    }

    report(
      {
        text,
        platform: PLATFORM,
        timestamp: new Date().toISOString(),
        client_findings: clientFindings,
        blocked: false,
        user_decision: "no_aplica",
      },
      clientFindings
    );
  }

  function report(payload, clientFindings) {
    try {
      chrome.runtime.sendMessage({ type: "analyze", payload }, (resp) => {
        if (chrome.runtime.lastError || !resp || resp.error) {
          showUnavailable(clientFindings);
        } else {
          showResult(resp);
        }
      });
    } catch (_err) {
      // "Extension context invalidated": se recargó la extensión sin refrescar la página.
      showLabel("gris", "⚪ Extensión recargada · refresca la página");
    }
  }

  // ------------------------------------------------------- etiqueta
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

  function categoryName(resp) {
    // Mientras el ML no exista (Fase 4) la confianza es 0 → "sin clasificar".
    if (!resp.category || !resp.category_confidence) return "sin clasificar";
    return CATEGORY_NAMES[resp.category] || resp.category;
  }

  function findingsText(findings) {
    const ordered = [...findings].sort(
      (a, b) => (a.source === "pii") - (b.source === "pii") // secretos primero
    );
    const parts = ordered.slice(0, 3).map((f) => `${TYPE_NAMES[f.type] || f.type} (${f.masked})`);
    if (ordered.length > 3) parts.push(`+${ordered.length - 3} más`);
    return parts.join(", ");
  }

  function showResult(resp) {
    const cat = categoryName(resp);
    const findings = resp.findings || [];
    const inj = Number(resp.injection_score || 0);
    const what = findings.length
      ? `contiene ${findingsText(findings)}`
      : `posible inyección (${inj.toFixed(2)})`;

    if (resp.risk === "alto") {
      showLabel("alto", `⛔ Riesgo alto · ${what} · ${cat}`);
    } else if (resp.risk === "medio") {
      showLabel("medio", `⚠️ Riesgo medio · ${what} · ${cat}`);
    } else {
      showLabel("bajo", `✅ Sin riesgo · ${cat} · ${PLATFORM_NAMES[PLATFORM] || PLATFORM}`);
    }
  }

  function showUnavailable(clientFindings) {
    let msg = "⚪ Backend no disponible";
    if (clientFindings && clientFindings.length) {
      msg += ` · detectado localmente: ${findingsText(clientFindings)}`;
    }
    showLabel("gris", msg);
  }

  let overlayHost = null;
  let stack = null;

  function ensureOverlay() {
    if (overlayHost && overlayHost.isConnected) return;
    if (!overlayHost) {
      overlayHost = document.createElement("auditor-ia-overlay");
      overlayHost.style.cssText =
        "all:initial;position:fixed;right:16px;bottom:16px;z-index:2147483647;pointer-events:none;";
      const shadow = overlayHost.attachShadow({ mode: "closed" });
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = chrome.runtime.getURL("overlay.css");
      stack = document.createElement("div");
      stack.className = "stack";
      stack.setAttribute("role", "status");
      stack.setAttribute("aria-live", "polite");
      shadow.append(link, stack);
    }
    // Las SPA a veces reemplazan el <body>; colgarlo de <html> es más estable.
    document.documentElement.appendChild(overlayHost);
  }

  function showLabel(level, message) {
    ensureOverlay();
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

  ensureOverlay();
})();
