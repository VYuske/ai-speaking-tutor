from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

SendFn = Callable[[dict[str, Any]], Awaitable[None]]


class EventSink:
    """Single-writer event queue.

    WebSocket sends are not concurrency safe, and a turn emits from several
    tasks (LLM stream, TTS workers, evaluator). Everything goes through this
    queue so ordering is FIFO and the socket has exactly one writer.
    """

    def __init__(self, send: SendFn) -> None:
        self._send = send
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._drain(), name="event-sink")

    async def emit(self, event_type: str, **payload: Any) -> None:
        if self._closed:
            return
        await self._queue.put({"type": event_type, **payload})

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._task is None:
            return
        await self._queue.put(None)
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def _drain(self) -> None:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            try:
                await self._send(event)
            except Exception as exc:  # noqa: BLE001 - client went away
                logger.info("dropping event after send failure: %s", exc)
                self._closed = True
                return
