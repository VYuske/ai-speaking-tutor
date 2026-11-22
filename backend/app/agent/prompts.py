from __future__ import annotations

import json
from typing import Any

from app.agent.scenario import Scenario
from app.core.errors import TutorError

LANGUAGE_LABEL = {
    "en": "English",
    "ja": "Japanese",
    "zh": "Mandarin Chinese",
    "de": "German",
}

SCRIPT_RULE = {
    "en": "Write natural conversational English.",
    "ja": "Write natural Japanese with kana and kanji. Never use romaji.",
    "zh": "Write natural Simplified Chinese characters. Never use pinyin in the dialogue.",
    "de": "Write natural German with correct umlauts and capitalized nouns.",
}

LEVEL_GUIDE = {
    1: (
        "absolute beginner: very short sentences, present tense, "
        "high-frequency vocabulary, one idea per turn."
    ),
    2: "elementary: short sentences, common past and future forms, simple connectors.",
    3: "intermediate: natural everyday speech, some subordinate clauses and idiomatic phrases.",
    4: "upper intermediate: fluent everyday speech, varied connectors and register awareness.",
    5: (
        "advanced: near-native range, nuance and idiomatic expressions, "
        "occasional complex structures."
    ),
    6: "proficient: fully natural, idiomatic and nuanced speech.",
}

EXPLAIN_FOCUS = ("grammar", "vocab", "naturalness", "register", "pronunciation")


def _validate_level(level: int) -> None:
    """Reject levels the guide cannot describe before touching ``LEVEL_GUIDE``."""
    if isinstance(level, bool) or not isinstance(level, int) or level not in LEVEL_GUIDE:
        raise TutorError(f"level must be an integer from 1 to 6, got {level!r}")


def _validate_explain_inputs(language: str, focus: str) -> None:
    if language not in LANGUAGE_LABEL:
        allowed = ", ".join(LANGUAGE_LABEL)
        raise TutorError(f"unsupported language {language!r}; expected one of: {allowed}")
    if focus not in EXPLAIN_FOCUS:
        allowed = ", ".join(EXPLAIN_FOCUS)
        raise TutorError(f"unsupported focus {focus!r}; expected one of: {allowed}")


def build_roleplay_messages(
    scenario: Scenario,
    level: int,
    level_scale: str,
    history: list[dict[str, str]],
    memory: str = "",
) -> list[dict[str, str]]:
    _validate_level(level)
    target = LANGUAGE_LABEL[scenario.language]
    scenario_block = json.dumps(scenario.to_prompt(), ensure_ascii=False, indent=2)
    memory_block = f"\nLearner memory:\n{memory}\n" if memory else ""

    system = f"""You are a patient {target} tutor playing a role in a speaking practice session.

LANGUAGE RULES (highest priority):
- Reply ONLY in {target}. {SCRIPT_RULE[scenario.language]}
- Never speak Portuguese or English unless the learner explicitly asks for an explanation.
- Never translate your own reply and never break character to comment on the conversation.

LEVEL: {level}/6 ({level_scale}) — {LEVEL_GUIDE[level]}
Adapt your vocabulary and sentence length to this level, but stay natural.

SCENARIO (stay inside it):
{scenario_block}
{memory_block}
TURN RULES:
- Answer in 1 to 3 short sentences.
- Always end with exactly one question that keeps the conversation moving.
- Do not correct the learner's grammar or pronunciation in this reply;
  a separate tutor pass handles corrections.
- If the learner says something unintelligible, ask them to repeat it naturally, in character.
"""
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    messages.extend(history[-12:])
    return messages


def build_free_conversation_messages(
    scenario: Scenario,
    level: int,
    level_scale: str,
    history: list[dict[str, str]],
    memory: str = "",
) -> list[dict[str, str]]:
    """Free conversation: no scenario block, the learner chooses the topic.

    Same language and level contract as ``build_roleplay_messages``, but the tutor
    follows the learner instead of playing a fixed scene.
    """
    _validate_level(level)
    target = LANGUAGE_LABEL[scenario.language]
    memory_block = f"\nLearner memory:\n{memory}\n" if memory else ""

    system = f"""You are a patient {target} conversation partner in a free speaking session.

LANGUAGE RULES (highest priority):
- Reply ONLY in {target}. {SCRIPT_RULE[scenario.language]}
- Never speak Portuguese or English unless the learner explicitly asks for an explanation.
- Never translate your own reply and never break character to comment on the conversation.

LEVEL: {level}/6 ({level_scale}) — {LEVEL_GUIDE[level]}
Adapt your vocabulary and sentence length to this level, but stay natural.
{memory_block}
FREE CONVERSATION RULES:
- The learner chooses the topic: follow their lead and never impose a scene or a script.
- Answer in 1 to 3 short sentences.
- Always end with exactly one question that keeps the conversation moving.
- Do not correct the learner's grammar or pronunciation in this reply;
  a separate tutor pass handles corrections.
- If the learner says something unintelligible, ask them to repeat it naturally.
"""
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    messages.extend(history[-12:])
    return messages


EXPLAIN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "explanation_pt": {"type": "string"},
        "example_target": {"type": "string"},
        "example_pt": {"type": "string"},
    },
    "required": ["explanation_pt", "example_target", "example_pt"],
}


def build_explain_messages(
    user_text: str,
    assistant_text: str,
    language: str,
    focus: str = "grammar",
) -> list[dict[str, str]]:
    _validate_explain_inputs(language, focus)
    target = LANGUAGE_LABEL[language]
    system = (
        "You are a bilingual tutor helping a Brazilian learner of "
        f"{target}. Explain in Brazilian Portuguese, objectively, in at most 4 sentences. "
        "Quote the learner's own words. Never invent errors that are not in the text."
    )
    user = f"""Learner said: {user_text!r}
Tutor replied: {assistant_text!r}
Focus: {focus}

Return:
- explanation_pt: a short explanation in Brazilian Portuguese about the most relevant point.
- example_target: one corrected or improved sentence in {target}.
- example_pt: the translation of that example into Brazilian Portuguese."""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_evaluation_messages(
    scenario: Scenario,
    level: int,
    user_text: str,
    assistant_text: str,
) -> list[dict[str, str]]:
    target = LANGUAGE_LABEL[scenario.language]
    system = f"""You are a strict but fair {target} teacher reviewing one learner turn.

Rules:
- 'original' must be copied verbatim from the learner's turn: the exact characters,
  including case, spacing and punctuation, and without surrounding quotes. If you
  cannot quote it exactly, do not report that correction.
- 'corrected' must differ from 'original' and must fix an actual problem.
- If the learner's turn is grammatically correct and natural, 'corrections' MUST be
  an empty list. Never suggest alternatives, synonyms or stylistic preferences.
- Do not report punctuation-only or capitalisation-only changes unless the meaning
  changes.
- Correct grammar, word choice, register and naturalness. Do not rewrite
  correctly-formed sentences.
- Maximum 3 corrections, ordered by importance.
- Explanations must be in Brazilian Portuguese and reference the learner's exact words.
- 'confidence' is your own certainty from 0 to 1; do not report anything below 0.6.
- 'type' must be one of: grammar, vocab, naturalness, register, pronunciation.
- 'severity' must be one of: low, medium, high.
- language_leak is true only if the learner switched away from {target} for no reason."""
    user = f"""Scenario: {scenario.title} — {scenario.goal}
Learner level: {level}/6
Learner said: {user_text}
Tutor replied: {assistant_text}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
