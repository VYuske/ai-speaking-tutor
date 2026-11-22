from __future__ import annotations

import io
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class Transcript:
    text: str
    language: str | None = None
    confidence: float | None = None
    duration_s: float = 0.0


@dataclass(slots=True)
class AudioChunk:
    wav_bytes: bytes
    sample_rate: int
    duration_s: float


class LLMProvider(Protocol):
    name: str

    def chat_stream(
        self, messages: list[dict[str, str]], **overrides: object
    ) -> AsyncIterator[str]: ...

    async def chat_json(
        self, messages: list[dict[str, str]], schema: dict[str, object], **overrides: object
    ) -> dict[str, object]: ...


class STTProvider(Protocol):
    name: str

    async def transcribe(
        self, pcm16: bytes, sample_rate: int, language: str | None = None
    ) -> Transcript: ...


class TTSProvider(Protocol):
    name: str

    async def synthesize(
        self, text: str, language: str, voice: str | None = None
    ) -> AudioChunk: ...


@dataclass(slots=True)
class Providers:
    llm: LLMProvider
    stt: STTProvider
    tts: TTSProvider
    warnings: list[str]


def wav_from_pcm16(pcm16: bytes, sample_rate: int, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm16)
    return buffer.getvalue()
