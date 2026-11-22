"""AST-053: regression tests for the pre-commit backend review findings (B1-B12).

Deterministic: fake providers, no network, no models. Ordering tests use explicit
`asyncio.Event` gates instead of sleeping on the clock, and every `receive_until`
stops on an event the server always emits (`turn.end`, `error`, `state:idle`), so a
missing implementation fails with an assertion instead of hanging the suite.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as SQLSession
from sqlmodel import select

import app.orchestrator.turn as turn_module
import app.providers.registry as registry
from app.agent.translate import clear_cache
from app.db.database import Database, TurnRow
from app.orchestrator.events import EventSink
from app.orchestrator.turn import TurnRunner
from app.providers.base import AudioChunk, Providers
from app.providers.fakes import FakeLLM, FakeSTT, FakeTTS
from app.providers.registry import ProviderCache, _with_fallback
from app.providers.stt import faster_whisper as whisper_stt

SCENARIO = "cafe-order"
FIRST_USER = "I would like a coffee"
SECOND_USER = "One latte please"
REPLY_BY_CALL: tuple[str, ...] = (
    "The coffee is brewing right now.",
    "The latte is already hot.",
)


# ------------------------------------------------------------------------ helpers
@pytest.fixture(autouse=True)
def _clean_translation_cache() -> Any:
    """`translate_pt` caches by (language, text) for the whole process."""
    clear_cache()
    yield
    clear_cache()


def brief(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Events without the audio payload, so an assertion message stays readable."""
    return [{key: value for key, value in event.items() if key != "wav_b64"} for event in events]


def receive_until(ws, predicate: Callable[[dict[str, Any]], bool], limit: int = 500) -> list:
    events: list[dict[str, Any]] = []
    for _ in range(limit):
        event = ws.receive_json()
        events.append(event)
        if predicate(event):
            return events
    raise AssertionError(f"event not received; last events: {brief(events[-5:])}")


def receive_or_fail(ws, timeout: float = 5.0) -> dict[str, Any]:
    """`ws.receive_json()` with a deadline.

    Starlette's `TestClient` keeps the receive queue open when the endpoint returns
    without calling `websocket.close()`, so a crash inside the receive loop would
    block the test forever instead of failing it. The deadline turns that hang into
    a readable assertion failure.
    """
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["event"] = ws.receive_json()
        except BaseException as exc:  # noqa: BLE001 - handed back to the caller
            box["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        pytest.fail(f"the receive loop stopped answering: no event within {timeout}s")
    if "error" in box:
        raise box["error"]
    return box["event"]


def is_idle(event: dict[str, Any]) -> bool:
    return event["type"] == "state" and event["state"] == "idle"


def is_turn_end(event: dict[str, Any]) -> bool:
    return event["type"] in {"turn.end", "error"}


def start_session(ws, **overrides: Any) -> dict[str, Any]:
    payload = {
        "type": "session.start",
        "language": "en",
        "level": 2,
        "scenario_id": SCENARIO,
        **overrides,
    }
    ws.send_json(payload)
    events = receive_until(ws, is_idle)
    return next(event for event in events if event["type"] == "session.ready")


class Recorder:
    """Event sink target with a barrier: `sync()` proves everything before it drained."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._synced: asyncio.Event | None = None

    async def send(self, event: dict[str, Any]) -> None:
        if event["type"] == "__sync__":
            assert self._synced is not None
            self._synced.set()
            return
        self.events.append(event)

    async def sync(self, sink: EventSink) -> None:
        self._synced = asyncio.Event()
        await sink.emit("__sync__")
        await self._synced.wait()

    def types(self) -> list[str]:
        return [event["type"] for event in self.events]


class ScriptedLLM(FakeLLM):
    """FakeLLM with one recorded prompt per call and a distinct reply per turn.

    `silent_turns` streams nothing (an empty reply), `failing_stream_turns` raises
    before the first token (an LLM failure).
    """

    def __init__(self, *, silent_turns: int = 0, failing_stream_turns: int = 0) -> None:
        super().__init__()
        self.silent_turns = silent_turns
        self.failing_stream_turns = failing_stream_turns
        self.stream_calls = 0
        self.stream_prompts: list[list[dict[str, str]]] = []
        self.json_prompts: list[list[dict[str, str]]] = []
        self.translation_calls = 0

    async def chat_stream(
        self, messages: list[dict[str, str]], **overrides: Any
    ) -> AsyncIterator[str]:
        self.stream_calls += 1
        call = self.stream_calls
        self.stream_prompts.append([dict(message) for message in messages])
        if call <= self.failing_stream_turns:
            raise RuntimeError("llm is down")
        if call <= self.silent_turns:
            return
        reply = REPLY_BY_CALL[(call - 1) % len(REPLY_BY_CALL)]
        for index, word in enumerate(reply.split(" ")):
            yield word if index == 0 else f" {word}"

    async def chat_json(
        self, messages: list[dict[str, str]], schema: dict[str, Any], **overrides: Any
    ) -> dict[str, Any]:
        self.json_prompts.append([dict(message) for message in messages])
        if "translation_pt" in schema.get("properties", {}):
            self.translation_calls += 1
        return await super().chat_json(messages, schema, **overrides)

    def last_prompt(self) -> str:
        return "\n".join(message["content"] for message in self.json_prompts[-1])

    def user_turns(self, prompt: list[dict[str, str]]) -> list[str]:
        return [message["content"] for message in prompt if message["role"] == "user"]


class GatedTTS:
    """TTS that blocks until released, so the caller can inspect the in-flight turn."""

    name = "gated"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.calls = 0

    async def synthesize(self, text: str, language: str, voice: str | None = None) -> AudioChunk:
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return AudioChunk(wav_bytes=b"RIFF____WAVE", sample_rate=24_000, duration_s=0.1)


async def make_runner(
    client: TestClient,
    *,
    llm: Any = None,
    tts: Any = None,
    language: str = "en",
) -> tuple[TurnRunner, Recorder, EventSink]:
    state = client.app.state
    resolved = state.config.resolve(language, None, state.settings)
    scenario = state.scenarios.get(SCENARIO)
    providers = Providers(
        llm=llm or FakeLLM(), stt=FakeSTT(), tts=tts or FakeTTS(), warnings=[]
    )
    session_id = f"review-{uuid4().hex}"
    state.db.create_session(session_id, language, 2, SCENARIO, resolved.profile)

    recorder = Recorder()
    sink = EventSink(recorder.send)
    await sink.start()
    runner = TurnRunner(
        config=resolved,
        scenario=scenario,
        level=2,
        providers=providers,
        sink=sink,
        db=state.db,
        session_id=session_id,
    )
    return runner, recorder, sink


def poll_session_status(
    db: Database, session_id: str, status: str, timeout: float = 2.0
) -> dict[str, Any] | None:
    """Wait up to `timeout` for the stored session to report `status`."""
    deadline = time.monotonic() + timeout
    while True:
        review = db.review(session_id)
        if review is not None and review["session"]["status"] == status:
            return review
        if time.monotonic() >= deadline:
            return review
        time.sleep(0.01)


# ----------------------------------------------------------------- B1: bad frames
@pytest.mark.parametrize("frame", ["42", "[1, 2]", '"hello"', "null"])
def test_a_non_object_json_frame_is_rejected_without_closing_the_socket(
    client: TestClient, frame: str
) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_text(frame)

        error = receive_or_fail(ws)
        assert error["type"] == "error", error
        assert error["code"] == "bad_json", error
        assert error["recoverable"] is True

        ws.send_json({"type": "ping"})
        assert receive_or_fail(ws)["type"] == "pong", "the receive loop died on a scalar frame"


def test_explain_with_a_non_integer_turn_id_does_not_close_the_socket(
    client: TestClient,
) -> None:
    with client.websocket_connect("/ws") as ws:
        start_session(ws)
        ws.send_json({"type": "text.input", "text": FIRST_USER})
        receive_until(ws, lambda event: event["type"] == "turn.end")

        ws.send_json({"type": "explain.request", "turn_id": "not-a-number"})

        answered = []
        while not answered:
            event = receive_or_fail(ws)
            if event["type"] in {"explain.result", "error"}:
                answered.append(event)
        assert answered[-1]["type"] == "explain.result", answered[-1]
        assert answered[-1]["turn_id"] == 1, "the request must fall back to the last turn"

        ws.send_json({"type": "ping"})
        assert receive_or_fail(ws)["type"] == "pong"


# ------------------------------------------------------- B2: explain uses turn_pairs
async def test_explain_uses_the_requested_turn_and_not_the_last_one(
    client: TestClient,
) -> None:
    llm = ScriptedLLM()
    runner, recorder, sink = await make_runner(client, llm=llm)
    try:
        await runner.run_turn(1, text=FIRST_USER)
        await runner.run_turn(2, text=SECOND_USER)
        await runner.wait_aux()
        await recorder.sync(sink)

        assert runner.turn_pairs[1] == (FIRST_USER, REPLY_BY_CALL[0])
        assert runner.turn_pairs[2] == (SECOND_USER, REPLY_BY_CALL[1])

        recorder.events.clear()
        await runner.explain(1)
        await recorder.sync(sink)

        prompt = llm.last_prompt()
        assert FIRST_USER in prompt, prompt
        assert REPLY_BY_CALL[0] in prompt, prompt
        assert SECOND_USER not in prompt, "turn 2 leaked into the explanation of turn 1"
        assert REPLY_BY_CALL[1] not in prompt, "turn 2 leaked into the explanation of turn 1"

        result = next(event for event in recorder.events if event["type"] == "explain.result")
        assert result["turn_id"] == 1
    finally:
        await sink.close()


async def test_explain_of_an_unknown_turn_reports_nothing_to_explain(
    client: TestClient,
) -> None:
    llm = ScriptedLLM()
    runner, recorder, sink = await make_runner(client, llm=llm)
    try:
        await runner.run_turn(1, text=FIRST_USER)
        await runner.wait_aux()
        await recorder.sync(sink)
        recorder.events.clear()

        await runner.explain(42)
        await recorder.sync(sink)

        errors = [event for event in recorder.events if event["type"] == "error"]
        assert errors, brief(recorder.events)
        assert errors[-1]["code"] == "nothing_to_explain"
        assert not [event for event in recorder.events if event["type"] == "explain.result"]
    finally:
        await sink.close()


async def test_the_opening_is_not_explainable(client: TestClient) -> None:
    runner, recorder, sink = await make_runner(client)
    try:
        await runner.speak_opening(0)
        assert 0 not in runner.turn_pairs, "the opening has no learner turn to explain"

        recorder.events.clear()
        await runner.explain(0)
        await recorder.sync(sink)

        errors = [event for event in recorder.events if event["type"] == "error"]
        assert errors and errors[-1]["code"] == "nothing_to_explain", brief(recorder.events)
    finally:
        await sink.close()


# ------------------------------------------- B4: whisper avg_logprob -> probability
@pytest.mark.parametrize(
    ("avg_logprob", "expected"),
    [
        (0.0, 1.0),
        (-1.0, pytest.approx(math.exp(-1.0))),
        (-0.5, pytest.approx(math.exp(-0.5))),
        (-3.0, pytest.approx(math.exp(-3.0))),
    ],
)
def test_logprob_to_confidence_maps_a_log_probability_to_a_probability(
    avg_logprob: float, expected: float
) -> None:
    assert whisper_stt.logprob_to_confidence(avg_logprob) == expected


@pytest.mark.parametrize(("avg_logprob", "expected"), [(2.0, 1.0), (-900.0, 0.0)])
def test_logprob_to_confidence_stays_inside_zero_and_one(
    avg_logprob: float, expected: float
) -> None:
    assert whisper_stt.logprob_to_confidence(avg_logprob) == expected


def test_logprob_to_confidence_keeps_none() -> None:
    assert whisper_stt.logprob_to_confidence(None) is None


# ---------------------------------------------- B5: disconnect finishes the session
def test_a_disconnect_finishes_the_session(client: TestClient) -> None:
    db = client.app.state.db

    with client.websocket_connect("/ws") as ws:
        ready = start_session(ws)
        session_id = ready["session_id"]
        ws.send_json({"type": "text.input", "text": FIRST_USER})
        receive_until(ws, lambda event: event["type"] == "turn.end")
        # no `session.end`: the tab was simply closed

    review = poll_session_status(db, session_id, "finished")
    assert review is not None
    assert review["session"]["status"] == "finished", (
        "a plain disconnect left the session 'active' in the review"
    )
    assert review["session"]["ended_at"] is not None


def test_end_session_is_idempotent(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.init()
    db.create_session("s1", "en", 2, SCENARIO, "fake")

    db.end_session("s1")
    first = db.review("s1")
    assert first is not None
    assert first["session"]["status"] == "finished"

    db.end_session("s1")
    second = db.review("s1")
    assert second is not None
    assert second["session"]["status"] == "finished"
    assert second["session"]["ended_at"] is not None

    db.end_session("does-not-exist")
    assert db.review("does-not-exist") is None


# ---------------------------------------- B6: a failed turn must not orphan the TTS
async def test_a_reading_failure_is_best_effort(client: TestClient, monkeypatch) -> None:
    def boom(text: str, language: str) -> Any:
        raise RuntimeError("pykakasi is broken")

    monkeypatch.setattr(turn_module, "reading_for", boom)

    runner, recorder, sink = await make_runner(client, llm=ScriptedLLM(), language="ja")
    try:
        await asyncio.wait_for(runner.run_turn(1, text="ラーメンをください"), timeout=5)
        await runner.wait_aux()
        await recorder.sync(sink)

        assert "turn.end" in recorder.types(), brief(recorder.events)
        assert not [event for event in recorder.events if "reading" in event]
        assert [event for event in recorder.events if "translation_pt" in event], (
            "a broken reading aid must not cancel the translation"
        )
    finally:
        await sink.close()


async def test_an_evaluator_failure_cancels_the_pending_speaker(
    client: TestClient, monkeypatch
) -> None:
    tts = GatedTTS()
    runner, recorder, sink = await make_runner(client, llm=ScriptedLLM(), tts=tts)

    async def boom(*args: Any, **kwargs: Any) -> Any:
        await asyncio.wait_for(tts.started.wait(), timeout=5)
        raise RuntimeError("evaluator is down")

    monkeypatch.setattr(runner.evaluator, "evaluate", boom)

    try:
        with pytest.raises(RuntimeError, match="evaluator is down"):
            await asyncio.wait_for(runner.run_turn(1, text=FIRST_USER), timeout=5)

        await asyncio.sleep(0)
        orphans = [task for task in asyncio.all_tasks() if task.get_name().startswith("tts-1")]
        assert orphans == [], "the TTS task outlived the failed turn"
        assert tts.cancelled, "the TTS task was left running instead of being cancelled"
    finally:
        await sink.close()


# -------------------------------------- B7: provider cache ignores the language
def _resolved(language: str) -> Any:
    from app.core.config import ResolvedConfig

    return ResolvedConfig(
        language=language,
        profile="balanced",
        llm={"provider": "fake"},
        stt={"provider": "faster_whisper", "model": "small"},
        tts={"provider": "kokoro", "voice": "af_heart"},
    )


async def test_provider_cache_builds_one_instance_for_two_languages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []
    fakes = Providers(llm=FakeLLM(), stt=FakeSTT(), tts=FakeTTS(), warnings=[])

    def fake_build(cfg: Any) -> Providers:
        calls.append(cfg)
        return fakes

    monkeypatch.setattr(registry, "build_providers", fake_build)

    cache = ProviderCache()
    english = await cache.get(_resolved("en"))
    japanese = await cache.get(_resolved("ja"))

    assert english is japanese, "the same profile loaded its models once per language"
    assert len(calls) == 1, f"built {len(calls)} times for the same profile"


# ------------------------------------------- B8: SystemExit reaches the fallbacks
def test_with_fallback_degrades_when_the_builder_exits_the_process() -> None:
    warnings: list[str] = []
    fallback = FakeTTS()

    def boom() -> object:
        raise SystemExit("spacy model download aborted")

    built = _with_fallback("tts", "kokoro", boom, fallback, warnings)

    assert built is fallback, "a SystemExit escaped the fallback"
    assert warnings, "the degraded provider must be reported in session.ready"
    assert "kokoro" in warnings[0]


async def test_provider_cache_degrades_on_systemexit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    fakes = Providers(llm=FakeLLM(), stt=FakeSTT(), tts=FakeTTS(), warnings=[])

    def fake_build(cfg: Any) -> Providers:
        calls.append(cfg)
        if len(calls) == 1:
            raise SystemExit("spacy model download aborted")
        return fakes

    monkeypatch.setattr(registry, "build_providers", fake_build)

    providers = await ProviderCache().get(_resolved("en"))

    assert providers is fakes
    assert len(calls) == 2, "a SystemExit must retry once with the degraded profile"
    assert calls[1].stt == {"provider": "fake"}
    assert calls[1].tts == {"provider": "fake"}
    assert providers.warnings, "the degraded build must be reported"


# --------------------------- B9: the auto translation waits for the remaining TTS
async def test_auto_translation_starts_only_after_the_speaker(client: TestClient) -> None:
    llm = ScriptedLLM()
    tts = GatedTTS()
    runner, recorder, sink = await make_runner(client, llm=llm, tts=tts, language="ja")

    try:
        turn = asyncio.create_task(runner.run_turn(1, text=FIRST_USER))
        await asyncio.wait_for(tts.started.wait(), timeout=5)
        await recorder.sync(sink)

        assert [event for event in recorder.events if "reading" in event], (
            "the reading must be emitted before the audio"
        )
        assert llm.translation_calls == 0, "the auto translation competed with the TTS"

        tts.release.set()
        await asyncio.wait_for(turn, timeout=5)
        await asyncio.wait_for(runner.wait_aux(), timeout=5)
        await recorder.sync(sink)

        assert llm.translation_calls == 1, "the auto translation never ran"
        translations = [event for event in recorder.events if "translation_pt" in event]
        assert translations, brief(recorder.events)
        assert translations[0]["turn_id"] == 1

        tts_audio = [
            index
            for index, event in enumerate(recorder.events)
            if event["type"] == "tts.audio" and event.get("source") == "reply"
        ]
        translation_index = next(
            index
            for index, event in enumerate(recorder.events)
            if "translation_pt" in event
        )
        assert tts_audio and translation_index > max(tts_audio), (
            "the translation landed before the reply audio finished"
        )
    finally:
        await sink.close()


# ------------------------------------------- B10: review survives corrupt timings
def test_review_survives_corrupt_timings_json(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.init()
    db.create_session("s1", "en", 2, SCENARIO, "fake")
    db.add_turn("s1", 1, FIRST_USER, "Sure!", None, {}, {"total_ms": 1200})
    db.add_turn("s1", 2, SECOND_USER, "Here!", None, {}, {"total_ms": 900})

    with SQLSession(db.engine) as session:
        rows = session.exec(select(TurnRow).order_by(TurnRow.idx)).all()
        rows[0].timings_json = "{not json"
        rows[1].timings_json = ""
        for row in rows:
            session.add(row)
        session.commit()

    review = db.review("s1")

    assert review is not None
    assert review["turns"][0]["timings"] == {}
    assert review["turns"][1]["timings"] == {}
    assert review["stats"]["turns"] == 2


def test_review_still_parses_valid_timings(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.init()
    db.create_session("s1", "en", 2, SCENARIO, "fake")
    db.add_turn("s1", 1, FIRST_USER, "Sure!", None, {}, {"total_ms": 1200, "stt_ms": 40})

    review = db.review("s1")

    assert review is not None
    assert review["turns"][0]["timings"] == {"total_ms": 1200, "stt_ms": 40}


# --------------------------------------------- B11: a failed persist is reported
async def test_a_failed_persist_emits_a_warning_and_keeps_the_turn(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, recorder, sink = await make_runner(client, llm=ScriptedLLM())

    def boom(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(runner.db, "add_turn", boom)

    try:
        await asyncio.wait_for(runner.run_turn(1, text=FIRST_USER), timeout=5)
        await runner.wait_aux()
        await recorder.sync(sink)

        types = recorder.types()
        assert "turn.end" in types, brief(recorder.events)
        assert "feedback.card" in types, "a persistence failure must not eat the feedback"

        warnings = [
            event
            for event in recorder.events
            if event["type"] == "warning" and event["code"] == "persist_failed"
        ]
        assert warnings, "the lost turn was never reported to the client"
        assert warnings[0]["message"]
    finally:
        await sink.close()


async def test_a_successful_persist_emits_no_warning(client: TestClient) -> None:
    runner, recorder, sink = await make_runner(client, llm=ScriptedLLM())
    try:
        await asyncio.wait_for(runner.run_turn(1, text=FIRST_USER), timeout=5)
        await runner.wait_aux()
        await recorder.sync(sink)

        codes = [event.get("code") for event in recorder.events if event["type"] == "warning"]
        assert "persist_failed" not in codes, brief(recorder.events)
        assert runner.db.review(runner.session_id)["stats"]["turns"] == 1
    finally:
        await sink.close()


# ------------------------------------ B12: an empty reply rolls the user turn back
async def test_an_empty_reply_rolls_back_the_user_message(client: TestClient) -> None:
    llm = ScriptedLLM(silent_turns=1)
    runner, recorder, sink = await make_runner(client, llm=llm)

    try:
        await asyncio.wait_for(runner.run_turn(1, text=FIRST_USER), timeout=5)
        await recorder.sync(sink)

        assert runner.history == [], "the user message stayed after an empty reply"
        assert 1 not in runner.turn_pairs

        await asyncio.wait_for(runner.run_turn(2, text=SECOND_USER), timeout=5)
        await runner.wait_aux()
        await recorder.sync(sink)

        prompt = llm.stream_prompts[-1]
        assert SECOND_USER in "\n".join(llm.user_turns(prompt))
        assert FIRST_USER not in "\n".join(llm.user_turns(prompt)), (
            "the dropped user message came back in the next prompt"
        )
        roles = [message["role"] for message in prompt[1:]]
        assert roles == ["user"], f"consecutive turns in the prompt: {roles}"
    finally:
        await sink.close()


async def test_an_llm_failure_rolls_back_the_user_message(client: TestClient) -> None:
    llm = ScriptedLLM(failing_stream_turns=1)
    runner, recorder, sink = await make_runner(client, llm=llm)

    try:
        await asyncio.wait_for(runner.run_turn(1, text=FIRST_USER), timeout=5)
        await runner.wait_aux()
        await recorder.sync(sink)

        assert runner.history == [], "the user message survived a failed generation"
        codes = [event.get("code") for event in recorder.events if event["type"] == "error"]
        assert "llm_failed" in codes, brief(recorder.events)
        assert "empty_reply" in codes, brief(recorder.events)
        assert 1 not in runner.turn_pairs
    finally:
        await sink.close()
