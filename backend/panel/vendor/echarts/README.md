# Apache ECharts 6.0.0 (copia local)

Copiada al repo para que el centro de control (`/panel`) funcione **sin internet** y sin pedir nada
fuera de `localhost` (RNF-03). El panel la carga como `/panel/vendor/echarts/echarts.min.js`.

| Campo | Valor |
|---|---|
| Paquete | `echarts@6.0.0` (registro de npm, publicado el 2025-07-30) |
| Archivo | `dist/echarts.min.js` sin modificar |
| SHA-256 | `baa8dfe7e1d9336b98e8986ba7e20ea15e7cdbea1ef42a59d59478632fa45a1d` |
| Licencia | Apache License 2.0 (`LICENSE`, `NOTICE`) |
| Subcomponentes incluidos en el bundle | zrender 6.0.0 — BSD 3-Clause (`LICENSE-zrender`); fragmentos de d3.js — BSD 3-Clause (`LICENSE-d3`); tslib — 0BSD (cabecera dentro del bundle) |

El bundle no hace peticiones de red (sin `fetch` ni `XMLHttpRequest`); las únicas URLs que contiene son
la de la licencia en un comentario y los *namespaces* XML/SVG. `tests/test_panel.py` lo verifica.
