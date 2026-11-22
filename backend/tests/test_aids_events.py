"""Contract for the learning aids wired into the turn runner and the WS session.

Deterministic: fake providers, no network, no models. Every `receive_until` waits for
an event the server always emits (`state:idle`, `turn.end`, `error`), so a missing
implementation fails with an assertion instead of hanging the suite.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.agent.translate import clear_cache
from app.orchestrator.events import EventSink
from app.orchestrator.turn import AidsConfig, TurnRunner
from app.providers.base import Providers
from app.providers.fakes import TRANSLATION_SAMPLE, FakeLLM, FakeSTT, FakeTTS

JA_SCENARIO = "ramen-shop-order"
EN_SCENARIO = "cafe-order"
ZH_SCENARIO = "restaurant-table"
TURN_TEXT = "ラーメンをください"
FLUSH_TEXT = "もう一度お願いします"
SENTINEL = "__sync__"


@pytest.fixture(autouse=True)
def _clean_translation_cache() -> Any:
    """`translate_pt` caches by (language, text) for the whole process."""
    clear_cache()
    yield
    clear_cache()


# ------------------------------------------------------------------------ helpers
def is_idle(event: dict[str, Any]) -> bool:
    return event["type"] == "state" and event["state"] == "idle"


def is_turn_end(event: dict[str, Any]) -> bool:
    return event["type"] == "turn.end"


def is_translation_result_or_error(event: dict[str, Any]) -> bool:
    return event["type"] in {"translation.result", "error"}


def is_warning_or_error(event: dict[str, Any]) -> bool:
    return event["type"] in {"warning", "error"}


def brief(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Events without the audio payload, so an assertion message stays readable."""
    return [{key: value for key, value in event.items() if key != "wav_b64"} for event in events]


class Session:
    """One WS session plus every event it emitted, in order."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.events: list[dict[str, Any]] = []

    def send(self, payload: dict[str, Any]) -> None:
        self.ws.send_json(payload)

    def receive_until(
        self, predicate: Callable[[dict[str, Any]], bool], limit: int = 500
    ) -> list[dict[str, Any]]:
        received: list[dict[str, Any]] = []
        for _ in range(limit):
            event = self.ws.receive_json()
            self.events.append(event)
            received.append(event)
            if predicate(event):
                return received
        raise AssertionError(f"event not received; last events: {self.events[-5:]}")

    def start(self, language: str = "ja", scenario_id: str = JA_SCENARIO, **extra: Any) -> dict:
        payload = {
            "type": "session.start",
            "language": language,
            "level": 2,
            "scenario_id": scenario_id,
            **extra,
        }
        self.send(payload)
        events = self.receive_until(is_idle)
        return next(event for event in events if event["type"] == "session.ready")

    def turn(self, text: str = TURN_TEXT) -> list[dict[str, Any]]:
        self.send({"type": "text.input", "text": text})
        return self.receive_until(is_turn_end)

    def flush(self) -> None:
        """Run one more turn so aux events for the previous turn have landed.

        A background translation may arrive after `turn.end` (that is the contract);
        starting another turn gives the event sink many scheduling points to drain it.
        """
        self.turn(FLUSH_TEXT)

    def aux(self, turn_id: int | None = None) -> list[dict[str, Any]]:
        return [
            event
            for event in self.events
            if event["type"] == "assistant.aux"
            and (turn_id is None or event.get("turn_id") == turn_id)
        ]

    def translations(self, turn_id: int | None = None) -> list[dict[str, Any]]:
        return [
            event for event in self.aux(turn_id) if "translation_pt" in event
        ]

    def readings(self, turn_id: int | None = None) -> list[dict[str, Any]]:
        return [event for event in self.aux(turn_id) if "reading" in event]


class Recorder:
    """Event sink target with a barrier: `sync()` proves everything before it drained."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._synced: Any = None

    async def send(self, event: dict[str, Any]) -> None:
        if event["type"] == SENTINEL:
            self._synced.set()
            return
        self.events.append(event)

    async def sync(self, sink: EventSink) -> None:
        self._synced = asyncio.Event()
        await sink.emit(SENTINEL)
        await self._synced.wait()

    def types(self) -> list[str]:
        return [event["type"] for event in self.events]


async def make_runner(
    client: TestClient,
    language: str = "ja",
    scenario_id: str = JA_SCENARIO,
    aids: AidsConfig | None = None,
    llm: Any = None,
) -> tuple[TurnRunner, Recorder, EventSink]:
    state = client.app.state
    resolved = state.config.resolve(language, None, state.settings)
    scenario = state.scenarios.get(scenario_id)
    providers = Providers(
        llm=llm or FakeLLM(), stt=FakeSTT(), tts=FakeTTS(), warnings=[]
    )
    session_id = f"aids-{uuid4().hex}"
    state.db.create_session(session_id, language, 2, scenario_id, resolved.profile)

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
        aids=aids,
    )
    return runner, recorder, sink


# --------------------------------------------------------------------- AidsConfig


def test_aids_config_defaults_to_auto_translation_and_reading() -> None:
    aids = AidsConfig()

    assert aids.translation == "auto"
    assert aids.reading is True
    assert aids.to_public() == {"translation": "auto", "reading": True}


def test_aids_config_parses_a_partial_payload() -> None:
    assert AidsConfig.from_payload({"translation": "manual", "reading": False}).to_public() == {
        "translation": "manual",
        "reading": False,
    }
    assert AidsConfig.from_payload({"translation": "manual"}).reading is True
    assert AidsConfig.from_payload({"reading": False}).translation == "auto"


@pytest.mark.parametrize("payload", [None, {}, "auto", [], {"translation": "sometimes"}])
def test_aids_config_falls_back_to_the_defaults(payload: Any) -> None:
    aids = AidsConfig.from_payload(payload)

    assert aids.translation == "auto"
    assert aids.reading is True


# ------------------------------------------------------------------ session.ready


def test_session_ready_reports_the_default_aids(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        session = Session(ws)
        ready = session.start()

        assert ready["aids"] == {"translation": "auto", "reading": True}


def test_session_ready_echoes_the_requested_aids(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        session = Session(ws)
        ready = session.start(aids={"translation": "manual", "reading": False})

        assert ready["aids"] == {"translation": "manual", "reading": False}


# ------------------------------------------------------------------- auto: ja


def test_auto_ja_emits_furigana_before_turn_end_and_translation_after(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        session = Session(ws)
        session.start()

        turn_events = session.turn()

        readings = [event for event in turn_events if "reading" in event]
        assert readings, f"no assistant.aux with reading; events: {brief(session.events)}"
        reading = readings[0]["reading"]
        assert readings[0]["turn_id"] == 1
        assert reading["kind"] == "furigana"
        assert reading["kana"], f"kana is empty: {reading}"
        assert reading["text"], f"romaji is empty: {reading}"
        assert reading["segments"], f"segments are empty: {reading}"
        assert "translation_pt" not in readings[0]

        session.flush()

        translations = session.translations(1)
        assert translations, f"no translation aux for turn 1; events: {brief(session.events)}"
        assert translations[0]["translation_pt"] == TRANSLATION_SAMPLE["ja"]


def test_auto_ja_opening_gets_reading_and_translation(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        session = Session(ws)
        session.start()

        opening = session.readings(0)
        assert opening, f"the opening (turn_id=0) got no reading; events: {brief(session.events)}"
        assert opening[0]["reading"]["kind"] == "furigana"

        session.flush()

        translations = session.translations(0)
        assert translations, f"the opening got no translation; events: {brief(session.events)}"
        assert translations[0]["translation_pt"] == TRANSLATION_SAMPLE["ja"]


async def test_translation_does_not_delay_turn_end(client: TestClient) -> None:
    """The turn finishes while the translation is still parked in the model call."""

    class ParkedTranslationLLM(FakeLLM):
        def __init__(self) -> None:
            super().__init__()
            self.release = asyncio.Event()
            self.translation_calls = 0

        async def chat_json(self, messages: Any, schema: Any, **overrides: Any) -> dict:
            if "translation_pt" in schema.get("properties", {}):
                self.translation_calls += 1
                await self.release.wait()
            return await super().chat_json(messages, schema, **overrides)

    llm = ParkedTranslationLLM()
    runner, recorder, sink = await make_runner(client, llm=llm)
    try:
        await asyncio.wait_for(runner.run_turn(1, text=TURN_TEXT), timeout=5)
        await recorder.sync(sink)

        assert "turn.end" in recorder.types(), recorder.types()
        assert llm.translation_calls == 1, "the auto translation was never requested"
        assert not [event for event in recorder.events if "translation_pt" in event], (
            "the translation was already delivered; it did not run in the background"
        )

        llm.release.set()
        await asyncio.wait_for(runner.wait_aux(), timeout=5)
        await recorder.sync(sink)

        late = [event for event in recorder.events if "translation_pt" in event]
        assert late, f"no late translation aux after turn.end: {recorder.types()}"
        assert late[0]["turn_id"] == 1
    finally:
        await sink.close()


async def test_auto_translation_failure_is_reported_and_keeps_the_turn(
    client: TestClient,
) -> None:
    class BrokenTranslationLLM(FakeLLM):
        async def chat_json(self, messages: Any, schema: Any, **overrides: Any) -> dict:
            if "translation_pt" in schema.get("properties", {}):
                raise RuntimeError("translation backend down")
            return await super().chat_json(messages, schema, **overrides)

    runner, recorder, sink = await make_runner(client, llm=BrokenTranslationLLM())
    try:
        await runner.run_turn(1, text=TURN_TEXT)
        await runner.wait_aux()
        await recorder.sync(sink)

        assert "turn.end" in recorder.types(), recorder.types()
        assert "error" not in recorder.types(), recorder.types()
        assert not [event for event in recorder.events if "translation_pt" in event]
        assert [event for event in recorder.events if "reading" in event], (
            "the reading aid must survive a translation failure"
        )

        failures = [
            event
            for event in recorder.events
            if event["type"] == "assistant.aux" and event.get("translation_failed")
        ]
        assert failures, f"the client was never told the translation failed: {recorder.types()}"
        assert failures[0]["turn_id"] == 1
        assert "translation_pt" not in failures[0]
    finally:
        await sink.close()


# ---------------------------------------------------------------- manual: ja


def test_manual_has_no_translation_until_translate_request(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        session = Session(ws)
        session.start(aids={"translation": "manual"})

        session.turn()
        session.flush()

        assert session.translations(0) == []
        assert session.translations(1) == []
        assert session.readings(1), "the reading aid does not depend on the translation mode"

        session.send({"type": "translate.request", "turn_id": 1})
        events = session.receive_until(is_translation_result_or_error)

        assert events[-1]["type"] == "translation.result", events[-1]
        assert events[-1]["turn_id"] == 1
        assert events[-1]["translation_pt"] == TRANSLATION_SAMPLE["ja"]


def test_translate_request_for_an_unknown_turn_reports_an_error(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        session = Session(ws)
        session.start(aids={"translation": "manual"})

        session.send({"type": "translate.request", "turn_id": 42})
        events = session.receive_until(is_translation_result_or_error)

        assert events[-1]["type"] == "error", events[-1]
        assert events[-1]["code"] == "nothing_to_translate"


# ------------------------------------------------------------------ aids.update


def test_aids_update_switches_the_next_turn_to_auto(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        session = Session(ws)
        session.start(language="en", scenario_id=EN_SCENARIO, aids={"translation": "manual"})

        session.turn("I would like a coffee")
        session.send({"type": "aids.update", "translation": "auto"})
        session.turn("Thanks")
        session.flush()

        assert session.translations(1) == [], "turn 1 ran before the switch"
        translations = session.translations(2)
        assert translations, f"turn 2 was not translated after aids.update: {brief(session.events)}"
        assert translations[0]["translation_pt"] == TRANSLATION_SAMPLE["en"]


def test_aids_update_rejects_an_unknown_mode(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        session = Session(ws)
        session.start(aids={"translation": "manual"})

        session.send({"type": "aids.update", "translation": "sometimes"})
        events = session.receive_until(is_warning_or_error)

        assert events[-1]["type"] == "warning", events[-1]
        assert events[-1]["code"] == "invalid_aids"

        session.turn()
        session.flush()
        assert session.translations(1) == [], "an invalid mode must not enable translation"


# ------------------------------------------------------------------ languages


def test_english_turn_translates_without_a_reading(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        session = Session(ws)
        session.start(language="en", scenario_id=EN_SCENARIO)

        session.turn("I would like a coffee")
        session.flush()

        assert session.readings() == [], "en has nothing to romanize"
        translations = session.translations(1)
        assert translations, f"en turn got no translation: {brief(session.events)}"
        assert translations[0]["translation_pt"] == TRANSLATION_SAMPLE["en"]


def test_zh_turn_gets_tonal_pinyin(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        session = Session(ws)
        session.start(language="zh", scenario_id=ZH_SCENARIO, aids={"translation": "manual"})

        session.turn("我想要一杯拿铁")

        readings = session.readings(1)
        assert readings, f"no reading for the zh reply: {brief(session.events)}"
        assert readings[0]["reading"]["kind"] == "pinyin"
        assert readings[0]["reading"]["kana"] is None
        assert readings[0]["reading"]["text"]


def test_reading_disabled_emits_no_reading_but_keeps_translation(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        session = Session(ws)
        session.start(aids={"translation": "manual", "reading": False})

        session.turn()
        session.flush()

        assert session.readings() == [], f"reading is disabled: {brief(session.events)}"

        session.send({"type": "translate.request", "turn_id": 1})
        events = session.receive_until(is_translation_result_or_error)
        assert events[-1]["type"] == "translation.result", events[-1]
        assert events[-1]["translation_pt"] == TRANSLATION_SAMPLE["ja"]
