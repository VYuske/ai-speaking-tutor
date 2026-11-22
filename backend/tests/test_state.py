from __future__ import annotations

import pytest

from app.orchestrator.state import InvalidTransition, TurnState, advance


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TurnState.IDLE, TurnState.LISTENING),
        (TurnState.LISTENING, TurnState.TRANSCRIBING),
        (TurnState.TRANSCRIBING, TurnState.THINKING),
        (TurnState.THINKING, TurnState.SPEAKING),
        (TurnState.SPEAKING, TurnState.FEEDBACK),
        (TurnState.FEEDBACK, TurnState.IDLE),
        (TurnState.THINKING, TurnState.IDLE),
    ],
)
def test_valid_transitions(current: TurnState, target: TurnState) -> None:
    assert advance(current, target) is target


def test_invalid_transition() -> None:
    with pytest.raises(InvalidTransition):
        advance(TurnState.IDLE, TurnState.SPEAKING)
