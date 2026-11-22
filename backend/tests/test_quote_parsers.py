"""AST-034: both learner-quote parsers stop reading the raw line as a Python literal.

AST-031 removed the ``repr`` from the evaluation prompt, so ``Learner said:`` is now
followed by the learner's words verbatim. ``app.providers.fakes._learner_quote`` and
``scripts/eval_prompts._canned_learner_quote`` still ran ``ast.literal_eval`` on that line,
so a turn that happens to look like a Python literal (``5``, ``True``, ``[1]``) came back
as a non-string and the quote was dropped. Only a line the prompt actually wrapped in
matching quotes may be passed to ``literal_eval``; anything else is used as-is.

The harness is loaded from its file path (it is not a package), the same technique
``tests/test_eval_prompts.py`` uses. No network, no model, no Ollama.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from app.providers.fakes import _learner_quote

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
MODULE_NAME = "scripts_eval_prompts_quote_under_test"


def _load_harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPTS_DIR / "eval_prompts.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness() -> ModuleType:
    return _load_harness()


def _messages(raw: str) -> list[dict[str, str]]:
    """One evaluation user block, in the four-line shape AST-031 produces."""
    block = (
        "Scenario: cafe-order (level 3/6)\n"
        "Learner level: 3/6\n"
        f"Learner said: {raw}\n"
        "Tutor replied: Of course! Small or large?\n"
    )
    return [
        {"role": "system", "content": "You review a learner turn."},
        {"role": "user", "content": block},
    ]


def _fakes(raw: str) -> str:
    return _learner_quote(_messages(raw))


def _canned(harness: ModuleType, raw: str) -> str:
    return harness._canned_learner_quote(_messages(raw))


# Every case the two parsers must handle, shared by the agreement test below.
RAW_CASES = (
    "5",
    "True",
    "None",
    "[1]",
    "I'd like a latte",
    "I'd like a latte, please",
    "すみません、ラテを一つください。",
)
QUOTED_CASES = (("'hello'", "hello"), ('"hello"', "hello"))


# --- raw lines are kept verbatim, never parsed as a literal --------------------------


@pytest.mark.parametrize("raw", RAW_CASES)
def test_fakes_keeps_raw_line_verbatim(raw: str) -> None:
    assert _fakes(raw) == raw


@pytest.mark.parametrize("raw", RAW_CASES)
def test_canned_keeps_raw_line_verbatim(harness: ModuleType, raw: str) -> None:
    assert _canned(harness, raw) == raw


@pytest.mark.parametrize("raw", ("5", "True", "[1]", "すみません、ラテを一つください。"))
def test_raw_literal_looking_turn_is_not_dropped(harness: ModuleType, raw: str) -> None:
    """The regression: a non-string literal used to collapse into an empty quote."""
    assert _fakes(raw) != ""
    assert _canned(harness, raw) != ""


# --- quoted lines keep the legacy unquoting ------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), QUOTED_CASES)
def test_quoted_line_is_unquoted(raw: str, expected: str) -> None:
    assert _fakes(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), QUOTED_CASES)
def test_canned_quoted_line_is_unquoted(harness: ModuleType, raw: str, expected: str) -> None:
    assert _canned(harness, raw) == expected


def test_unmatched_quote_stays_raw() -> None:
    """Not wrapped in matching quotes, so it is text, quotes included."""
    assert _fakes("'hello") == "'hello"
    assert _fakes("hello'") == "hello'"


def test_internal_apostrophe_stays_intact() -> None:
    text = "I don't think it's [1] at 5 o'clock, True?"
    assert _fakes(text) == text


# --- both parsers agree --------------------------------------------------------------


def test_parsers_agree_on_every_case(harness: ModuleType) -> None:
    cases = RAW_CASES + tuple(raw for raw, _ in QUOTED_CASES)
    cases += ("'hello", "hello'", "I don't think it's [1] at 5 o'clock, True?")
    for raw in cases:
        assert _fakes(raw) == _canned(harness, raw), f"parsers disagree on {raw!r}"


# --- error path ----------------------------------------------------------------------


def test_block_without_learner_line_returns_empty(harness: ModuleType) -> None:
    messages = [{"role": "user", "content": "Scenario: cafe-order\nTutor replied: Hi!\n"}]
    assert _learner_quote(messages) == ""
    assert harness._canned_learner_quote(messages) == ""


def test_system_message_is_never_scanned(harness: ModuleType) -> None:
    messages = [
        {"role": "system", "content": "Learner said: 5"},
        {"role": "user", "content": "Learner said: I'd like a latte\n"},
    ]
    assert _learner_quote(messages) == "I'd like a latte"
    assert harness._canned_learner_quote(messages) == "I'd like a latte"
