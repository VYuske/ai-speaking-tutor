from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.audio import wav_bytes
from app.core.config import AppConfig, Settings
from app.core.errors import ProviderUnavailable
from app.core.text import sanitize_for_tts
from app.orchestrator.events import EventSink
from app.orchestrator.turn import TurnRunner
from app.providers.base import AudioChunk, Providers
from app.providers.fakes import FakeLLM, FakeSTT
from app.providers.tts.kokoro import (
    DEFAULT_CPU_THREADS,
    apply_torch_threads,
    cpu_threads_from_config,
)

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_MARKS = ("*", "`", "#", "](")

# ------------------------------------------------------------------ sanitizer


@pytest.mark.parametrize(
    "raw,spoken",
    [
        ("You'd like an **orange** coffee.", "You'd like an orange coffee."),
        ("Ask the [menu](https://example.com/menu) first.", "Ask the menu first."),
        ("Order a `latte` please.", "Order a latte please."),
        ("## Menu\nPick one.", "Menu Pick one."),
        ("*Hot* or **iced**?", "Hot or iced?"),
        ("- latte\n- mocha", "latte mocha"),
        ("1. latte\n2. mocha", "latte mocha"),
        ("**An**  orange\n\nlatte", "An orange latte"),
        ("I would like a large coffee, please.", "I would like a large coffee, please."),
        ("コーヒーを温めてください。", "コーヒーを温めてください。"),
        ("好的，您想要什么菜呢？", "好的，您想要什么菜呢？"),
        ("", ""),
    ],
)
def test_sanitize_for_tts(raw: str, spoken: str) -> None:
    assert sanitize_for_tts(raw) == spoken


# ------------------------------------------------------- _emit_tts text path


class RecordingTTS:
    name = "recording"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def synthesize(
        self, text: str, language: str, voice: str | None = None
    ) -> AudioChunk:
        self.calls.append(text)
        pcm = b"\x00\x00" * 240
        return AudioChunk(
            wav_bytes=wav_bytes(pcm, 24_000),
            sample_rate=24_000,
            duration_s=len(pcm) / 2 / 24_000,
        )


class MarkdownLLM(FakeLLM):
    """Fake LLM that answers with markdown, like the real qwen3.5:4b does."""

    async def chat_stream(
        self, messages: list[dict[str, str]], **overrides: Any
    ) -> AsyncIterator[str]:
        for delta in ["You'd like an **orange** ", "coffee, please."]:
            yield delta


def build_runner(client: TestClient, tts: Any, llm: Any) -> tuple[TurnRunner, list]:
    state = client.app.state
    resolved = state.config.resolve("en", None, state.settings)
    events: list[dict[str, Any]] = []

    async def send(event: dict[str, Any]) -> None:
        events.append(event)

    sink = EventSink(send)
    runner = TurnRunner(
        config=resolved,
        scenario=state.scenarios.get("cafe-order"),
        level=2,
        providers=Providers(llm=llm, stt=FakeSTT(), tts=tts, warnings=[]),
        sink=sink,
        db=state.db,
        session_id="tts-text-test",
    )
    return runner, events


def audio_texts(events: list[dict[str, Any]]) -> list[str]:
    return [
        str(event.get("text", "")) for event in events if event["type"] == "tts.audio"
    ]


async def test_emit_tts_sanitizes_before_synthesis(client: TestClient) -> None:
    tts = RecordingTTS()
    runner, events = build_runner(client, tts, FakeLLM())
    await runner.sink.start()

    assert await runner._emit_tts(1, 0, "You'd like an **orange** coffee.", "reply")
    await runner.sink.close()

    assert tts.calls == ["You'd like an orange coffee."]
    assert audio_texts(events) == ["You'd like an orange coffee."]


async def test_emit_tts_skips_a_chunk_that_is_pure_markdown(client: TestClient) -> None:
    tts = RecordingTTS()
    runner, events = build_runner(client, tts, FakeLLM())
    await runner.sink.start()

    assert not await runner._emit_tts(1, 0, "**", "reply")
    await runner.sink.close()

    assert tts.calls == []
    assert events == []


async def test_reply_transcript_keeps_markdown_but_tts_does_not(client: TestClient) -> None:
    tts = RecordingTTS()
    runner, events = build_runner(client, tts, MarkdownLLM())
    await runner.sink.start()
    await runner.run_turn(1, text="I would like an orange coffee")
    await runner.sink.close()

    assert tts.calls
    assert all(mark not in text for text in tts.calls for mark in MARKDOWN_MARKS)
    assert all(mark not in text for text in audio_texts(events) for mark in MARKDOWN_MARKS)

    spoken = " ".join(tts.calls)
    assert "orange" in spoken
    assert runner.history[-1] == {
        "role": "assistant",
        "content": "You'd like an **orange** coffee, please.",
    }


# --------------------------------------------------------- kokoro cpu threads


def test_cpu_threads_defaults_to_six() -> None:
    assert DEFAULT_CPU_THREADS == 6
    assert cpu_threads_from_config({}) == 6


@pytest.mark.parametrize("value", [4, "4", 1])
def test_cpu_threads_reads_the_config(value: Any) -> None:
    assert cpu_threads_from_config({"cpu_threads": value}) == int(value)


@pytest.mark.parametrize("value", [0, -2, "many", None])
def test_cpu_threads_rejects_invalid_values(value: Any) -> None:
    with pytest.raises(ProviderUnavailable):
        cpu_threads_from_config({"cpu_threads": value})


def test_compact_profile_exposes_cpu_threads() -> None:
    config = AppConfig.load(ROOT / "config" / "models.toml")
    resolved = config.resolve("ja", "compact")
    assert resolved.tts["provider"] == "kokoro"
    assert resolved.tts["cpu_threads"] == 6
    # zh has no profile of its own: the container selects it with AST_PROFILE.
    settings = Settings(config_path=ROOT / "config" / "models.toml", profile="compact")
    from_env = config.resolve("zh", settings=settings)
    assert from_env.profile == "compact"
    assert from_env.tts["cpu_threads"] == 6


def test_torch_threads_are_applied() -> None:
    torch = pytest.importorskip("torch")
    original = torch.get_num_threads()
    try:
        apply_torch_threads(3)
        assert torch.get_num_threads() == 3
    finally:
        torch.set_num_threads(original)
