"""Dashboard en vivo del Auditor de Prompts (Fase 5) — solo interfaz.

    streamlit run dashboard/app.py              (o: python dashboard/run.py)

Todas las consultas están en queries.py (SQLite en solo lectura). Este archivo no tiene SQL,
nunca escribe en la BD y nunca muestra texto de prompts con el "Modo presentación" activado.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import queries as Q  # noqa: E402

BACKEND_HEALTH = "http://127.0.0.1:8000/health"
REFRESH_S = 3

# Colores fijos (validados para daltonismo con el validador de la skill dataviz).
PLATFORM_COLORS = {"chatgpt": "#009E73", "claude": "#AA3377", "gemini": "#0072B2"}
RISK_COLORS = {"bajo": "#56B4E9", "medio": "#E69F00", "alto": "#D55E00"}
NEUTRAL = "#4B5563"
MUTED = "#9CA3AF"
PLATFORM_NAMES = {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini"}
TOPIC_NAMES = {"programacion": "Programación", "academico": "Académico", "redaccion": "Redacción",
               "ciberseguridad": "Ciberseguridad", "consulta_general": "Consulta general", "personal": "Personal",
               Q.UNCERTAIN: "Incierto", Q.UNCLASSIFIED: "Sin clasificar"}
TYPE_NAMES = {"openai_key": "API key OpenAI", "anthropic_key": "API key Anthropic", "aws_access_key": "Clave AWS",
              "github_token": "Token GitHub", "google_api_key": "API key Google", "jwt": "JWT",
              "private_key": "Clave privada", "password": "Contraseña", "credit_card": "Tarjeta",
              "email": "Correo", "phone_mx": "Teléfono", "curp": "CURP", "rfc": "RFC", "person": "Nombre",
              "address": "Dirección"}
FONT = dict(family="Segoe UI, Arial, sans-serif", size=15, color="#111827")

st.set_page_config(page_title="Auditor de Prompts", page_icon="🛡️", layout="wide")

# ------------------------------------------------------------------ privacidad (RNF-03)
if st.get_option("browser.gatherUsageStats") or st.get_option("server.address") not in ("127.0.0.1", "localhost"):
    st.error("Configuración insegura: la telemetría de Streamlit está activa o el servidor no escucha solo en "
             "localhost. Lánzalo con `python dashboard/run.py`, que fuerza `server.address=127.0.0.1` y "
             "`browser.gatherUsageStats=false`.")
    st.stop()

st.markdown("""<style>
  [data-testid="stMetricValue"] { font-size: 2.1rem; }
  /* valores largos ("Consulta general") hacen salto de línea en vez de cortarse con "…" */
  [data-testid="stMetricValue"] [data-testid="stMarkdownContainer"] { overflow: visible !important; }
  [data-testid="stMetricValue"] p { white-space: normal !important; line-height: 1.15; }
  [data-testid="stMetricLabel"] p { font-size: 1.05rem; }
  .hallazgo { font-size: 1.45rem; line-height: 1.5; padding: .9rem 1.2rem; border-left: 6px solid #0072B2;
              background: #F2F4F7; border-radius: 6px; }
  .dot { display:inline-block; width:.8rem; height:.8rem; border-radius:50%; margin-right:.35rem; vertical-align:middle; }
</style>""", unsafe_allow_html=True)

DB = Q.db_path()
THR = Q.confidence_threshold()


# ------------------------------------------------------------------ filtros (no se refrescan)
with st.sidebar:
    st.header("🛡️ Auditor de Prompts")
    bounds = Q.date_bounds(DB)
    today = date.today()
    last = max(bounds[1], today) if bounds else today
    first = bounds[0] if bounds else today - timedelta(days=13)
    default = (max(first, last - timedelta(days=13)), last)
    picked = st.date_input("Rango de fechas (hora de la Ciudad de México)", value=default,
                           min_value=min(first, default[0]), max_value=last + timedelta(days=1), format="DD/MM/YYYY")
    start, end = (picked if isinstance(picked, (tuple, list)) and len(picked) == 2 else (default[0], default[1]))
    platforms = st.multiselect("Plataforma", Q.PLATFORMS, default=Q.PLATFORMS,
                               format_func=lambda p: PLATFORM_NAMES[p])
    source = st.radio("Origen", ["ambos", "extension", "import"], horizontal=True,
                      format_func={"ambos": "Ambos", "extension": "Extensión", "import": "Importado"}.get)
    presentation = st.toggle("Modo presentación", value=True,
                             help="Oculta TODO texto de prompts (aunque esté enmascarado) para proyectar.")
    st.caption(f"BD: `{DB}` (solo lectura)")
    st.caption("Umbral de tema incierto: " + (f"{THR:.2f}" if THR is not None else "no disponible"))

F = Q.Filters(start=start, end=end, platforms=tuple(platforms), source=source)


# ------------------------------------------------------------------ utilidades de UI
def backend_status() -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(BACKEND_HEALTH, timeout=0.8) as r:
            data = json.loads(r.read())
        models = data.get("models", {})
        off = [k for k, v in models.items() if not v.get("loaded")]
        return bool(data.get("ok")), ("modelos: todos cargados" if not off else f"sin: {', '.join(off)}")
    except Exception:  # noqa: BLE001 — cualquier fallo = backend no disponible
        return False, "backend no disponible"


def style(fig: go.Figure, height: int = 340) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=40, b=10), font=FONT,
                      plot_bgcolor="white", paper_bgcolor="white", title_font=dict(size=18),
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1, title=None),
                      hoverlabel=dict(font_size=14))
    fig.update_xaxes(showgrid=False, linecolor="#D1D5DB")
    fig.update_yaxes(gridcolor="#EEF0F3", zeroline=False)
    return fig


def empty_note(fig: go.Figure, msg: str = "Sin datos en este periodo") -> go.Figure:
    fig.add_annotation(text=msg, showarrow=False, font=dict(size=16, color=MUTED), xref="paper", yref="paper",
                       x=0.5, y=0.5)
    return fig


def delta_txt(value, unit: str = "") -> str | None:
    if value == 0:
        return "= igual que antes"
    return f"{value:+g}{unit} vs. anterior"


# ------------------------------------------------------------------ contenido en vivo
@st.fragment(run_every=REFRESH_S)
def live(F: Q.Filters, presentation: bool) -> None:
    ok, detail = backend_status()
    color = "#009E73" if ok else "#D55E00"
    st.markdown(f"<span class='dot' style='background:{color}'></span>"
                f"Backend {'en línea' if ok else 'apagado'} · {detail} · "
                f"Última actualización: <b>{datetime.now():%H:%M:%S}</b> · "
                f"{F.start:%d/%m/%Y} – {F.end:%d/%m/%Y}", unsafe_allow_html=True)

    # ---------------- KPIs
    k = Q.kpis(DB, F, THR)
    cur, d = k["actual"], k["delta"]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Prompts", f"{cur['total']:,}", delta_txt(d["total"]))
    c2.metric("Con datos sensibles", f"{cur['pct_sensibles']:g} %", delta_txt(d["pct_sensibles"], " pp"),
              delta_color="inverse",
              help="Riesgo medio + alto: secretos, PII y también detecciones de inyección/jailbreak. "
                   "El % de prompts con datos personales está en el Hallazgo.")
    c3.metric("Envíos bloqueados", cur["bloqueados"], delta_txt(d["bloqueados"]), delta_color="inverse",
              help="Bloqueados por la extensión antes de salir")
    c3.caption(f"Cancelados: **{cur['cancelados']}** · enviados de todos modos: **{cur['enviados_igual']}**")
    c4.metric("Plataforma más usada", PLATFORM_NAMES.get(cur["plataforma_top"], "—"))
    prev_plat = k["anterior"]["plataforma_top"]
    c4.caption(f"Periodo anterior: {PLATFORM_NAMES.get(prev_plat, '—')}")
    c5.metric("Tema más frecuente", TOPIC_NAMES.get(cur["tema_top"], "—"))
    c5.caption(f"Periodo anterior: {TOPIC_NAMES.get(k['anterior']['tema_top'], '—')}")

    # ---------------- Hallazgo (golpe 3 de la demo)
    h = Q.headline(DB, F, names=PLATFORM_NAMES)
    st.markdown(f"<div class='hallazgo'>💡 <b>Hallazgo:</b> {h['frase']}</div>", unsafe_allow_html=True)
    st.write("")

    # ---------------- Gráficas
    g1, g2 = st.columns(2)
    bp = Q.by_platform(DB, F).assign(nombre=lambda x: x["platform"].map(PLATFORM_NAMES))
    fig = px.bar(bp, x="nombre", y="prompts", color="platform", color_discrete_map=PLATFORM_COLORS, text="prompts",
                 title="Prompts por plataforma")
    fig.update_traces(textposition="outside", cliponaxis=False, hovertemplate="%{x}: %{y} prompts<extra></extra>",
                      marker_line_width=0)
    fig = style(fig).update_layout(showlegend=False, xaxis_title=None, yaxis_title="prompts", bargap=0.45)
    g1.plotly_chart(empty_note(fig) if bp["prompts"].sum() == 0 else fig, use_container_width=True, key="plat")

    tp = Q.topics(DB, F, THR)
    tp = tp.assign(nombre=tp["tema"].map(lambda t: TOPIC_NAMES.get(t, t)),
                   color=tp["tema"].map(lambda t: MUTED if t in (Q.UNCERTAIN, Q.UNCLASSIFIED) else NEUTRAL))
    fig = go.Figure(go.Bar(x=tp["prompts"], y=tp["nombre"], orientation="h", marker_color=tp["color"],
                           text=tp["prompts"], textposition="outside", cliponaxis=False,
                           hovertemplate="%{y}: %{x} prompts<extra></extra>"))
    fig = style(fig).update_layout(title="Temas más frecuentes (gris: incierto / sin clasificar)",
                                   yaxis=dict(autorange="reversed"), xaxis_title="prompts")
    g2.plotly_chart(empty_note(fig) if tp.empty else fig, use_container_width=True, key="topics")

    g3, g4 = st.columns(2)
    rp = Q.risk_by_platform(DB, F).assign(nombre=lambda x: x["platform"].map(PLATFORM_NAMES))
    fig = px.bar(rp, x="nombre", y="prompts", color="risk", color_discrete_map=RISK_COLORS,
                 category_orders={"risk": Q.RISKS}, text="prompts", title="Riesgo por plataforma",
                 labels={"risk": "riesgo"})
    fig.update_traces(texttemplate="%{text:.0f}", textposition="inside", textangle=0, insidetextanchor="middle",
                      marker_line_color="white", marker_line_width=2,
                      hovertemplate="%{x} · riesgo %{fullData.name}: %{y}<extra></extra>")
    fig.for_each_trace(lambda t: t.update(text=[v if v else None for v in t.text]))
    fig = style(fig).update_layout(barmode="stack", xaxis_title=None, yaxis_title="prompts", bargap=0.45,
                                   uniformtext=dict(minsize=12, mode="hide"))  # oculta etiquetas que no caben
    g3.plotly_chart(empty_note(fig) if rp["prompts"].sum() == 0 else fig, use_container_width=True, key="risk")

    hm = Q.heatmap(DB, F)
    fig = go.Figure(go.Heatmap(z=hm.values, x=[f"{h:02d}" for h in hm.columns], y=list(hm.index),
                               colorscale=[[0, "#F7FBFF"], [0.15, "#C6DBEF"], [0.5, "#4292C6"], [1, "#08306B"]],
                               xgap=2, ygap=2, colorbar=dict(title="prompts", thickness=12),
                               hovertemplate="%{y} %{x}:00 · %{z} prompts<extra></extra>"))
    fig = style(fig).update_layout(title="Cuándo usas la IA (día × hora, Ciudad de México)",
                                   yaxis=dict(autorange="reversed"), xaxis_title="hora")
    g4.plotly_chart(empty_note(fig) if hm.values.sum() == 0 else fig, use_container_width=True, key="heat")

    g5, g6 = st.columns(2)
    tr = Q.daily_trend(DB, F)
    fig = go.Figure()
    fig.add_scatter(x=tr["dia"], y=tr["prompts"], mode="lines+markers", name="Todos", line=dict(color=NEUTRAL, width=2),
                    marker=dict(size=8), hovertemplate="%{x|%d/%m}: %{y} prompts<extra>Todos</extra>")
    fig.add_scatter(x=tr["dia"], y=tr["riesgosos"], mode="lines+markers", name="Riesgo medio o alto",
                    line=dict(color=RISK_COLORS["alto"], width=2), marker=dict(size=8),
                    hovertemplate="%{x|%d/%m}: %{y} riesgosos<extra>Riesgo medio o alto</extra>")
    fig = style(fig).update_layout(title="Tendencia diaria", hovermode="x unified", yaxis_title="prompts",
                                   xaxis=dict(tickformat="%d/%m"))
    g5.plotly_chart(empty_note(fig) if tr["prompts"].sum() == 0 else fig, use_container_width=True, key="trend")

    ft = Q.finding_types(DB, F)
    ft = ft.assign(nombre=ft["tipo"].map(lambda t: TYPE_NAMES.get(t, t)))
    fig = go.Figure(go.Bar(x=ft["prompts"], y=ft["nombre"], orientation="h", marker_color=NEUTRAL,
                           text=ft["prompts"], textposition="outside", cliponaxis=False,
                           hovertemplate="%{y}: %{x} prompts<extra></extra>"))
    fig = style(fig).update_layout(title="Tipos de hallazgo más comunes", yaxis=dict(autorange="reversed"),
                                   xaxis_title="prompts con ese hallazgo")
    g6.plotly_chart(empty_note(fig) if ft.empty else fig, use_container_width=True, key="types")

    # ---------------- Alertas
    st.subheader("Alertas recientes (riesgo medio o alto)")
    alerts = Q.recent_alerts(DB, F, limit=20, include_text=not presentation, threshold=THR)
    if alerts.empty:
        st.info("Sin alertas en este periodo.")
    else:
        alerts = alerts.assign(plataforma=alerts["plataforma"].map(PLATFORM_NAMES),
                               tema=alerts["tema"].map(lambda t: TOPIC_NAMES.get(t, t)),
                               riesgo=alerts["riesgo"].map({"medio": "⚠️ medio", "alto": "⛔ alto"}),
                               hallazgos=alerts["hallazgos"].map(
                                   lambda s: ", ".join(TYPE_NAMES.get(t.strip(), t.strip()) for t in s.split(","))
                                   if s != "—" else s))
        st.dataframe(alerts, hide_index=True, use_container_width=True)
    if presentation:
        st.caption("🔒 Modo presentación: el texto de los prompts está oculto.")


live(F, presentation)
