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
 *
 * Fase 5C: token de sesión (meta centinela-token) en el header X-Centinela-Token de cada petición (en
 * /events, como ?token= porque EventSource no manda headers); detalle de evento (/events/{id}, sin texto
 * en modo presentación); «Pregúntale a Centinela» (/agent/preguntar, NDJSON en streaming) y niveles de IA
 * (/ia/*). Nada de esto escribe en la BD de prompts.
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
  const TOKEN = (document.querySelector('meta[name="centinela-token"]') || {}).content || "";

  /** fetch al backend con el token de sesión (mismo origen; la CSP no permite otro). */
  const api = (path, opts = {}) =>
    fetch(path, { cache: "no-store", ...opts, headers: { ...(opts.headers || {}), "X-Centinela-Token": TOKEN } });

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
      const r = await api("/health");
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
      if (!$("detail").hidden) showDetail(); // el texto aparece o se oculta al momento
      Object.values(state.charts).forEach((c) => c.resize());
    });
  }

  // ------------------------------------------------------------ carga de /stats
  async function load() {
    const qs = new URLSearchParams({ detalle: "1", inicio: $("f-inicio").value, fin: $("f-fin").value });
    try {
      const r = await api(`/stats?${qs}`);
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
    renderTable(p.recientes);
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
    li.setAttribute("aria-label", `${r.label}, ${PLATFORMS[ev.plataforma] || ev.plataforma}, ${ev.hora}${motive ? ", " + motive : ""}. Abrir detalle`);
    li.tabIndex = 0;
    li.setAttribute("role", "button");
    li._ev = ev;
    li.addEventListener("click", () => openDetail("feed", li));
    li.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDetail("feed", li); } });
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
    const es = new EventSource(`/events?token=${encodeURIComponent(TOKEN)}`);
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


  // ================================================================ Fase 5C
  // ------------------------------------------------------------ pestañas (En vivo · Tabla · Pregúntale)
  function initTabs() {
    const tabs = [...document.querySelectorAll('.tabs [role="tab"]')];
    const select = (tab, focus = true) => {
      for (const t of tabs) {
        const on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        $(t.getAttribute("aria-controls")).hidden = !on;
      }
      if (focus) tab.focus();
      store.set("pestana", tab.id);
      if (tab.id === "tab-chat") refreshIA();
    };
    tabs.forEach((t, i) => {
      t.addEventListener("click", () => select(t, false));
      t.addEventListener("keydown", (e) => {
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
          e.preventDefault();
          select(tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length]);
        }
      });
    });
    const saved = tabs.find((t) => t.id === store.get("pestana", "tab-vivo"));
    if (saved) select(saved, false);
  }

  // ------------------------------------------------------------ tabla de eventos
  function renderTable(items) {
    const body = $("tabla").tBodies[0];
    body.replaceChildren();
    for (const ev of items || []) {
      const r = RISK[ev.riesgo] || RISK.bajo;
      const tr = el("tr");
      tr.tabIndex = 0;
      tr._ev = ev;
      const cells = [ev.hora, PLATFORMS[ev.plataforma] || ev.plataforma, `${r.icon} ${r.label}`,
        (ev.motivo || []).join(", ") || "—", DECISIONS[ev.decision] || "—"];
      cells.forEach((c, i) => tr.append(el("td", i === 2 ? `r-${ev.riesgo}` : null, c)));
      tr.setAttribute("aria-label", `${r.label}, ${PLATFORMS[ev.plataforma] || ev.plataforma}, ${ev.hora}. Abrir detalle`);
      tr.addEventListener("click", () => openDetail("tabla", tr));
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDetail("tabla", tr); } });
      body.append(tr);
    }
    $("tabla-empty").hidden = !!(items && items.length);
  }

  // ------------------------------------------------------------ detalle de evento
  const detail = { source: null, node: null, opener: null };
  const SECRET_MK = /KEY|TOKEN|CLAVE|STRIPE|WEBHOOK|URI|SECRET|CONTRASENA|PASSWORD|TARJETA|CARD|JWT|PRIVATE|ORG|AWS|BEARER|API/;
  const TERM_MK = /TERMINO/;
  const MARKER_RE = /<[A-Z_]+>|\[[A-ZÁÉÍÓÚÑ_]+\]/g;

  function detailNodes() {
    return detail.source === "tabla" ? [...$("tabla").tBodies[0].rows] : [...$("feed").children];
  }

  function openDetail(source, node) {
    detail.source = source;
    detail.node = node;
    if (!detail.opener) detail.opener = document.activeElement;
    $("detail").hidden = false;
    $("detail-backdrop").hidden = false;
    showDetail();
    $("detail-close").focus();
  }

  function closeDetail() {
    $("detail").hidden = true;
    $("detail-backdrop").hidden = true;
    document.querySelectorAll(".current").forEach((n) => n.classList.remove("current"));
    const back = detail.node && detail.node.isConnected ? detail.node : detail.opener;
    detail.node = detail.opener = null;
    if (back && back.focus) back.focus();
  }

  function moveDetail(step) {
    const nodes = detailNodes();
    const i = nodes.indexOf(detail.node);
    const next = nodes[i + step];
    if (!next) return;
    detail.node = next;
    showDetail();
  }

  function markedText(container, text) {
    container.replaceChildren();
    let pos = 0;
    for (const m of text.matchAll(MARKER_RE)) {
      container.append(text.slice(pos, m.index));
      const name = m[0].slice(1, -1);
      const kind = TERM_MK.test(name) ? "term" : SECRET_MK.test(name) ? "secret" : "pii";
      const chip = el("span", `mk ${kind}`, m[0]);
      chip.title = kind === "secret" ? "Secreto reemplazado" : kind === "term" ? "Término propio reemplazado" : "Dato personal reemplazado";
      container.append(chip);
      pos = m.index + m[0].length;
    }
    container.append(text.slice(pos));
  }

  function fillDetail(d, simulated = false) {
    const r = RISK[d.riesgo] || RISK.bajo;
    $("detail-title").textContent = simulated ? `Evento simulado ${d.id}` : `Evento #${d.id}`;
    const risk = $("detail-risk");
    risk.className = `risk-line ${d.riesgo}`;
    risk.textContent = `${r.icon} Riesgo ${r.label.toLowerCase()}${(d.motivo || []).length ? " · " + d.motivo.join(" · ") : ""}`;
    const pct = (x) => (x == null ? "—" : `${Math.round(x * 100)} %`);
    const rows = [
      ["Fecha y hora", `${d.fecha || ""} ${d.hora}`.trim()],
      ["Plataforma", PLATFORMS[d.plataforma] || d.plataforma],
      ["Origen", simulated ? "simulado (no existe en la BD)" : d.origen === "import" ? "importado del historial" : "extensión (en vivo)"],
      ["Categoría", `${TOPICS[d.categoria] || d.categoria || "—"}${d.confianza != null ? ` · confianza ${pct(d.confianza)}` : ""}`],
      ["Score de inyección", d.injection_score == null ? "—" : d.injection_score.toFixed(3)],
      ["Decisión", DECISIONS[d.decision] || d.decision || "—"],
      ["Bloqueado", d.bloqueado ? "sí" : "no"],
      ["Latencia", d.latencia_ms == null ? "—" : `${d.latencia_ms} ms`],
    ];
    if (d.longitud != null) rows.push(["Longitud", `${fmtInt(d.longitud)} caracteres`]);
    const dl = $("detail-meta");
    dl.replaceChildren();
    for (const [k, v] of rows) dl.append(el("dt", null, k), el("dd", null, v));

    const ul = $("detail-findings");
    ul.replaceChildren();
    const fs = d.hallazgos || (d.tipos || []).map((t) => ({ type: t, nombre: typeName(t), masked: "—" }));
    if (!fs.length) ul.append(el("li", "muted", "Sin hallazgos"));
    for (const f of fs) {
      const li = el("li");
      li.append(el("span", `chip ${SECRET_TYPES.has(f.type) ? "danger" : "warn"}`, f.nombre || typeName(f.type)), el("code", null, f.masked));
      if (f.valido === false) li.append(el("span", "muted", "letra de control no válida"));
      ul.append(li);
    }

    const box = $("detail-text");
    box.classList.toggle("hidden-text", !d.texto);
    if (simulated) box.textContent = "Evento simulado: no hay texto.";
    else if (d.texto_oculto) box.textContent = "Oculto en modo presentación (desactívalo para ver el texto enmascarado).";
    else if (!d.texto) box.textContent = "(vacío)";
    else markedText(box, d.texto);

    const nodes = detailNodes();
    const i = nodes.indexOf(detail.node);
    $("detail-prev").disabled = i <= 0;
    $("detail-next").disabled = i < 0 || i >= nodes.length - 1;
    document.querySelectorAll(".current").forEach((n) => n.classList.remove("current"));
    if (detail.node) {
      detail.node.classList.add("current");
      detail.node.scrollIntoView({ block: "nearest", behavior: reduced.matches ? "auto" : "smooth" });
    }
  }

  async function showDetail() {
    const ev = detail.node && detail.node._ev;
    if (!ev) return;
    if (ev.simulado) return fillDetail(ev, true);
    fillDetail({ ...ev, texto: null, texto_oculto: true, hallazgos: null }); // inmediato; luego el detalle real
    const presentation = document.body.classList.contains("presentacion");
    try {
      const r = await api(`/events/${encodeURIComponent(ev.id)}?presentacion=${presentation ? 1 : 0}`);
      if (!r.ok) throw new Error(r.status === 404 ? "El evento ya no existe" : `HTTP ${r.status}`);
      if (detail.node && detail.node._ev === ev) fillDetail(await r.json());
    } catch (err) {
      $("detail-text").textContent = `No se pudo leer el detalle (${err.message}).`;
    }
  }

  function initDetail() {
    $("detail-close").addEventListener("click", closeDetail);
    $("detail-backdrop").addEventListener("click", closeDetail);
    $("detail-prev").addEventListener("click", () => moveDetail(-1));
    $("detail-next").addEventListener("click", () => moveDetail(1));
    $("detail").addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); closeDetail(); }
      else if (e.key === "ArrowDown" || e.key === "ArrowRight") { e.preventDefault(); moveDetail(1); }
      else if (e.key === "ArrowUp" || e.key === "ArrowLeft") { e.preventDefault(); moveDetail(-1); }
      else if (e.key === "Tab") trapFocus(e, $("detail"));
    });
  }

  function trapFocus(e, root) {
    const f = [...root.querySelectorAll("button:not([disabled]), input:not([disabled]), a[href], [tabindex='0']")].filter((n) => n.offsetParent !== null);
    if (!f.length) return;
    const first = f[0];
    const last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  // ------------------------------------------------------------ Pregúntale a Centinela
  const chat = { busy: false };

  async function initChat() {
    try {
      const r = await api("/agent/sugeridas");
      const j = await r.json();
      const box = $("sugeridas");
      box.replaceChildren();
      for (const q of j.preguntas || []) {
        const b = el("button", null, q);
        b.type = "button";
        b.addEventListener("click", () => ask(q));
        box.append(b);
      }
    } catch { /* el chat sigue funcionando con preguntas libres */ }
    $("chat-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const q = $("chat-input").value.trim();
      if (q) ask(q);
    });
  }

  async function ask(question) {
    if (chat.busy) return;
    chat.busy = true;
    $("chat-send").disabled = true;
    $("chat-input").value = "";
    const log = $("chat-log");
    log.append(el("div", "msg-q", question));
    const a = el("div", "msg-a pending");
    const answer = el("div", "answer");
    const tools = el("div", "tools");
    a.append(answer);
    log.append(a);
    log.scrollTop = log.scrollHeight;
    const used = [];
    let done = null;
    try {
      const r = await api("/agent/preguntar", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pregunta: question }) });
      if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done: end } = await reader.read();
        if (end) break;
        buf += dec.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) continue;
          const ev = JSON.parse(line);
          if (ev.type === "delta") answer.append(ev.text);
          else if (ev.type === "tool") used.push(ev);
          else if (ev.type === "error") answer.append(ev.text);
          else if (ev.type === "done") done = ev;
          log.scrollTop = log.scrollHeight;
        }
      }
    } catch (err) {
      answer.append(`No pude contactar al asistente (${err.message}).`);
    }
    a.classList.remove("pending");
    // «Herramientas usadas»: nombre y argumentos de cada llamada, para que la respuesta sea auditable.
    tools.append(el("span", null, `Herramientas usadas${done ? ` · ${done.proveedor} · ${fmtInt(done.ms)} ms` : ""}:`));
    const ul = el("ul");
    const list = (done && done.herramientas) || used;
    if (!list.length) ul.append(el("li", null, "ninguna"));
    for (const t of list) {
      const li = el("li", t.ok ? null : "bad");
      li.append(el("code", null, `${t.nombre}(${JSON.stringify(t.argumentos || {})})`), t.ok ? " ✓" : ` ✕ ${t.error || "rechazada"}`);
      ul.append(li);
    }
    tools.append(ul);
    a.append(tools);
    log.scrollTop = log.scrollHeight;
    chat.busy = false;
    $("chat-send").disabled = false;
  }

  // ------------------------------------------------------------ niveles de IA
  const ia = { data: null, poll: null };
  const STEP_ICON = { ok: "✓", error: "✕", en_curso: "⟳", esperando: "?", omitido: "–", pendiente: "·", cancelado: "–" };
  const fmtBytes = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(2)} GB` : `${(n / 1e6).toFixed(0)} MB`);

  async function refreshIA() {
    try {
      const r = await api("/ia/estado");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      ia.data = await r.json();
      renderIA(ia.data);
    } catch {
      pill("st-ia", "", "IA: sin datos");
    }
  }

  function renderIA(d) {
    const label = d.nivel === 2 ? `IA local · ${d.ollama.modelo}` : d.nivel === 3 ? `IA nube · ${d.nube.dominio}` : "Sin IA";
    const p = $("st-ia");
    p.className = `pill pill-btn ${d.nivel === 2 ? "ia2" : d.nivel === 3 ? "ia3" : ""}`;
    p.querySelector(".txt").textContent = `IA: ${label}`;
    $("st-nube").hidden = d.nivel !== 3;
    $("chat-level").textContent = d.nivel === 1 ? "Nivel 1 · Sin IA: respuestas con reglas sobre las mismas herramientas."
      : d.nivel === 2 ? `Nivel 2 · IA local (${d.ollama.modelo}): nada sale de tu computadora.`
        : `Nivel 3 · ${d.nube.dominio}: solo se envían cifras agregadas, nunca prompts.`;
    for (const r of document.querySelectorAll('input[name="nivel"]')) r.checked = Number(r.value) === d.nivel;
    $("ia-model").textContent = `· modelo ${d.ollama.modelo}`;

    const L = d.local;
    const steps = $("ia-steps");
    steps.replaceChildren();
    if (L.estado !== "inactivo") {
      for (const s of L.pasos) {
        const li = el("li", s.estado);
        li.append(el("span", null, STEP_ICON[s.estado] || "·"), el("span", null, s.titulo));
        if (s.detalle) li.append(el("span", "det", s.detalle));
        steps.append(li);
      }
    } else if (d.ollama.lista) {
      steps.append(el("li", "ok", "✓ IA local lista."));
    }
    const busy = L.estado === "en_curso" || L.estado === "esperando_confirmacion";
    $("ia-local-on").disabled = busy;
    $("ia-local-on").textContent = d.ollama.lista ? "Revisar IA local" : "Activar IA local";
    $("ia-local-off").disabled = busy || (!d.ollama.lista && d.nivel !== 2);
    $("ia-confirm").hidden = !L.confirmacion;
    $("ia-confirm-t").textContent = L.confirmacion || "";
    const pr = L.progreso;
    $("ia-progress").hidden = !pr;
    if (pr) {
      const pct = pr.total ? Math.min(100, (100 * pr.completado) / pr.total) : 0;
      $("ia-progress").querySelector(".bar span").style.width = `${pct.toFixed(1)}%`;
      $("ia-progress").querySelector(".bar").setAttribute("aria-valuenow", pct.toFixed(0));
      $("ia-progress-t").textContent = `${pr.estado} · ${fmtBytes(pr.completado)} / ${fmtBytes(pr.total)} (${pct.toFixed(0)} %)`;
    }
    $("ia-out").hidden = !(L.salida && L.salida.length);
    $("ia-out").textContent = (L.salida || []).join("\n");
    $("ia-error").hidden = !L.error;
    if (L.error) {
      $("ia-error-causa").textContent = L.error.causa;
      const ol = $("ia-error-pasos");
      ol.replaceChildren(...L.error.instrucciones.map((t) => el("li", null, t)));
    }
    const n = d.nube;
    $("nube-clave").textContent = n.clave ? `Clave: configurada · ${n.clave}` : "Clave: no configurada";
    if (n.base_url && !$("nube-url").value) $("nube-url").value = n.base_url;
    if (n.modelo && !$("nube-modelo").value) $("nube-modelo").value = n.modelo;
    updateAviso();
    clearInterval(ia.poll);
    if (busy || !$("ia-drawer").hidden) ia.poll = setInterval(refreshIA, busy ? 800 : 3000);
  }

  function nubeDomain() {
    try { return new URL($("nube-url").value.trim()).hostname || "—"; } catch { return "—"; }
  }
  function updateAviso() {
    $("nube-aviso").textContent = `Se enviarán cifras agregadas (nunca prompts) a ${nubeDomain()}`;
  }

  async function iaPost(path, body) {
    const r = await api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : `HTTP ${r.status}`);
    return j;
  }

  function iaMsg(text) { $("ia-msg").textContent = text || ""; }

  function openIA() {
    $("ia-drawer").hidden = false;
    $("ia-backdrop").hidden = false;
    iaMsg("");
    refreshIA();
    $("ia-close").focus();
  }
  function closeIA() {
    $("ia-drawer").hidden = true;
    $("ia-backdrop").hidden = true;
    $("nube-key").value = ""; // la key nunca se queda en el formulario
    $("st-ia").focus();
  }

  function initIA() {
    $("st-ia").addEventListener("click", openIA);
    $("ia-close").addEventListener("click", closeIA);
    $("ia-backdrop").addEventListener("click", closeIA);
    $("ia-drawer").addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); closeIA(); }
      else if (e.key === "Tab") trapFocus(e, $("ia-drawer"));
    });
    for (const r of document.querySelectorAll('input[name="nivel"]')) {
      r.addEventListener("change", async () => {
        try { await iaPost("/ia/nivel", { nivel: Number(r.value) }); iaMsg(""); }
        catch (err) { iaMsg(err.message); }
        refreshIA();
      });
    }
    $("ia-local-on").addEventListener("click", async () => {
      iaMsg("");
      try { await iaPost("/ia/local/activar"); } catch (err) { iaMsg(err.message); }
      refreshIA();
    });
    $("ia-local-off").addEventListener("click", async () => {
      try { await iaPost("/ia/local/desactivar"); iaMsg("IA local desactivada (Ollama sigue instalado)."); } catch (err) { iaMsg(err.message); }
      refreshIA();
    });
    $("ia-confirm-yes").addEventListener("click", async () => { try { await iaPost("/ia/local/confirmar", { aceptar: true }); } catch (err) { iaMsg(err.message); } refreshIA(); });
    $("ia-confirm-no").addEventListener("click", async () => { try { await iaPost("/ia/local/confirmar", { aceptar: false }); } catch (err) { iaMsg(err.message); } refreshIA(); });
    $("nube-url").addEventListener("input", () => { updateAviso(); $("nube-acepto").checked = false; });
    $("ia-nube-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!$("nube-acepto").checked) return iaMsg(`Debes aceptar que se enviarán cifras agregadas a ${nubeDomain()}.`);
      const body = { base_url: $("nube-url").value.trim(), modelo: $("nube-modelo").value.trim(), acepto_envio: true, dominio_aceptado: nubeDomain() };
      const key = $("nube-key").value.trim();
      if (key) body.api_key = key;
      try { await iaPost("/ia/nube/configurar", body); iaMsg("Proveedor configurado y nivel 3 activo."); }
      catch (err) { iaMsg(err.message); }
      $("nube-key").value = "";
      refreshIA();
    });
    $("nube-probar").addEventListener("click", async () => {
      iaMsg("Probando…");
      try { const j = await iaPost("/ia/nube/probar"); iaMsg(`${j.ok ? "✓" : "✕"} ${j.detalle}`); } catch (err) { iaMsg(err.message); }
    });
    $("nube-borrar").addEventListener("click", async () => {
      try { await iaPost("/ia/nube/borrar"); iaMsg("Clave borrada del Administrador de credenciales."); } catch (err) { iaMsg(err.message); }
      $("nube-acepto").checked = false;
      refreshIA();
    });
    refreshIA();
    setInterval(() => { if ($("ia-drawer").hidden) refreshIA(); }, 15000);
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
    initTabs();
    initDetail();
    initChat();
    initIA();
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
