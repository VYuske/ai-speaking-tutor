"""AST-031: the evaluation user block embeds the learner turn without repr quotes.

``build_evaluation_messages`` used ``Learner said: {user_text!r}``, so the model sometimes
copied the surrounding ``'`` into ``original`` and the containment filter in
``evaluator.py`` dropped a legitimate correction. The ``Learner said:`` / ``Tutor replied:``
labels stay, because ``app/providers/fakes.py`` and ``scripts/eval_prompts.py`` parse them;
only the repr goes, and the system prompt now says the words must be copied verbatim
without surrounding quotes.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pytest

from app.agent.prompts import build_evaluation_messages
from app.agent.scenario import Scenario, ScenarioRepository

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("en", "ja", "zh", "de")
LEVEL = 3

# Mirrors tests/test_prompt_snapshots.py: one real scenario per language.
SCENARIO_BY_LANGUAGE = {
    "en": "cafe-order",
    "ja": "ramen-shop-order",
    "zh": "restaurant-table",
    "de": "bahn-ticket",
}

USER_TEXT = "I go to the station yesterday and buy two ticket."
ASSISTANT_TEXT = "Two tickets for the express, then. Where are you heading?"
APOSTROPHE_TEXT = "I'd like a latte, please"


@functools.lru_cache(maxsize=1)
def _repository() -> ScenarioRepository:
    repository = ScenarioRepository(ROOT / "scenarios")
    repository.load()
    return repository


def _scenario(language: str) -> Scenario:
    return _repository().get(SCENARIO_BY_LANGUAGE[language])


def _messages(language: str, user_text: str = USER_TEXT) -> list[dict[str, str]]:
    return build_evaluation_messages(_scenario(language), LEVEL, user_text, ASSISTANT_TEXT)


def _user(language: str, user_text: str = USER_TEXT) -> str:
    return _messages(language, user_text)[1]["content"]


def _line(block: str, label: str) -> str:
    matches = [line for line in block.splitlines() if line.startswith(label)]
    assert len(matches) == 1, f"expected one {label!r} line, got {matches!r} in {block!r}"
    return matches[0]


@pytest.mark.parametrize("language", LANGUAGES)
def test_user_block_carries_no_repr_quotes(language: str) -> None:
    user = _user(language)
    assert "Learner said: '" not in user
    assert "Tutor replied: '" not in user
    assert 'Learner said: "' not in user
    assert 'Tutor replied: "' not in user


@pytest.mark.parametrize("language", LANGUAGES)
def test_raw_turns_sit_on_their_own_label_lines(language: str) -> None:
    user = _user(language)
    assert _line(user, "Learner said:") == f"Learner said: {USER_TEXT}"
    assert _line(user, "Tutor replied:") == f"Tutor replied: {ASSISTANT_TEXT}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_internal_apostrophe_survives_verbatim(language: str) -> None:
    user = _user(language, APOSTROPHE_TEXT)
    assert _line(user, "Learner said:") == f"Learner said: {APOSTROPHE_TEXT}"
    assert APOSTROPHE_TEXT in user


@pytest.mark.parametrize("language", LANGUAGES)
def test_learner_quotes_and_backslashes_are_not_escaped(language: str) -> None:
    text = 'I said "oat milk" at C:\\temp, said the learner.'
    learner_line = _line(_user(language, text), "Learner said:")
    assert learner_line == f"Learner said: {text}"
    assert "C:\\\\temp" not in learner_line, "repr escaped the learner's backslash"
    assert '"oat milk"' in learner_line


@pytest.mark.parametrize("language", LANGUAGES)
def test_system_prompt_keeps_verbatim_and_empty_list_rules(language: str) -> None:
    messages = _messages(language)
    assert [message["role"] for message in messages] == ["system", "user"]
    system = messages[0]["content"]
    assert "'original' must be copied verbatim" in system
    assert "an empty list" in system
    assert "without surrounding quotes" in system


@pytest.mark.parametrize("language", LANGUAGES)
def test_user_block_keeps_its_four_lines(language: str) -> None:
    lines = _user(language).splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("Scenario: ")
    assert lines[1].startswith("Learner level: ")
    assert lines[2].startswith("Learner said: ")
    assert lines[3].startswith("Tutor replied: ")
