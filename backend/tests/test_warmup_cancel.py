from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import ResolvedConfig
from app.providers import registry
from app.providers.base import Providers
from app.providers.fakes import FakeSTT, FakeTTS

# The warm-up only ever runs `chat_stream`. It is started by `session.start` and
# must be stopped by `session.end` or by the client walking away, otherwise a
# real model would keep the GPU busy until the process dies and the UI pill
# would stay in "loading model..." forever.
EMPTY_EVALUATION: dict[str, Any] = {
    "corrections": [],
    "vocab": [],
    "scene_progress": {"goals_met": [], "status": "ongoing"},
    "language_leak": False,
}


class SlowLLM:
    """LLM whose warm-up call hangs until something cancels it."""

    name = "slow"

    def __init__(
        self, started: asyncio.Event, block: asyncio.Event, cancelled: asyncio.Event
    ) -> None:
        self.started = started
        self.block = block
        self.cancelled = cancelled

    async def chat_stream(
        self, messages: list[dict[str, str]], **overrides: Any
    ) -> AsyncIterator[str]:
        try:
            self.started.set()
            await self.block.wait()
            yield ""
        finally:
            self.cancelled.set()

    async def chat_json(
        self, messages: list[dict[str, str]], schema: dict[str, Any], **overrides: Any
    ) -> dict[str, Any]:
        return dict(EMPTY_EVALUATION)


# --------------------------------------------------------------------- helpers
def receive_until(ws, predicate, limit: int = 500) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for _ in range(limit):
        event = ws.receive_json()
        events.append(event)
        if predicate(event):
            return events
    raise AssertionError(f"event not received; last events: {events[-5:]}")


def is_idle(event: dict[str, Any]) -> bool:
    return event["type"] == "state" and event["state"] == "idle"


def wait_flag(flag: asyncio.Event, timeout: float) -> bool:
    """Poll an event set from the app's loop; False when the deadline expires."""
    deadline = time.monotonic() + timeout
    while not flag.is_set():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


def install_slow_providers(monkeypatch: pytest.MonkeyPatch) -> SlowLLM:
    """Force the ProviderCache to build a warm-up that never finishes on its own."""
    llm = SlowLLM(
        started=asyncio.Event(), block=asyncio.Event(), cancelled=asyncio.Event()
    )

    def build(cfg: ResolvedConfig) -> Providers:
        return Providers(llm=llm, stt=FakeSTT(), tts=FakeTTS(), warnings=[])

    monkeypatch.setattr(registry, "build_providers", build)
    return llm


def start_session(ws) -> list[dict[str, Any]]:
    ws.send_json(
        {
            "type": "session.start",
            "language": "en",
            "level": 2,
            "scenario_id": "cafe-order",
        }
    )
    return receive_until(ws, is_idle)


# ------------------------------------------------------------------------ cases
def test_session_end_cancels_pending_warmup(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = install_slow_providers(monkeypatch)

    with client.websocket_connect("/ws") as ws:
        opening = start_session(ws)
        assert wait_flag(llm.started, 1.0), "the warm-up never started"

        started_at = time.monotonic()
        ws.send_json({"type": "session.end"})
        events = receive_until(ws, lambda event: event["type"] == "review")
        elapsed = time.monotonic() - started_at

    assert elapsed < 2.0, f"session.end took {elapsed:.2f}s; the warm-up blocked the reply"
    assert events[-1]["review"]["session"]["status"] == "finished"
    assert llm.cancelled.is_set(), "the warm-up task was not cancelled on session.end"

    types = [event["type"] for event in opening + events]
    assert "warmup.done" not in types
    assert "warmup.failed" not in types


def test_disconnect_cancels_pending_warmup(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = install_slow_providers(monkeypatch)

    with client.websocket_connect("/ws") as ws:
        start_session(ws)
        assert wait_flag(llm.started, 1.0), "the warm-up never started"
        # no `session.end`: the client just walks away

    assert wait_flag(llm.cancelled, 1.0), "the warm-up task survived the disconnect"
