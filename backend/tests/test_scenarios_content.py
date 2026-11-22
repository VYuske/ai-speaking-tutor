"""AST-009: content contract for the scenario catalogue (8 per language, 32 total)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.agent.scenario import Scenario, ScenarioRepository

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("en", "ja", "zh", "de")
EXPECTED_PER_LANGUAGE = 8
PORTUGUESE_MARKERS = (
    "Você",
    "você",
    "Olá",
    "olá",
    "Obrigado",
    "Obrigada",
    "obrigado",
    "obrigada",
    "Por favor",
    "Bom dia",
    "Boa tarde",
)
SCRIPT_PATTERNS = {
    "ja": r"[\u3040-\u30ff\u4e00-\u9fff]",
    "zh": r"[\u4e00-\u9fff]",
}
KEBAB_CASE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
EXPECTED_IDS = (
    "airport-checkin",
    "arzttermin",
    "bahn-ticket",
    "bakery-breakfast",
    "buergeramt-anmeldung",
    "cafe-order",
    "courier-pickup",
    "doctor-appointment",
    "fahrradwerkstatt",
    "garbage-sorting",
    "hospital-registration",
    "hotel-checkin",
    "izakaya-reservation",
    "job-interview",
    "konbini-checkout",
    "nachbar-ruhezeiten",
    "onsen-ryokan",
    "pharmacy-consultation",
    "post-office-parcel",
    "ramen-shop-order",
    "restaurant-table",
    "returning-item",
    "street-directions",
    "subway-directions",
    "supermarkt-pfand",
    "taxi-ride",
    "train-transfer",
    "weekend-plans",
    "wet-market",
    "wohnungsbesichtigung",
    "zh-hotel-checkin",
    "zh-weekend-plans",
)


@pytest.fixture(scope="module")
def repository() -> ScenarioRepository:
    repo = ScenarioRepository(ROOT / "scenarios")
    repo.load()
    return repo


def _all_scenarios(repository: ScenarioRepository) -> list[Scenario]:
    return [scenario for language in LANGUAGES for scenario in repository.list(language)]


@pytest.mark.parametrize("language", LANGUAGES)
def test_each_language_has_eight_scenarios(
    repository: ScenarioRepository, language: str
) -> None:
    scenarios = repository.list(language)
    assert len(scenarios) == EXPECTED_PER_LANGUAGE, (
        f"{language}: expected {EXPECTED_PER_LANGUAGE} scenarios, "
        f"found {len(scenarios)}: {[item.id for item in scenarios]}"
    )


def test_total_is_thirty_two(repository: ScenarioRepository) -> None:
    assert len(_all_scenarios(repository)) == EXPECTED_PER_LANGUAGE * len(LANGUAGES)


def test_ids_are_globally_unique(repository: ScenarioRepository) -> None:
    ids = [scenario.id for scenario in _all_scenarios(repository)]
    duplicates = sorted({scenario_id for scenario_id in ids if ids.count(scenario_id) > 1})
    assert duplicates == [], f"duplicate scenario ids: {duplicates}"


def test_catalogue_loads_with_unchanged_ids(repository: ScenarioRepository) -> None:
    ids = sorted(scenario.id for scenario in _all_scenarios(repository))
    assert len(ids) == EXPECTED_PER_LANGUAGE * len(LANGUAGES), (
        f"expected {EXPECTED_PER_LANGUAGE * len(LANGUAGES)} scenarios, found {len(ids)}"
    )
    assert ids == list(EXPECTED_IDS), (
        f"scenario ids changed: {sorted(set(EXPECTED_IDS) ^ set(ids))}"
    )


@pytest.mark.parametrize("language", LANGUAGES)
def test_filenames_do_not_repeat_the_language_prefix(language: str) -> None:
    directory = ROOT / "scenarios" / language
    offenders = sorted(
        path.name for path in directory.glob("*.yaml") if path.stem.startswith(f"{language}-")
    )
    assert offenders == [], (
        f"{language}/: filenames must not repeat the language prefix '{language}-': {offenders}"
    )


def test_ids_are_kebab_case(repository: ScenarioRepository) -> None:
    invalid = [
        scenario.id
        for scenario in _all_scenarios(repository)
        if not KEBAB_CASE.fullmatch(scenario.id)
    ]
    assert invalid == [], f"ids must be kebab-case: {invalid}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_required_text_fields_are_non_empty(
    repository: ScenarioRepository, language: str
) -> None:
    for scenario in repository.list(language):
        for field in ("title", "title_pt", "persona", "setting", "goal", "opening_line"):
            value = getattr(scenario, field)
            assert isinstance(value, str) and value.strip(), (
                f"{scenario.id}: field '{field}' is empty"
            )


@pytest.mark.parametrize("language", LANGUAGES)
def test_levels_within_one_to_six(repository: ScenarioRepository, language: str) -> None:
    for scenario in repository.list(language):
        assert 1 <= scenario.level_min <= scenario.level_max <= 6, (
            f"{scenario.id}: invalid level range {scenario.level_min}..{scenario.level_max}"
        )


@pytest.mark.parametrize("language", LANGUAGES)
def test_minimum_lists_per_scenario(repository: ScenarioRepository, language: str) -> None:
    for scenario in repository.list(language):
        assert len(scenario.success_criteria) >= 3, (
            f"{scenario.id}: needs at least 3 success_criteria"
        )
        assert len(scenario.vocab_targets) >= 3, f"{scenario.id}: needs at least 3 vocab_targets"
        assert len(scenario.guardrails) >= 1, f"{scenario.id}: needs at least 1 guardrail"


@pytest.mark.parametrize("language", LANGUAGES)
def test_list_items_are_non_empty_strings(
    repository: ScenarioRepository, language: str
) -> None:
    for scenario in repository.list(language):
        for field in ("success_criteria", "vocab_targets", "guardrails"):
            for item in getattr(scenario, field):
                assert item.strip(), f"{scenario.id}: empty entry in {field}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_opening_line_is_target_language(repository: ScenarioRepository, language: str) -> None:
    for scenario in repository.list(language):
        line = scenario.opening_line
        found = [marker for marker in PORTUGUESE_MARKERS if marker in line]
        assert found == [], f"{scenario.id}: opening_line looks Portuguese ({found}): {line}"
        pattern = SCRIPT_PATTERNS.get(language)
        if pattern is not None:
            assert re.search(pattern, line), (
                f"{scenario.id}: opening_line has no {language} script: {line}"
            )


@pytest.mark.parametrize("language", LANGUAGES)
def test_titles_are_unique_within_language(
    repository: ScenarioRepository, language: str
) -> None:
    titles = [scenario.title for scenario in repository.list(language)]
    duplicates = sorted({title for title in titles if titles.count(title) > 1})
    assert duplicates == [], f"{language}: duplicated themes/titles: {duplicates}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_title_pt_is_present_and_distinct(
    repository: ScenarioRepository, language: str
) -> None:
    for scenario in repository.list(language):
        assert scenario.title_pt != scenario.title, (
            f"{scenario.id}: title_pt must be a Portuguese title, not a copy of title"
        )
        pattern = SCRIPT_PATTERNS.get(language)
        if pattern is not None:
            assert not re.search(pattern, scenario.title_pt), (
                f"{scenario.id}: title_pt must be Portuguese, found {language} script"
            )
