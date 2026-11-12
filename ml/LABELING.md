# Guía de etiquetado de TEMA (Fase 4C)

Es la guía para etiquetar el **test real** (CE-4), el **probe set** (`data/topic_probe.csv`) y
los **sintéticos v2**. Una sola etiqueta por prompt: la del **tema central**, es decir, lo que
la persona quiere lograr.

## Reglas de desempate (en este orden)

1. **¿Pide producir o transformar un texto que NO es código?** (escribir, redactar, corregir,
   resumir, traducir, parafrasear, cambiar el tono) → `redaccion`, aunque el texto trate de
   seguridad, de una materia o de su vida.
2. **¿El tema central es seguridad?** (ataques, vulnerabilidades, malware, cifrado,
   autenticación, privacidad, hardening, forense, fraude digital) → `ciberseguridad`, aunque
   incluya código. Mencionar una contraseña, un banco o internet de pasada **no** basta.
3. **¿Hay código concreto o una tarea de desarrollo?** (escribir, depurar o explicar código,
   un error, un comando, una librería, una consulta SQL, configurar un proyecto) → `programacion`.
4. **¿Hay contexto escolar o un concepto formal de una materia?** (tarea, examen, clase, tesis,
   investigación; teorema, ley física, proceso biológico, hecho histórico) → `academico`.
5. **¿Habla de su propia vida?** (emociones, relaciones, salud, dinero propio, decisiones,
   planes personales) → `personal`.
6. **Todo lo demás** → `consulta_general`, **incluida la tecnología de uso cotidiano**:
   hardware, redes, apps, cómo funciona un aparato o qué es un lenguaje de programación, sin
   código y sin contexto escolar.

> **Hardware, redes o software en general NO son `ciberseguridad`.** "¿Qué es una tarjeta
> gráfica?" es `consulta_general`; este caso, mal clasificado en vivo, motivó esta guía.

---

## programacion
**Definición:** escribir, depurar o explicar **código concreto**, o una tarea práctica de
desarrollo (errores, comandos, librerías, SQL, git, despliegue).

| Positivos |
|---|
| "Mi código de Python da `KeyError: 'id'` al leer el JSON, ¿por qué?" |
| "Escribe una función en JavaScript que quite los duplicados de un arreglo" |
| "¿Cómo hago un LEFT JOIN entre clientes y pedidos en SQL?" |

| Frontera | Etiqueta | Regla |
|---|---|---|
| "¿Qué es Python y para qué se usa?" | `consulta_general` | Preguntar **qué es** un lenguaje, sin código ni tarea → 6 |
| "Explícame la recursión para mi examen de Estructuras de Datos" | `academico` | Concepto para una clase, sin código que escribir o depurar → 4 |
| "Mi función que guarda contraseñas en texto plano, ¿cómo la hago segura con bcrypt?" | `ciberseguridad` | El objetivo central es la seguridad → 2 |

## academico
**Definición:** tareas, exámenes, conceptos de una materia o investigación (incluida la
tesis y las citas).

| Positivos |
|---|
| "Explícame la segunda ley de Newton con un ejercicio resuelto" |
| "¿Cuáles fueron las causas de la Revolución Mexicana? Es para mi examen" |
| "¿Cómo planteo la hipótesis de mi tesis?" |

| Frontera | Etiqueta | Regla |
|---|---|---|
| "¿Cómo funciona una tarjeta gráfica?" | `consulta_general` | Tecnología cotidiana, sin contexto escolar → 6 |
| "Para mi clase de Arquitectura de Computadoras, ¿qué hace la ALU?" | `academico` | Contexto escolar explícito → 4 |
| "Resume este artículo científico en un párrafo" | `redaccion` | Pide transformar un texto → 1 |

## redaccion
**Definición:** escribir, corregir, resumir, traducir o reformular **textos** (no código).

| Positivos |
|---|
| "Redacta un correo formal para pedir una prórroga" |
| "Corrige la ortografía de este párrafo" |
| "Haz más corto y profesional este mensaje para mi jefe" |

| Frontera | Etiqueta | Regla |
|---|---|---|
| "Escribe un ensayo sobre los riesgos del phishing" | `redaccion` | Pide producir un texto → 1 (gana sobre el tema) |
| "Agrega comentarios a este código de Python" | `programacion` | El texto es código → 1 no aplica → 3 |
| "¿Cuál es la diferencia entre 'haber' y 'a ver'?" | `redaccion` | Duda de escritura/ortografía para redactar bien |

## ciberseguridad
**Definición:** SOLO cuando el tema central es **seguridad**: ataques, vulnerabilidades,
malware, cifrado, autenticación, privacidad, hardening, forense, fraude digital.

| Positivos |
|---|
| "¿Cómo detecto un correo de phishing?" |
| "Explica cómo prevenir una inyección SQL" |
| "¿Qué diferencia hay entre cifrado simétrico y asimétrico?" |

| Frontera | Etiqueta | Regla |
|---|---|---|
| "¿Qué es una tarjeta gráfica?" | `consulta_general` | Hardware sin seguridad → 6 |
| "¿Cómo configuro mi router para que el wifi llegue a toda la casa?" | `consulta_general` | Redes sin seguridad (cobertura, no protección) → 6 |
| "¿Cómo cambio la contraseña del wifi para que mis vecinos no se conecten?" | `ciberseguridad` | El objetivo es proteger el acceso → 2 |

## consulta_general
**Definición:** dudas generales de la vida diaria y **tecnología de uso cotidiano**
(hardware, apps, redes, cómo funciona algo, qué es un lenguaje) sin contexto escolar; recetas,
viajes, compras, trámites y datos curiosos.

| Positivos |
|---|
| "Explícame qué es una tarjeta gráfica" (caso que motivó la Fase 4C) |
| "¿Qué laptop me recomiendas para editar video?" |
| "¿Cómo saco mi pasaporte por primera vez?" |

| Frontera | Etiqueta | Regla |
|---|---|---|
| "¿Qué es la fotosíntesis? Lo vemos mañana en biología" | `academico` | Contexto escolar → 4 |
| "¿Cuánto debería ahorrar si gano 15 mil al mes?" | `personal` | Su propio dinero → 5 |
| "¿Es seguro conectarme al wifi de un aeropuerto?" | `ciberseguridad` | La pregunta es de seguridad → 2 |

## personal
**Definición:** vida personal: emociones, salud, finanzas personales, relaciones, planes y
decisiones propias.

| Positivos |
|---|
| "Me siento muy estresado y no puedo dormir" |
| "¿Cómo le digo a mis papás que quiero cambiar de carrera?" |
| "Ayúdame a armar un presupuesto con mi sueldo" |

| Frontera | Etiqueta | Regla |
|---|---|---|
| "¿Cuál es la tasa de interés de los CETES?" | `consulta_general` | Información general, no su situación → 6 |
| "Escribe una carta para disculparme con mi novia" | `redaccion` | Pide producir un texto → 1 |
| "Me hackearon el Instagram, ¿cómo lo recupero?" | `ciberseguridad` | El problema central es de seguridad → 2 |
