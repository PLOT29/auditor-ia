"""Bus de eventos en memoria para el feed en vivo (Fase 5B, `GET /events`, Server-Sent Events).

- `publish(evento)` se llama desde /analyze, que FastAPI corre en un hilo del threadpool: se pasa al
  bucle de asyncio con `call_soon_threadsafe` (thread-safe).
- Cada cliente conectado tiene su propia cola acotada (MAX_QUEUE). Si un cliente lento la llena, se
  descarta su evento más viejo: el feed nunca bloquea /analyze.
- Solo viajan resúmenes SIN texto (analytics.event_summary): id, hora, plataforma, riesgo, motivo,
  tipos de hallazgo, categoría, decisión y latencia. Nada se persiste aquí.
"""
from __future__ import annotations

import asyncio
import threading

MAX_QUEUE = 100


class Broadcaster:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self.published = 0

    def subscribe(self) -> asyncio.Queue:
        """Desde una corrutina (el endpoint SSE): registra una cola nueva."""
        q: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE)
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subs)

    @staticmethod
    def _put(q: asyncio.Queue, event: dict) -> None:
        if q.full():
            try:
                q.get_nowait()  # cliente lento: se pierde su evento más viejo, no se bloquea a nadie
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(event)

    def publish(self, event: dict) -> int:
        """Desde cualquier hilo. Devuelve a cuántos clientes se envió."""
        with self._lock:
            subs, loop = list(self._subs), self._loop
        self.published += 1
        if not subs or loop is None or loop.is_closed():
            return 0
        for q in subs:
            try:
                loop.call_soon_threadsafe(self._put, q, event)
            except RuntimeError:  # bucle cerrado (apagado del servidor)
                return 0
        return len(subs)


bus = Broadcaster()
