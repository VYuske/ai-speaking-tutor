from __future__ import annotations

from enum import StrEnum


class TurnState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    FEEDBACK = "feedback"


ALLOWED: dict[TurnState, frozenset[TurnState]] = {
    TurnState.IDLE: frozenset({TurnState.LISTENING, TurnState.THINKING, TurnState.IDLE}),
    TurnState.LISTENING: frozenset({TurnState.TRANSCRIBING, TurnState.IDLE}),
    TurnState.TRANSCRIBING: frozenset({TurnState.THINKING, TurnState.IDLE}),
    TurnState.THINKING: frozenset({TurnState.SPEAKING, TurnState.FEEDBACK, TurnState.IDLE}),
    TurnState.SPEAKING: frozenset({TurnState.FEEDBACK, TurnState.IDLE}),
    TurnState.FEEDBACK: frozenset({TurnState.IDLE}),
}


class InvalidTransition(ValueError):
    pass


def advance(current: TurnState, target: TurnState) -> TurnState:
    if target not in ALLOWED[current]:
        raise InvalidTransition(f"cannot transition from {current} to {target}")
    return target
