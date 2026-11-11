"""Genera ml/data/topic_synthetic.csv — ~80 prompts sintéticos por categoría (SOLO train).

    python ml/make_topic_synthetic.py

Cada categoría tiene ~36 prompts base escritos a mano + variantes automáticas
(registro informal, sin acentos, faltas de ortografía, saludos/relleno) hasta 80.
Columnas: text,label,source. `source` = "synthetic:<categoria>-<nn>:<variante>";
el id de la base sirve como GRUPO en la validación cruzada (GroupKFold) para que
las variantes de un mismo prompt no queden en train y validación a la vez.

Criterio de etiquetas (también para etiquetar a mano el test real):
  programacion     código, errores, librerías, bases de datos, git, desarrollo de software
  academico        tareas y conceptos de materias escolares (mate, física, historia,
                   biología, economía…), estudiar, exámenes, citas/bibliografía
  redaccion        escribir, corregir, resumir, traducir o cambiar el tono de un texto
  ciberseguridad   ataques, defensas, vulnerabilidades, contraseñas, malware, redes seguras
  consulta_general dudas cotidianas: recetas, viajes, compras, trámites, datos curiosos
  personal         vida propia: emociones, relaciones, salud personal, decisiones, metas
"""
from __future__ import annotations

import csv
import random
import unicodedata
from pathlib import Path

SEED = 42
PER_CLASS = 80
OUT = Path(__file__).resolve().parent / "data" / "topic_synthetic.csv"

BASE: dict[str, list[str]] = {
    "programacion": [
        "¿Cómo invierto una lista en Python sin usar reverse()?",
        "Me sale 'TypeError: cannot read properties of undefined' en React cuando hago map, ¿por qué?",
        "Explícame la diferencia entre let, const y var en JavaScript",
        "Escribe una función en Java que valide si una cadena es palíndromo",
        "¿Cómo hago un JOIN entre dos tablas en SQL para traer clientes con sus pedidos?",
        "Mi código de C++ da segmentation fault al recorrer un vector, aquí está: for(int i=0;i<=v.size();i++)",
        "¿Qué es una API REST y cómo la consumo con fetch?",
        "Ayúdame a optimizar este query de Postgres que tarda 10 segundos",
        "¿Cómo resuelvo un conflicto de merge en git?",
        "Convierte este bucle for en una list comprehension: for x in datos: if x>0: res.append(x*2)",
        "¿Para qué sirve el decorador @staticmethod en Python?",
        "Hazme un script en bash que renombre todos los .jpg de una carpeta",
        "¿Cuál es la complejidad de búsqueda binaria y cómo la implemento?",
        "Cómo conecto mi app de Flask a una base de datos MySQL",
        "Tengo un error 'ModuleNotFoundError: No module named pandas' aunque ya lo instalé",
        "Explica qué hace async/await en JavaScript con un ejemplo",
        "¿Cómo creo un Dockerfile para una app de Node?",
        "Quiero centrar un div vertical y horizontalmente con CSS",
        "Diferencia entre clase abstracta e interfaz en programación orientada a objetos",
        "¿Cómo leo un archivo CSV con pandas y saco el promedio de una columna?",
        "Mi página en Django no carga los archivos estáticos en producción",
        "Escribe pruebas unitarias con pytest para una función que suma dos números",
        "¿Qué es la recursión? Dame un ejemplo con factorial en C",
        "¿Cómo hago un endpoint POST en FastAPI que reciba un JSON?",
        "El build de mi proyecto de Android Studio falla por una versión de Gradle",
        "Refactoriza este código para que no tenga tantos if anidados",
        "¿Cómo uso useEffect para llamar una API solo una vez?",
        "Necesito una expresión regular que valide correos electrónicos",
        "¿Qué patrón de diseño uso para crear objetos de distintos tipos según un parámetro?",
        "Cómo paso de una lista de diccionarios a un DataFrame",
        "Mi programa en Python se queda sin memoria al leer un archivo de 5 GB",
        "¿Cómo despliego mi app de Next.js en Vercel?",
        "Explícame qué es el big O de un algoritmo de ordenamiento por burbuja",
        "Haz una clase Carro en Python con atributos marca, modelo y un método arrancar",
        "¿Por qué mi variable de entorno no se lee en el contenedor de Docker?",
        "Quiero aprender TypeScript, ¿qué tipos básicos debo conocer?",
    ],
    "academico": [
        "Explícame el teorema de Pitágoras con un ejemplo",
        "¿Cuáles fueron las causas de la Revolución Mexicana?",
        "Resuelve la integral de x por e a la x",
        "¿Qué es la fotosíntesis y qué productos genera?",
        "Ayúdame a entender la ley de la oferta y la demanda para mi examen de economía",
        "¿Cómo se calcula la desviación estándar de una muestra?",
        "Hazme un resumen de la Segunda Guerra Mundial para estudiar",
        "¿Qué diferencia hay entre mitosis y meiosis?",
        "Explica la segunda ley de Newton con ejercicios resueltos",
        "¿Cómo cito un artículo científico en formato APA 7?",
        "¿Qué es el método científico y cuáles son sus pasos?",
        "Dame ejercicios de derivadas para practicar con sus respuestas",
        "¿Quién fue Sor Juana Inés de la Cruz y por qué es importante?",
        "Explícame qué es un número primo y cómo saber si 97 lo es",
        "¿Cuál es la estructura de una célula eucariota?",
        "Tengo examen de química mañana, explícame cómo balancear ecuaciones",
        "¿Qué fue la Ilustración y qué filósofos la representan?",
        "Explica la teoría de la relatividad de forma sencilla",
        "¿Cómo se hace una tabla de frecuencias en estadística?",
        "Diferencia entre PIB nominal y PIB real",
        "¿Qué es el ADN y cómo se replica?",
        "Explícame el concepto de límite en cálculo",
        "¿Cuáles son las partes de un ensayo académico y cómo se estructura un marco teórico?",
        "Dame un resumen del libro Pedro Páramo para mi clase de literatura",
        "¿Qué es la entropía en termodinámica?",
        "Explica la diferencia entre correlación y causalidad",
        "¿Cómo funciona el sistema inmunológico?",
        "Resuelve este sistema de ecuaciones: 2x + y = 7, x - y = 2",
        "¿Cuáles son los tipos de investigación cualitativa?",
        "Explícame qué es una matriz inversa y cómo se calcula",
        "¿Qué fue la Guerra Fría?",
        "Ayúdame con mi tarea de probabilidad: lanzo dos dados, ¿probabilidad de que sumen 7?",
        "¿Qué son los enlaces covalentes e iónicos?",
        "Explica el ciclo de Krebs paso a paso",
        "¿Cómo planteo la hipótesis de mi tesis de licenciatura?",
        "¿Qué es la inflación y cómo la mide el Banco de México?",
    ],
    "redaccion": [
        "Corrige la ortografía y la gramática de este párrafo: haber si nos vemos mañana para ablar del proyecto",
        "Redacta un correo formal para pedirle una prórroga a mi profesor",
        "Hazme más formal este mensaje: oye, ¿me pasas el reporte cuando puedas?",
        "Escribe una carta de presentación para un puesto de practicante en marketing",
        "Resume este texto en tres líneas",
        "Mejora la redacción de mi introducción, se lee muy repetitiva",
        "Parafrasea este párrafo para que no parezca copiado",
        "Escríbeme un poema corto sobre el otoño",
        "Traduce al inglés este correo para un cliente",
        "Ayúdame a escribir el discurso de graduación, que dure dos minutos",
        "Hazme una publicación para LinkedIn anunciando mi nuevo trabajo",
        "¿Cómo puedo hacer que esta conclusión suene más contundente?",
        "Escribe una reseña de 150 palabras de la película que vi ayer",
        "Revisa la puntuación de este texto, creo que abuso de las comas",
        "Redacta un mensaje para cancelar una reunión de forma amable",
        "Haz un cuento infantil de un perro que aprende a nadar",
        "Escribe la descripción de un producto para mi tienda en línea de velas aromáticas",
        "Convierte estas notas en un párrafo bien redactado",
        "Escríbeme un mensaje de felicitación de cumpleaños para mi jefa",
        "¿Está bien escrito 'a ver' o 'haber' en esta oración?",
        "Haz que este texto suene más profesional para un reporte de la empresa",
        "Redacta una queja formal a la aerolínea por pérdida de equipaje",
        "Acorta este párrafo a la mitad sin perder la idea principal",
        "Escribe un guion de 30 segundos para un video de Instagram",
        "Corrige este texto y explícame los errores que tenía",
        "Escribe un titular llamativo para un artículo sobre ahorro",
        "Dame tres versiones distintas del asunto de este correo",
        "Redacta el agradecimiento de mi tesis",
        "Pasa este texto de primera a tercera persona",
        "Escribe un ensayo de opinión de una cuartilla sobre el uso de celulares en clase",
        "Mejora el tono de este correo, suena muy agresivo",
        "Hazme un resumen ejecutivo de este informe para el director",
        "Escribe un mensaje de bienvenida para el grupo de WhatsApp del curso",
        "Ayúdame a escribir mi biografía para la página de la empresa",
        "Revisa si este párrafo tiene coherencia y cohesión",
        "Redacta una invitación para la fiesta de fin de año de la oficina",
    ],
    "ciberseguridad": [
        "¿Qué es un ataque de phishing y cómo lo detecto?",
        "Explica qué es una inyección SQL y cómo se previene",
        "¿Qué diferencia hay entre cifrado simétrico y asimétrico?",
        "¿Cómo configuro la autenticación de dos factores en mi correo?",
        "¿Qué es el ransomware y qué hago si mi empresa es infectada?",
        "Explícame qué es un firewall y para qué sirve",
        "¿Qué es el OWASP Top 10?",
        "¿Cómo sé si mi contraseña fue filtrada en una brecha de datos?",
        "¿Qué es un ataque de denegación de servicio distribuido?",
        "Explica qué es XSS y cómo protejo mi formulario web",
        "¿Cómo funciona una VPN y realmente me protege en un wifi público?",
        "¿Qué es el principio de mínimo privilegio?",
        "¿Qué hace un analista de un SOC?",
        "Diferencia entre IDS e IPS",
        "¿Cómo funciona el protocolo TLS y qué es un certificado digital?",
        "¿Qué es la ingeniería social?",
        "¿Cómo creo una contraseña segura que pueda recordar?",
        "¿Qué es un hash y por qué se usa sal (salt) al guardar contraseñas?",
        "¿Qué es una vulnerabilidad de día cero?",
        "Explica el modelo de confianza cero (zero trust)",
        "¿Qué es MITRE ATT&CK y para qué sirve?",
        "¿Cómo protejo mi red wifi de casa?",
        "¿Qué es un análisis de vulnerabilidades y en qué se diferencia de un pentest?",
        "¿Qué datos personales protege la LFPDPPP en México?",
        "¿Qué es un keylogger y cómo lo detecto en mi computadora?",
        "Explica la tríada CIA: confidencialidad, integridad y disponibilidad",
        "¿Qué es el prompt injection en modelos de lenguaje?",
        "¿Cómo respondo ante un incidente de seguridad en una pyme?",
        "¿Qué es un gestor de contraseñas y es seguro usarlo?",
        "¿Para qué sirve Wireshark en el análisis de tráfico?",
        "¿Qué es una botnet?",
        "¿Cómo funciona la firma digital?",
        "¿Qué controles recomienda la ISO 27001?",
        "¿Qué es el DLP o prevención de pérdida de datos?",
        "Me llegó un SMS del banco con un link raro, ¿es fraude?",
        "¿Qué es el cross-site request forgery?",
    ],
    "consulta_general": [
        "¿Cuál es la receta de unos chilaquiles verdes?",
        "¿Qué lugares me recomiendas visitar en Oaxaca en tres días?",
        "¿Cuánto tiempo se hierve un huevo para que quede tibio?",
        "¿Qué celular de gama media me recomiendas comprar este año?",
        "¿Cómo saco mi pasaporte mexicano por primera vez?",
        "¿Cuál es la capital de Australia?",
        "¿Cómo quito una mancha de café de una camisa blanca?",
        "¿Qué películas de ciencia ficción me recomiendas?",
        "¿Cuántos kilómetros hay de la Ciudad de México a Guadalajara?",
        "¿Cómo cuido una planta de suculenta?",
        "¿Qué es mejor para viajar a Europa, euros en efectivo o tarjeta?",
        "Dame ideas de regalo para el día del padre",
        "¿Cómo se juega el dominó?",
        "¿A qué hora es el partido de la selección hoy?",
        "¿Qué significa la expresión 'estar en la luna'?",
        "¿Cómo hago un pastel de chocolate sin horno?",
        "¿Cuál es la diferencia entre un café americano y un espresso?",
        "Recomiéndame un libro para leer en vacaciones",
        "¿Cómo tramito la credencial del INE si la perdí?",
        "¿Qué necesito para rentar un coche en Estados Unidos?",
        "¿Cuántas calorías tiene un plátano?",
        "¿Por qué el cielo es azul?",
        "¿Cómo organizo una fiesta infantil con poco presupuesto?",
        "¿Qué es mejor, una freidora de aire o un horno eléctrico?",
        "Dame una lista de compras para la semana para dos personas",
        "¿Cómo limpio la pantalla de mi televisión sin rayarla?",
        "¿Qué documentos piden para abrir una cuenta de banco?",
        "¿Qué series están de moda en Netflix?",
        "¿Cuál es el animal más rápido del mundo?",
        "¿Cómo se dice 'gracias' en japonés?",
        "¿Dónde puedo reciclar pilas en la CDMX?",
        "¿Qué ropa llevo para ir a la playa en diciembre?",
        "¿Cómo calculo la propina en un restaurante?",
        "¿Cuál es la mejor época para visitar Chiapas?",
        "¿Cómo armo un mueble de IKEA si perdí el instructivo?",
        "¿Qué raza de perro es buena para un departamento pequeño?",
    ],
    "personal": [
        "Me siento muy estresado con la escuela y no sé cómo organizarme",
        "Terminé con mi pareja y no puedo dejar de pensar en ella, ¿qué hago?",
        "¿Cómo le digo a mis papás que quiero cambiarme de carrera?",
        "No puedo dormir bien desde hace semanas, ¿qué me recomiendas?",
        "Quiero bajar de peso, ayúdame a armar una rutina de ejercicio para mí",
        "Tengo ansiedad antes de mis presentaciones, ¿cómo la controlo?",
        "Mi mejor amigo se enojó conmigo y no sé por qué",
        "¿Cómo puedo ahorrar si gano 12 mil pesos al mes?",
        "Me siento solo desde que me mudé de ciudad",
        "Quiero dejar de procrastinar, siempre dejo todo para el final",
        "¿Debería aceptar una oferta de trabajo que paga más pero está lejos de mi casa?",
        "Mi jefe me trata mal y ya no sé si renunciar",
        "Ayúdame a planear mis metas para el próximo año",
        "Me cuesta mucho hablar con gente nueva, ¿cómo hago amigos?",
        "Estoy pensando en adoptar un gato, pero no sé si tengo tiempo",
        "¿Cómo le pido perdón a mi hermana después de una pelea fuerte?",
        "Me duele la cabeza todas las tardes, ¿puede ser por el estrés?",
        "No tengo motivación para nada últimamente",
        "¿Cómo manejo los celos en mi relación?",
        "Quiero empezar a meditar, ¿por dónde empiezo?",
        "Mis compañeros de cuarto no limpian y ya me cansé",
        "¿Cómo organizo mi día para estudiar y trabajar al mismo tiempo?",
        "Me da miedo no encontrar trabajo cuando me gradúe",
        "Mi abuela está enferma y me siento muy triste",
        "¿Cómo puedo mejorar mi autoestima?",
        "Quiero aprender a cocinar para comer más sano",
        "Mi novia quiere que vivamos juntos y no sé si estoy listo",
        "Gasto demasiado en comida a domicilio, ¿cómo lo controlo?",
        "¿Es normal sentirme agotado aunque duerma ocho horas?",
        "Quiero hablar con mi papá de algo que me molesta pero siempre terminamos peleando",
        "¿Cómo dejo de revisar el celular a cada rato?",
        "Me siento presionado por mi familia para casarme",
        "¿Qué hago si mi roomie no me paga su parte de la renta?",
        "Quiero empezar a correr, pero me canso muy rápido",
        "Hoy fue un día muy malo y solo quiero desahogarme",
        "¿Cómo pongo límites con un amigo que siempre me pide dinero?",
    ],
}

FILLERS_START = ["oye, ", "una pregunta: ", "hola, ", "buenas, ", "porfa, ", "necesito ayuda: ",
                 "Buenas tardes. ", "Hola! ", "ayudame con algo: ", "duda rápida: "]
FILLERS_END = [" porfa", " gracias", " xfa", " :)", " es urgente", " please", "?", " plis", "", ""]


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def typo(s: str, rng: random.Random) -> str:
    """1–2 faltas: letra omitida, duplicada o intercambiada en palabras largas."""
    words = s.split(" ")
    idx = [i for i, w in enumerate(words) if len(w) > 4 and w.isalpha()]
    for i in rng.sample(idx, min(len(idx), rng.choice([1, 2]))):
        w = words[i]
        j = rng.randrange(1, len(w) - 1)
        op = rng.choice(["drop", "dup", "swap"])
        if op == "drop":
            w = w[:j] + w[j + 1:]
        elif op == "dup":
            w = w[:j] + w[j] + w[j:]
        else:
            w = w[:j - 1] + w[j] + w[j - 1] + w[j + 1:]
        words[i] = w
    return " ".join(words)


def informal(s: str, rng: random.Random) -> str:
    s = strip_accents(s).lower().replace("¿", "").replace("¡", "")
    return rng.choice(FILLERS_START) + s + rng.choice(FILLERS_END)


def filler(s: str, rng: random.Random) -> str:
    return rng.choice(FILLERS_START) + s[0].lower() + s[1:] + rng.choice(FILLERS_END)


VARIANTS = {
    "informal": informal,
    "typo": typo,
    "informal_typo": lambda s, r: typo(informal(s, r), r),
    "relleno": filler,
    "sin_acentos": lambda s, r: strip_accents(s),
}


def build() -> list[dict]:
    rng = random.Random(SEED)
    rows = []
    for label, bases in BASE.items():
        seen = set()
        out = []
        for i, text in enumerate(bases):
            out.append({"text": text, "label": label, "source": f"synthetic:{label}-{i:02d}:original"})
            seen.add(text)
        tries = 0
        while len(out) < PER_CLASS and tries < 10_000:
            tries += 1
            i = rng.randrange(len(bases))
            name = rng.choice(list(VARIANTS))
            text = VARIANTS[name](bases[i], rng)
            if text in seen:
                continue
            seen.add(text)
            out.append({"text": text, "label": label, "source": f"synthetic:{label}-{i:02d}:{name}"})
        rows += out
    rng.shuffle(rows)
    return rows


def main() -> None:
    rows = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["text", "label", "source"])
        w.writeheader()
        w.writerows(rows)
    counts = {}
    for r in rows:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
    print(f"{len(rows)} prompts -> {OUT}")
    print(counts)


if __name__ == "__main__":
    main()
