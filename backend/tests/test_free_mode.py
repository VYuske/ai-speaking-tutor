"""AST-048: free conversation mode (no scenario) in the backend.

Deterministic: fake providers, no network, no models. Every `receive_until` waits for
an event the server always emits (`state:idle`, `turn.end`, `warmup.done`, `error`),
so a missing implementation fails with an assertion instead of hanging the suite.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.agent.prompts import LANGUAGE_LABEL, build_free_conversation_messages
from app.agent.scenario import (
    FREE_SCENARIO_ID,
    ScenarioError,
    ScenarioRepository,
    free_scenario,
)
from app.core.errors import TutorError
from app.orchestrator.events import EventSink
from app.orchestrator.turn import TurnRunner
from app.providers.base import Providers
from app.providers.fakes import FakeLLM, FakeSTT, FakeTTS

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("en", "ja", "zh", "de")
HISTORY: list[dict[str, str]] = [
    {"role": "user", "content": "I watched a documentary about volcanoes yesterday."}
]
MEMORY = "Name: Ana. Level: false beginner.\nPrefers short answers."


def brief(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Events without the audio payload, so an assertion message stays readable."""
    return [{key: value for key, value in event.items() if key != "wav_b64"} for event in events]


def receive_until(ws, predicate, limit: int = 500) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for _ in range(limit):
        event = ws.receive_json()
        events.append(event)
        if predicate(event):
            return events
    raise AssertionError(f"event not received; last events: {brief(events[-5:])}")


def is_settled(event: dict[str, Any]) -> bool:
    """A free session has no opening, so `state:idle` only arrives after a turn."""
    return event["type"] in {"warmup.done", "warmup.failed", "error"}


def is_idle_or_error(event: dict[str, Any]) -> bool:
    return (event["type"] == "state" and event["state"] == "idle") or event["type"] == "error"


def is_turn_end(event: dict[str, Any]) -> bool:
    """Also stops on `error`, so a broken session fails instead of blocking forever."""
    return event["type"] in {"turn.end", "error"}


def start_free(ws, **overrides: Any) -> list[dict[str, Any]]:
    payload = {"type": "session.start", "language": "en", "level": 2, "mode": "free", **overrides}
    ws.send_json(payload)
    return receive_until(ws, is_settled)


# ------------------------------------------------------------------ free_scenario


@pytest.mark.parametrize("language", LANGUAGES)
def test_free_scenario_shape(language: str) -> None:
    scenario = free_scenario(language)

    assert scenario.id == FREE_SCENARIO_ID == "free"
    assert scenario.language == language
    assert scenario.title == "Conversa livre"
    assert scenario.title_pt == "Conversa livre"
    assert scenario.opening_line == "", "free mode never speaks an opening line"
    assert scenario.persona.strip(), "the evaluator needs a persona"
    assert scenario.goal.strip(), "the evaluator needs a goal"
    assert scenario.success_criteria == ()
    assert scenario.vocab_targets == ()
    assert scenario.guardrails == ()
    assert (scenario.level_min, scenario.level_max) == (1, 6)
    assert all(scenario.supports_level(level) for level in range(1, 7))
    assert not scenario.supports_level(0)
    assert not scenario.supports_level(7)


def test_free_scenario_is_not_in_the_repository() -> None:
    repository = ScenarioRepository(ROOT / "scenarios")
    repository.load()

    with pytest.raises(ScenarioError):
        repository.get(FREE_SCENARIO_ID)
    listed = [item.id for language in LANGUAGES for item in repository.list(language)]
    assert FREE_SCENARIO_ID not in listed, "the synthetic scenario is not a YAML scenario"


def test_free_scenario_rejects_an_unsupported_language() -> None:
    with pytest.raises(ScenarioError):
        free_scenario("fr")


# ---------------------------------------------------------- free conversation prompt


@pytest.mark.parametrize("language", LANGUAGES)
def test_free_prompt_drops_the_scenario_block_and_keeps_the_rules(language: str) -> None:
    target = LANGUAGE_LABEL[language]
    messages = build_free_conversation_messages(
        free_scenario(language), 3, "CEFR", HISTORY, memory=MEMORY
    )

    assert messages[0]["role"] == "system"
    system = messages[0]["content"]

    assert "SCENARIO" not in system, f"the free prompt kept a scenario block:\n{system}"
    assert '"guardrails"' not in system and '"success_criteria"' not in system

    lowered = system.lower()
    assert "learner chooses the topic" in lowered, "lost the learner-leads rule"
    assert "follow their lead" in lowered, "lost the 'follow the learner' rule"
    assert "1 to 3" in system, "lost the 1-3 sentence limit"
    assert "one question" in lowered, "lost the one-question-per-turn rule"

    assert f"Reply ONLY in {target}" in system, f"{language}: lost the target-language rule"
    assert "Learner memory:" in system and MEMORY in system, f"{language}: lost the memory block"
    assert "LEVEL: 3/6 (CEFR)" in system, f"{language}: lost the level guide"
    assert messages[1:] == HISTORY


def test_free_prompt_keeps_the_no_correction_rule() -> None:
    system = build_free_conversation_messages(
        free_scenario("en"), 2, "CEFR", HISTORY, memory=MEMORY
    )[0]["content"]

    assert "Do not correct the learner's grammar or pronunciation" in system


def test_free_prompt_without_memory_omits_the_memory_block() -> None:
    system = build_free_conversation_messages(free_scenario("en"), 3, "CEFR", HISTORY)[0][
        "content"
    ]

    assert "Learner memory:" not in system
    assert MEMORY not in system


def test_free_prompt_rejects_an_invalid_level() -> None:
    with pytest.raises(TutorError):
        build_free_conversation_messages(free_scenario("en"), 9, "CEFR", HISTORY)


# --------------------------------------------------------------- speak_opening


class RecordingLLM(FakeLLM):
    """`FakeLLM` that keeps the prompt of every streamed turn."""

    def __init__(self) -> None:
        super().__init__()
        self.stream_messages: list[list[dict[str, str]]] = []

    async def chat_stream(
        self, messages: list[dict[str, str]], **overrides: Any
    ) -> AsyncIterator[str]:
        self.stream_messages.append([dict(message) for message in messages])
        async for chunk in super().chat_stream(messages, **overrides):
            yield chunk


def make_runner(
    client: TestClient, *, free: bool = False, llm: Any = None
) -> tuple[TurnRunner, list[dict[str, Any]], EventSink]:
    state = client.app.state
    language = "en"
    resolved = state.config.resolve(language, None, state.settings)
    scenario = free_scenario(language) if free else state.scenarios.get("cafe-order")
    providers = Providers(
        llm=llm or FakeLLM(), stt=FakeSTT(), tts=FakeTTS(), warnings=[]
    )
    events: list[dict[str, Any]] = []

    async def send(event: dict[str, Any]) -> None:
        events.append(event)

    sink = EventSink(send)
    session_id = f"free-{uuid4().hex}"
    state.db.create_session(session_id, language, 2, scenario.id, resolved.profile)
    runner = TurnRunner(
        config=resolved,
        scenario=scenario,
        level=2,
        providers=providers,
        sink=sink,
        db=state.db,
        session_id=session_id,
    )
    return runner, events, sink


async def test_speak_opening_is_silent_in_free_mode(client: TestClient) -> None:
    runner, events, sink = make_runner(client, free=True)
    await sink.start()
    try:
        await asyncio.wait_for(runner.speak_opening(0), timeout=5)
    finally:
        await sink.close()

    assert events == [], f"the free session emitted {brief(events)}"
    assert runner.history == [], "an empty opening must not enter the history"


async def test_speak_opening_still_speaks_the_scenario_line(client: TestClient) -> None:
    runner, events, sink = make_runner(client)
    await sink.start()
    try:
        await asyncio.wait_for(runner.speak_opening(0), timeout=5)
    finally:
        await sink.close()

    types = [event["type"] for event in events]
    assert "assistant.message" in types, brief(events)
    message = next(event for event in events if event["type"] == "assistant.message")
    assert message["opening"] is True
    assert message["text"] == runner.scenario.opening_line
    assert message["text"]


async def test_runner_uses_the_free_prompt_in_free_mode(client: TestClient) -> None:
    llm = RecordingLLM()
    runner, events, sink = make_runner(client, free=True, llm=llm)
    await sink.start()
    try:
        await asyncio.wait_for(runner.run_turn(1, text="I love old films."), timeout=20)
    finally:
        await sink.close()

    assert llm.stream_messages, "the free turn never reached the model"
    system = llm.stream_messages[0][0]["content"]
    assert "SCENARIO" not in system, f"the free turn used the roleplay prompt:\n{system}"
    assert "learner chooses the topic" in system.lower()
    assert "turn.end" in [event["type"] for event in events], brief(events)


async def test_runner_keeps_the_scenario_prompt_in_scenario_mode(client: TestClient) -> None:
    llm = RecordingLLM()
    runner, events, sink = make_runner(client, llm=llm)
    await sink.start()
    try:
        await asyncio.wait_for(runner.run_turn(1, text="I would like a coffee"), timeout=20)
    finally:
        await sink.close()

    system = llm.stream_messages[0][0]["content"]
    assert "SCENARIO (stay inside it):" in system, "the scenario session lost its scenario block"


# --------------------------------------------------------------------- WS: free


def test_session_start_free_has_no_opening(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        events = start_free(ws)

        assert not [event for event in events if event["type"] == "error"], brief(events)
        ready = next(event for event in events if event["type"] == "session.ready")
        assert ready["mode"] == "free"
        assert ready["opening"] is False
        assert ready["scenario"]["id"] == "free"
        assert ready["scenario"]["title"] == "Conversa livre"
        assert ready["language"] == "en"
        assert ready["level"] == 2

        assert [event for event in events if event["type"] == "assistant.message"] == []
        assert [event for event in events if event["type"] == "tts.audio"] == []


def test_free_mode_ignores_the_scenario_id(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        events = start_free(ws, scenario_id="does-not-exist")

        assert not [event for event in events if event["type"] == "error"], brief(events)
        ready = next(event for event in events if event["type"] == "session.ready")
        assert ready["scenario"]["id"] == "free"


def test_free_text_turn_reaches_turn_end_and_review(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        start_free(ws)

        ws.send_json({"type": "text.input", "text": "I love old films."})
        events = receive_until(ws, is_turn_end)

        types = [event["type"] for event in events]
        assert "stt.final" in types, brief(events)
        assert "llm.delta" in types, brief(events)
        assert "feedback.card" in types, brief(events)
        assert "error" not in types, brief(events)

        ws.send_json({"type": "session.end"})
        review_events = receive_until(ws, lambda event: event["type"] in {"review", "error"})
        assert review_events[-1]["type"] == "review", brief(review_events)
        review = review_events[-1]["review"]

        assert review["session"]["scenario_id"] == "free"
        assert review["session"]["status"] == "finished"
        assert review["stats"]["turns"] == 1
        assert review["turns"][0]["user_text"] == "I love old films."


def test_scenario_session_still_speaks_its_opening(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {"type": "session.start", "language": "en", "level": 2, "scenario_id": "cafe-order"}
        )
        events = receive_until(ws, is_idle_or_error)

        ready = next(event for event in events if event["type"] == "session.ready")
        assert ready["mode"] == "scenario"
        assert ready["opening"] is True
        assert ready["scenario"]["id"] == "cafe-order"

        opening = next(event for event in events if event["type"] == "assistant.message")
        assert opening["opening"] is True
        assert opening["text"], "the scenario opening line is empty"
        audio = next(event for event in events if event["type"] == "tts.audio")
        assert audio["source"] == "opening"


def test_explicit_scenario_mode_matches_the_default(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "type": "session.start",
                "language": "en",
                "level": 2,
                "scenario_id": "cafe-order",
                "mode": "scenario",
            }
        )
        events = receive_until(ws, is_idle_or_error)

        ready = next(event for event in events if event["type"] == "session.ready")
        assert ready["mode"] == "scenario"
        assert ready["opening"] is True


def test_unknown_mode_is_rejected(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {"type": "session.start", "language": "en", "level": 2, "mode": "chat"}
        )
        events = receive_until(ws, lambda event: event["type"] == "error")

        error = events[-1]
        assert error["code"] == "invalid_mode", brief(events)
        assert error["recoverable"] is False
