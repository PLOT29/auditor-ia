/*
 * Centinela · Centro de control (Fase 5B).
 *
 * Datos: GET /stats?detalle=1 (KPIs, gráficas, frase, últimos eventos), GET /health (estado) y
 * GET /events (Server-Sent Events: un evento por cada prompt que /analyze guarda). Todo del mismo
 * origen (la CSP del backend bloquea cualquier otro). El panel NUNCA recibe ni muestra texto de
 * prompts: solo metadatos, tipos de hallazgo y conteos. Nunca escribe en la BD.
 *
 * ?demo=1 → botón «Simular»: inyecta 5 eventos FALSOS al feed (marcados como simulados), sin tocar
 * el backend ni la BD.
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  const C = {
    bg: "#0B1020", surface: "#131A2E", border: "#24304D", text: "#E6EAF2", muted: "#A3ADC2",
    accent: "#22D3EE", safe: "#34D399", warn: "#FBBF24", danger: "#F87171",
  };
  const FONT = '"Segoe UI", system-ui, sans-serif';
  const MONO = '"Cascadia Code", Consolas, ui-monospace, monospace';
  const RISK = {
    bajo: { color: C.safe, icon: "●", label: "Bajo" },
    medio: { color: C.warn, icon: "◆", label: "Medio" },
    alto: { color: C.danger, icon: "▲", label: "Alto" },
  };
  const PLATFORMS = { chatgpt: "ChatGPT", claude: "Claude", gemini: "Gemini", test: "Prueba" };
  const PLATFORM_COLORS = { ChatGPT: "#22D3EE", Claude: "#C084FC", Gemini: "#60A5FA" };
  const TOPICS = {
    programacion: "Programación", academico: "Académico", redaccion: "Redacción", ciberseguridad: "Ciberseguridad",
    consulta_general: "Consulta general", personal: "Personal", incierto: "Incierto", sin_clasificar: "Sin clasificar",
  };
  const MOTIVES = {
    secreto: { label: "Secreto", color: C.danger },
    "dato personal": { label: "Dato personal (PII)", color: C.warn },
    "inyección": { label: "Inyección", color: "#C084FC" },
    "término propio": { label: "Término propio", color: C.accent },
  };
  const DECISIONS = {
    cancelado: "bloqueado · cancelado", censurado: "censurado y enviado", enviado: "bloqueado · enviado igual",
    advertido: "advertido · enviado", no_aplica: "",
  };
  const SECRET_TYPES = new Set([
    "private_key", "openai_key", "anthropic_key", "google_api_key", "stripe_key", "stripe_webhook_secret",
    "aws_access_key", "aws_secret_key", "github_token", "jwt", "bearer_token", "uri_credentials", "password",
    "secret_assignment", "credit_card",
  ]);
  const TYPE_NAMES = {}; // se llena con /stats (catálogo del backend)
  const FEED_MAX = 40;

  // ------------------------------------------------------------ estado
  const state = {
    data: null,
    charts: {},
    feedIds: new Set(),
    latencies: [], // ms desde que /analyze publica hasta que el evento aparece en el feed
    refreshTimer: null,
    demo: new URLSearchParams(location.search).get("demo") === "1",
  };
  window.__centinelaPanel = state; // inspección/medición (sin datos sensibles: no hay texto)

  const store = {
    get(k, d) { try { const v = localStorage.getItem(`centinela.${k}`); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(`centinela.${k}`, JSON.stringify(v)); } catch { /* modo privado */ } },
  };

  // ------------------------------------------------------------ utilidades
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const fmtInt = (n) => Math.round(n).toLocaleString("es-MX");
  const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
  const typeName = (t) => TYPE_NAMES[t] || t;

  function animateNumber(node, to, fmt) {
    const from = Number(node.dataset.value || 0);
    node.dataset.value = to;
    const render = (v) => (node.textContent = fmt === "pct" ? `${v.toFixed(1)} %` : fmtInt(v));
    if (reduced.matches || from === to) return render(to);
    const t0 = performance.now();
    const dur = 900;
    const step = (t) => {
      const k = Math.min(1, (t - t0) / dur);
      const e = 1 - Math.pow(1 - k, 3);
      render(from + (to - from) * e);
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function setDelta(node, d, unit = "") {
    node.className = "delta mono";
    if (d == null || d === 0) { node.textContent = d === 0 ? "= que el periodo anterior" : ""; return; }
    node.classList.add(d > 0 ? "up" : "down");
    node.textContent = `${d > 0 ? "+" : ""}${unit === "pp" ? d.toFixed(1) : fmtInt(d)}${unit === "pp" ? " pp" : ""} vs. periodo anterior`;
  }

  function pill(id, level, text, title = "") {
    const p = $(id);
    p.className = `pill ${level}`;
    p.querySelector(".txt").textContent = text;
    if (title) p.title = title;
  }

  function showAlert(msg) {
    const a = $("alert");
    a.hidden = !msg;
    a.textContent = msg || "";
  }

  // ------------------------------------------------------------ reloj y estado del backend
  function tick() {
    $("clock").textContent = new Date().toLocaleTimeString("es-MX", { hour12: false });
  }

  async function checkHealth() {
    try {
      const r = await fetch("/health", { cache: "no-store" });
      const h = await r.json();
      pill("st-backend", "ok", "Backend conectado");
      const models = h.models || {};
      const names = { embeddings: "embeddings", topic: "tema", injection: "inyección", presidio: "Presidio" };
      const off = Object.entries(models).filter(([, v]) => !v.loaded).map(([k]) => names[k] || k);
      if (!off.length) pill("st-models", "ok", "Modelos cargados", "embeddings, tema, inyección y Presidio");
      else pill("st-models", "warn", `Modo degradado (${off.length})`, `Sin cargar: ${off.join(", ")}`);
    } catch {
      pill("st-backend", "bad", "Backend no disponible");
      pill("st-models", "", "Modelos: sin datos");
    }
  }

  // ------------------------------------------------------------ filtros
  function initRange() {
    const today = new Date();
    const saved = store.get("rango", null);
    $("f-fin").value = saved ? saved.fin : isoDay(today);
    $("f-inicio").value = saved ? saved.inicio : isoDay(addDays(today, -13));
    markPreset(saved ? saved.preset : "14");
    document.querySelectorAll(".chip-btn[data-days]").forEach((b) =>
      b.addEventListener("click", () => {
        const days = b.dataset.days;
        const end = new Date();
        let start = addDays(end, -(Number(days) - 1));
        if (days === "todo") {
          const r = state.data && state.data.rango_datos;
          start = r && r[0] ? new Date(`${r[0]}T00:00:00`) : addDays(end, -364);
        }
        $("f-inicio").value = isoDay(start);
        $("f-fin").value = isoDay(end);
        markPreset(days);
        saveRange(days);
        load();
      })
    );
    for (const id of ["f-inicio", "f-fin"]) {
      $(id).addEventListener("change", () => {
        if ($("f-inicio").value > $("f-fin").value) $("f-fin").value = $("f-inicio").value;
        markPreset(null);
        saveRange(null);
        load();
      });
    }
  }
  const markPreset = (days) =>
    document.querySelectorAll(".chip-btn[data-days]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.days === days)));
  const saveRange = (preset) => store.set("rango", { inicio: $("f-inicio").value, fin: $("f-fin").value, preset });

  function initPresentation() {
    const on = store.get("presentacion", true);
    $("presentacion").checked = on;
    document.body.classList.toggle("presentacion", on);
    $("presentacion").addEventListener("change", (e) => {
      document.body.classList.toggle("presentacion", e.target.checked);
      store.set("presentacion", e.target.checked);
      Object.values(state.charts).forEach((c) => c.resize());
    });
  }

  // ------------------------------------------------------------ carga de /stats
  async function load() {
    const qs = new URLSearchParams({ detalle: "1", inicio: $("f-inicio").value, fin: $("f-fin").value });
    try {
      const r = await fetch(`/stats?${qs}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      state.data = j.panel;
      showAlert(null);
      render(j.panel);
    } catch (err) {
      showAlert(`No se pudo leer /stats (${err.message}). Se muestran los últimos datos; se reintenta en 10 s.`);
      clearTimeout(state.refreshTimer);
      state.refreshTimer = setTimeout(load, 10000);
    }
  }

  // Tras cada evento en vivo se recalculan KPIs y gráficas (agrupado: como mucho una vez por segundo).
  let reloadTimer = null;
  const scheduleReload = () => {
    clearTimeout(reloadTimer);
    reloadTimer = setTimeout(load, 1000);
  };

  // ------------------------------------------------------------ render
  function render(p) {
    Object.assign(TYPE_NAMES, p.nombres_tipos || {});
    for (const t of p.tipos_hallazgo || []) TYPE_NAMES[t.tipo] = t.nombre;
    for (const li of document.querySelectorAll("#feed .chip[data-type]")) li.textContent = typeName(li.dataset.type);
    const k = p.kpis.actual;
    const d = p.kpis.delta;
    animateNumber($("k-total"), k.total, "int");
    animateNumber($("k-pct"), k.pct_sensibles, "pct");
    animateNumber($("k-evitados"), k.evitados, "int");
    animateNumber($("k-censurados"), k.censurados, "int");
    $("k-plataforma").textContent = k.plataforma_top ? PLATFORMS[k.plataforma_top] || k.plataforma_top : "—";
    setDelta($("d-total"), d.total);
    setDelta($("d-pct"), d.pct_sensibles, "pp");
    setDelta($("d-evitados"), d.evitados);
    setDelta($("d-censurados"), d.censurados);
    $("d-plataforma").textContent = k.tema_top ? `tema top: ${TOPICS[k.tema_top] || k.tema_top}` : "";

    renderHeadline(p);
    renderGauge(p.exposicion);
    renderSankey(p.sankey);
    renderHeat(p.mapa_calor);
    renderTrend(p.tendencia);
    renderTypes(p.tipos_hallazgo);
    renderMotives(p.motivos);
    if (!$("feed").children.length) for (const ev of [...p.recientes].reverse()) addEvent(ev, { initial: true });
    updateFeedEmpty();
  }

  function renderHeadline(p) {
    const h = p.hallazgo;
    const f = $("frase");
    f.replaceChildren();
    if (!h || !h.total) {
      f.textContent = "Todavía no hay prompts en este periodo.";
    } else {
      // La frase de la fase 5, con las cifras resaltadas (texto construido con nodos, nunca innerHTML).
      const plat = PLATFORMS[h.plataforma_mas_riesgo] || h.plataforma_mas_riesgo;
      f.append("El ", el("b", null, `${h.pct_pii} %`), " de tus prompts contenían datos personales; la plataforma con más riesgo fue ",
        el("b", null, plat), " (", el("b", null, `${h.pct_riesgo_plataforma} %`), " de sus prompts con riesgo medio o alto).");
    }
    const fl = p.filtros;
    $("periodo").textContent = `Periodo ${fl.inicio} → ${fl.fin} · ${fmtInt(p.kpis.actual.total)} prompts`;
  }

  // ------------------------------------------------------------ ECharts
  function chart(id) {
    if (!window.echarts) return null;
    if (!state.charts[id]) state.charts[id] = echarts.init($(id), null, { renderer: "canvas" });
    return state.charts[id];
  }
  const base = () => ({
    animation: !reduced.matches,
    animationDuration: 700,
    textStyle: { fontFamily: FONT, color: C.text, fontSize: 14 },
    tooltip: { backgroundColor: "#0E1528", borderColor: C.accent, textStyle: { color: C.text, fontFamily: FONT } },
  });
  const axisStyle = {
    axisLine: { lineStyle: { color: C.border } }, axisTick: { show: false },
    axisLabel: { color: C.muted, fontSize: 13 }, splitLine: { lineStyle: { color: "#1C2642" } },
  };

  function empty(id, isEmpty, msg = "Sin datos en este periodo") {
    const card = $(id).closest(".card");
    let note = card.querySelector(".empty-note");
    if (isEmpty && !note) {
      note = el("div", "empty-note", msg);
      card.append(note);
    } else if (!isEmpty && note) note.remove();
    else if (note) note.textContent = msg;
  }

  function renderGauge(exp) {
    const tip = $("exp-tip");
    tip.replaceChildren(el("strong", null, "Cómo se calcula"), el("br"), el("code", null, exp.formula), el("br"),
      `Bandas: 0–20 bajo · 20–50 moderado · 50–100 alto. Periodo: ${fmtInt(exp.por_riesgo.medio)} medio, ${fmtInt(exp.por_riesgo.alto)} alto de ${fmtInt(exp.total)}.`);
    const v = exp.valor;
    const band = v < 20 ? ["baja", C.safe, "●"] : v < 50 ? ["moderada", C.warn, "◆"] : ["alta", C.danger, "▲"];
    const lab = $("exp-label");
    lab.textContent = exp.total ? `${band[2]} Exposición ${band[0]}` : "Sin eventos en el periodo";
    lab.style.color = exp.total ? band[1] : C.muted;
    const c = chart("c-gauge");
    if (!c) return;
    c.setOption({
      ...base(),
      series: [{
        type: "gauge", min: 0, max: 100, startAngle: 210, endAngle: -30, radius: "96%", center: ["50%", "58%"],
        axisLine: { lineStyle: { width: 18, color: [[0.2, C.safe], [0.5, C.warn], [1, C.danger]] } },
        pointer: { width: 6, length: "62%", itemStyle: { color: C.text } },
        anchor: { show: true, size: 14, itemStyle: { color: C.text } },
        axisTick: { show: false }, splitLine: { length: 18, lineStyle: { color: C.bg, width: 3 } },
        axisLabel: { color: C.muted, distance: 24, fontSize: 12, fontFamily: MONO },
        detail: { valueAnimation: !reduced.matches, formatter: (x) => x.toFixed(0), color: C.text, fontSize: 40, fontFamily: MONO, fontWeight: 700, offsetCenter: [0, "38%"] },
        data: [{ value: v }],
      }],
    }, true);
  }

  function renderSankey(s) {
    const c = chart("c-sankey");
    empty("c-sankey", !s.links.length);
    if (!c) return;
    const riskColor = { "Riesgo bajo": C.safe, "Riesgo medio": C.warn, "Riesgo alto": C.danger };
    c.setOption({
      ...base(),
      tooltip: { ...base().tooltip, trigger: "item", formatter: (p) => (p.dataType === "edge" ? `${p.data.source} → ${p.data.target}: ${p.data.value}` : `${p.name}: ${p.value}`) },
      series: [{
        type: "sankey", left: 8, right: 110, top: 8, bottom: 8, nodeWidth: 14, nodeGap: 10, draggable: false,
        emphasis: { focus: "adjacency" },
        data: s.nodes.map((n) => ({ name: n.name, itemStyle: { color: riskColor[n.name] || PLATFORM_COLORS[n.name] || "#64748B", borderColor: C.surface } })),
        links: s.links,
        lineStyle: { color: "gradient", opacity: 0.35, curveness: 0.5 },
        label: { color: C.text, fontSize: 14, fontFamily: FONT },
      }],
    }, true);
  }

  function renderHeat(h) {
    const c = chart("c-heat");
    const data = [];
    let max = 0;
    h.valores.forEach((row, y) => row.forEach((v, x) => { data.push([x, y, v]); max = Math.max(max, v); }));
    empty("c-heat", max === 0);
    if (!c) return;
    c.setOption({
      ...base(),
      tooltip: { ...base().tooltip, formatter: (p) => `${h.dias[p.value[1]]} ${String(p.value[0]).padStart(2, "0")}:00 · ${p.value[2]} prompts` },
      grid: { left: 44, right: 10, top: 8, bottom: 52 },
      xAxis: { type: "category", data: h.horas.map((x) => String(x).padStart(2, "0")), ...axisStyle, splitArea: { show: false }, axisLabel: { ...axisStyle.axisLabel, interval: 2 } },
      yAxis: { type: "category", data: h.dias, inverse: true, ...axisStyle },
      visualMap: { min: 0, max: Math.max(1, max), calculable: false, orient: "horizontal", left: "center", bottom: 0, itemHeight: 140, itemWidth: 10,
        inRange: { color: ["#16213D", "#0E7490", "#22D3EE"] }, textStyle: { color: C.muted } },
      series: [{ type: "heatmap", data, itemStyle: { borderColor: C.surface, borderWidth: 2, borderRadius: 3 } }],
    }, true);
  }

  function renderTrend(t) {
    const c = chart("c-trend");
    empty("c-trend", !t.some((d) => d.bajo + d.medio + d.alto));
    if (!c) return;
    c.setOption({
      ...base(),
      tooltip: { ...base().tooltip, trigger: "axis", axisPointer: { type: "shadow" } },
      legend: { top: 0, right: 0, textStyle: { color: C.muted }, itemWidth: 12, itemHeight: 12,
        data: ["bajo", "medio", "alto"].map((r) => ({ name: `${RISK[r].icon} ${RISK[r].label}` })) },
      grid: { left: 36, right: 8, top: 30, bottom: 26 },
      xAxis: { type: "category", data: t.map((d) => d.dia.slice(8, 10) + "/" + d.dia.slice(5, 7)), ...axisStyle },
      yAxis: { type: "value", minInterval: 1, ...axisStyle },
      series: ["bajo", "medio", "alto"].map((r) => ({
        name: `${RISK[r].icon} ${RISK[r].label}`, type: "bar", stack: "r", barMaxWidth: 26,
        itemStyle: { color: RISK[r].color }, data: t.map((d) => d[r]),
      })),
    }, true);
  }

  function renderTypes(types) {
    const c = chart("c-types");
    const top = (types || []).slice(0, 8).reverse();
    empty("c-types", !top.length, "Sin hallazgos en este periodo");
    if (!c) return;
    c.setOption({
      ...base(),
      tooltip: { ...base().tooltip, trigger: "axis", axisPointer: { type: "shadow" } },
      grid: { left: 8, right: 34, top: 6, bottom: 6, containLabel: true },
      xAxis: { type: "value", minInterval: 1, ...axisStyle, axisLabel: { show: false } },
      yAxis: { type: "category", data: top.map((t) => t.nombre), ...axisStyle, axisLabel: { color: C.text, fontSize: 13, width: 150, overflow: "truncate" } },
      series: [{
        type: "bar", barMaxWidth: 18, data: top.map((t) => ({ value: t.prompts, itemStyle: { color: SECRET_TYPES.has(t.tipo) ? C.danger : C.warn, borderRadius: [0, 4, 4, 0] } })),
        label: { show: true, position: "right", color: C.text, fontFamily: MONO },
      }],
    }, true);
  }

  function renderMotives(m) {
    const c = chart("c-motives");
    const data = Object.entries(MOTIVES).map(([k, v]) => ({ name: v.label, value: m[k] || 0, itemStyle: { color: v.color } }));
    const total = data.reduce((a, d) => a + d.value, 0);
    empty("c-motives", total === 0, "Sin eventos de riesgo");
    if (!c) return;
    c.setOption({
      ...base(),
      tooltip: { ...base().tooltip, trigger: "item", formatter: (p) => `${p.name}: ${p.value} (${p.percent}%)` },
      legend: { bottom: 0, left: "center", textStyle: { color: C.muted, fontSize: 12 }, itemWidth: 10, itemHeight: 10, itemGap: 8 },
      series: [{
        type: "pie", radius: ["48%", "74%"], center: ["50%", "44%"], avoidLabelOverlap: true,
        itemStyle: { borderColor: C.surface, borderWidth: 3 },
        label: { show: true, position: "inside", formatter: (p) => (p.value ? p.value : ""), color: C.bg, fontWeight: 700, fontFamily: MONO, fontSize: 14 },
        data,
      }],
      graphic: [{ type: "text", left: "center", top: "39%", style: { text: fmtInt(total), fill: C.text, font: `700 22px ${MONO}`, textAlign: "center" } }],
    }, true);
  }

  // ------------------------------------------------------------ feed en vivo
  function addEvent(ev, { initial = false } = {}) {
    const key = `${ev.simulado ? "sim" : "ev"}-${ev.id}`;
    if (state.feedIds.has(key)) return; // nunca duplicar (p. ej. tras una reconexión)
    state.feedIds.add(key);
    const r = RISK[ev.riesgo] || RISK.bajo;
    const li = el("li", `ev ${ev.riesgo}${ev.simulado ? " sim" : ""}`);
    if (initial) li.style.animation = "none";
    const top = el("div", "ev-top");
    top.append(el("span", "ev-risk", `${r.icon} ${r.label}`), el("span", "ev-plat", PLATFORMS[ev.plataforma] || ev.plataforma),
      el("span", "ev-time", ev.hora));
    const motive = (ev.motivo || []).join(" · ");
    const line = el("div", "ev-line");
    line.append(`${TOPICS[ev.categoria] || ev.categoria || "sin clasificar"}${motive ? " · " + motive : ""}`);
    const tech = el("span", "tech", ` · #${ev.id}${ev.latencia_ms != null ? ` · ${ev.latencia_ms} ms` : ""}`);
    line.append(tech);
    const chips = el("div", "ev-chips");
    for (const t of ev.tipos || []) {
      const chip = el("span", `chip ${SECRET_TYPES.has(t) ? "danger" : "warn"}`, typeName(t));
      chip.dataset.type = t;
      chips.append(chip);
    }
    if (DECISIONS[ev.decision]) chips.append(el("span", "chip dec", DECISIONS[ev.decision]));
    if (ev.simulado) chips.append(el("span", "chip sim", "SIMULADO"));
    li.append(top, line);
    if (chips.children.length) li.append(chips);
    li.setAttribute("aria-label", `${r.label}, ${PLATFORMS[ev.plataforma] || ev.plataforma}, ${ev.hora}${motive ? ", " + motive : ""}`);
    const list = $("feed");
    list.prepend(li);
    while (list.children.length > FEED_MAX) list.lastElementChild.remove();
    updateFeedEmpty();
    if (!initial && ev.bloqueado) flash();
  }

  function updateFeedEmpty() {
    const n = $("feed").children.length;
    $("feed-empty").hidden = n > 0;
    $("feed-count").textContent = n ? `${n} recientes` : "";
  }

  function flash() {
    const f = document.querySelector(".flash");
    f.classList.remove("on");
    void f.offsetWidth; // reinicia la animación
    f.classList.add("on");
    setTimeout(() => f.classList.remove("on"), reduced.matches ? 600 : 1150);
  }

  function connectFeed() {
    if (!("EventSource" in window)) {
      pill("st-feed", "bad", "Feed no soportado");
      return;
    }
    const es = new EventSource("/events");
    pill("st-feed", "warn", "Feed conectando…");
    es.addEventListener("open", () => {
      pill("st-feed", "ok", "Feed en vivo");
      document.querySelector(".feed").classList.remove("off");
    });
    es.addEventListener("prompt", (e) => {
      const ev = JSON.parse(e.data);
      addEvent(ev);
      if (ev.publicado_ms) state.latencies.push(Date.now() - ev.publicado_ms);
      scheduleReload();
    });
    es.addEventListener("error", () => {
      // EventSource reconecta solo ante errores de red (retry: 3000 del servidor). Si el navegador lo
      // da por cerrado (p. ej. una respuesta no-200 mientras el backend arranca), se recrea aquí.
      pill("st-feed", "bad", "Feed reconectando…");
      document.querySelector(".feed").classList.add("off");
      if (es.readyState === EventSource.CLOSED) setTimeout(connectFeed, 3000);
    });
  }

  // ------------------------------------------------------------ modo demo (?demo=1)
  function initDemo() {
    if (!state.demo) return;
    const btn = $("simular");
    btn.hidden = false;
    let seq = 0;
    btn.addEventListener("click", () => {
      // 5 eventos FALSOS, solo en esta pantalla: no se llama al backend ni se escribe en la BD.
      const fake = [
        { plataforma: "chatgpt", riesgo: "alto", tipos: ["openai_key"], motivo: ["secreto"], categoria: "programacion", decision: "cancelado", bloqueado: true },
        { plataforma: "claude", riesgo: "medio", tipos: ["email", "phone_mx"], motivo: ["dato personal"], categoria: "redaccion", decision: "advertido", bloqueado: false },
        { plataforma: "gemini", riesgo: "bajo", tipos: [], motivo: [], categoria: "academico", decision: "no_aplica", bloqueado: false },
        { plataforma: "chatgpt", riesgo: "alto", tipos: ["aws_access_key", "aws_secret_key"], motivo: ["secreto"], categoria: "programacion", decision: "censurado", bloqueado: true },
        { plataforma: "claude", riesgo: "alto", tipos: [], motivo: ["inyección"], categoria: "ciberseguridad", decision: "no_aplica", bloqueado: false },
      ];
      fake.forEach((f, i) =>
        setTimeout(() => {
          const now = new Date();
          addEvent({ ...f, id: `S${++seq}`, hora: now.toLocaleTimeString("es-MX", { hour12: false }), latencia_ms: 100 + i * 7, simulado: true });
        }, i * 900)
      );
    });
  }

  // ------------------------------------------------------------ arranque
  function resizeAll() {
    Object.values(state.charts).forEach((c) => c.resize());
  }

  function start() {
    tick();
    setInterval(tick, 1000);
    initPresentation();
    initRange();
    initDemo();
    if (!window.echarts) showAlert("No se pudo cargar la librería de gráficas (vendor/echarts). Los KPIs y el feed siguen funcionando.");
    checkHealth();
    setInterval(checkHealth, 10000);
    load();
    setInterval(load, 60000); // además de recargar tras cada evento en vivo
    connectFeed();
    window.addEventListener("resize", resizeAll);
    $("exp-info").addEventListener("click", () => $("exp-tip").classList.toggle("show"));
    $("exp-info").addEventListener("keydown", (e) => { if (e.key === "Escape") $("exp-tip").classList.remove("show"); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
