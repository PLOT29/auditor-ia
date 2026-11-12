/*
 * content.js — Centinela en chatgpt.com, claude.ai, gemini.google.com
 * (y localhost:5500 para test.html, solo en desarrollo). Fase 5A.
 *
 * 1. Escudo en vivo: al escribir o pegar (debounce 150 ms) evalúa el texto LOCALMENTE
 *    (patterns.evaluate) — sin backend — y pinta el escudo: verde / ámbar / rojo.
 * 2. Políticas por tipo (popup → chrome.storage, en memoria): bloquear / advertir / permitir.
 * 3. Al enviar (Enter o clic, fase de captura, decisión SÍNCRONA):
 *      bloquear  → se cancela el envío y se abre el modal: Cancelar · Censurar y enviar · Enviar de todos modos.
 *      advertir  → si el banner ya estaba visible para esos mismos hallazgos, el envío sigue (el usuario
 *                  ya fue avisado) y se registra "advertido". Si no lo había visto (p. ej. pulsó Enter antes
 *                  de los 150 ms), se cancela ESE envío una sola vez y aparece el banner.
 *      permitir  → sigue normal.
 * 4. Diccionario propio: los términos solo existen aquí y en chrome.storage.local. Al backend llega
 *    el tipo "termino_propio" y el texto con el término reemplazado por <TERMINO_PROPIO>.
 * 5. Pegado grande (> umbral, default 3 000): aviso discreto con tamaño y tipos detectados.
 *
 * FLUJO "ADVERTIR" (RNF-06: no perder ni duplicar):
 *   - Nunca se borra texto: «Editar» solo cierra el banner y devuelve el foco; «Censurar» solo reemplaza
 *     los hallazgos por marcadores y verifica el resultado; no envía.
 *   - Solo se reenvía de forma programática con «Enviar así», con la misma estrategia verificada que
 *     el modal (bypassOnce + verificación de que el editor se vació). Un Enter real del usuario con el
 *     banner visible pasa una sola vez: no hay reenvío nuestro, así que no puede duplicarse.
 *
 * CENSURA EN EL CAMPO (modal «Censurar y enviar» y banner «Censurar»):
 *   - textarea: setSelectionRange + execCommand("insertText"); si falla, setRangeText + evento input.
 *   - contenteditable (ProseMirror / Quill / Lexical…): se ubica el valor en los nodos de texto, se
 *     selecciona con un Range y se usa execCommand("insertText"), que genera beforeinput/input reales
 *     que el framework procesa como si el usuario escribiera.
 *   - SIEMPRE se verifica después que el campo ya no contenga ningún valor detectado. Si falla, no se
 *     envía: se copia la versión censurada al portapapeles y, en ese sitio, el botón pasa a ser
 *     «Copiar versión censurada» (se recuerda en chrome.storage.local).
 *   Estrategia por plataforma (actualizar tras la prueba manual):
 *     test.html (textarea y contenteditable) → execCommand insertText ✔ verificado.
 *     ChatGPT / Claude / Gemini → PENDIENTE de validar en vivo (la verificación protege igual).
 *
 * PRIVACIDAD: el texto, los valores detectados y los términos del diccionario nunca se imprimen ni se
 * guardan. Solo se loguean métricas (ms, conteos).
 */
(() => {
  "use strict";

  if (window.__centinelaLoaded) return;
  window.__centinelaLoaded = true;

  const P = globalThis.AuditorPatterns;
  const UI = globalThis.CentinelaUI;
  UI.init(chrome.runtime.getURL("overlay.css"));

  // ---------------------------------------------------------------- config
  const DEDUP_MS = 500;
  const BYPASS_MS = 2000;
  const MANUAL_BYPASS_MS = 10000;
  const VERIFY_MS = 1500;
  const DEBOUNCE_MS = 150;
  const EDITOR_SELECTOR =
    'textarea, [contenteditable="true"], [contenteditable=""], [contenteditable="plaintext-only"]';
  const BUTTON_SELECTOR = 'button, [role="button"], input[type="submit"]';
  const SEND_HINT = /send|enviar|submit/i;

  const PLATFORM = detectPlatform(location.hostname);
  const PLATFORM_NAMES = { chatgpt: "ChatGPT", claude: "Claude", gemini: "Gemini", test: "Prueba" };

  const DEFAULT_POLICIES = Object.fromEntries([
    ...P.SECRET_TYPES.map((t) => [t, "bloquear"]),
    ...P.PII_TYPES.map((t) => [t, "advertir"]),
    [P.DICT_TYPE, "advertir"],
  ]);
  const DEFAULT_SETTINGS = {
    monitoring: true,
    blocking: true,
    policies: DEFAULT_POLICIES,
    dictionary: [],
    pasteThreshold: 3000,
    censorFallbackHosts: [],
  };

  // Ajustes en MEMORIA: la decisión de envío es síncrona y no puede esperar a storage.get().
  const settings = structuredClone(DEFAULT_SETTINGS);
  function applySettings(s) {
    for (const k of Object.keys(DEFAULT_SETTINGS)) {
      if (!(k in s)) continue;
      settings[k] = k === "policies" ? { ...DEFAULT_POLICIES, ...(s[k] || {}) } : s[k] ?? DEFAULT_SETTINGS[k];
    }
    settings.dictionary = P.normalizeDictionary(settings.dictionary);
    live.refresh();
  }
  chrome.storage.local.get(Object.keys(DEFAULT_SETTINGS)).then(applySettings);
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    const s = {};
    for (const [k, v] of Object.entries(changes)) s[k] = v.newValue ?? DEFAULT_SETTINGS[k];
    applySettings(s);
  });

  const monitoringOn = () => settings.monitoring !== false;
  const policiesOn = () => monitoringOn() && settings.blocking !== false; // "Modo bloqueo" = aplicar políticas
  const policyFor = (type) => (policiesOn() ? settings.policies[type] || DEFAULT_POLICIES[type] || "advertir" : "permitir");
  const RANK = { permitir: 0, advertir: 1, bloquear: 2 };

  function detectPlatform(host) {
    if (host === "chatgpt.com" || host.endsWith(".chatgpt.com")) return "chatgpt";
    if (host === "claude.ai" || host.endsWith(".claude.ai")) return "claude";
    if (host === "gemini.google.com") return "gemini";
    if (host === "localhost" || host === "127.0.0.1") return "test";
    return "desconocida";
  }

  /** Hallazgos con su acción según la política. */
  function evaluate(text) {
    return P.evaluate(text, { dictionary: settings.dictionary }).map((f) => ({ ...f, action: policyFor(f.type) }));
  }

  const maxAction = (findings) =>
    findings.reduce((a, f) => (RANK[f.action] > RANK[a] ? f.action : a), "permitir");
  const signature = (findings) => findings.map((f) => `${f.type}:${f.masked}:${f.action}`).join("|");

  // ---------------------------------------------------------- editores
  function editorFrom(node) {
    if (!(node instanceof Element)) return null;
    if (node.tagName === "TEXTAREA") return node;
    if (node.isContentEditable) {
      let h = node;
      while (h.parentElement && h.parentElement.isContentEditable) h = h.parentElement;
      return h;
    }
    return null;
  }

  function isVisible(el) {
    if (!el || !el.isConnected || el.getClientRects().length === 0) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none";
  }

  function readText(editor) {
    const raw = editor.tagName === "TEXTAREA" ? editor.value : editor.innerText;
    return (raw || "").replace(/ /g, " ").trim();
  }

  let lastEditor = null;
  document.addEventListener(
    "focusin",
    (e) => {
      const ed = editorFrom(e.composedPath()[0]);
      if (ed) {
        lastEditor = ed;
        live.schedule(ed, 0);
      }
    },
    true
  );

  function nearest(start, selector, map) {
    let node = start.parentElement;
    for (let depth = 0; node && depth < 10; depth++, node = node.parentElement) {
      const hits = [...node.querySelectorAll(selector)].map(map).filter((x) => x && isVisible(x));
      if (hits.length) return hits;
    }
    return [];
  }

  function findEditorNear(button) {
    const editors = nearest(button, EDITOR_SELECTOR, editorFrom);
    if (editors.length) return lastEditor && editors.includes(lastEditor) ? lastEditor : editors[0];
    return lastEditor && isVisible(lastEditor) ? lastEditor : null;
  }

  function findSendButtonNear(editor) {
    const candidates = nearest(editor, BUTTON_SELECTOR, sendButtonFrom);
    return candidates.find((btn) => nearest(btn, EDITOR_SELECTOR, editorFrom).includes(editor)) || null;
  }

  function sendButtonFrom(target) {
    if (!(target instanceof Element) || UI.isOwn(target)) return null;
    const btn = target.closest(BUTTON_SELECTOR);
    if (!btn) return null;
    if (btn.disabled || btn.getAttribute("aria-disabled") === "true") return null;
    const label = [btn.getAttribute("aria-label"), btn.getAttribute("title"), btn.getAttribute("data-testid"),
      btn.value, (btn.textContent || "").trim().slice(0, 40)].filter(Boolean).join(" ");
    if (SEND_HINT.test(label)) return btn;
    if (btn.type === "submit" && btn.form && btn.form.querySelector(EDITOR_SELECTOR)) return btn;
    return null;
  }

  // ------------------------------------------------- escudo en vivo + banner
  const live = (() => {
    let timer = null;
    let editor = null;
    let current = { findings: [], sig: "", text: "" };
    let dismissedSig = null; // «Editar»: no volver a mostrar el banner para los mismos hallazgos

    function schedule(ed, delay = DEBOUNCE_MS) {
      editor = ed;
      clearTimeout(timer);
      timer = setTimeout(run, delay);
    }

    function run() {
      if (!editor || !isVisible(editor)) {
        UI.shield.hide();
        UI.banner.hide();
        return;
      }
      if (!monitoringOn()) {
        UI.shield.update({ rect: editor.getBoundingClientRect(), state: "off", items: [] });
        UI.banner.hide();
        return;
      }
      const text = readText(editor);
      const t0 = performance.now();
      const findings = evaluate(text);
      const ms = performance.now() - t0;
      const sig = signature(findings);
      current = { findings, sig, text };
      const state = findings.some((f) => f.source === "secret") ? "danger" : findings.length ? "warn" : "ok";
      UI.shield.update({ rect: editor.getBoundingClientRect(), state, items: findings, ms });
      const warn = findings.filter((f) => f.action === "advertir");
      if (warn.length && maxAction(findings) === "advertir" && dismissedSig !== signature(warn)) {
        if (!UI.banner.visible() || bannerSig !== signature(warn)) showBanner(editor, findings, false);
      } else if (!warn.length || maxAction(findings) !== "advertir") {
        UI.banner.hide();
        bannerSig = null;
      }
      if (ms > 10) console.debug(`[centinela] evaluación lenta: ${ms.toFixed(1)}ms len=${text.length}`);
    }

    function reposition() {
      if (editor && isVisible(editor) && current) run();
    }

    let raf = 0;
    const onMove = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        reposition();
      });
    };
    window.addEventListener("scroll", onMove, { capture: true, passive: true });
    window.addEventListener("resize", onMove, { passive: true });

    return {
      schedule,
      refresh: () => editor && schedule(editor, 0),
      dismiss: (sig) => (dismissedSig = sig),
      resetDismiss: () => (dismissedSig = null),
      get editor() { return editor; },
    };
  })();

  document.addEventListener(
    "input",
    (e) => {
      const ed = editorFrom(e.composedPath()[0]);
      if (ed) live.schedule(ed);
    },
    true
  );

  let bannerSig = null;

  const WITH_ARTICLE = {
    email: "un correo", phone_mx: "un teléfono", curp: "un CURP", rfc: "un RFC", termino_propio: "un término propio",
    openai_key: "una API key", anthropic_key: "una API key", google_api_key: "una API key", aws_access_key: "una clave de AWS",
    github_token: "un token", jwt: "un token", private_key: "una clave privada", password: "una contraseña", credit_card: "una tarjeta",
  };

  function warnMessage(findings) {
    const items = [...new Set(findings.filter((f) => f.action === "advertir").map((f) => WITH_ARTICLE[f.type] || UI.typeName(f.type)))];
    const list = items.length > 1 ? `${items.slice(0, -1).join(", ")} y ${items.at(-1)}` : items[0];
    return `Tu mensaje incluye ${list}`;
  }

  function showBanner(editor, findings, focus, ctx = null) {
    const warn = findings.filter((f) => f.action === "advertir");
    bannerSig = signature(warn);
    UI.banner.show({
      rect: editor.getBoundingClientRect(),
      message: warnMessage(findings),
      censorLabel: censorUnsupported() ? "Copiar censurado" : "Censurar",
      focus,
      onCensor: async () => {
        UI.banner.hide();
        bannerSig = null;
        const text = readText(editor);
        const fs = evaluate(text).filter((f) => f.action === "advertir");
        const res = await censorInField(editor, text, fs);
        if (res.ok) {
          UI.toast({ level: "info", title: "Datos censurados en el campo", text: "Revisa el mensaje y envíalo cuando quieras." });
        } else {
          await copyCensored(text, fs);
        }
        live.schedule(editor, 0);
      },
      onSend: () => {
        UI.banner.hide();
        bannerSig = null;
        const text = readText(editor);
        const fs = evaluate(text);
        resend({ editor, button: null, text, findings: fs, decisionMs: 0 }, "advertido");
      },
      onEdit: () => {
        UI.banner.hide();
        live.dismiss(bannerSig);
        bannerSig = null;
        const text = readText(editor);
        const first = evaluate(text).find((f) => f.action === "advertir");
        focusEnd(editor);
        if (first) selectValue(editor, text.slice(first.start, first.end));
      },
    });
  }

  // ------------------------------------------------------------ captura
  window.addEventListener("keydown", onKeyDown, true);
  window.addEventListener("click", onClick, true);
  document.addEventListener("paste", onPaste, true);

  function cancelEvent(e) {
    e.preventDefault();
    e.stopImmediatePropagation();
    e.stopPropagation();
  }

  function onKeyDown(e) {
    if (fromCentinela(e)) return; // teclas dentro del modal/banner: las maneja ui.js
    if (UI.modal.isOpen()) {
      if (e.key === "Escape") {
        cancelEvent(e);
        UI.modal.cancel();
        return;
      }
      if (e.key === "Enter" && editorFrom(e.composedPath()[0])) cancelEvent(e);
      return;
    }
    if (e.key === "Escape" && UI.banner.visible() && editorFrom(e.composedPath()[0])) {
      UI.banner.hide();
      live.dismiss(bannerSig);
      bannerSig = null;
      return;
    }
    if (!monitoringOn()) return;
    if (e.key !== "Enter" || e.shiftKey || e.altKey) return;
    if (e.isComposing || e.keyCode === 229) return;
    const editor = editorFrom(e.composedPath()[0]);
    if (!editor) return;
    intercept(e, editor, null, "enter");
  }

  // Eventos que nacen en la capa de Centinela (sus propios botones) nunca son "enviar" de la página.
  const fromCentinela = (e) => e.composedPath().some((n) => UI.isOwn(n));

  function onClick(e) {
    if (e.button !== 0 || fromCentinela(e)) return;
    const btn = sendButtonFrom(e.composedPath()[0]);
    if (!btn) return;
    if (UI.modal.isOpen()) {
      cancelEvent(e);
      return;
    }
    if (!monitoringOn()) return;
    const editor = findEditorNear(btn);
    if (!editor) return;
    intercept(e, editor, btn, "click");
  }

  function onPaste(e) {
    if (!monitoringOn()) return;
    const editor = editorFrom(e.composedPath()[0]);
    if (!editor) return;
    const pasted = (e.clipboardData && e.clipboardData.getData("text/plain")) || "";
    const limit = Number(settings.pasteThreshold) || DEFAULT_SETTINGS.pasteThreshold;
    if (pasted.length <= limit) return;
    const t0 = performance.now();
    const fs = evaluate(pasted);
    const ms = performance.now() - t0;
    const types = [...new Set(fs.map((f) => UI.typeName(f.type)))];
    UI.toast({
      level: fs.some((f) => f.source === "secret") ? "alto" : fs.length ? "medio" : "info",
      title: `Pegaste ${pasted.length.toLocaleString("es-MX")} caracteres`,
      text: types.length ? `Detectado: ${types.join(", ")}` : `Sin datos sensibles · ${ms.toFixed(0)} ms`,
    });
  }

  // --------------------------------------------------------- bypassOnce
  let bypass = null; // { text, until, manual, ctx, decision }

  function armBypass(text, ms, manual = false, ctx = null, decision = "enviado") {
    const b = { text, until: performance.now() + ms, manual, ctx, decision };
    bypass = b;
    if (manual) {
      setTimeout(() => {
        if (bypass !== b) return;
        bypass = null;
        report(buildPayload(ctx, true, "cancelado"), { blocked: true });
        UI.card({ level: "alto", title: "Envío bloqueado · reenvío expirado", chips: findingChips(ctx.findings) });
      }, ms + 50);
    }
  }

  function takeBypass(text) {
    const b = bypass;
    if (!b || b.text !== text) return null;
    bypass = null;
    return performance.now() <= b.until ? b : null;
  }

  // ------------------------------------------------------- decisión de envío
  let lastSend = null;

  function intercept(e, editor, button, trigger) {
    const t0 = performance.now();
    const text = readText(editor);
    if (!text) return;

    const b = takeBypass(text);
    if (b) {
      if (b.manual) finishSend(b.ctx, `manual (${trigger}, isTrusted=${e.isTrusted})`, b.decision);
      return;
    }

    const findings = evaluate(text);
    const action = maxAction(findings);
    const decisionMs = performance.now() - t0;
    const ctx = { editor, button, text, findings, decisionMs, trigger };

    if (action === "bloquear") {
      cancelEvent(e);
      console.info(`[centinela] BLOQUEADO ${trigger} platform=${PLATFORM} hallazgos=${findings.length} decision=${decisionMs.toFixed(2)}ms`);
      openBlockModal(ctx);
      return;
    }
    if (action === "advertir") {
      const warnSig = signature(findings.filter((f) => f.action === "advertir"));
      if (!(UI.banner.visible() && bannerSig === warnSig)) {
        cancelEvent(e); // el usuario aún no vio el aviso: se detiene ESTE envío una vez, el texto queda intacto
        live.resetDismiss();
        showBanner(editor, findings, true, ctx);
        console.info(`[centinela] ADVERTIDO (detenido una vez) ${trigger} decision=${decisionMs.toFixed(2)}ms`);
        return;
      }
      UI.banner.hide();
      bannerSig = null;
      return sendNow(ctx, "advertido");
    }
    sendNow(ctx, "no_aplica");
  }

  function sendNow(ctx, decision) {
    const { text, decisionMs, trigger } = ctx;
    if (lastSend && lastSend.text === text && performance.now() - lastSend.t < DEDUP_MS) return;
    const mark = { text, t: performance.now() };
    lastSend = mark;
    setTimeout(() => {
      if (lastSend === mark) lastSend = null;
    }, DEDUP_MS * 2);
    console.debug(`[centinela] ${trigger} platform=${PLATFORM} len=${text.length} hallazgos=${ctx.findings.length} decision=${decisionMs.toFixed(2)}ms`);
    report(buildPayload(ctx, decision === "advertido", decision), { findings: ctx.findings, decision });
  }

  /** Payload para el backend. El término propio NUNCA viaja: se reemplaza por <TERMINO_PROPIO>. */
  function buildPayload(ctx, flagged, decision, textOverride = null) {
    const text = textOverride ?? ctx.text;
    const dict = textOverride ? [] : ctx.findings.filter((f) => f.type === P.DICT_TYPE);
    return {
      text: dict.length ? P.maskText(text, dict) : text,
      platform: PLATFORM,
      timestamp: new Date().toISOString(),
      client_findings: ctx.findings.map(({ type, masked }) => ({ type, masked })),
      blocked: decision === "advertido" ? false : flagged,
      user_decision: decision,
      client_latency_ms: Math.round((ctx.decisionMs || 0) * 100) / 100,
    };
  }

  // ------------------------------------------------------------ modal
  function fragment(text, findings, f, radius = 42) {
    const from = Math.max(0, f.start - radius);
    const to = Math.min(text.length, f.end + radius);
    const piece = (a, b) => {
      // Cualquier otro hallazgo dentro de la ventana también se muestra enmascarado.
      let out = "";
      let pos = a;
      for (const g of findings) {
        if (g === f || g.end <= a || g.start >= b) continue;
        out += text.slice(pos, Math.max(pos, g.start)) + g.masked;
        pos = Math.min(b, g.end);
      }
      return (out + text.slice(pos, b)).replace(/\s+/g, " ");
    };
    return {
      type: f.type,
      action: f.action,
      masked: f.masked,
      before: (from > 0 ? "…" : "") + piece(from, f.start),
      after: piece(f.end, to) + (to < text.length ? "…" : ""),
    };
  }

  function censorUnsupported() {
    return (settings.censorFallbackHosts || []).includes(location.hostname);
  }

  function openBlockModal(ctx) {
    const shown = ctx.findings.filter((f) => f.action !== "permitir").slice(0, 4);
    const extra = ctx.findings.length - shown.length;
    UI.modal.open({
      title: "Posible fuga de información",
      intro: "Tu mensaje NO se envió. Centinela encontró datos que no deberían salir de tu computadora:",
      items: shown.map((f) => fragment(ctx.text, ctx.findings, f)),
      note: `${extra > 0 ? `y ${extra} hallazgo(s) más · ` : ""}Detectado localmente en ${ctx.decisionMs.toFixed(1)} ms. Nada se ha enviado.`,
      censorLabel: censorUnsupported() ? "Copiar versión censurada" : "Censurar y enviar",
      onCancel: () => {
        focusEnd(ctx.editor);
        report(buildPayload(ctx, true, "cancelado"), { blocked: true });
        UI.card({ level: "alto", title: "Envío bloqueado", chips: findingChips(ctx.findings) });
      },
      onSend: () => resend(ctx, "enviado"),
      onCensor: () => censorAndSend(ctx),
    });
  }

  async function censorAndSend(ctx) {
    const targets = ctx.findings.filter((f) => f.action !== "permitir");
    if (censorUnsupported()) {
      await copyCensored(ctx.text, targets);
      report(buildPayload(ctx, true, "cancelado"), { blocked: true });
      return;
    }
    const res = await censorInField(ctx.editor, ctx.text, targets);
    if (!res.ok) {
      rememberCensorFallback();
      await copyCensored(ctx.text, targets);
      report(buildPayload(ctx, true, "cancelado"), { blocked: true });
      return;
    }
    // El campo ya no contiene ningún valor detectado: se envía la versión censurada.
    resend({ ...ctx, text: res.text, censored: true }, "censurado");
  }

  // ------------------------------------------------------------ censura
  /** Reemplaza cada hallazgo por su marcador DENTRO del campo y verifica el resultado. */
  async function censorInField(editor, text, findings) {
    const values = [...new Set(findings.map((f) => text.slice(f.start, f.end)))]; // solo en memoria
    if (!values.length) return { ok: true, text };
    const byValue = new Map(findings.map((f) => [text.slice(f.start, f.end), P.censorMarker(f.type)]));
    let strategy = "insertText";
    try {
      for (const value of [...values].sort((a, b) => b.length - a.length)) {
        const marker = byValue.get(value);
        let guard = 0;
        while (guard++ < 20) {
          const done = editor.tagName === "TEXTAREA"
            ? replaceInTextarea(editor, value, marker)
            : replaceInContentEditable(editor, value, marker);
          if (done === "none") break;
          if (done === "fallback") strategy = "setRangeText";
          if (done === false) throw new Error("no se pudo reemplazar");
        }
      }
    } catch (_err) {
      return { ok: false };
    }
    await new Promise((r) => setTimeout(r, 60)); // los frameworks actualizan el estado en el siguiente tick
    const after = readText(editor);
    const clean = !values.some((v) => after.includes(v)) && !values.some((v) => after.includes(v.replace(/\s+/g, " ")));
    console.info(`[centinela] censura ${clean ? "verificada" : "FALLÓ"} estrategia=${strategy} hallazgos=${values.length}`);
    return clean ? { ok: true, text: after } : { ok: false };
  }

  function replaceInTextarea(ta, value, marker) {
    const i = ta.value.indexOf(value);
    if (i < 0) return "none";
    ta.focus();
    ta.setSelectionRange(i, i + value.length);
    if (document.execCommand("insertText", false, marker)) return true;
    ta.setRangeText(marker, i, i + value.length, "end");
    ta.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertReplacementText", data: marker }));
    return "fallback";
  }

  /** Ubica `value` en los nodos de texto del editor (aunque cruce varios) y lo reemplaza con insertText. */
  function replaceInContentEditable(root, value, marker) {
    const range = rangeFor(root, value);
    if (!range) return "none";
    root.focus({ preventScroll: true });
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    return document.execCommand("insertText", false, marker) ? true : false;
  }

  function rangeFor(root, value) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    let flat = "";
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      nodes.push({ node: n, start: flat.length });
      flat += n.data.replace(/ /g, " ");
    }
    let target = value;
    let i = flat.indexOf(target);
    if (i < 0) {
      target = value.replace(/\n+/g, ""); // valores multilínea (PEM): los párrafos no dejan "\n" en el texto
      i = flat.indexOf(target);
    }
    if (i < 0 || !target) return null;
    const locate = (pos, isEnd) => {
      for (let k = nodes.length - 1; k >= 0; k--) {
        const { node, start } = nodes[k];
        if (isEnd ? pos > start : pos >= start) return { node, offset: Math.min(node.data.length, pos - start) };
      }
      return null;
    };
    const a = locate(i, false);
    const b = locate(i + target.length, true);
    if (!a || !b) return null;
    const range = document.createRange();
    range.setStart(a.node, a.offset);
    range.setEnd(b.node, b.offset);
    return range;
  }

  function selectValue(editor, value) {
    if (editor.tagName === "TEXTAREA") {
      const i = editor.value.indexOf(value);
      if (i >= 0) editor.setSelectionRange(i, i + value.length);
      return;
    }
    const range = rangeFor(editor, value);
    if (!range) return;
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  async function copyCensored(text, findings) {
    const censored = P.censorText(text, findings);
    let copied = false;
    try {
      await navigator.clipboard.writeText(censored);
      copied = true;
    } catch (_err) {
      copied = false;
    }
    UI.card({
      level: "medio",
      title: copied ? "Versión censurada copiada" : "No se pudo editar el campo",
      meta: [copied ? "Pégala en el chat en lugar del texto original. No se envió nada." : "Borra los datos a mano. No se envió nada."],
      ttl: 9000,
    });
  }

  function rememberCensorFallback() {
    const hosts = new Set(settings.censorFallbackHosts || []);
    hosts.add(location.hostname);
    chrome.storage.local.set({ censorFallbackHosts: [...hosts] });
  }

  // ------------------------------------------------------------ reenvío
  async function resend(ctx, decision) {
    const strategies = [];
    const btn = ctx.button && ctx.button.isConnected && sendButtonFrom(ctx.button) ? ctx.button : findSendButtonNear(ctx.editor);
    if (btn) strategies.push(["button.click", () => btn.click()]);
    strategies.push(["enter", () => dispatchEnter(ctx.editor)]);

    for (const [name, fire] of strategies) {
      if (!ctx.editor.isConnected || readText(ctx.editor) !== ctx.text) break;
      armBypass(ctx.text, BYPASS_MS, false, null, decision);
      focusEnd(ctx.editor);
      fire();
      if (await editorCleared(ctx)) {
        bypass = null;
        finishSend(ctx, `${name} (isTrusted=false)`, decision);
        return;
      }
    }
    if (!ctx.editor.isConnected || readText(ctx.editor) !== ctx.text) {
      bypass = null;
      finishSend(ctx, "desconocida", decision);
      return;
    }
    armBypass(ctx.text, MANUAL_BYPASS_MS, true, ctx, decision);
    focusEnd(ctx.editor);
    UI.card({ level: "medio", title: "Presiona Enviar otra vez", meta: ["La página no aceptó el reenvío automático (10 s)."] });
  }

  function finishSend(ctx, strategy, decision) {
    console.info(`[centinela] reenviado con ${strategy} platform=${PLATFORM} decision=${decision}`);
    const flagged = decision !== "advertido";
    report(buildPayload(ctx, flagged, decision, ctx.censored ? ctx.text : null), { blocked: flagged, findings: ctx.findings, decision });
    const titles = { enviado: "Enviado con datos sensibles", censurado: "Enviado censurado", advertido: "Enviado con advertencia" };
    UI.card({ level: decision === "censurado" ? "bajo" : "medio", title: titles[decision] || "Enviado", chips: findingChips(ctx.findings) });
  }

  function dispatchEnter(editor) {
    const init = { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true, composed: true };
    editor.dispatchEvent(new KeyboardEvent("keydown", init));
    editor.dispatchEvent(new KeyboardEvent("keyup", init));
  }

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
    if (!editor || !editor.isConnected) return;
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

  // ------------------------------------------------------- backend + tarjetas
  const CATEGORY_NAMES = {
    programacion: "programación", academico: "académico", redaccion: "redacción", ciberseguridad: "ciberseguridad",
    consulta_general: "consulta general", personal: "personal",
  };

  function findingChips(findings) {
    return (findings || []).slice(0, 5).map((f) => ({
      text: UI.typeName(f.type),
      value: f.masked,
      level: f.source === "secret" || f.source === "client" ? "danger" : "warn",
    }));
  }

  function categoryName(resp) {
    if (!resp.category || !resp.category_confidence) return "sin clasificar";
    const name = CATEGORY_NAMES[resp.category] || resp.category;
    return resp.category_uncertain === true ? `tema incierto (¿${name}?)` : name;
  }

  function report(payload, { blocked = false, findings = [], decision = null } = {}) {
    try {
      chrome.runtime.sendMessage({ type: "analyze", payload }, (resp) => {
        const failed = chrome.runtime.lastError || !resp || resp.error;
        if (decision === "advertido" && !failed) return showResult(resp);
        if (blocked || decision === "advertido") {
          if (failed) {
            UI.card({ level: "gris", title: "Backend no disponible", meta: [resp && resp.queued ? "Evento guardado para reintento" : "El bloqueo local sigue activo"] });
          }
          return;
        }
        if (failed) {
          UI.card({ level: "gris", title: "Backend no disponible", chips: findingChips(findings),
            meta: ["Detección local activa", PLATFORM_NAMES[PLATFORM] || PLATFORM] });
          return;
        }
        showResult(resp);
      });
    } catch (_err) {
      UI.card({ level: "gris", title: "Extensión recargada", meta: ["Refresca la página"] });
    }
  }

  function showResult(resp) {
    const findings = resp.findings || [];
    const inj = resp.injection_score;
    const titles = { alto: "Riesgo alto", medio: "Riesgo medio", bajo: "Sin riesgo" };
    const chips = findingChips(findings);
    if (!findings.length && inj != null && resp.risk !== "bajo") {
      chips.push({ text: "Posible inyección", value: inj.toFixed(2), level: "danger" });
    }
    UI.card({
      level: resp.risk,
      title: titles[resp.risk] || resp.risk,
      chips,
      meta: [categoryName(resp), PLATFORM_NAMES[PLATFORM] || PLATFORM,
        resp.rtt_ms != null ? `${resp.rtt_ms} ms` : resp.latency_ms != null ? `${resp.latency_ms} ms` : null],
    });
  }
})();
