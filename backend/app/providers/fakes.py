from __future__ import annotations

import ast
import asyncio
import copy
import math
import re
from collections.abc import AsyncIterator
from typing import Any

import numpy as np

from app.core.audio import wav_bytes
from app.providers.base import AudioChunk, Transcript

CANNED_USER_TEXT: dict[str, str] = {
    "en": "Hi, I would like a large latte with oat milk, please.",
    "ja": "すみません、ラテを一つください。",
    "zh": "你好，我想要一杯拿铁。",
    "de": "Hallo, ich möchte einen Latte, bitte.",
}

CANNED_REPLY: dict[str, str] = {
    "en": "Of course! Would you like that hot or iced, and do you want anything to eat with it?",
    "ja": "もちろんです。ホットとアイスのどちらにしますか。何か食べ物もいかがですか。",
    "zh": "好的，您要热的还是冰的？需要点什么吃的吗？",
    "de": "Natürlich! Möchten Sie ihn heiß oder eisgekühlt, und dazu etwas zu essen?",
}

EXPLAIN_SAMPLE: dict[str, dict[str, Any]] = {
    "en": {
        "explanation_pt": "Em inglês falado, 'I would like' soa mais natural contraído.",
        "example_target": "I'd like a coffee, please.",
        "example_pt": "Eu gostaria de um café, por favor.",
    },
    "ja": {
        "explanation_pt": "「ください」é a forma educada de pedir algo.",
        "example_target": "ラテを一つください。",
        "example_pt": "Um latte, por favor.",
    },
    "zh": {
        "explanation_pt": "「我想要」é a forma natural de dizer 'eu quero'.",
        "example_target": "我想要一杯拿铁。",
        "example_pt": "Eu quero um latte.",
    },
    "de": {
        "explanation_pt": "「möchte」é o verbo modal educado para pedir algo.",
        "example_target": "Ich möchte eine Fahrkarte, bitte.",
        "example_pt": "Eu gostaria de uma passagem, por favor.",
    },
}

TRANSLATION_SAMPLE: dict[str, str] = {
    "en": "Claro! Quer quente ou gelado, e deseja algo para comer junto?",
    "ja": "Claro. Prefere quente ou gelado? Também gostaria de algo para comer?",
    "zh": "Certo. Você quer quente ou gelado? Deseja algo para comer?",
    "de": "Naturalmente! Quer quente ou gelado, e algo para comer junto?",
}

EVALUATION_SAMPLE: dict[str, dict[str, Any]] = {
    "en": {
        "corrections": [
            {
                "original": "I would like a large latte",
                "corrected": "I'd like a large latte",
                "explanation_pt": "Contração natural em inglês falado.",
                "type": "naturalness",
                "severity": "low",
                "confidence": 0.8,
            }
        ],
        "vocab": [{"term": "oat milk", "translation_pt": "leite de aveia"}],
        "scene_progress": {"goals_met": ["pediu bebida"], "status": "ongoing"},
        "language_leak": False,
    }
}


_EVAL_LEARNER = re.compile(r"Learner said:\s*(.+?)(?:\n|$)", re.S)
_EVAL_QUOTES = ("'", '"')
_EVAL_SUFFIX = {
    "en": " (more natural)",
    "ja": "（より自然）",
    "zh": "（更自然）",
    "de": " (natürlicher)",
}


def _unquote_learner_line(raw: str) -> str:
    """Unquote a line the prompt wrapped in matching quotes; keep raw text as-is (AST-034).

    ``build_evaluation_messages`` writes ``Learner said: {user_text}`` verbatim, so a turn
    such as ``5`` or ``[1]`` must never reach ``literal_eval``: it would come back as a
    non-string. Mirrored in ``scripts/eval_prompts.py``; the two cannot import each other.
    """
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in _EVAL_QUOTES:
        try:
            value = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return raw
        return value if isinstance(value, str) else raw
    return raw


def _learner_quote(messages: list[dict[str, str]]) -> str:
    """Quote the learner turn from the evaluation prompt, keeping the fake realistic."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        match = _EVAL_LEARNER.search(message.get("content", ""))
        if not match:
            continue
        value = _unquote_learner_line(match.group(1).strip())
        if value.strip():
            return value.strip()
    return ""


class FakeLLM:
    name = "fake"

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        cfg = cfg or {}
        self.delay_ms = int(cfg.get("delay_ms", 0))

    async def chat_stream(
        self, messages: list[dict[str, str]], **overrides: Any
    ) -> AsyncIterator[str]:
        language = str(overrides.get("language", "en"))
        reply = CANNED_REPLY.get(language, CANNED_REPLY["en"])
        words = reply.split(" ")
        for index, word in enumerate(words):
            if self.delay_ms:
                await asyncio.sleep(self.delay_ms / 1000)
            yield word if index == 0 else f" {word}"

    async def chat_json(
        self, messages: list[dict[str, str]], schema: dict[str, Any], **overrides: Any
    ) -> dict[str, Any]:
        language = str(overrides.get("language", "en"))
        if "translation_pt" in schema.get("properties", {}):
            return {"translation_pt": TRANSLATION_SAMPLE.get(language, TRANSLATION_SAMPLE["en"])}
        if "explanation_pt" in schema.get("properties", {}):
            return EXPLAIN_SAMPLE.get(language, EXPLAIN_SAMPLE["en"])
        payload = copy.deepcopy(EVALUATION_SAMPLE.get(language, EVALUATION_SAMPLE["en"]))
        quote = _learner_quote(messages)
        if quote and payload.get("corrections"):
            payload["corrections"][0]["original"] = quote
            payload["corrections"][0]["corrected"] = (
                f"{quote}{_EVAL_SUFFIX.get(language, _EVAL_SUFFIX['en'])}"
            )
        return payload

    async def aclose(self) -> None:
        return None


class FakeSTT:
    name = "fake"

    async def transcribe(
        self, pcm16: bytes, sample_rate: int, language: str | None = None
    ) -> Transcript:
        lang = language or "en"
        duration = (len(pcm16) / 2) / float(sample_rate) if sample_rate else 0.0
        return Transcript(
            text=CANNED_USER_TEXT.get(lang, CANNED_USER_TEXT["en"]),
            language=lang,
            confidence=0.95,
            duration_s=duration,
        )


class FakeTTS:
    name = "fake"

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        self.sample_rate = 24_000

    async def synthesize(
        self, text: str, language: str, voice: str | None = None
    ) -> AudioChunk:
        seconds = min(1.6, max(0.35, len(text) * 0.02))
        frames = int(self.sample_rate * seconds)
        t = np.arange(frames, dtype=np.float32) / self.sample_rate
        envelope = np.minimum(1.0, np.minimum(t * 20.0, (seconds - t) * 20.0))
        tone = 0.18 * np.sin(2 * math.pi * 440.0 * t) * np.clip(envelope, 0.0, 1.0)
        pcm = (tone * 32767.0).astype(np.int16).tobytes()
        return AudioChunk(
            wav_bytes=wav_bytes(pcm, self.sample_rate),
            sample_rate=self.sample_rate,
            duration_s=seconds,
        )
