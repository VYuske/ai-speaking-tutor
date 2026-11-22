"""AST-008a: golden-file snapshots of the prompt builders.

Every case below is deterministic: one real scenario per language, fixed
history, fixed memory, no clock and no network. A change in ``prompts.py``,
``scenario.py`` or in the YAML scenario shows up as a readable JSON diff in
review instead of slipping through silently.

Regenerate after an intentional prompt change::

    cd backend && UPDATE_SNAPSHOTS=1 uv run pytest tests/test_prompt_snapshots.py -q

Then read the diff of ``tests/snapshots/`` in the pull request. Snapshots are
generated files: never hand-edit them.
"""

from __future__ import annotations

import difflib
import functools
import json
import os
from pathlib import Path

import pytest

from app.agent.prompts import (
    LANGUAGE_LABEL,
    build_evaluation_messages,
    build_explain_messages,
    build_free_conversation_messages,
    build_roleplay_messages,
)
from app.agent.scenario import Scenario, ScenarioRepository, free_scenario

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = Path(__file__).resolve().parent / "snapshots"

LANGUAGES = ("en", "ja", "zh", "de")
LEVELS = (1, 3, 6)
UPDATE = os.environ.get("UPDATE_SNAPSHOTS") == "1"

# Mirrors config/models.toml (en/de CEFR, ja JLPT, zh HSK).
LEVEL_SCALE = {"en": "CEFR", "ja": "JLPT", "zh": "HSK", "de": "CEFR"}

# One fixed beginner-to-intermediate scenario per language, all with level 1..4.
SCENARIO_BY_LANGUAGE = {
    "en": "cafe-order",
    "ja": "ramen-shop-order",
    "zh": "restaurant-table",
    "de": "bahn-ticket",
}

HISTORY: tuple[dict[str, str], ...] = (
    {"role": "assistant", "content": "Good morning! How can I help you today?"},
    {"role": "user", "content": "Hello. I would like one coffee and a snack, please."},
)
MEMORY = (
    "Name: Ana. Level: false beginner.\n"
    "Prefers short answers. Struggles with past tense and polite forms."
)
EVALUATION_USER_TEXT = "I go to the station yesterday and buy two ticket."
EVALUATION_ASSISTANT_TEXT = "Two tickets for the express, then. Where are you heading?"
EXPLAIN_USER_TEXT = "I go yesterday"
EXPLAIN_ASSISTANT_TEXT = "Yesterday you went?"


@functools.lru_cache(maxsize=1)
def _repository() -> ScenarioRepository:
    repository = ScenarioRepository(ROOT / "scenarios")
    repository.load()
    return repository


def _scenario(language: str) -> Scenario:
    return _repository().get(SCENARIO_BY_LANGUAGE[language])


def _serialize(messages: list[dict[str, str]]) -> str:
    return json.dumps(messages, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def _assert_snapshot(name: str, messages: list[dict[str, str]]) -> None:
    text = _serialize(messages)
    path = SNAPSHOTS / name
    if UPDATE:
        SNAPSHOTS.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    if not path.is_file():
        pytest.fail(
            f"missing snapshot {path.relative_to(ROOT)}; "
            f"generate it with: UPDATE_SNAPSHOTS=1 uv run pytest {Path(__file__).name} -q"
        )
    expected = path.read_text(encoding="utf-8")
    if text != expected:
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                text.splitlines(),
                fromfile=f"snapshots/{name}",
                tofile="current prompt",
                lineterm="",
            )
        )
        pytest.fail(
            f"prompt drift in {path.relative_to(ROOT)}.\n"
            "If the change is intended, regenerate with "
            "UPDATE_SNAPSHOTS=1 and review the diff. Otherwise fix prompts.py.\n"
            f"{diff}"
        )


ROLEPLAY_CASES = [(language, level) for language in LANGUAGES for level in LEVELS]
FREE_LEVEL = 3


@pytest.mark.parametrize(
    ("language", "level"),
    ROLEPLAY_CASES,
    ids=[f"{language}-level{level}" for language, level in ROLEPLAY_CASES],
)
def test_roleplay_snapshot(language: str, level: int) -> None:
    messages = build_roleplay_messages(
        _scenario(language),
        level,
        LEVEL_SCALE[language],
        list(HISTORY),
        memory=MEMORY,
    )
    _assert_snapshot(f"roleplay_{language}_level{level}.json", messages)


@pytest.mark.parametrize("language", LANGUAGES)
def test_free_conversation_snapshot(language: str) -> None:
    """AST-048: free mode has its own golden, so the roleplay one cannot drift with it."""
    messages = build_free_conversation_messages(
        free_scenario(language),
        FREE_LEVEL,
        LEVEL_SCALE[language],
        list(HISTORY),
        memory=MEMORY,
    )
    _assert_snapshot(f"free_conversation_{language}_level{FREE_LEVEL}.json", messages)


@pytest.mark.parametrize("language", LANGUAGES)
def test_evaluation_snapshot(language: str) -> None:
    messages = build_evaluation_messages(
        _scenario(language),
        3,
        EVALUATION_USER_TEXT,
        EVALUATION_ASSISTANT_TEXT,
    )
    _assert_snapshot(f"evaluation_{language}.json", messages)


@pytest.mark.parametrize("language", LANGUAGES)
def test_explain_snapshot(language: str) -> None:
    messages = build_explain_messages(EXPLAIN_USER_TEXT, EXPLAIN_ASSISTANT_TEXT, language)
    _assert_snapshot(f"explain_{language}.json", messages)


@pytest.mark.parametrize("language", LANGUAGES)
def test_roleplay_system_prompt_keeps_its_contract(language: str) -> None:
    scenario = _scenario(language)
    target = LANGUAGE_LABEL[language]
    messages = build_roleplay_messages(
        scenario,
        3,
        LEVEL_SCALE[language],
        list(HISTORY),
        memory=MEMORY,
    )

    assert messages[0]["role"] == "system"
    system = messages[0]["content"]
    assert target in system, f"{language}: system prompt lost the target language name"
    assert f"Reply ONLY in {target}" in system, f"{language}: lost the 'Reply ONLY in' rule"
    assert "Learner memory:" in system and MEMORY in system, f"{language}: lost the memory block"
    assert json.dumps(scenario.to_prompt(), ensure_ascii=False, indent=2) in system, (
        f"{language}: lost the scenario JSON block"
    )
    assert messages[1:] == list(HISTORY)


def test_roleplay_without_memory_omits_the_memory_block() -> None:
    messages = build_roleplay_messages(_scenario("en"), 1, "CEFR", list(HISTORY))
    system = messages[0]["content"]
    assert "Learner memory:" not in system
    assert MEMORY not in system


def test_snapshot_files_have_no_orphans() -> None:
    if UPDATE:
        pytest.skip("snapshots are being regenerated")
    expected = {
        *(f"roleplay_{language}_level{level}.json" for language, level in ROLEPLAY_CASES),
        *(f"free_conversation_{language}_level{FREE_LEVEL}.json" for language in LANGUAGES),
        *(f"evaluation_{language}.json" for language in LANGUAGES),
        *(f"explain_{language}.json" for language in LANGUAGES),
    }
    on_disk = {path.name for path in SNAPSHOTS.glob("*.json")}
    assert on_disk == expected, (
        f"missing: {sorted(expected - on_disk)} / unexpected: {sorted(on_disk - expected)}"
    )
