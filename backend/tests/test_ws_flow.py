from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.orchestrator.events import EventSink
from app.orchestrator.turn import TurnRunner
from app.providers.base import Providers
from app.providers.fakes import FakeLLM, FakeSTT, FakeTTS


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


def is_warmup_settled(event: dict[str, Any]) -> bool:
    return event["type"] in {"warmup.done", "warmup.failed"}


def start_session(ws, **overrides: Any) -> dict[str, Any]:
    payload = {
        "type": "session.start",
        "language": "en",
        "level": 2,
        "scenario_id": "cafe-order",
        **overrides,
    }
    ws.send_json(payload)
    events = receive_until(ws, is_idle)
    ready = next(event for event in events if event["type"] == "session.ready")
    return ready


def test_full_text_turn(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ready = start_session(ws)
        assert ready["scenario"]["id"] == "cafe-order"
        assert ready["models"]["llm"] == "fake"

        ws.send_json({"type": "text.input", "text": "I would like a coffee"})
        events = receive_until(ws, lambda event: event["type"] == "turn.end")

        types = [event["type"] for event in events]
        assert "stt.final" in types
        assert "llm.delta" in types
        assert "feedback.card" in types
        assert "tts.audio" in types

        stt = next(event for event in events if event["type"] == "stt.final")
        assert stt["text"] == "I would like a coffee"
        assert stt["source"] == "text"

        audio = next(
            event
            for event in events
            if event["type"] == "tts.audio" and event["source"] == "reply"
        )
        assert audio["wav_b64"]

        feedback = next(event for event in events if event["type"] == "feedback.card")
        assert len(feedback["corrections"]) == 1

        turn_end = events[-1]
        assert set(turn_end["timings"]) == {
            "stt_ms",
            "first_token_ms",
            "first_audio_ms",
            "total_ms",
            "cold_start_ms",
        }

        session_id = ready["session_id"]

    review = client.get(f"/api/sessions/{session_id}").json()
    assert review["stats"]["turns"] == 1
    assert review["turns"][0]["user_text"] == "I would like a coffee"


def test_audio_turn(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        start_session(ws)
        ws.send_json({"type": "audio.start"})
        ws.send_bytes(b"\x01\x00" * 16_000)
        ws.send_json({"type": "audio.end"})

        events = receive_until(ws, lambda event: event["type"] == "turn.end")
        stt = next(event for event in events if event["type"] == "stt.final")
        assert stt["source"] == "audio"
        assert stt["text"]
        assert stt["stt_ms"] >= 0


def test_short_audio_is_rejected(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        start_session(ws)
        ws.send_json({"type": "audio.start"})
        ws.send_json({"type": "audio.end"})
        events = receive_until(
            ws, lambda event: event["type"] == "warning" and event["code"] == "audio_too_short"
        )
        assert events[-1]["code"] == "audio_too_short"


def test_explain_returns_portuguese_and_audio(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        start_session(ws)
        ws.send_json({"type": "text.input", "text": "I would like a coffee"})
        receive_until(ws, lambda event: event["type"] == "turn.end")

        ws.send_json({"type": "explain.request", "turn_id": 1, "focus": "grammar"})
        events = receive_until(ws, lambda event: event["type"] == "explain.result")
        result = events[-1]
        assert result["explanation_pt"]
        assert result["example_target"]
        assert result["wav_b64"]


def test_session_end_returns_review(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        start_session(ws)
        ws.send_json({"type": "text.input", "text": "hello"})
        receive_until(ws, lambda event: event["type"] == "turn.end")

        ws.send_json({"type": "session.end"})
        events = receive_until(ws, lambda event: event["type"] == "review")
        review = events[-1]["review"]
        assert review["session"]["status"] == "finished"
        assert review["stats"]["turns"] == 1


def test_requires_session_first(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "text.input", "text": "hi"})
        events = receive_until(ws, lambda event: event["type"] == "error")
        assert events[-1]["code"] == "no_session"


def test_unknown_scenario_reports_config_error(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "type": "session.start",
                "language": "en",
                "level": 2,
                "scenario_id": "does-not-exist",
            }
        )
        events = receive_until(ws, lambda event: event["type"] == "error")
        assert events[-1]["code"] == "config_error"


def test_config_reports_the_profile_selected_by_env(settings: Settings) -> None:
    """The UI echoes `default_profile` back in `session.start`.

    When `AST_PROFILE` is set (Docker runs `compact`), the config file's
    `default_profile` may still say `skeleton`. Exposing that would downgrade every
    browser session to fake audio, so the endpoint must report the active profile.
    """
    overridden = replace(settings, profile="alternate")
    app = create_app(overridden)
    with TestClient(app) as client:
        payload = client.get("/api/config").json()

    resolved = {
        cfg["profile"]
        for cfg in payload["languages"].values()
        if isinstance(cfg, dict) and cfg.get("profile")
    }
    assert resolved == {"alternate"}, "the env profile must win for every language"
    assert payload["default_profile"] == "alternate", (
        f"/api/config exposes '{payload['default_profile']}' while languages resolve to "
        f"{sorted(resolved)}; the UI would start sessions with the wrong profile"
    )


def test_config_survives_an_unknown_env_profile(settings: Settings) -> None:
    """A typo in `AST_PROFILE` must not 500 the UI bootstrap.

    `resolve` rejects the unknown name per language, so the endpoint keeps the
    per-language error entries and advertises the file default instead of a
    profile the resolver would refuse.
    """
    overridden = replace(settings, profile="ghost")
    app = create_app(overridden)
    with TestClient(app) as client:
        response = client.get("/api/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_profile"] == "fake", "unknown env profile must fall back to the file"
    assert payload["default_profile"] in payload["profiles"], (
        f"advertised '{payload['default_profile']}' is not a profile the resolver accepts"
    )
    assert all(
        "profile" in cfg or "error" in cfg for cfg in payload["languages"].values()
    ), payload["languages"]


def test_health_and_config_endpoints(client: TestClient) -> None:
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    config = client.get("/api/config").json()
    assert "en" in config["languages"]
    assert config["languages"]["ja"]["level_scale"] == "JLPT"

    scenarios = client.get("/api/scenarios", params={"language": "zh"}).json()
    assert scenarios["items"]
    assert all(item["language"] == "zh" for item in scenarios["items"])


def test_session_ready_starts_a_warmup(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "type": "session.start",
                "language": "en",
                "level": 2,
                "scenario_id": "cafe-order",
            }
        )
        events = receive_until(ws, is_warmup_settled)

        ready = next(event for event in events if event["type"] == "session.ready")
        assert ready["warmup"] == "pending"

        settled = events[-1]
        assert settled["type"] == "warmup.done"
        assert isinstance(settled["cold_start_ms"], int)
        assert settled["cold_start_ms"] >= 0


def test_turn_end_reports_cold_start(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        start_session(ws)
        ws.send_json({"type": "text.input", "text": "I would like a coffee"})
        events = receive_until(ws, lambda event: event["type"] == "turn.end")

        timings = events[-1]["timings"]
        assert "cold_start_ms" in timings
        assert isinstance(timings["cold_start_ms"], int)
        assert timings["cold_start_ms"] >= 0


class _BrokenLLM:
    name = "broken"

    async def chat_stream(
        self, messages: list[dict[str, str]], **overrides: Any
    ) -> AsyncIterator[str]:
        raise RuntimeError("llm is down")
        yield ""

    async def chat_json(
        self, messages: list[dict[str, str]], schema: dict[str, Any], **overrides: Any
    ) -> dict[str, Any]:
        raise RuntimeError("llm is down")


async def make_runner(
    client: TestClient, llm: Any = None
) -> tuple[TurnRunner, list[dict[str, Any]]]:
    state = client.app.state
    resolved = state.config.resolve("en", None, state.settings)
    scenario = state.scenarios.get("cafe-order")
    providers = Providers(
        llm=llm or FakeLLM(), stt=FakeSTT(), tts=FakeTTS(), warnings=[]
    )

    events: list[dict[str, Any]] = []

    async def send(event: dict[str, Any]) -> None:
        events.append(event)

    sink = EventSink(send)
    await sink.start()
    runner = TurnRunner(
        config=resolved,
        scenario=scenario,
        level=2,
        providers=providers,
        sink=sink,
        db=state.db,
        session_id="warmup-test",
    )
    return runner, events


async def test_warmup_is_silent(client: TestClient) -> None:
    runner, events = await make_runner(client)
    await asyncio.wait_for(runner.start_warmup(), timeout=5)
    await runner.sink.close()

    assert runner.history == []
    assert runner.cold_start_ms is not None
    assert runner.cold_start_ms >= 0
    assert [event["type"] for event in events] == ["warmup.done"]


async def test_warmup_failure_is_reported_not_raised(client: TestClient) -> None:
    runner, events = await make_runner(client, llm=_BrokenLLM())
    await asyncio.wait_for(runner.start_warmup(), timeout=5)
    await runner.sink.close()

    assert events == [{"type": "warmup.failed", "message": "llm is down"}]
    assert runner.cold_start_ms is None
    assert runner.history == []

