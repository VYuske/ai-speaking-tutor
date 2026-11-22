from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session as SQLSession

from app.agent.prompts import build_roleplay_messages
from app.agent.scenario import Scenario
from app.db.database import Database, LearnerProfileRow
from app.orchestrator.events import EventSink
from app.orchestrator.turn import TurnRunner
from app.providers.base import Providers
from app.providers.fakes import FakeLLM, FakeSTT, FakeTTS

SCENARIO = Scenario(
    id="cafe-order",
    language="en",
    title="Ordering a coffee",
    title_pt="Pedindo um café",
    level_min=1,
    level_max=4,
    persona="a friendly barista",
    setting="a small cafe",
    goal="order a drink and a snack",
    opening_line="Hi! What can I get you?",
    success_criteria=("orders a drink",),
    vocab_targets=("latte",),
    guardrails=("never speak Portuguese",),
)


# --------------------------------------------------------------------- helpers
def make_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "app.db")
    db.init()
    return db


def seed_session(
    db: Database,
    session_id: str,
    corrections: list[dict[str, Any]],
    level: int = 2,
    language: str = "en",
) -> None:
    db.create_session(session_id, language, level, "cafe-order", "fake")
    row_id = db.add_turn(session_id, 1, "I go", "Great!", None, {}, {})
    db.add_corrections(session_id, row_id, corrections)


def grammar(original: str, corrected: str) -> dict[str, Any]:
    return {
        "type": "grammar",
        "original": original,
        "corrected": corrected,
        "explanation_pt": "Tempo verbal.",
        "severity": "medium",
        "confidence": 0.9,
    }


def stored_profile(db: Database, language: str = "en") -> dict[str, Any] | None:
    with SQLSession(db.engine) as session:
        row = session.get(LearnerProfileRow, language)
        if row is None:
            return None
        return json.loads(row.profile_json)


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


def start_session(ws, language: str = "en") -> dict[str, Any]:
    ws.send_json(
        {
            "type": "session.start",
            "language": language,
            "level": 2,
            "scenario_id": "cafe-order",
        }
    )
    events = receive_until(ws, is_idle)
    return next(event for event in events if event["type"] == "session.ready")


class RecordingLLM:
    """FakeLLM that keeps every prompt it was asked to answer."""

    name = "recording"

    def __init__(self) -> None:
        self.calls: list[list[dict[str, str]]] = []
        self._inner = FakeLLM()

    async def chat_stream(
        self, messages: list[dict[str, str]], **overrides: Any
    ) -> AsyncIterator[str]:
        self.calls.append(list(messages))
        async for chunk in self._inner.chat_stream(messages, **overrides):
            yield chunk

    async def chat_json(
        self, messages: list[dict[str, str]], schema: dict[str, Any], **overrides: Any
    ) -> dict[str, Any]:
        return await self._inner.chat_json(messages, schema, **overrides)

    def system_prompts(self) -> list[str]:
        return [call[0]["content"] for call in self.calls if call and call[0]["role"] == "system"]


def poll_profile(
    db: Database, sessions: int, language: str = "en", timeout: float = 1.0
) -> dict[str, Any] | None:
    """Wait up to `timeout` for the profile row to report `sessions` sessions.

    Returns whatever is stored when the deadline expires, so a double count
    surfaces as a readable assertion failure instead of a hang.
    """
    deadline = time.monotonic() + timeout
    while True:
        profile = stored_profile(db, language)
        if profile is not None and profile.get("sessions") == sessions:
            return profile
        if time.monotonic() >= deadline:
            return profile
        time.sleep(0.01)


async def make_runner(
    client: TestClient, *, language: str = "en", llm: Any = None
) -> tuple[TurnRunner, list[dict[str, Any]]]:
    state = client.app.state
    resolved = state.config.resolve(language, None, state.settings)
    scenario = state.scenarios.get("cafe-order")
    providers = Providers(llm=llm or FakeLLM(), stt=FakeSTT(), tts=FakeTTS(), warnings=[])

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
        session_id=f"memory-{language}",
    )
    return runner, events


# ---------------------------------------------------------- finalize + memory
def test_finalize_counts_sessions_and_errors(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed_session(db, "s1", [grammar("I go", "I went"), grammar("a apple", "an apple")])
    first = db.finalize_session("s1", "en", 2, [])

    assert first["sessions"] == 1
    assert first["level"] == 2
    assert first["error_counts"] == {"grammar": 2}
    assert first["recent_corrections"] == [
        {"original": "I go", "corrected": "I went"},
        {"original": "a apple", "corrected": "an apple"},
    ]

    seed_session(db, "s2", [grammar("she go", "she goes")], level=3)
    second = db.finalize_session("s2", "en", 3, [])

    assert second["sessions"] == 2
    assert second["level"] == 3
    assert second["error_counts"] == {"grammar": 3}
    assert len(second["recent_corrections"]) == 3

    # A second init must not wipe data: no migration, only create-if-absent.
    Database(tmp_path / "app.db").init()
    assert stored_profile(db) == second


def test_finalize_dedupes_and_caps_vocab(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    seed_session(db, "s1", [], level=3)

    many = [{"term": f"term-{index}", "translation_pt": f"t{index}"} for index in range(25)]
    profile = db.finalize_session("s1", "en", 3, many)
    assert len(profile["vocab"]) == 20
    assert profile["vocab"][-1] == "term-24"

    repeated = [
        {"term": "receipt", "translation_pt": "recibo"},
        {"term": "Receipt", "translation_pt": "recibo"},
        {"term": "  ", "translation_pt": "empty"},
    ]
    profile = db.finalize_session("s1", "en", 3, repeated)

    # deduped case-insensitively, newest last, and the cap keeps the newest terms
    assert len(profile["vocab"]) == 20
    assert [term for term in profile["vocab"] if term.lower() == "receipt"] == ["Receipt"]
    assert profile["vocab"][-1] == "Receipt"
    assert "term-5" not in profile["vocab"]


def test_get_learner_memory_is_empty_then_useful(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    assert db.get_learner_memory("en") == ""

    seed_session(db, "s1", [grammar("I go", "I went"), grammar("I go", "I went")], level=3)
    db.finalize_session("s1", "en", 3, [{"term": "latte", "translation_pt": "latte"}])

    memory = db.get_learner_memory("en")
    assert memory
    assert "Previous sessions: 1." in memory
    assert "Level: 3." in memory
    assert "Recurring errors: grammar (2)." in memory
    assert 'Recent corrections: "I go" → "I went".' in memory
    assert "Known vocabulary: latte." in memory
    assert len(memory) <= 600

    # other languages are independent
    assert db.get_learner_memory("ja") == ""


def test_malformed_profile_degrades_to_empty_memory(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    with SQLSession(db.engine) as session:
        session.add(LearnerProfileRow(language="en", profile_json="{not json"))
        session.commit()
    assert db.get_learner_memory("en") == ""

    with SQLSession(db.engine) as session:
        row = session.get(LearnerProfileRow, "en")
        row.profile_json = "[1, 2, 3]"
        session.add(row)
        session.commit()
    assert db.get_learner_memory("en") == ""

    # a broken row must not stop the next session from being written
    seed_session(db, "s1", [grammar("I go", "I went")])
    recovered = db.finalize_session("s1", "en", 3, [])
    assert recovered["sessions"] == 1
    assert recovered["error_counts"] == {"grammar": 1}


def test_roleplay_prompt_carries_the_memory_block() -> None:
    memory = (
        "Previous sessions: 2. Level: 3. Recurring errors: grammar (4), vocab (2). "
        'Recent corrections: "I go" → "I went". Known vocabulary: latte.'
    )
    messages = build_roleplay_messages(
        SCENARIO, 3, "CEFR", [{"role": "user", "content": "hi"}], memory=memory
    )

    system = messages[0]
    assert system["role"] == "system"
    assert "Learner memory:" in system["content"]
    assert memory in system["content"]

    without = build_roleplay_messages(SCENARIO, 3, "CEFR", [{"role": "user", "content": "hi"}])
    assert "Learner memory:" not in without[0]["content"]


# ------------------------------------------------------------------- end to end
def test_ws_session_end_persists_profile(client: TestClient) -> None:
    db = client.app.state.db

    with client.websocket_connect("/ws") as ws:
        start_session(ws)
        ws.send_json({"type": "text.input", "text": "I would like a coffee"})
        receive_until(ws, lambda event: event["type"] == "turn.end")
        ws.send_json({"type": "session.end"})
        events = receive_until(ws, lambda event: event["type"] == "review")
        assert events[-1]["review"]["session"]["status"] == "finished"

    profile = stored_profile(db)
    assert profile is not None
    assert profile["sessions"] == 1
    assert profile["error_counts"] == {"naturalness": 1}
    assert profile["vocab"] == ["oat milk"]

    with client.websocket_connect("/ws") as ws:
        start_session(ws)
        ws.send_json({"type": "text.input", "text": "One latte, please"})
        receive_until(ws, lambda event: event["type"] == "turn.end")
        ws.send_json({"type": "session.end"})
        receive_until(ws, lambda event: event["type"] == "review")

    second = stored_profile(db)
    assert second is not None
    assert second["sessions"] == 2
    assert second["error_counts"] == {"naturalness": 2}


def test_ws_disconnect_without_session_end_finalizes_profile(client: TestClient) -> None:
    db = client.app.state.db

    with client.websocket_connect("/ws") as ws:
        start_session(ws)
        ws.send_json({"type": "text.input", "text": "I would like a coffee"})
        receive_until(ws, lambda event: event["type"] == "turn.end")
        # no `session.end`: the client just walks away

    profile = poll_profile(db, sessions=1)
    assert profile is not None, "profile was never finalized on disconnect"
    assert profile["sessions"] == 1
    assert profile["error_counts"] == {"naturalness": 1}
    assert profile["vocab"] == ["oat milk"]


def test_ws_session_end_then_disconnect_counts_one_session(client: TestClient) -> None:
    db = client.app.state.db

    with client.websocket_connect("/ws") as ws:
        start_session(ws)
        ws.send_json({"type": "text.input", "text": "I would like a coffee"})
        receive_until(ws, lambda event: event["type"] == "turn.end")
        ws.send_json({"type": "session.end"})
        receive_until(ws, lambda event: event["type"] == "review")
        # the socket closes after the session was already finalized

    profile = poll_profile(db, sessions=1)
    assert profile is not None
    assert profile["sessions"] == 1
    assert profile["error_counts"] == {"naturalness": 1}

    with client.websocket_connect("/ws") as ws:
        start_session(ws)
        ws.send_json({"type": "text.input", "text": "One latte, please"})
        receive_until(ws, lambda event: event["type"] == "turn.end")
        # and a plain disconnect still adds exactly one more session

    second = poll_profile(db, sessions=2)
    assert second is not None
    assert second["sessions"] == 2
    assert second["error_counts"] == {"naturalness": 2}


async def test_second_session_runner_starts_with_memory_in_the_prompt(
    client: TestClient,
) -> None:
    db = client.app.state.db
    seed_session(db, "seed-session", [grammar("I go", "I went")])
    db.finalize_session("seed-session", "en", 2, [])

    llm = RecordingLLM()
    runner, _events = await make_runner(client, llm=llm)

    assert "Previous sessions: 1" in runner.memory
    assert "grammar (1)" in runner.memory

    await runner.run_turn(1, text="I go to the cafe")
    await runner.sink.close()

    systems = llm.system_prompts()
    assert systems, "the roleplay prompt was never built"
    assert "Learner memory:" in systems[-1]
    assert runner.memory in systems[-1]

    runner.finalize()
    runner.finalize()
    profile = stored_profile(db)
    assert profile is not None
    assert profile["sessions"] == 2
    assert profile["error_counts"] == {"grammar": 1, "naturalness": 1}
