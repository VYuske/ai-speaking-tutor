from __future__ import annotations

from typing import Any

import pytest

from app.agent import translate as translate_module
from app.agent.translate import (
    TRANSLATION_SCHEMA,
    build_translation_messages,
    clear_cache,
    translate_pt,
)

TUTOR_TEXT = 'He said "hi" — it\'s fine.'
TUTOR_TEXT_PT = "Ele disse “oi” — está tudo bem."
JA_TEXT = "もちろんです。ホットとアイスのどちらにしますか。"


class RecordingLLM:
    """Returns a fixed payload (or raises) and keeps every call for assertions."""

    name = "recording"

    def __init__(
        self,
        payload: Any = None,
        error: Exception | None = None,
    ) -> None:
        self.payload = {"translation_pt": "Olá"} if payload is None else payload
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        **overrides: Any,
    ) -> dict[str, Any]:
        self.calls.append({"messages": messages, "schema": schema, "overrides": overrides})
        if self.error is not None:
            raise self.error
        return self.payload


@pytest.fixture(autouse=True)
def _clean_cache() -> Any:
    clear_cache()
    yield
    clear_cache()


def _system(messages: list[dict[str, str]]) -> str:
    return "\n".join(message["content"] for message in messages if message["role"] == "system")


def _user(messages: list[dict[str, str]]) -> str:
    return "\n".join(message["content"] for message in messages if message["role"] == "user")


# --- messages -------------------------------------------------------------------------


def test_system_prompt_asks_for_brazilian_portuguese_without_explanations() -> None:
    system = _system(build_translation_messages(TUTOR_TEXT, "en")).lower()
    assert "brazilian portuguese" in system
    assert "no explanations" in system or "no explanation" in system
    assert "markdown" in system


def test_user_message_embeds_the_raw_text_and_the_language() -> None:
    messages = build_translation_messages(JA_TEXT, "ja")
    user = _user(messages)
    assert JA_TEXT in user
    assert "ja" in user
    assert "Japanese" in user


def test_user_message_does_not_quote_the_text_with_repr() -> None:
    user = _user(build_translation_messages(TUTOR_TEXT, "en"))
    assert TUTOR_TEXT in user
    assert repr(TUTOR_TEXT) not in user
    assert "\\'" not in user


def test_schema_asks_only_for_the_translation_field() -> None:
    assert TRANSLATION_SCHEMA["properties"] == {"translation_pt": {"type": "string"}}
    assert TRANSLATION_SCHEMA["required"] == ["translation_pt"]


# --- translate_pt ---------------------------------------------------------------------


async def test_returns_the_translation_trimmed() -> None:
    llm = RecordingLLM({"translation_pt": "  Olá  \n"})
    assert await translate_pt(llm, TUTOR_TEXT, "en") == "Olá"


async def test_calls_chat_json_with_the_dedicated_schema_and_temperature() -> None:
    llm = RecordingLLM({"translation_pt": "Olá"})
    await translate_pt(llm, TUTOR_TEXT, "en")
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["schema"] is TRANSLATION_SCHEMA
    assert call["overrides"] == {"temperature": 0.0, "language": "en"}


async def test_failure_returns_none_without_raising() -> None:
    llm = RecordingLLM(error=RuntimeError("boom"))
    assert await translate_pt(llm, TUTOR_TEXT, "en") is None


async def test_blank_text_returns_none_and_skips_the_llm() -> None:
    llm = RecordingLLM()
    assert await translate_pt(llm, "", "en") is None
    assert await translate_pt(llm, "   \n\t ", "en") is None
    assert llm.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"translation_pt": ""},
        {"translation_pt": "   "},
        {"translation_pt": None},
        {"translation_pt": 42},
        {"other": "Olá"},
    ],
)
async def test_malformed_payload_returns_none(payload: dict[str, Any]) -> None:
    assert await translate_pt(RecordingLLM(payload), TUTOR_TEXT, "en") is None


# --- cache ----------------------------------------------------------------------------


async def test_same_text_is_translated_once() -> None:
    llm = RecordingLLM({"translation_pt": TUTOR_TEXT_PT})
    first = await translate_pt(llm, TUTOR_TEXT, "en")
    second = await translate_pt(llm, TUTOR_TEXT, "en")
    assert first == second == TUTOR_TEXT_PT
    assert len(llm.calls) == 1


async def test_another_text_calls_the_llm_again() -> None:
    llm = RecordingLLM({"translation_pt": TUTOR_TEXT_PT})
    await translate_pt(llm, TUTOR_TEXT, "en")
    await translate_pt(llm, "Would you like anything else?", "en")
    assert len(llm.calls) == 2


async def test_the_language_is_part_of_the_cache_key() -> None:
    llm = RecordingLLM({"translation_pt": TUTOR_TEXT_PT})
    await translate_pt(llm, TUTOR_TEXT, "en")
    await translate_pt(llm, TUTOR_TEXT, "de")
    assert len(llm.calls) == 2


async def test_clear_cache_forces_a_new_call() -> None:
    llm = RecordingLLM({"translation_pt": TUTOR_TEXT_PT})
    await translate_pt(llm, TUTOR_TEXT, "en")
    clear_cache()
    await translate_pt(llm, TUTOR_TEXT, "en")
    assert len(llm.calls) == 2


async def test_failures_are_not_cached() -> None:
    failing = RecordingLLM(error=RuntimeError("boom"))
    assert await translate_pt(failing, TUTOR_TEXT, "en") is None
    working = RecordingLLM({"translation_pt": TUTOR_TEXT_PT})
    assert await translate_pt(working, TUTOR_TEXT, "en") == TUTOR_TEXT_PT
    assert len(working.calls) == 1


async def test_cache_stays_bounded() -> None:
    llm = RecordingLLM({"translation_pt": TUTOR_TEXT_PT})
    for index in range(translate_module.CACHE_SIZE + 20):
        await translate_pt(llm, f"Line {index}.", "en")
    assert len(translate_module._cache) <= translate_module.CACHE_SIZE
