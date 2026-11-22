from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.config import SUPPORTED_LANGUAGES
from app.core.errors import TutorError


class ScenarioError(TutorError):
    pass


FREE_SCENARIO_ID = "free"
FREE_SCENARIO_TITLE = "Conversa livre"
FREE_SCENARIO_PERSONA = "a friendly conversation partner with no fixed role"
FREE_SCENARIO_SETTING = "an open conversation with no fixed scene"
FREE_SCENARIO_GOAL = "Keep a natural conversation going about the topic the learner chose."


REQUIRED_FIELDS = (
    "id",
    "language",
    "level",
    "title",
    "persona",
    "setting",
    "goal",
    "opening_line",
    "success_criteria",
)


@dataclass(frozen=True, slots=True)
class Scenario:
    id: str
    language: str
    title: str
    title_pt: str
    level_min: int
    level_max: int
    persona: str
    setting: str
    goal: str
    opening_line: str
    success_criteria: tuple[str, ...]
    vocab_targets: tuple[str, ...]
    guardrails: tuple[str, ...]

    def supports_level(self, level: int) -> bool:
        return self.level_min <= level <= self.level_max

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "language": self.language,
            "title": self.title,
            "title_pt": self.title_pt,
            "level_min": self.level_min,
            "level_max": self.level_max,
            "goal": self.goal,
            "setting": self.setting,
        }

    def to_prompt(self) -> dict[str, Any]:
        return {
            "persona": self.persona,
            "setting": self.setting,
            "goal": self.goal,
            "success_criteria": list(self.success_criteria),
            "vocab_targets": list(self.vocab_targets),
            "guardrails": list(self.guardrails),
        }


def free_scenario(language: str) -> Scenario:
    """Synthetic scenario for free conversation: no scene and no opening line.

    It is not part of the YAML repository and never reaches ``/api/scenarios``; it
    exists so the prompt builder, the evaluator and the review keep a ``Scenario``
    to work with when the learner picks the topic.
    """
    if language not in SUPPORTED_LANGUAGES:
        allowed = ", ".join(SUPPORTED_LANGUAGES)
        raise ScenarioError(f"unsupported language {language!r}; expected one of: {allowed}")
    return Scenario(
        id=FREE_SCENARIO_ID,
        language=language,
        title=FREE_SCENARIO_TITLE,
        title_pt=FREE_SCENARIO_TITLE,
        level_min=1,
        level_max=6,
        persona=FREE_SCENARIO_PERSONA,
        setting=FREE_SCENARIO_SETTING,
        goal=FREE_SCENARIO_GOAL,
        opening_line="",
        success_criteria=(),
        vocab_targets=(),
        guardrails=(),
    )


class ScenarioRepository:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._items: dict[str, Scenario] = {}

    def load(self) -> None:
        items: dict[str, Scenario] = {}
        for language in SUPPORTED_LANGUAGES:
            directory = self.root / language
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.yaml")):
                scenario = _parse(path, expected_language=language)
                if scenario.id in items:
                    raise ScenarioError(f"duplicate scenario id '{scenario.id}' at {path}")
                items[scenario.id] = scenario
        self._items = items

    def list(self, language: str) -> list[Scenario]:
        return sorted(
            (item for item in self._items.values() if item.language == language),
            key=lambda item: item.id,
        )

    def get(self, scenario_id: str) -> Scenario:
        try:
            return self._items[scenario_id]
        except KeyError as exc:
            raise ScenarioError(f"unknown scenario '{scenario_id}'") from exc

    def languages(self) -> list[str]:
        return sorted({item.language for item in self._items.values()})


def _parse(path: Path, expected_language: str) -> Scenario:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScenarioError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ScenarioError(f"{path} must contain a YAML mapping")

    missing = [field for field in REQUIRED_FIELDS if field not in raw]
    if missing:
        raise ScenarioError(f"{path} is missing required fields: {', '.join(missing)}")

    language = str(raw["language"])
    if language != expected_language:
        raise ScenarioError(
            f"{path} declares language '{language}' but lives under '{expected_language}/'"
        )

    level = raw["level"]
    if isinstance(level, int):
        level_min = level_max = level
    elif isinstance(level, (list, tuple)) and len(level) == 2:
        level_min, level_max = int(level[0]), int(level[1])
    else:
        raise ScenarioError(f"{path}: 'level' must be an int or [min, max]")
    if not (1 <= level_min <= level_max <= 6):
        raise ScenarioError(f"{path}: levels must be within 1..6")

    def string_list(value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ScenarioError(f"{path}: expected a list, got {type(value).__name__}")
        return tuple(str(item).strip() for item in value if str(item).strip())

    return Scenario(
        id=str(raw["id"]),
        language=language,
        title=str(raw["title"]),
        title_pt=str(raw.get("title_pt") or raw["title"]),
        level_min=level_min,
        level_max=level_max,
        persona=str(raw["persona"]),
        setting=str(raw["setting"]),
        goal=str(raw["goal"]),
        opening_line=str(raw["opening_line"]),
        success_criteria=string_list(raw.get("success_criteria")),
        vocab_targets=string_list(raw.get("vocab_targets")),
        guardrails=string_list(raw.get("guardrails")),
    )
