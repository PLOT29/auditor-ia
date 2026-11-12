"""Agente «Pregúntale a Centinela» (Fase 5C).

- tools.py      herramientas CERRADAS (function calling): solo agregados numéricos, en solo lectura.
- rules.py      nivel 1 «Sin IA»: responde con reglas usando las mismas herramientas.
- llm.py        niveles 2 y 3: bucle de function calling con un proveedor compatible con la API de OpenAI.
- state.py      nivel activo y configuración; la API key del nivel 3 vive en el Administrador de
                credenciales de Windows (keyring), nunca en archivos.
- installer.py  nivel 2: ÚNICO módulo que ejecuta comandos del sistema (lista blanca fija).
- api.py        rutas /agent/* e /ia/* (exigen token de sesión, ver security.py).
"""
