from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.agent.prompts import build_evaluation_messages
from app.agent.scenario import Scenario
from app.providers.base import LLMProvider

logger = logging.getLogger(__name__)

# Japanese/Chinese carry no word spaces, so a verbatim quote may still shift them.
CJK_PATTERN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

EVALUATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "corrections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "original": {"type": "string"},
                    "corrected": {"type": "string"},
                    "explanation_pt": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["grammar", "vocab", "naturalness", "register", "pronunciation"],
                    },
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "original",
                    "corrected",
                    "explanation_pt",
                    "type",
                    "severity",
                    "confidence",
                ],
            },
        },
        "vocab": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "translation_pt": {"type": "string"},
                },
                "required": ["term", "translation_pt"],
            },
        },
        "scene_progress": {
            "type": "object",
            "properties": {
                "goals_met": {"type": "array", "items": {"type": "string"}},
                "status": {"type": "string", "enum": ["ongoing", "complete"]},
            },
            "required": ["goals_met", "status"],
        },
        "language_leak": {"type": "boolean"},
    },
    "required": ["corrections", "vocab", "scene_progress", "language_leak"],
}


@dataclass(frozen=True, slots=True)
class Correction:
    original: str
    corrected: str
    explanation_pt: str
    type: str
    severity: str
    confidence: float

    def to_public(self) -> dict[str, Any]:
        return {
            "original": self.original,
            "corrected": self.corrected,
            "explanation_pt": self.explanation_pt,
            "type": self.type,
            "severity": self.severity,
            "confidence": round(self.confidence, 2),
        }


@dataclass(slots=True)
class Evaluation:
    corrections: list[Correction] = field(default_factory=list)
    vocab: list[dict[str, str]] = field(default_factory=list)
    goals_met: list[str] = field(default_factory=list)
    status: str = "ongoing"
    language_leak: bool = False

    def to_public(self) -> dict[str, Any]:
        return {
            "corrections": [item.to_public() for item in self.corrections],
            "vocab": self.vocab,
            "scene_progress": {"goals_met": self.goals_met, "status": self.status},
            "language_leak": self.language_leak,
        }


class Evaluator:
    def __init__(
        self,
        llm: LLMProvider,
        min_confidence: float = 0.6,
        max_corrections: int = 3,
        max_vocab: int = 5,
    ) -> None:
        self.llm = llm
        self.min_confidence = min_confidence
        self.max_corrections = max_corrections
        self.max_vocab = max_vocab

    async def evaluate(
        self,
        scenario: Scenario,
        level: int,
        user_text: str,
        assistant_text: str,
    ) -> Evaluation:
        messages = build_evaluation_messages(scenario, level, user_text, assistant_text)
        try:
            raw = await self.llm.chat_json(
                messages, EVALUATION_SCHEMA, language=scenario.language
            )
        except Exception as exc:  # noqa: BLE001 - feedback is best-effort
            logger.warning("evaluation failed: %s", exc)
            return Evaluation()
        return normalize(
            raw,
            self.min_confidence,
            self.max_corrections,
            self.max_vocab,
            user_text=user_text,
            language=scenario.language,
        )


def _fold(text: str) -> str:
    """Casefold and collapse whitespace, so a quote matches across case and spacing."""
    return " ".join(text.casefold().split())


def _quote_appears(original: str, user_text: str) -> bool:
    """True when the learner's own turn contains ``original``.

    CJK text has no word spaces, so the comparison also tries both sides with every
    space removed; that keeps a verbatim Japanese/Chinese quote that only shifted a
    space, without loosening the word boundaries of space-separated languages.
    """
    needle = _fold(original)
    if not needle:
        return False
    haystack = _fold(user_text)
    if needle in haystack:
        return True
    if CJK_PATTERN.search(needle) or CJK_PATTERN.search(haystack):
        return needle.replace(" ", "") in haystack.replace(" ", "")
    return False


def _is_same_text(left: str, right: str, language: str | None = None) -> bool:
    """True when the two strings only differ in case or whitespace.

    AST-032b: German capitalisation carries meaning, so for ``de`` a pure case difference
    is a real correction rather than the same text. Punctuation and spacing never matter,
    there as everywhere else. Every other language keeps folding case.
    """
    if _fold(left) == _fold(right):
        case_matters = (
            language == "de" and _alphanumeric_exact(left) != _alphanumeric_exact(right)
        )
        return not case_matters
    if CJK_PATTERN.search(left) or CJK_PATTERN.search(right):
        return _fold(left).replace(" ", "") == _fold(right).replace(" ", "")
    return False


def _keeps_correction(
    original: str, corrected: str, user_text: str, language: str | None = None
) -> bool:
    """AST-027: a correction is kept only when it quotes the turn and changes it."""
    if _is_same_text(original, corrected, language):
        logger.debug("dropped correction %r: corrected text is identical", original)
        return False
    if not _quote_appears(original, user_text):
        logger.debug("dropped correction %r: not present in the learner turn", original)
        return False
    return True


def _alphanumeric(text: str) -> str:
    """Casefolded alphanumeric content: case, punctuation and spacing removed."""
    return "".join(char for char in text.casefold() if char.isalnum())


def _alphanumeric_exact(text: str) -> str:
    """Alphanumeric content with case kept: only punctuation and spacing removed."""
    return "".join(char for char in text if char.isalnum())


def _is_punctuation_only(original: str, corrected: str) -> bool:
    """AST-030: True when only punctuation or spacing differs between the two.

    ``str.isalnum`` covers CJK, so a Japanese particle or a Chinese measure word still
    counts as content: only a cosmetic rewrite of the same characters is dropped.
    """
    return _alphanumeric_exact(original) == _alphanumeric_exact(corrected)


def _is_case_only(original: str, corrected: str) -> bool:
    """AST-032: True when only letter case differs, punctuation and spacing aside."""
    return _alphanumeric(original) == _alphanumeric(corrected) and not _is_punctuation_only(
        original, corrected
    )


def _is_cosmetic(original: str, corrected: str, language: str | None) -> bool:
    """AST-030/AST-032: True when the pair rewrites the same words without changing meaning.

    Punctuation and spacing never carry meaning, so those rewrites are dropped for every
    language. Letter case does carry meaning in German, where nouns and sentence starts are
    capitalized, so a case-only correction survives there and is dropped everywhere else.
    A real word or particle change is never cosmetic.
    """
    if _is_punctuation_only(original, corrected):
        return True
    return _is_case_only(original, corrected) and language != "de"


def _covers(outer: tuple[str, str], inner: tuple[str, str]) -> bool:
    """AST-033: True when both halves of ``inner`` sit inside the folded ``outer`` pair."""
    return inner[0] in outer[0] and inner[1] in outer[1]


def _collapse_subsumed(corrections: list[Correction]) -> list[Correction]:
    """AST-033: keep one pair when a fragment and the full sentence say the same thing.

    The model often reports a fragment and the whole sentence it belongs to as two
    corrections. A pair is dropped only when *both* halves are inside another pair, so two
    different fixes of the same fragment still survive; a later, more complete pair takes
    the slot of the fragment it covers.
    """
    kept: list[Correction] = []
    pairs: list[tuple[str, str]] = []
    for candidate in corrections:
        pair = (_fold(candidate.original), _fold(candidate.corrected))
        if any(_covers(existing, pair) for existing in pairs):
            logger.debug(
                "dropped subsumed correction %r -> %r", candidate.original, candidate.corrected
            )
            continue
        covered = {index for index, existing in enumerate(pairs) if _covers(pair, existing)}
        position = min(covered) if covered else len(kept)
        kept = [item for index, item in enumerate(kept) if index not in covered]
        pairs = [entry for index, entry in enumerate(pairs) if index not in covered]
        kept.insert(position, candidate)
        pairs.insert(position, pair)
    return kept


def normalize(
    raw: dict[str, Any],
    min_confidence: float = 0.6,
    max_corrections: int = 3,
    max_vocab: int = 5,
    user_text: str | None = None,
    language: str | None = None,
) -> Evaluation:
    evaluation = Evaluation()

    corrections = raw.get("corrections")
    if isinstance(corrections, list):
        seen: set[tuple[str, str]] = set()
        candidates: list[Correction] = []
        for item in corrections:
            if not isinstance(item, dict):
                continue
            try:
                confidence = float(item.get("confidence", 0))
            except (TypeError, ValueError):
                continue
            if confidence < min_confidence:
                continue
            original = str(item.get("original", "")).strip()
            corrected = str(item.get("corrected", "")).strip()
            explanation = str(item.get("explanation_pt", "")).strip()
            if not (original and corrected and explanation):
                continue
            if user_text is not None and not _keeps_correction(
                original, corrected, user_text, language
            ):
                continue
            # AST-030: the model repeats itself and pads the list with cosmetic rewrites.
            key = (_fold(original), _fold(corrected))
            if key in seen:
                logger.debug("dropped duplicate correction %r -> %r", original, corrected)
                continue
            seen.add(key)
            if _is_cosmetic(original, corrected, language):
                logger.debug("dropped cosmetic correction %r -> %r", original, corrected)
                continue
            kind = str(item.get("type", "grammar"))
            if kind not in {"grammar", "vocab", "naturalness", "register", "pronunciation"}:
                kind = "grammar"
            severity = str(item.get("severity", "low"))
            if severity not in {"low", "medium", "high"}:
                severity = "low"
            candidates.append(
                Correction(
                    original=original,
                    corrected=corrected,
                    explanation_pt=explanation,
                    type=kind,
                    severity=severity,
                    confidence=min(1.0, confidence),
                )
            )
        evaluation.corrections = _collapse_subsumed(candidates)[:max_corrections]

    vocab = raw.get("vocab")
    if isinstance(vocab, list):
        for item in vocab:
            if not isinstance(item, dict):
                continue
            term = str(item.get("term", "")).strip()
            translation = str(item.get("translation_pt", "")).strip()
            if term and translation:
                evaluation.vocab.append({"term": term, "translation_pt": translation})
            if len(evaluation.vocab) >= max_vocab:
                break

    progress = raw.get("scene_progress")
    if isinstance(progress, dict):
        goals = progress.get("goals_met")
        if isinstance(goals, list):
            evaluation.goals_met = [str(goal).strip() for goal in goals if str(goal).strip()][:10]
        status = progress.get("status")
        if status in {"ongoing", "complete"}:
            evaluation.status = status

    evaluation.language_leak = bool(raw.get("language_leak", False))
    return evaluation
