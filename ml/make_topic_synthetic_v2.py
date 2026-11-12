"""Genera ml/data/topic_synthetic_v2.csv — ~150 prompts sintéticos por categoría (Fase 4C).

    python ml/make_topic_synthetic_v2.py

Sigue ml/LABELING.md. Fuentes de candidatos (todas con seed fija):
  1. Las bases v1 (make_topic_synthetic.BASE), revisadas con la guía.
  2. Bases nuevas escritas a mano, con énfasis en la FRONTERA: tecnología sin seguridad en
     consulta_general / academico / programacion, y ciberseguridad bien específica.
  3. Plantillas con listas de temas (hardware, redes, materias, lenguajes, amenazas…): mucha
     variedad de contenido con la misma intención.
  4. Variantes: informal, sin acentos, faltas de ortografía, spanglish, pregunta ↔ instrucción,
     con contexto extra (más largas) o recortadas (más cortas).
Filtros (embeddings MiniLM normalizados):
  - casi duplicados: se descarta un candidato si su coseno con uno ya aceptado es > 0.95;
  - probe set: se descarta si coincide o su coseno con algún prompt del probe es > 0.90.
Columnas: text,label,source. source = "v2:<categoria>:<grupo>:<variante>"; el grupo (id de la
base o de la plantilla) se usa en GroupKFold: todas las instancias de una plantilla caen en el
mismo fold, para que la CV no premie memorizar la plantilla.
"""
from __future__ import annotations

import csv
import random
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ML = Path(__file__).resolve().parent
sys.path.insert(0, str(ML))
sys.stdout.reconfigure(encoding="utf-8")

SEED = 42
PER_CLASS = 150
MAX_NON_VARIANT = 115  # originales + plantillas por clase; el resto (≥ 35) se reserva a variantes de registro
DUP_COS = 0.95
PROBE_COS = 0.90
OUT = ML / "data" / "topic_synthetic_v2.csv"

# ------------------------------------------------------------------ 1. bases v1 revisadas
# (texto v1 → nueva etiqueta) cuando LABELING.md decide distinto. El resto se conserva.
V1_RELABEL = {
    "Explícame qué es el big O de un algoritmo de ordenamiento por burbuja": "academico",
    "Diferencia entre clase abstracta e interfaz en programación orientada a objetos": "academico",
    "¿Qué es el prompt injection en modelos de lenguaje?": "ciberseguridad",
}

# ------------------------------------------------------------------ 2. bases nuevas (frontera)
NEW = {
    "consulta_general": [
        "Explícame qué hace un procesador y por qué importa cuántos núcleos tiene",
        "¿Vale la pena comprar un monitor de 144 Hz para trabajar?",
        "¿Qué es mejor para una oficina pequeña, laptop o computadora de escritorio?",
        "Mi celular se calienta mucho cuando lo cargo, ¿es normal?",
        "¿Cómo paso mis fotos del iPhone a una computadora con Windows?",
        "¿Qué significa que un televisor sea 4K HDR?",
        "¿Cuál es la diferencia entre megas y gigas en el plan de internet?",
        "Mi internet es muy lento en las noches, ¿por qué pasa eso?",
        "¿Qué es un repetidor de wifi y sirve de verdad?",
        "¿Cómo funciona la fibra óptica?",
        "¿Qué es el 5G y en qué se diferencia del 4G?",
        "¿Para qué sirve el Bluetooth 5.3?",
        "¿Qué es una Raspberry Pi y qué se puede hacer con ella?",
        "¿Qué es un lenguaje de programación?",
        "¿Qué es JavaScript y en qué se diferencia de Java?",
        "¿Qué hace un ingeniero de software en su día a día?",
        "¿Cómo funciona ChatGPT a grandes rasgos?",
        "¿Qué es la inteligencia artificial generativa?",
        "¿Qué es un sistema operativo?",
        "¿Conviene más Windows o macOS para estudiar diseño?",
        "¿Cómo libero espacio en mi celular Android?",
        "¿Qué es una fuente de poder modular?",
        "¿Qué es la memoria caché de un procesador?",
        "¿Cómo conecto mi laptop a la televisión?",
        "¿Qué consola me recomiendas para jugar con mis hijos?",
        "¿Qué es un disco NVMe?",
        "¿Por qué mi laptop hace mucho ruido con el ventilador?",
        "¿Cuántos watts consume una computadora gamer?",
        "¿Qué audífonos inalámbricos tienen buena cancelación de ruido?",
        "¿Cómo funciona una impresora 3D?",
        "¿Cómo se recicla un celular viejo?",
        "¿Qué significa que una app esté en la nube?",
        "¿Qué es Linux y por qué lo usan en servidores?",
        "¿Qué es un píxel?",
        "¿Cuál es la diferencia entre RAM y almacenamiento?",
        "¿Cómo funciona la carga rápida de los celulares?",
        "¿Qué smartwatch me recomiendas para correr?",
        "¿Qué es Starlink y cómo funciona el internet satelital?",
        "¿Qué es un QR y cómo se lee?",
        "¿Cuándo sale el próximo modelo de iPhone?",
        "¿Qué receta fácil hago con pollo y arroz?",
        "¿Qué necesito para tramitar mi licencia de conducir en la CDMX?",
        "¿A qué temperatura se cocina un pastel de zanahoria?",
        "¿Qué es el horario de verano y por qué ya no se usa en México?",
        "¿Cuál es el río más largo del mundo?",
    ],
    "academico": [
        "Para mi examen de Redes, explícame qué hace la capa de transporte del modelo OSI",
        "Explícame cómo funciona la memoria virtual para mi clase de Sistemas Operativos",
        "¿Qué es la arquitectura de Von Neumann? Me lo preguntan en el parcial",
        "Tengo tarea de Bases de Datos: ¿qué es la tercera forma normal?",
        "Explícame el algoritmo de Dijkstra para mi examen de Algoritmos",
        "¿Qué es una máquina de Turing? Es para Teoría de la Computación",
        "Para mi clase de Electrónica, ¿cómo funciona un transistor?",
        "¿Qué es el álgebra booleana y cómo simplifico una expresión con mapas de Karnaugh?",
        "Explícame la diferencia entre TCP y UDP para mi tarea de Redes",
        "¿Cómo se representan los números en complemento a dos? Lo vimos en clase",
        "Explícame qué es un pipeline en un procesador para mi materia de Arquitectura",
        "Para estadística: ¿cuándo uso una prueba t y cuándo una chi cuadrada?",
        "¿Qué es el teorema del límite central?",
        "Explícame la ley de Ohm con un ejercicio resuelto",
        "¿Qué es la oferta agregada? Tengo examen de macroeconomía",
        "¿Cómo se forma un enlace de hidrógeno?",
        "Explícame la Revolución Industrial y sus consecuencias sociales",
        "¿Qué diferencia hay entre una célula procariota y una eucariota?",
        "¿Cómo calculo el determinante de una matriz de 3x3?",
        "¿Qué es el método de Newton-Raphson? Lo piden en Métodos Numéricos",
        "¿Qué corrientes filosóficas hubo en la Grecia antigua?",
        "Explícame la teoría de grafos básica para Matemáticas Discretas",
        "¿Cómo planteo el marco teórico de mi tesina?",
        "¿Qué es la entropía de Shannon? Es para Teoría de la Información",
        "Explícame cómo funciona una red neuronal para mi materia de Inteligencia Artificial",
        "¿Qué son las leyes de Kirchhoff?",
        "Resume las etapas del ciclo celular para estudiar",
        "¿Qué es el efecto fotoeléctrico?",
        "Tengo que exponer sobre el Porfiriato, ¿qué puntos principales menciono?",
        "¿Qué es la deriva continental?",
    ],
    "programacion": [
        "Mi código de PyTorch dice 'CUDA out of memory' en la tarjeta gráfica, ¿cómo lo soluciono?",
        "¿Cómo leo la temperatura del procesador desde un script de Python?",
        "Escribe un script en Python que haga ping a una lista de IPs y guarde cuáles responden",
        "¿Cómo abro un socket TCP en Java para mandar un mensaje a un servidor?",
        "Mi Arduino no lee el sensor de temperatura, este es mi código: analogRead(A0)...",
        "¿Cómo configuro Nginx como proxy inverso para mi app de Node?",
        "Tengo un error 'port 3000 already in use' al correr npm start",
        "Haz una función en C que invierta una cadena sin usar strrev",
        "¿Cómo paso parámetros por query string en una ruta de Express?",
        "Mi componente de React se renderiza dos veces, ¿por qué?",
        "Escribe una consulta SQL que saque el top 5 de clientes por ventas del mes",
        "¿Cómo hago un merge de dos DataFrames de pandas por una columna en común?",
        "¿Por qué mi Dockerfile tarda tanto en construir? Uso COPY . . al principio",
        "Convierte este código de Python 2 a Python 3: print 'hola'",
        "¿Cómo hago pruebas unitarias con Jest para una función async?",
        "Mi app de Flutter no compila en iOS por un error de CocoaPods",
        "¿Cómo uso multiprocessing en Python para procesar imágenes en paralelo?",
        "Escribe un programa en C++ que lea un archivo línea por línea",
        "¿Cómo configuro ESLint y Prettier en un proyecto de TypeScript?",
        "Tengo un memory leak en mi app de Node, ¿cómo lo encuentro?",
        "¿Cómo hago una petición HTTP con requests y manejo los timeouts?",
        "Explícame qué hace este código: [x for x in range(10) if x % 2 == 0]",
        "¿Cómo despliego una base de datos PostgreSQL en Docker Compose?",
        "Haz una clase en Kotlin para un adaptador de RecyclerView",
        "¿Cómo uso la GPU en un notebook de Google Colab para entrenar mi modelo?",
    ],
    "ciberseguridad": [
        "¿Cómo configuro el firewall de Windows para bloquear conexiones entrantes no autorizadas?",
        "Mi router tiene la contraseña de fábrica, ¿qué riesgos hay y cómo lo aseguro?",
        "¿Qué es un ataque de hombre en el medio en una red wifi pública?",
        "¿Cómo cifro un disco duro externo con BitLocker?",
        "¿Cómo reviso si una extensión de Chrome está robando mis datos?",
        "¿Qué es el SIM swapping y cómo protejo mi número?",
        "Explícame qué es un exploit de día cero",
        "¿Cómo se usa Nmap para auditar los puertos abiertos de mi propio servidor?",
        "¿Qué es un certificado SSL caducado y por qué es peligroso?",
        "¿Cómo detecto si alguien entró a mi cuenta de Google?",
        "¿Qué es la autenticación multifactor resistente al phishing con llaves FIDO2?",
        "¿Cómo protejo mi API REST contra ataques de fuerza bruta?",
        "¿Qué es un ataque de ingeniería social por teléfono (vishing)?",
        "¿Cómo hago hardening de un servidor Ubuntu recién instalado?",
        "¿Qué hago si descargué un archivo .exe sospechoso de un correo?",
        "¿Qué es el principio de mínimo privilegio en la nube?",
        "¿Qué es un rootkit y cómo se detecta?",
        "¿Cómo funciona el cifrado de extremo a extremo de WhatsApp?",
        "¿Qué es una cadena de suministro comprometida en software?",
        "¿Cómo protejo los datos personales de mis clientes según la LFPDPPP?",
        "¿Qué es un honeypot?",
        "¿Cómo reviso los logs de acceso para encontrar un intento de intrusión?",
        "¿Qué permisos peligrosos debo revisar en las apps de mi celular?",
        "¿Cómo hago un plan de respuesta a incidentes para mi pyme?",
        "¿Qué es un ataque de denegación de servicio y cómo se mitiga?",
        "¿Cómo guardo contraseñas de forma segura en la base de datos de mi app?",
        "¿Qué es el robo de cookies de sesión?",
        "¿Cómo sé si mi cámara web está siendo usada sin mi permiso?",
        "¿Qué es el cryptojacking?",
        "¿Es seguro usar el mismo PIN para todas mis tarjetas?",
    ],
    "redaccion": [
        "Escribe un artículo para el blog de la empresa sobre cómo elegir una laptop",
        "Resume este texto sobre la historia de los procesadores en un párrafo",
        "Redacta un aviso para los empleados sobre el cambio de contraseñas del lunes",
        "Corrige la redacción de este correo para soporte técnico",
        "Traduce al español esta reseña de una tarjeta gráfica",
        "Escribe la descripción de un curso de introducción a Python para la página web",
        "Mejora el título de mi presentación sobre redes 5G",
        "Haz un resumen ejecutivo del informe de ventas del trimestre",
        "Reescribe este párrafo en lenguaje sencillo para niños",
        "Escribe un mensaje de bienvenida para nuevos clientes",
        "Redacta un mensaje para avisar a mi equipo que la reunión se pospone",
        "Hazme un guion para un video de TikTok sobre ahorrar dinero",
        "Escribe una carta de recomendación para un exalumno",
        "Escribe una invitación formal para la graduación",
        "Corrige la puntuación de este cuento corto",
    ],
    "personal": [
        "Llevo semanas sin ganas de nada y no sé por qué",
        "¿Cómo organizo mis gastos si me pagan por quincena?",
        "Quiero dejar de fumar, ¿por dónde empiezo?",
        "Mi pareja y yo discutimos por las tareas de la casa",
        "Me ofrecieron un trabajo en otra ciudad y no sé si aceptarlo",
        "¿Cómo dejo de pasar tantas horas en el celular en la noche?",
        "Quiero ahorrar para comprarme una computadora, ¿cómo me organizo?",
        "Me siento inseguro con mi cuerpo",
        "Mi hermano y yo no nos hablamos desde hace un año",
        "¿Cómo manejo el estrés del trabajo sin descuidar a mi familia?",
        "Me da miedo hablar en público en las juntas del trabajo",
        "Quiero mejorar mi alimentación pero como fuera todos los días",
        "Tengo deudas en tres tarjetas, ¿cuál pago primero?",
        "Me cuesta despertarme temprano",
        "Quiero adoptar un perro pero vivo en un departamento chico",
    ],
}

# ------------------------------------------------------------------ 3. plantillas
TECH = ["una tarjeta gráfica", "un procesador", "la memoria RAM", "un SSD", "una tarjeta madre", "una fuente de poder",
        "un router", "un módem", "un repetidor de wifi", "una red mesh", "un cable ethernet categoría 6", "la fibra óptica",
        "el 5G", "el Bluetooth", "un puerto USB-C", "un cable HDMI 2.1", "una pantalla OLED", "un monitor 4K",
        "una Chromebook", "una tablet", "un smartwatch", "una consola de videojuegos", "una impresora láser",
        "un disco duro externo", "la nube", "un sistema operativo", "Linux", "Windows 11", "un navegador web",
        "una Raspberry Pi", "un servidor", "un centro de datos", "la computación cuántica", "un chip ARM",
        "una GPU para IA", "el overclocking", "la refrigeración líquida", "un teclado mecánico", "una webcam 4K",
        "una memoria USB", "el NFC del celular", "el GPS", "un dron", "la realidad virtual", "un asistente de voz"]
USES = ["editar video", "jugar en 4K", "estudiar en línea", "programar", "una oficina pequeña", "ver series",
        "trabajar desde casa", "diseño gráfico", "una casa de dos pisos", "mi mamá que casi no usa la computadora"]
CONSULTA_T = [
    "¿Qué es {t}?", "Explícame qué es {t}", "¿Para qué sirve {t}?", "¿Cómo funciona {t}?",
    "¿Qué {t2} me recomiendas para {u}?", "¿Vale la pena comprar {t} para {u}?",
    "¿Cuánto cuesta más o menos {t}?", "¿Cómo elijo {t} para {u}?",
]
CONSULTA_T2 = ["laptop", "monitor", "router", "celular", "tablet", "impresora", "silla gamer", "audífono",
               "disco SSD", "tarjeta gráfica", "teclado", "plan de internet"]

SUBJECTS = {
    "Arquitectura de Computadoras": ["qué hace la unidad de control", "cómo funciona la memoria caché",
                                     "qué es el ciclo fetch-decode-execute", "qué es un registro"],
    "Redes": ["qué es el modelo TCP/IP", "cómo funciona el protocolo DNS", "qué es una máscara de subred",
              "cómo funciona el enrutamiento"],
    "Sistemas Operativos": ["qué es un proceso y un hilo", "qué es un interbloqueo", "cómo funciona la paginación",
                            "qué hace el planificador"],
    "Algoritmos": ["qué es la programación dinámica", "cómo funciona quicksort", "qué es un árbol AVL",
                   "qué es la búsqueda en anchura"],
    "Física": ["qué es la energía cinética", "cómo funciona la ley de Coulomb", "qué es la inducción electromagnética",
               "qué es el momento angular"],
    "Química": ["qué es el pH", "cómo se balancea una reacción redox", "qué es la electronegatividad",
                "qué es una solución amortiguadora"],
    "Historia": ["las causas de la Independencia de México", "qué fue la Guerra Fría", "qué fue el Renacimiento",
                 "qué fue la Revolución Francesa"],
    "Biología": ["qué es la meiosis", "cómo funciona el sistema inmune", "qué es la selección natural",
                 "qué hace el ARN mensajero"],
    "Cálculo": ["qué es una integral definida", "cómo se aplica la regla de la cadena", "qué es una serie de Taylor",
                "qué es un límite al infinito"],
    "Economía": ["qué es la elasticidad de la demanda", "qué es el PIB per cápita", "qué es un monopolio",
                 "cómo funciona la política monetaria"],
}
ACADEMICO_T = [
    "Para mi clase de {m}, explícame {c}", "Tengo examen de {m}: ¿{c}?", "Explícame {c}, es para mi tarea de {m}",
    "No entendí en {m} {c}, ¿me lo explicas con un ejemplo?", "¿{c}? Lo vemos en {m} y no me queda claro",
]

LANGS = ["Python", "JavaScript", "Java", "C#", "C++", "Go", "Rust", "TypeScript", "PHP", "Kotlin", "Swift", "R"]
DEV_TASKS = ["lea un archivo CSV y saque el promedio de una columna", "valide que una contraseña tenga 8 caracteres",
             "ordene una lista de diccionarios por fecha", "cuente las palabras de un texto",
             "convierta grados Celsius a Fahrenheit", "descargue una página web y extraiga los títulos",
             "calcule el factorial de forma iterativa", "elimine los elementos repetidos de una lista",
             "lea la temperatura de un sensor por puerto serial", "haga una petición GET a una API y muestre el JSON"]
ERRORS = ["NullPointerException", "TypeError: undefined is not a function", "segmentation fault",
          "IndexError: list index out of range", "SyntaxError: unexpected EOF", "error 500 en el servidor",
          "ModuleNotFoundError", "CORS policy blocked", "stack overflow", "deadlock entre hilos"]
PROG_T = [
    "Escribe una función en {l} que {k}", "¿Cómo hago en {l} un programa que {k}?",
    "Mi código en {l} da '{e}', ¿cómo lo arreglo?", "Me sale {e} en mi proyecto de {l}, ayúdame a depurarlo",
]

ASSETS = ["mi cuenta de banco en línea", "mi correo de Gmail", "la red wifi de mi casa", "el servidor de mi empresa",
          "mi celular", "la base de datos de clientes", "mi cuenta de Instagram", "la API de mi app",
          "las laptops de mis empleados", "mi página de WordPress"]
THREATS = ["phishing", "ransomware", "fuerza bruta", "robo de sesión", "malware", "inyección SQL",
           "accesos no autorizados", "fugas de datos", "suplantación de identidad", "keyloggers"]
SEC_CONCEPTS = ["un ataque XSS", "el cifrado AES", "una VPN", "la autenticación de dos factores", "un firewall de red",
                "un antivirus de nueva generación", "el hashing con bcrypt", "un SIEM", "una prueba de penetración",
                "la gestión de parches de seguridad"]
SEC_T = [
    "¿Cómo protejo {a} contra {th}?", "¿Cómo detecto {th} en {a}?", "¿Qué es {sc} y cómo me protege?",
    "Me preocupa {th} en {a}, ¿qué medidas de seguridad tomo?",
]

TEXTS = ["un correo para mi jefe", "el resumen de mi tesis", "una publicación de LinkedIn", "un mensaje para mis clientes",
         "el reporte de soporte técnico", "una reseña de una laptop", "un artículo sobre ciberseguridad",
         "la descripción de mi producto", "una carta de presentación", "un post sobre tarjetas gráficas"]
RED_T = [
    "Corrige la ortografía de {x}", "Haz más formal {x}", "Resume en tres líneas {x}", "Traduce al inglés {x}",
    "Escribe {x} que suene profesional", "Reformula {x} para que sea más claro",
]

FEELINGS = ["triste", "ansioso", "agotado", "frustrado", "solo", "confundido", "preocupado", "sin motivación"]
SITUATIONS = ["mi trabajo", "mi relación", "mi familia", "el dinero", "mi salud", "la mudanza",
              "mis amistades", "el cambio de carrera"]
GOALS = ["ahorrar para un viaje", "dormir mejor", "hacer ejercicio diario", "pagar mis deudas",
         "comer más sano", "llevarme mejor con mis papás", "organizar mi semana", "dejar el azúcar"]
PERS_T = ["Me siento {f} por {s}", "Últimamente estoy {f} por {s}, ¿qué hago?", "Quiero {g}, ¿cómo empiezo?",
          "Ayúdame a hacer un plan para {g}"]


def from_templates(rng: random.Random) -> list[tuple[str, str, str]]:
    """(texto, etiqueta, grupo). Grupo = id de plantilla."""
    out = []
    for i, tpl in enumerate(CONSULTA_T):
        for t in TECH:
            out.append((tpl.format(t=t, t2=rng.choice(CONSULTA_T2), u=rng.choice(USES)), "consulta_general", f"tc{i}"))
    for i, tpl in enumerate(ACADEMICO_T):
        for m, cs in SUBJECTS.items():
            for c in cs:
                out.append((tpl.format(m=m, c=c), "academico", f"ta{i}"))
    for i, tpl in enumerate(PROG_T):
        for lang in LANGS:
            out.append((tpl.format(l=lang, k=rng.choice(DEV_TASKS), e=rng.choice(ERRORS)), "programacion", f"tp{i}"))
    for i, tpl in enumerate(SEC_T):
        for a in ASSETS:
            out.append((tpl.format(a=a, th=rng.choice(THREATS), sc=rng.choice(SEC_CONCEPTS)), "ciberseguridad", f"ts{i}"))
    for i, tpl in enumerate(RED_T):
        for x in TEXTS:
            out.append((tpl.format(x=x), "redaccion", f"tr{i}"))
    for i, tpl in enumerate(PERS_T):
        for _ in range(12):
            out.append((tpl.format(f=rng.choice(FEELINGS), s=rng.choice(SITUATIONS), g=rng.choice(GOALS)), "personal", f"tq{i}"))
    return out


# ------------------------------------------------------------------ 4. variantes
def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def typo(s: str, rng: random.Random) -> str:
    words = s.split(" ")
    idx = [i for i, w in enumerate(words) if len(w) > 4 and w.isalpha()]
    for i in rng.sample(idx, min(len(idx), 2)):
        w = words[i]
        j = rng.randrange(1, len(w) - 1)
        words[i] = rng.choice([w[:j] + w[j + 1:], w[:j] + w[j] + w[j:], w[:j - 1] + w[j] + w[j - 1] + w[j + 1:]])
    return " ".join(words)


SPANGLISH = {"ayuda": "help", "ayúdame": "help me", "computadora": "compu", "correo": "email", "archivo": "file",
             "error": "bug", "código": "code", "función": "function", "por favor": "please", "actualizar": "updatear",
             "contraseña": "password", "trabajo": "chamba", "reunión": "meeting", "rápido": "fast"}
PREFIX = ["Oye, una pregunta rápida: ", "Hola! ", "Buenas tardes, quisiera saber algo. ", "porfa ",
          "Estoy algo perdido con esto: ", "Una duda que tengo desde hace rato: "]
SUFFIX = [" Gracias!", " porfa", " (es urgente)", " Te lo agradecería mucho.", " xfa", "?? 🙏"]
CONTEXT = ["Llevo toda la tarde buscando en internet y no encuentro una respuesta clara. ",
           "Te cuento un poco de contexto antes de preguntar. ",
           "No soy experto en esto, así que explícamelo con calma. "]


def to_instruction(s: str) -> str:
    q = s.strip("¿?").strip()
    for a, b in (("Qué es ", "Explícame qué es "), ("Cómo ", "Dime cómo "), ("Para qué sirve ", "Explícame para qué sirve ")):
        if q.startswith(a):
            return b + q[len(a):]
    return s


def spanglish(s: str) -> str:
    out = s
    for es, en in SPANGLISH.items():
        out = out.replace(es, en).replace(es.capitalize(), en.capitalize())
    return out


VARIANTS = {
    "informal": lambda s, r: r.choice(PREFIX) + strip_accents(s).lower().replace("¿", "").replace("¡", "") + r.choice(SUFFIX),
    "typo": typo,
    "sin_acentos": lambda s, r: strip_accents(s),
    "spanglish": lambda s, r: spanglish(s),
    "instruccion": lambda s, r: to_instruction(s),
    "largo": lambda s, r: r.choice(CONTEXT) + s + r.choice(SUFFIX),
    "corto": lambda s, r: " ".join(s.split()[: max(4, len(s.split()) // 2)]).rstrip(",.:;") + "?",
}


# ------------------------------------------------------------------ armado
def candidates(rng: random.Random) -> list[dict]:
    import make_topic_synthetic as v1

    bases = []  # (texto, etiqueta, grupo)
    for label, texts in v1.BASE.items():
        for i, t in enumerate(texts):
            bases.append((t, V1_RELABEL.get(t, label), f"v1b{label[:4]}{i:02d}"))
    for label, texts in NEW.items():
        for i, t in enumerate(texts):
            bases.append((t, label, f"n{label[:4]}{i:02d}"))
    templated = from_templates(rng)

    out = [{"text": t, "label": l, "group": g, "variant": "original", "prio": 0} for t, l, g in bases]
    out += [{"text": t, "label": l, "group": g, "variant": "plantilla", "prio": 1} for t, l, g in templated]
    for t, l, g in bases + rng.sample(templated, len(templated) // 3):
        for name in rng.sample(list(VARIANTS), 3):
            v = VARIANTS[name](t, rng)
            if v and v != t:
                out.append({"text": v, "label": l, "group": g, "variant": name, "prio": 2})
    return out


def main() -> None:
    import numpy as np

    import mlutils as U

    rng = random.Random(SEED)
    cands = candidates(rng)
    rng.shuffle(cands)
    cands.sort(key=lambda c: c["prio"])  # originales → plantillas → variantes (orden estable)

    probe = U.load_topic_probe()
    probe_texts = set(probe["text"])
    X = U.embed([c["text"] for c in cands], cache="topic_v2_cands")
    P = U.embed(probe["text"].tolist(), cache="topic_probe")
    max_probe = (X @ P.T).max(axis=1)

    kept_idx: list[int] = []
    per_class = Counter()
    non_variant = Counter()
    dropped = Counter()
    K = np.zeros((0, X.shape[1]), dtype=X.dtype)
    for i, c in enumerate(cands):
        if c["text"] in probe_texts or max_probe[i] > PROBE_COS:
            dropped["parecido_al_probe"] += 1
            continue
        if per_class[c["label"]] >= PER_CLASS:
            continue
        if c["prio"] < 2 and non_variant[c["label"]] >= MAX_NON_VARIANT:
            continue
        if len(K) and float((K @ X[i]).max()) > DUP_COS:
            dropped["casi_duplicado"] += 1
            continue
        kept_idx.append(i)
        K = np.vstack([K, X[i]])
        per_class[c["label"]] += 1
        non_variant[c["label"]] += c["prio"] < 2

    rows = [cands[i] for i in kept_idx]
    rng.shuffle(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["text", "label", "source"])
        w.writeheader()
        for r in rows:
            w.writerow({"text": r["text"], "label": r["label"],
                        "source": f"v2:{r['label']}:{r['group']}:{r['variant']}"})
    by_variant = defaultdict(Counter)
    for r in rows:
        by_variant[r["label"]][r["variant"]] += 1
    print(f"{len(rows)} prompts -> {OUT.name} · candidatos {len(cands)} · descartados {dict(dropped)}")
    print("por clase:", dict(sorted(per_class.items())))
    print("máx. coseno con el probe entre los aceptados:", round(float(max_probe[kept_idx].max()), 3))
    for label in sorted(by_variant):
        print(f"  {label:<17}", dict(by_variant[label].most_common()))


if __name__ == "__main__":
    main()
