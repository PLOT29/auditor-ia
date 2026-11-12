/*
 * ui.js — Centinela · componentes visuales dentro de la página (Fase 5A).
 *
 * Todo vive en UN Shadow DOM cerrado colgado de <html> (la página no puede leerlo ni
 * romper sus estilos). Sin recursos externos: los estilos son extension/overlay.css (recurso
 * de la propia extensión) y los íconos son SVG en línea. Sin innerHTML con datos: todo texto
 * se pone con textContent.
 *
 * API (globalThis.CentinelaUI):
 *   init(cssUrl)                         carga estilos (una vez)
 *   isOwn(node)                          ¿el nodo es la capa de Centinela?
 *   shield.update({rect, state, items, ms, note}) / shield.hide()
 *   banner.show({rect, message, onCensor, onSend, onEdit, censorLabel, focus}) / hide() / visible()
 *   modal.open({...}) / close() / isOpen() / cancel()
 *   card({level, title, chips, meta})    tarjeta de resultado (etiqueta flotante)
 *   toast({level, title, text})          aviso discreto (pegado grande, info)
 *   typeName(type)                       nombre legible de un tipo de hallazgo
 */
(function (root) {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const SHIELD_PATH = "M12 2.2 19.6 5v6.1c0 5-3.2 8.9-7.6 10.7C7.6 20 4.4 16.1 4.4 11.1V5Z";
  const GLYPHS = {
    ok: "M8.6 12.1l2.3 2.3 4.6-4.9",
    warn: "M12 7.6v5.2M12 15.6v.4",
    danger: "M9.3 9.3l5.4 5.4M14.7 9.3l-5.4 5.4",
    off: "M8.5 12h7",
    info: "M12 10.8v4.6M12 8.2v.4",
  };

  const TYPE_NAMES = {
    openai_key: "API key de OpenAI", anthropic_key: "API key de Anthropic", aws_access_key: "Clave de AWS",
    github_token: "Token de GitHub", google_api_key: "API key de Google", jwt: "JWT", private_key: "Clave privada",
    password: "Contraseña", credit_card: "Tarjeta", email: "Correo", phone_mx: "Teléfono", curp: "CURP",
    rfc: "RFC", person: "Nombre", address: "Dirección", termino_propio: "Término propio",
  };
  const typeName = (t) => TYPE_NAMES[t] || t;
  const ACTION_NAMES = { bloquear: "bloquear", advertir: "advertir", permitir: "permitir" };

  let host = null;
  let shadow = null;
  let layer = null;
  const sheet = new CSSStyleSheet();
  let cssUrl = null;
  let cssReady = false;

  function init(url) {
    if (cssUrl) return;
    cssUrl = url;
    fetch(url)
      .then((r) => r.text())
      .then((css) => {
        sheet.replaceSync(css);
        cssReady = true;
      })
      .catch(() => {});
  }

  function ensure() {
    if (!host) {
      host = document.createElement("centinela-root");
      host.style.cssText = "all:initial;position:fixed;inset:0;pointer-events:none;z-index:2147483647;";
      shadow = host.attachShadow({ mode: "closed" });
      shadow.adoptedStyleSheets = [sheet];
      if (!cssReady && cssUrl) {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = cssUrl;
        shadow.append(link);
      }
      layer = el("div", "layer");
      shadow.append(layer);
    }
    // Las SPA a veces reemplazan el <body>; colgar de <html> y mantenerlo al final.
    if (!host.isConnected || host.nextSibling) document.documentElement.appendChild(host);
    return layer;
  }

  const isOwn = (node) => !!host && node === host;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function icon(kind) {
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const shieldPath = document.createElementNS(NS, "path");
    shieldPath.setAttribute("d", SHIELD_PATH);
    shieldPath.setAttribute("fill", "currentColor");
    shieldPath.setAttribute("fill-opacity", "0.18");
    shieldPath.setAttribute("stroke", "currentColor");
    shieldPath.setAttribute("stroke-width", "1.6");
    shieldPath.setAttribute("stroke-linejoin", "round");
    const glyph = document.createElementNS(NS, "path");
    glyph.setAttribute("d", GLYPHS[kind] || GLYPHS.ok);
    glyph.setAttribute("fill", "none");
    glyph.setAttribute("stroke", "currentColor");
    glyph.setAttribute("stroke-width", "2");
    glyph.setAttribute("stroke-linecap", "round");
    glyph.setAttribute("stroke-linejoin", "round");
    svg.append(shieldPath, glyph);
    return svg;
  }

  function chip(text, value, level) {
    const c = el("span", `chip${level ? " " + level : ""}`, text);
    if (value) c.append(el("code", null, value));
    return c;
  }

  // ------------------------------------------------------------ escudo en vivo
  const shield = (() => {
    let btn = null;
    let tip = null;
    let last = null;

    function build() {
      ensure();
      btn = el("button", "shield");
      btn.type = "button";
      btn.setAttribute("aria-label", "Centinela: estado del mensaje");
      tip = el("div", "tip");
      tip.setAttribute("role", "tooltip");
      tip.id = "centinela-tip";
      btn.setAttribute("aria-describedby", tip.id);
      const show = () => { place(); tip.classList.add("show"); };
      const hide = () => tip.classList.remove("show");
      btn.addEventListener("mouseenter", show);
      btn.addEventListener("mouseleave", hide);
      btn.addEventListener("focus", show);
      btn.addEventListener("blur", hide);
      btn.addEventListener("mousedown", (e) => e.preventDefault()); // no robar el foco del editor
      layer.append(btn, tip);
    }

    function place() {
      if (!last) return;
      const r = btn.getBoundingClientRect();
      const tw = Math.min(320, Math.max(220, tip.offsetWidth));
      const left = Math.max(8, Math.min(window.innerWidth - tw - 8, r.right - tw));
      const top = r.top - tip.offsetHeight - 8 > 8 ? r.top - tip.offsetHeight - 8 : r.bottom + 8;
      tip.style.left = `${left}px`;
      tip.style.top = `${top}px`;
    }

    function update({ rect, state, items, ms, note }) {
      if (!btn || !btn.isConnected) build();
      ensure();
      last = { rect, state };
      btn.hidden = false;
      btn.dataset.state = state;
      btn.replaceChildren(icon(state));
      const labels = { ok: "sin datos sensibles", warn: "datos personales o término propio", danger: "secreto detectado", off: "pausado" };
      btn.setAttribute("aria-label", `Centinela: ${labels[state] || state}`);
      // Posición: esquina inferior derecha DENTRO del campo activo; si no hay campo, la de la ventana.
      const size = 30;
      let left, top;
      if (rect && rect.width > 60 && rect.height > 20 && rect.bottom > 0 && rect.top < window.innerHeight) {
        left = Math.min(window.innerWidth - size - 8, rect.right - size - 8);
        top = Math.min(window.innerHeight - size - 8, rect.bottom - size - 6);
      } else {
        left = window.innerWidth - size - 20;
        top = window.innerHeight - size - 20;
      }
      btn.style.left = `${Math.max(8, left)}px`;
      btn.style.top = `${Math.max(8, top)}px`;

      tip.replaceChildren();
      tip.append(el("h4", null, "Centinela · evaluación local"));
      if (!items || !items.length) {
        tip.append(el("div", "none", state === "off" ? "Monitoreo pausado" : "Sin datos sensibles"));
      } else {
        const ul = el("ul");
        for (const it of items) {
          const li = el("li");
          const left = el("span");
          left.append(document.createTextNode(typeName(it.type) + " "), el("code", "val", it.masked));
          li.append(left, el("span", `act ${it.action}`, ACTION_NAMES[it.action] || it.action));
          ul.append(li);
        }
        tip.append(ul);
      }
      tip.append(el("div", "foot", note || (ms != null ? `evaluado en ${ms.toFixed(1)} ms · sin salir del navegador` : "")));
      if (tip.classList.contains("show")) place();
    }

    function hide() {
      if (btn) btn.hidden = true;
      if (tip) tip.classList.remove("show");
      last = null;
    }

    return { update, hide };
  })();

  // ------------------------------------------------------------ banner (advertir)
  const banner = (() => {
    let box = null;

    function show({ rect, message, onCensor, onSend, onEdit, censorLabel, focus }) {
      hide();
      ensure();
      box = el("div", "banner");
      box.setAttribute("role", "alert");
      const msg = el("div", "msg");
      msg.append(icon("warn"), el("span", null, message));
      const btns = el("div", "btns");
      const mk = (label, cls, fn) => {
        const b = el("button", `btn ${cls}`, label);
        b.type = "button";
        b.addEventListener("mousedown", (e) => e.preventDefault()); // conservar la selección del editor
        b.addEventListener("click", fn);
        return b;
      };
      const censor = mk(censorLabel || "Censurar", "primary", onCensor);
      btns.append(censor, mk("Enviar así", "ghost", onSend), mk("Editar", "ghost", onEdit));
      box.append(msg, btns);
      box.addEventListener("keydown", (e) => {
        e.stopPropagation();
        if (e.key === "Escape") {
          e.preventDefault();
          hide();
          if (onEdit) onEdit(); // Escape = «Editar»: cerrar y volver al campo
        }
      });
      layer.append(box);
      const h = box.offsetHeight || 48;
      const w = Math.min(640, window.innerWidth - 24);
      const left = rect ? Math.max(12, Math.min(window.innerWidth - w - 12, rect.left)) : 12;
      const top = rect && rect.top - h - 10 > 8 ? rect.top - h - 10 : Math.max(8, (rect ? rect.bottom : window.innerHeight) - h - 60);
      box.style.left = `${left}px`;
      box.style.top = `${top}px`;
      if (focus) censor.focus();
    }

    function hide() {
      if (box) box.remove();
      box = null;
    }

    return { show, hide, visible: () => !!box && box.isConnected };
  })();

  // ------------------------------------------------------------ modal de bloqueo
  const modal = (() => {
    let backdrop = null;
    let handlers = null;

    function open({ title, intro, items, note, censorLabel, onCancel, onCensor, onSend }) {
      close();
      ensure();
      handlers = { onCancel };
      backdrop = el("div", "backdrop");
      const box = el("div", "modal");
      box.setAttribute("role", "alertdialog");
      box.setAttribute("aria-modal", "true");
      box.setAttribute("aria-labelledby", "cn-title");
      box.setAttribute("aria-describedby", "cn-intro");
      const header = el("header");
      const h2 = el("h2", null, title || "Posible fuga de información");
      h2.id = "cn-title";
      header.append(icon("danger"), h2);
      const p = el("p", "intro", intro || "Tu mensaje NO se envió.");
      p.id = "cn-intro";
      box.append(header, p);

      for (const it of items || []) {
        const frag = el("div", "frag");
        frag.append(el("span", "kind", `${typeName(it.type)} · ${it.action === "advertir" ? "advertir" : "bloquear"}`));
        if (it.before) frag.append(document.createTextNode(it.before));
        frag.append(el("mark", it.action === "advertir" ? "warn" : null, it.masked)); // nunca el valor real
        if (it.after) frag.append(document.createTextNode(it.after));
        box.append(frag);
      }
      if (note) box.append(el("p", "note", note));

      const actions = el("div", "actions");
      const mk = (label, cls, fn) => {
        const b = el("button", `btn ${cls}`, label);
        b.type = "button";
        b.addEventListener("click", fn);
        return b;
      };
      const send = mk("Enviar de todos modos", "danger", () => { const h = take(); if (h) onSend(); });
      const censor = mk(censorLabel || "Censurar y enviar", "ghost", () => { const h = take(); if (h) onCensor(); });
      const cancel = mk("Cancelar", "primary", () => cancelModal());
      actions.append(send, censor, cancel);
      box.append(actions);

      // Trampa de foco y teclas: no llegan a la página.
      backdrop.addEventListener("keydown", (e) => {
        e.stopPropagation();
        if (e.key === "Escape") {
          e.preventDefault();
          cancelModal();
          return;
        }
        if (e.key !== "Tab") return;
        e.preventDefault();
        const order = [send, censor, cancel];
        const i = order.indexOf(shadow.activeElement);
        order[(i + (e.shiftKey ? -1 : 1) + order.length) % order.length].focus();
      });
      backdrop.append(box);
      layer.append(backdrop);
      cancel.focus(); // «Cancelar» es el default
    }

    function take() {
      const h = handlers;
      close();
      return h;
    }

    function cancelModal() {
      const h = take();
      if (h && h.onCancel) h.onCancel();
    }

    function close() {
      if (backdrop) backdrop.remove();
      backdrop = null;
      handlers = null;
    }

    return { open, close, cancel: cancelModal, isOpen: () => !!backdrop };
  })();

  // ------------------------------------------------------------ tarjetas y avisos
  let stack = null;
  const MAX_CARDS = 4;

  function ensureStack() {
    ensure();
    if (!stack || !stack.isConnected) {
      stack = el("div", "stack");
      stack.setAttribute("role", "status");
      stack.setAttribute("aria-live", "polite");
      layer.append(stack);
    }
    return stack;
  }

  function dismiss(node) {
    if (!node.isConnected || node.classList.contains("leaving")) return;
    node.classList.add("leaving");
    setTimeout(() => node.remove(), 230);
  }

  /** Tarjeta de resultado: franja por riesgo, chips por hallazgo, categoría, plataforma, latencia. */
  function card({ level = "gris", title, chips = [], meta = [], ttl = 6000 }) {
    const s = ensureStack();
    const node = el("div", `card ${level}`);
    node.title = "Centinela · clic para cerrar";
    const t = el("div", "title");
    const kind = level === "alto" ? "danger" : level === "medio" ? "warn" : level === "bajo" ? "ok" : "info";
    t.append(icon(kind), el("span", null, title));
    node.append(t);
    if (chips.length) {
      const c = el("div", "chips");
      for (const ch of chips) c.append(chip(ch.text, ch.value, ch.level));
      node.append(c);
    }
    if (meta.length) {
      const m = el("div", "meta");
      for (const x of meta) if (x) m.append(el("span", null, x));
      node.append(m);
    }
    node.addEventListener("click", () => dismiss(node));
    s.append(node);
    while (s.children.length > MAX_CARDS) s.firstElementChild.remove();
    setTimeout(() => dismiss(node), ttl);
    return node;
  }

  function toast({ level = "info", title, text, ttl = 7000 }) {
    return card({ level, title, meta: text ? [text] : [], ttl });
  }

  root.CentinelaUI = { init, isOwn, shield, banner, modal, card, toast, typeName };
})(globalThis);
