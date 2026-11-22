from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.scenario import ScenarioError, ScenarioRepository

ROOT = Path(__file__).resolve().parents[1]


def test_loads_all_languages() -> None:
    repository = ScenarioRepository(ROOT / "scenarios")
    repository.load()
    for language in ("en", "ja", "zh", "de"):
        assert repository.list(language), f"no scenarios for {language}"


def test_get_returns_scenario() -> None:
    repository = ScenarioRepository(ROOT / "scenarios")
    repository.load()
    scenario = repository.get("cafe-order")
    assert scenario.language == "en"
    assert scenario.supports_level(2)
    assert scenario.opening_line


def test_unknown_scenario_raises() -> None:
    repository = ScenarioRepository(ROOT / "scenarios")
    repository.load()
    with pytest.raises(ScenarioError):
        repository.get("nope")


def test_language_directory_mismatch(tmp_path: Path) -> None:
    directory = tmp_path / "en"
    directory.mkdir(parents=True)
    (directory / "bad.yaml").write_text(
        "\n".join(
            [
                "id: bad",
                "language: ja",
                "level: 2",
                "title: Bad",
                "persona: p",
                "setting: s",
                "goal: g",
                "opening_line: hi",
                "success_criteria: []",
            ]
        ),
        encoding="utf-8",
    )
    repository = ScenarioRepository(tmp_path)
    with pytest.raises(ScenarioError):
        repository.load()


def test_duplicate_id_detected(tmp_path: Path) -> None:
    directory = tmp_path / "en"
    directory.mkdir(parents=True)
    body = "\n".join(
        [
            "id: same",
            "language: en",
            "level: 2",
            "title: T",
            "persona: p",
            "setting: s",
            "goal: g",
            "opening_line: hi",
            "success_criteria: []",
        ]
    )
    (directory / "a.yaml").write_text(body, encoding="utf-8")
    (directory / "b.yaml").write_text(body, encoding="utf-8")
    repository = ScenarioRepository(tmp_path)
    with pytest.raises(ScenarioError):
        repository.load()
