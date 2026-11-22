"""AST-025: validate prompt inputs instead of failing deep inside the builder.

A level outside 1..6 used to raise a bare ``KeyError`` from ``LEVEL_GUIDE``, an
unknown explanation language silently fell back to the raw code and an unknown
``focus`` was interpolated into the prompt unchecked. All three are now
``TutorError``, and ``TurnRunner.explain`` turns them into a recoverable
``invalid_explain_request`` error so an invalid request cannot kill the session.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.prompts import (
    LANGUAGE_LABEL,
    build_explain_messages,
    build_roleplay_messages,
)
from app.agent.scenario import Scenario, ScenarioRepository
from app.core.errors import TutorError

ROOT = Path(__file__).resolve().parents[1]

SCENARIO_BY_LANGUAGE = {
    "en": "cafe-order",
    "ja": "ramen-shop-order",
    "zh": "restaurant-table",
    "de": "bahn-ticket",
}

LEVEL_SCALE = {"en": "CEFR", "ja": "JLPT", "zh": "HSK", "de": "CEFR"}

HISTORY: list[dict[str, str]] = [
    {"role": "assistant", "content": "Good morning! How can I help you today?"},
    {"role": "user", "content": "Hello. I would like one coffee and a snack, please."},
]

VALID_FOCUS = ("grammar", "vocab", "naturalness", "register", "pronunciation")


@functools.lru_cache(maxsize=1)
def _repository() -> ScenarioRepository:
    repository = ScenarioRepository(ROOT / "scenarios")
    repository.load()
    return repository


def _scenario(language: str) -> Scenario:
    return _repository().get(SCENARIO_BY_LANGUAGE[language])


# --------------------------------------------------------------- roleplay level
@pytest.mark.parametrize("level", [0, 7])
def test_roleplay_rejects_level_outside_1_to_6(level: int) -> None:
    with pytest.raises(TutorError) as excinfo:
        build_roleplay_messages(_scenario("en"), level, LEVEL_SCALE["en"], list(HISTORY))
    message = str(excinfo.value)
    assert "level" in message.lower()
    assert "1" in message and "6" in message


@pytest.mark.parametrize("level", [0, 7])
def test_roleplay_level_error_is_not_a_key_error(level: int) -> None:
    try:
        build_roleplay_messages(_scenario("en"), level, LEVEL_SCALE["en"], list(HISTORY))
    except TutorError:
        return
    except KeyError as exc:  # pragma: no cover - the bug this task fixes
        pytest.fail(f"level {level} still raises a bare KeyError: {exc!r}")
    pytest.fail(f"level {level} was accepted; expected a TutorError")


@pytest.mark.parametrize("language", sorted(LANGUAGE_LABEL))
@pytest.mark.parametrize("level", [1, 3, 6])
def test_roleplay_accepts_every_valid_level(language: str, level: int) -> None:
    scenario = _scenario(language)
    messages = build_roleplay_messages(
        scenario, level, LEVEL_SCALE[language], list(HISTORY)
    )

    assert [message["role"] for message in messages] == [
        "system",
        *(message["role"] for message in HISTORY),
    ]
    assert messages[1:] == HISTORY
    system = messages[0]["content"]
    assert system.startswith(f"You are a patient {LANGUAGE_LABEL[language]} tutor")
    assert f"LEVEL: {level}/6 ({LEVEL_SCALE[language]})" in system


# --------------------------------------------------------------- explain inputs
@pytest.mark.parametrize("language", ["fr", "es", "EN", "", "en-US", "xx"])
def test_explain_rejects_unknown_language(language: str) -> None:
    with pytest.raises(TutorError) as excinfo:
        build_explain_messages("I go yesterday", "Yesterday you went?", language)
    assert "language" in str(excinfo.value).lower()


@pytest.mark.parametrize("focus", ["nonsense", "", "Grammar", "pronunciation "])
def test_explain_rejects_unknown_focus(focus: str) -> None:
    with pytest.raises(TutorError) as excinfo:
        build_explain_messages("I go yesterday", "Yesterday you went?", "en", focus)
    assert "focus" in str(excinfo.value).lower()


@pytest.mark.parametrize("language", sorted(LANGUAGE_LABEL))
@pytest.mark.parametrize("focus", VALID_FOCUS)
def test_explain_accepts_valid_inputs(language: str, focus: str) -> None:
    messages = build_explain_messages(
        "I go yesterday", "Yesterday you went?", language, focus
    )

    assert [message["role"] for message in messages] == ["system", "user"]
    system = messages[0]["content"]
    user = messages[1]["content"]
    assert LANGUAGE_LABEL[language] in system
    assert "Brazilian Portuguese" in system
    assert f"Focus: {focus}" in user
    assert "Learner said: 'I go yesterday'" in user
    assert "Tutor replied: 'Yesterday you went?'" in user
    assert f"in {LANGUAGE_LABEL[language]}." in user


def test_explain_default_focus_is_grammar() -> None:
    default = build_explain_messages("I go yesterday", "Yesterday you went?", "en")
    explicit = build_explain_messages(
        "I go yesterday", "Yesterday you went?", "en", "grammar"
    )
    assert default == explicit


# -------------------------------------------------------------- WS integration
def _receive_until(
    ws: Any, predicate: Callable[[dict[str, Any]], bool], limit: int = 500
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for _ in range(limit):
        event = ws.receive_json()
        events.append(event)
        if predicate(event):
            return events
    raise AssertionError(f"event not received; last events: {events[-5:]}")


def _is_idle(event: dict[str, Any]) -> bool:
    return event["type"] == "state" and event["state"] == "idle"


def _start_session(ws: Any) -> dict[str, Any]:
    ws.send_json(
        {
            "type": "session.start",
            "language": "en",
            "level": 2,
            "scenario_id": "cafe-order",
        }
    )
    events = _receive_until(ws, _is_idle)
    return next(event for event in events if event["type"] == "session.ready")


def test_invalid_explain_focus_does_not_kill_the_session(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        _start_session(ws)

        # explain needs a learner turn to talk about, so open the session normally.
        ws.send_json({"type": "text.input", "text": "I would like a coffee"})
        _receive_until(ws, lambda event: event["type"] == "turn.end")

        ws.send_json({"type": "explain.request", "turn_id": 1, "focus": "nonsense"})
        # Either the request is rejected (fail fast) or it went through — never
        # block on a socket that will stay silent.
        events = _receive_until(
            ws, lambda event: event["type"] in {"error", "explain.result"}
        )
        error = events[-1]
        assert error["type"] == "error", f"invalid focus was accepted: {error}"
        assert error["code"] == "invalid_explain_request"
        assert error["recoverable"] is True
        assert "focus" in error["message"].lower()

        # The session must still be usable after the rejected request.
        ws.send_json({"type": "text.input", "text": "One coffee, please"})
        events = _receive_until(ws, lambda event: event["type"] == "turn.end")
        assert events[-1]["turn_id"] == 2
        assert any(event["type"] == "feedback.card" for event in events)

        ws.send_json({"type": "session.end"})
        review = _receive_until(ws, lambda event: event["type"] == "review")[-1]["review"]
        assert review["stats"]["turns"] == 2
