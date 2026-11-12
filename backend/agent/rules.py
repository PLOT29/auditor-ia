"""Nivel 1 «Sin IA» (Fase 5C): responde con reglas usando las MISMAS herramientas cerradas.

Es el nivel por defecto y el respaldo de los niveles 2 y 3 (si la IA falla, contesta este): la demo
nunca queda rota. Reconoce la intención por palabras clave (resumen, riesgo por plataforma, hallazgos,
bloqueos, temas, tendencia, comparación), el periodo ("hoy", "ayer", "esta semana", "semana pasada",
"este mes", "últimos N días") y la plataforma. Las peticiones de leer prompts se rechazan sin llamar a
ninguna herramienta: Centinela no tiene acceso a ese texto.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

import analytics
from agent import tools

SUGERIDAS = [
    "¿Cuántos prompts hice en los últimos 14 días y cuántos tenían datos sensibles?",
    "¿Qué plataforma tiene más riesgo?",
    "¿Qué tipos de hallazgo aparecen más?",
    "¿Cuántos envíos bloqueó Centinela esta semana?",
    "¿De qué temas pregunto más?",
    "Compara esta semana con la semana pasada",
]

PLATFORM_NAMES = {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini"}
REFUSAL = ("No puedo mostrar ni leer el texto de ningún prompt: Centinela no lo guarda en claro y el "
           "asistente solo tiene acceso a cifras agregadas (conteos, porcentajes, tipos de hallazgo). "
           "Puedo decirte, por ejemplo, cuántos prompts tenían datos sensibles o qué plataforma tuvo más riesgo.")


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def wants_prompt_text(q: str) -> bool:
    """¿Pide ver/leer el contenido de prompts o la base? (y no una cifra)"""
    asks_content = re.search(r"\b(muestr\w*|mostrar|lee\w*|leer|ensen\w*|dame|ver|copia\w*|imprime|dime que (decia|dijo)|"
                             r"contenido|texto|text_masked|select|sql|volcad\w*|dump)\b", q)
    about_prompts = re.search(r"\b(prompts?|mensajes?|texto|base( de datos)?|bd|tabla|registros?|conversacion\w*|instrucciones)\b", q)
    asks_count = re.search(r"\b(cuant\w*|porcentaje|numero|total|cifras?|estadistic\w*)\b", q)
    injection = re.search(r"\b(ignora\w*|olvida\w*)\b.*\b(instrucciones|reglas)\b", q)
    return bool(injection or (asks_content and about_prompts and not asks_count))


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def parse_period(q: str) -> tuple[date, date, str]:
    t = tools.today()
    if "ayer" in q:
        d = t - timedelta(days=1)
        return d, d, "ayer"
    if "hoy" in q:
        return t, t, "hoy"
    if re.search(r"semana pasada|semana anterior", q):
        s = _week_start(t) - timedelta(days=7)
        return s, s + timedelta(days=6), "la semana pasada"
    if "esta semana" in q or "la semana" in q:
        return _week_start(t), t, "esta semana"
    if "este mes" in q or "el mes" in q:
        return t.replace(day=1), t, "este mes"
    m = re.search(r"ultim\w*\s+(\d{1,3})\s+dias?", q)
    if m:
        n = max(1, min(int(m.group(1)), tools.MAX_DAYS))
        return t - timedelta(days=n - 1), t, f"los últimos {n} días"
    if re.search(r"\b(todo|siempre|historico|total)\b", q):
        return t - timedelta(days=tools.MAX_DAYS - 1), t, "todo el historial (último año)"
    return t - timedelta(days=tools.DEFAULT_DAYS - 1), t, f"los últimos {tools.DEFAULT_DAYS} días"


def parse_platform(q: str) -> str | None:
    for p in PLATFORM_NAMES:
        if p in q:
            return p
    return None


def intent(q: str) -> str:
    if re.search(r"compar|vs\.?|versus|frente a|contra la", q):
        return "comparar_periodos"
    if re.search(r"bloque|cancel|censur|evit", q):
        return "bloqueos"
    if re.search(r"plataforma", q) and re.search(r"riesg|peligr|segur|expuest", q):
        return "riesgo_por_plataforma"
    if re.search(r"cuant\w* prompts|resumen|en general|como voy", q):
        return "resumen_periodo"
    if re.search(r"tipo|hallazg|secreto|api key|clave|correo|curp|rfc|dni|direccion|datos personales", q):
        return "hallazgos_por_tipo"
    if re.search(r"tema|categor|sobre que|de que", q):
        return "temas_frecuentes"
    if re.search(r"tendencia|por dia|evolucion|cada dia|diari", q):
        return "tendencia"
    return "resumen_periodo"


def _n(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _pct(n: float) -> str:
    return f"{n:g} %"


def _period_text(label: str, start: date, end: date) -> str:
    return f"{label} ({start.isoformat()} → {end.isoformat()})" if start != end else f"{label} ({start.isoformat()})"


def answer(question: str):
    """Generador de eventos: {"type": "tool", ...} y {"type": "delta", "text": ...}."""
    q = _norm(question or "")
    if wants_prompt_text(q):
        yield {"type": "delta", "text": REFUSAL}
        return
    start, end, label = parse_period(q)
    plat = parse_platform(q)
    kind = intent(q)
    period = _period_text(label, start, end)
    where = f" en {PLATFORM_NAMES[plat]}" if plat else ""

    def run(name, args):
        args = {k: v for k, v in args.items() if v is not None}
        result = tools.call(name, args)
        yield_ev = {"type": "tool", "nombre": name, "argumentos": args, "ok": True}
        return result, yield_ev

    if kind == "comparar_periodos":
        t = tools.today()
        if re.search(r"mes", q):
            b0 = t.replace(day=1)
            a1 = b0 - timedelta(days=1)
            a0, b1, la, lb = a1.replace(day=1), t, "el mes pasado", "este mes"
        else:
            b0 = _week_start(t)
            a0, a1, b1, la, lb = b0 - timedelta(days=7), b0 - timedelta(days=1), t, "la semana pasada", "esta semana"
        args = {"desde_a": a0.isoformat(), "hasta_a": a1.isoformat(), "desde_b": b0.isoformat(),
                "hasta_b": b1.isoformat(), "plataforma": plat}
        r, ev = run("comparar_periodos", args)
        yield ev
        a, b, d = r["periodo_a"], r["periodo_b"], r["diferencia_b_menos_a"]
        sign = lambda x: f"+{x:g}" if x > 0 else f"{x:g}"  # noqa: E731
        text = (f"{lb.capitalize()}{where}: {b['prompts']} prompts, {_pct(b['pct_con_datos_sensibles'])} con datos "
                f"sensibles y {b['bloqueos']} bloqueos. {la.capitalize()}: {a['prompts']} prompts, "
                f"{_pct(a['pct_con_datos_sensibles'])} con datos sensibles y {a['bloqueos']} bloqueos. "
                f"Diferencia: {sign(d['prompts'])} prompts, {sign(d['pct_con_datos_sensibles'])} puntos de datos "
                f"sensibles y {sign(d['bloqueos'])} bloqueos; el índice de exposición pasó de "
                f"{a['indice_exposicion']:g} a {b['indice_exposicion']:g}.")
    elif kind == "riesgo_por_plataforma":
        r, ev = run("riesgo_por_plataforma", {"desde": start.isoformat(), "hasta": end.isoformat()})
        yield ev
        rows = [(p, v) for p, v in r["plataformas"].items() if v["total"]]
        if not rows:
            text = f"No hay prompts registrados en {period}."
        else:
            worst = max(rows, key=lambda x: (x[1]["pct_medio_o_alto"], x[1]["alto"]))
            parts = [f"{PLATFORM_NAMES[p]}: {_pct(v['pct_medio_o_alto'])} de {v['total']} prompts "
                     f"({v['alto']} de riesgo alto)" for p, v in rows]
            text = (f"En {period}, la plataforma con más riesgo es {PLATFORM_NAMES[worst[0]]}, con "
                    f"{_pct(worst[1]['pct_medio_o_alto'])} de sus prompts con riesgo medio o alto. "
                    f"Detalle: {'; '.join(parts)}.")
    elif kind == "hallazgos_por_tipo":
        r, ev = run("hallazgos_por_tipo", {"desde": start.isoformat(), "hasta": end.isoformat(), "plataforma": plat})
        yield ev
        if not r["tipos"]:
            text = f"No hubo hallazgos{where} en {period}."
        else:
            parts = [f"{t['nombre']} ({t['prompts']})" for t in r["tipos"][:5]]
            text = f"Los tipos de hallazgo más frecuentes{where} en {period} fueron: {', '.join(parts)}."
    elif kind == "temas_frecuentes":
        r, ev = run("temas_frecuentes", {"desde": start.isoformat(), "hasta": end.isoformat(), "plataforma": plat})
        yield ev
        if not r["temas"]:
            text = f"No hay prompts{where} en {period}."
        else:
            parts = [f"{t['nombre']} ({t['prompts']})" for t in r["temas"][:5]]
            text = f"Tus temas más frecuentes{where} en {period}: {', '.join(parts)}."
    elif kind == "bloqueos":
        r, ev = run("bloqueos", {"desde": start.isoformat(), "hasta": end.isoformat(), "plataforma": plat})
        yield ev
        d = r["por_decision"]
        if not r["bloqueos"]:
            text = f"Centinela no bloqueó ningún envío{where} en {period}."
        else:
            tipos = ", ".join(f"{analytics.TYPE_NAMES.get(t, t)} ({n})" for t, n in list(r["tipos_en_bloqueos"].items())[:3])
            text = (f"En {period}{where}, Centinela bloqueó {_n(r['bloqueos'], 'envío', 'envíos')}: "
                    f"{_n(d['cancelado'], 'cancelado', 'cancelados')}, {_n(d['censurado'], 'censurado', 'censurados')} y "
                    f"{_n(d['enviado'], 'enviado de todos modos', 'enviados de todos modos')}, es decir "
                    f"{_n(r['evitados'], 'fuga evitada', 'fugas evitadas')}." + (f" Tipos más comunes: {tipos}." if tipos else ""))
    elif kind == "tendencia":
        r, ev = run("tendencia", {"desde": start.isoformat(), "hasta": end.isoformat(), "plataforma": plat})
        yield ev
        days = r["por_dia"]
        totals = [(x["dia"], x["bajo"] + x["medio"] + x["alto"], x["medio"] + x["alto"]) for x in days]
        total = sum(t for _, t, _ in totals)
        if not total:
            text = f"No hay prompts{where} en {period}."
        else:
            peak = max(totals, key=lambda x: x[1])
            risky = sum(r_ for _, _, r_ in totals)
            text = (f"En {period}{where} hubo {total} prompts, {risky} de riesgo medio o alto. El día con más uso "
                    f"fue {peak[0]} ({peak[1]} prompts); {sum(1 for _, t, _ in totals if t)} de {len(totals)} días "
                    f"tuvieron actividad.")
    else:
        r, ev = run("resumen_periodo", {"desde": start.isoformat(), "hasta": end.isoformat(), "plataforma": plat})
        yield ev
        if not r["prompts"]:
            text = f"No hay prompts registrados{where} en {period}."
        else:
            text = (f"En {period}{where} registraste {r['prompts']} prompts; {r['con_datos_sensibles']} "
                    f"({_pct(r['pct_con_datos_sensibles'])}) tenían datos sensibles o riesgo. Centinela bloqueó "
                    f"{r['bloqueos']} envíos ({r['bloqueos_evitados']} fugas evitadas, {r['censurados']} censurados). "
                    f"Índice de exposición: {r['indice_exposicion']:g}/100.")
            if not plat and r["plataforma_principal"]:
                text += f" Plataforma principal: {PLATFORM_NAMES.get(r['plataforma_principal'], r['plataforma_principal'])}."
    yield {"type": "delta", "text": text}
