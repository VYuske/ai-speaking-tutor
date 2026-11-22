"""Tests for the AST-008b prompt-evaluation harness (`scripts/eval_prompts.py`).

The script is not a package, so it is loaded from its file path — the same technique
`tests/test_scripts_bench.py` uses for `bench_llm.py`. Nothing here touches the network,
loads a model or starts Ollama: the dry-run path uses the script's canned provider and
the fakes at the bottom of this file.

Every test that builds run data uses the script's own dataclasses on purpose: if the
metric names drift, the tests break instead of silently reporting zeros.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
MODULE_NAME = "scripts_eval_prompts_under_test"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPTS_DIR / "eval_prompts.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness() -> ModuleType:
    return _load()


@pytest.fixture(scope="module")
def scenarios(harness: ModuleType) -> dict[str, object]:
    repository = harness.ScenarioRepository(harness.SCENARIOS_DIR)
    repository.load()
    return {
        language: repository.get(scenario_id)
        for language, scenario_id in harness.SCENARIO_BY_LANGUAGE.items()
    }


# --- adherence (delegated to bench_llm) ----------------------------------------------


@pytest.mark.parametrize(
    ("language", "text", "expected_ok"),
    [
        ("en", "Sure! Would you like a small or a large cup?", True),
        ("en", "かしこまりました。ホットコーヒーはいかがですか。", False),
        ("ja", "かしこまりました。SサイズとMサイズがあります。", True),
        ("ja", "Sure, small or large?", False),
        ("zh", "好的，一杯拿铁。请问要热的还是冰的？", True),
        ("zh", "かしこまりました。", False),
        ("de", "Gerne! Möchten Sie ihn klein oder groß?", True),
        ("de", "Sure, small or large?", False),
    ],
)
def test_check_adherence(
    harness: ModuleType, language: str, text: str, expected_ok: bool
) -> None:
    ok, reason = harness.check_adherence(language, text)

    assert ok is expected_ok
    assert reason


def test_check_adherence_flags_empty_reply(harness: ModuleType) -> None:
    for language in harness.DEFAULT_LANGUAGES:
        assert harness.check_adherence(language, "  \n ") == (False, "empty response")


def test_p50_latency_uses_median(harness: ModuleType) -> None:
    assert harness.p50_latency([]) is None
    assert harness.p50_latency([120.0]) == 120.0
    assert harness.p50_latency([300.0, 100.0]) == 200.0
    assert harness.p50_latency([5.0, 1.0, 3.0]) == 3.0


# --- aggregation ---------------------------------------------------------------------


def turn(
    harness: ModuleType,
    *,
    language: str = "en",
    run: int = 1,
    turn_index: int = 1,
    reply: str = "Sure, one flat white coming up. Anything else?",
    latency_ms: float | None = 100.0,
    adherence_ok: bool | None = None,
    evaluated: bool = True,
    json_valid: bool = True,
    corrections: int = 1,
    error: str | None = None,
) -> object:
    if adherence_ok is None:
        adherence_ok = bool(reply.strip()) and error is None
    return harness.TurnResult(
        language=language,
        run=run,
        turn=turn_index,
        user_text="could I get a flat white",
        reply=reply,
        latency_ms=latency_ms,
        adherence_ok=adherence_ok,
        adherence_reason="ok" if adherence_ok else "empty response",
        evaluated=evaluated,
        json_valid=json_valid,
        corrections=corrections,
        error=error,
    )


def test_aggregate_counts_per_language_metrics(harness: ModuleType) -> None:
    runs = [
        harness.RunResult(
            language="en",
            run=1,
            scenario_id="cafe-order",
            turns=[
                turn(harness, latency_ms=100.0, corrections=1),
                turn(harness, turn_index=2, latency_ms=300.0, corrections=2),
            ],
        ),
        harness.RunResult(
            language="ja",
            run=1,
            scenario_id="konbini-checkout",
            turns=[
                turn(
                    harness,
                    language="ja",
                    reply="",
                    latency_ms=None,
                    adherence_ok=False,
                    json_valid=False,
                    corrections=0,
                )
            ],
        ),
    ]

    metrics = harness.aggregate(runs, ("en", "ja"))

    assert [metric.language for metric in metrics] == ["en", "ja"]
    english, japanese = metrics
    assert (english.turns, english.adherence_passed, english.empty_replies) == (2, 2, 0)
    assert (english.evals, english.json_valid, english.corrections) == (2, 2, 3)
    assert english.p50_latency_ms == 200.0
    assert (japanese.turns, japanese.adherence_passed, japanese.empty_replies) == (1, 0, 1)
    assert (japanese.evals, japanese.json_valid, japanese.corrections) == (1, 0, 0)
    assert japanese.p50_latency_ms is None


def test_aggregate_keeps_failed_run_visible(harness: ModuleType) -> None:
    runs = [
        harness.RunResult(
            language="de", run=1, scenario_id="bahn-ticket", error="LLM request failed: boom"
        )
    ]

    (metric,) = harness.aggregate(runs, ("de",))

    assert metric.turns == 0
    assert metric.failed_runs == 1
    assert metric.adherence_passed == 0
    assert metric.p50_latency_ms is None


def test_aggregate_reports_languages_without_runs(harness: ModuleType) -> None:
    (metric,) = harness.aggregate([], ("zh",))

    assert metric.language == "zh"
    assert (metric.runs, metric.turns, metric.failed_runs) == (0, 0, 0)


def test_summarize_totals(harness: ModuleType) -> None:
    runs = [
        harness.RunResult(
            language="en",
            run=1,
            scenario_id="cafe-order",
            turns=[
                turn(harness, latency_ms=100.0, corrections=1),
                turn(harness, turn_index=2, latency_ms=300.0, corrections=2),
            ],
        ),
        harness.RunResult(
            language="zh", run=1, scenario_id="restaurant-table", error="timeout"
        ),
    ]

    totals = harness.summarize(harness.aggregate(runs, ("en", "zh")))

    assert totals.turns == 2
    assert (totals.adherence_passed, totals.empty_replies) == (2, 0)
    assert (totals.evals, totals.json_valid, totals.corrections) == (2, 2, 3)
    assert totals.p50_latency_ms == 200.0
    assert totals.languages_failed == 1


# --- report --------------------------------------------------------------------------


def meta(harness: ModuleType, **overrides: object) -> object:
    values: dict[str, object] = {
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen3.5:4b",
        "languages": ("en", "ja"),
        "runs": 1,
        "timeout_s": 60.0,
        "dry_run": True,
        "level": 3,
        "json_mode": "json_object",
        "config_label": "test defaults",
    }
    values.update(overrides)
    return harness.ReportMeta(**values)


def sample_runs(harness: ModuleType) -> list[object]:
    return [
        harness.RunResult(
            language="en",
            run=1,
            scenario_id="cafe-order",
            turns=[
                turn(harness, latency_ms=200.0, corrections=1),
                turn(harness, turn_index=2, latency_ms=400.0, corrections=1),
            ],
        ),
        harness.RunResult(
            language="ja", run=1, scenario_id="konbini-checkout", error="LLM HTTP 500: nope"
        ),
    ]


def render(harness: ModuleType, runs: list[object], notes: list[str] | None = None) -> str:
    languages = tuple(run.language for run in runs) or ("en",)
    metrics = harness.aggregate(runs, languages)
    return harness.render_report(
        meta=meta(harness, languages=languages),
        runs=runs,
        metrics=metrics,
        totals=harness.summarize(metrics),
        notes=notes or [],
        report_path=Path("/tmp/ast-eval/eval-prompts-2026-11-18.md"),
        report_date="2026-11-18",
    )


def test_render_report_has_per_language_table_and_totals(harness: ModuleType) -> None:
    text = render(harness, sample_runs(harness), notes=["2 scripted turns per language"])

    assert text.startswith("# Prompt evaluation — 2026-11-18")
    assert "## Environment" in text
    assert "## Per-language results" in text
    assert "| Language | Scenario | Runs | Turns | Adherence |" in text
    assert "| en | cafe-order | 1 | 2 | 2/2 | 0 | 2/2 | 2 | 1.00 | 300 |" in text
    assert "## Totals" in text
    assert "## Notes" in text
    assert "- 2 scripted turns per language" in text


def test_render_report_lists_failed_runs(harness: ModuleType) -> None:
    text = render(harness, sample_runs(harness))

    assert "## Errors" in text
    assert "`ja` run 1: LLM HTTP 500: nope" in text
    assert "## Diagnosis" in text


def test_render_report_without_failures_omits_errors_section(harness: ModuleType) -> None:
    runs = [
        harness.RunResult(
            language="en", run=1, scenario_id="cafe-order", turns=[turn(harness)]
        )
    ]

    text = render(harness, runs)

    assert "## Errors" not in text
    assert "## Diagnosis" not in text


# --- write policy --------------------------------------------------------------------


def test_is_temp_dir(harness: ModuleType, tmp_path: Path) -> None:
    assert harness.is_temp_dir(tmp_path) is True
    assert harness.is_temp_dir(tmp_path / "nested") is True
    assert harness.is_temp_dir(REPO_ROOT / "docs" / "spikes") is False


def test_decide_write_matrix(harness: ModuleType, tmp_path: Path) -> None:
    target = tmp_path / "eval-prompts-2026-11-18.md"

    # Real run: writes, unless the file exists and --force was not given.
    assert harness.decide_write(
        dry_run=False, force=False, out_dir=tmp_path, target=target
    ) == (True, "")
    target.write_text("previous\n", encoding="utf-8")
    write, reason = harness.decide_write(
        dry_run=False, force=False, out_dir=tmp_path, target=target
    )
    assert write is False
    assert "refusing to overwrite" in reason and "--force" in reason
    assert harness.decide_write(
        dry_run=False, force=True, out_dir=tmp_path, target=target
    ) == (True, "")

    # Dry run: nowhere to persist unless --force *and* a temporary --out-dir.
    write, reason = harness.decide_write(
        dry_run=True, force=False, out_dir=tmp_path, target=target
    )
    assert write is False
    assert "--force" in reason
    write, reason = harness.decide_write(
        dry_run=True, force=True, out_dir=REPO_ROOT / "docs" / "spikes", target=target
    )
    assert write is False
    assert "temporary" in reason
    assert harness.decide_write(
        dry_run=True, force=True, out_dir=tmp_path, target=target
    ) == (True, "")


def test_report_path_and_resolve_out_dir(harness: ModuleType, tmp_path: Path) -> None:
    assert harness.report_path(tmp_path, "2026-11-18") == tmp_path / "eval-prompts-2026-11-18.md"
    assert harness.report_path(tmp_path, "2026-11-18").name == "eval-prompts-2026-11-18.md"
    assert harness.resolve_out_dir("docs/spikes") == REPO_ROOT / "docs" / "spikes"
    assert harness.resolve_out_dir(str(tmp_path)) == tmp_path


def test_write_report_refuses_without_force(harness: ModuleType, tmp_path: Path) -> None:
    target = tmp_path / "eval-prompts-2026-11-18.md"
    target.write_text("previous\n", encoding="utf-8")

    code, message = harness.write_report(target, "new\n", force=False)

    assert code == 2
    assert "refusing to overwrite" in message
    assert target.read_text(encoding="utf-8") == "previous\n"


def test_write_report_writes_with_force_and_creates_parents(
    harness: ModuleType, tmp_path: Path
) -> None:
    target = tmp_path / "nested" / "eval-prompts-2026-11-18.md"

    code, message = harness.write_report(target, "new\n", force=True)

    assert code == 0
    assert str(target) in message
    assert target.read_text(encoding="utf-8") == "new\n"


def test_write_report_reports_oserror_as_exit_1(harness: ModuleType, tmp_path: Path) -> None:
    blocker = tmp_path / "blocker.md"
    blocker.write_text("not a directory\n", encoding="utf-8")

    code, message = harness.write_report(blocker / "report.md", "new\n", force=True)

    assert code == 1
    assert "could not write report" in message


# --- scenario alignment (AST-026) ----------------------------------------------------

# The German learner script used to order a coffee inside the `bahn-ticket` role, so the
# model (correctly) refused and the turn became metric noise instead of language signal.
COFFEE_WORDS = ("kaffee", "café", "cafe", "latte", "espresso", "kuchen")
TRAIN_WORDS = (
    "fahrkarte",
    "ticket",
    "bahn",
    "zug",
    "gleis",
    "ziel",
    "hamburg",
    "rückfahrt",
    "hin und zurück",
)


def test_learner_scripts_are_keyed_by_scenario(harness: ModuleType) -> None:
    assert set(harness.SCRIPTED_TURNS) == set(harness.SCENARIO_BY_LANGUAGE.values())


def test_every_scenario_has_its_own_two_turn_script(
    harness: ModuleType, scenarios: dict
) -> None:
    scripts = {
        scenario.id: harness.scripted_turns(scenario.id) for scenario in scenarios.values()
    }

    assert len(scripts) == len(harness.SCENARIO_BY_LANGUAGE)
    for scenario_id, turns in scripts.items():
        assert len(turns) == harness.SCRIPTED_TURNS_PER_RUN, scenario_id
        assert all(line.strip() for line in turns), scenario_id
    # One shared script is exactly what made `de` order a coffee inside the train role.
    assert len(set(scripts.values())) == len(scripts)


def test_each_script_uses_its_own_scenario_vocabulary(
    harness: ModuleType, scenarios: dict
) -> None:
    for scenario in scenarios.values():
        text = " ".join(harness.scripted_turns(scenario.id))
        hits = [target for target in scenario.vocab_targets if target in text]

        assert hits, f"{scenario.id}: script shares no term with {scenario.vocab_targets}"


def test_german_script_buys_a_train_ticket(harness: ModuleType, scenarios: dict) -> None:
    text = " ".join(harness.scripted_turns(scenarios["de"].id)).lower()

    assert [word for word in COFFEE_WORDS if word in text] == []
    assert [word for word in TRAIN_WORDS if word in text] != []


# --- warm-up (AST-026) ---------------------------------------------------------------


async def test_run_language_discards_one_warmup_call(harness: ModuleType, scenarios: dict) -> None:
    args = harness.parse_args(["--dry-run"])
    llm = harness.CannedLLM(scenarios["de"])

    run = await harness.run_language(args, scenarios["de"], llm, 1, "CEFR")

    # One warm-up call plus the measured turns; the warm-up never becomes a TurnResult.
    assert llm.calls == 1 + harness.SCRIPTED_TURNS_PER_RUN
    assert run.warmup_ms is not None and run.warmup_ms >= 0
    assert run.warmup_error is None
    assert [turn.turn for turn in run.turns] == [1, 2]


async def test_warmup_happens_once_per_language(
    harness: ModuleType, scenarios: dict
) -> None:
    args = harness.parse_args(["--dry-run"])
    first_llm = harness.CannedLLM(scenarios["en"])
    first = await harness.run_language(args, scenarios["en"], first_llm, 1, "CEFR")
    later_llm = harness.CannedLLM(scenarios["en"])
    later = await harness.run_language(args, scenarios["en"], later_llm, 2, "CEFR")

    assert first_llm.calls == 1 + harness.SCRIPTED_TURNS_PER_RUN
    assert later_llm.calls == harness.SCRIPTED_TURNS_PER_RUN
    assert first.warmup_ms is not None
    assert later.warmup_ms is None


async def test_warmup_failure_is_recorded_and_does_not_eat_a_measured_turn(
    harness: ModuleType, scenarios: dict
) -> None:
    args = harness.parse_args(["--dry-run"])

    run = await harness.run_language(args, scenarios["en"], ExplodingLLM(), 1, "CEFR")

    assert run.warmup_ms is None
    assert run.warmup_error is not None and "endpoint unreachable" in run.warmup_error
    assert len(run.turns) == 1
    assert run.turns[0].error is not None


def test_report_notes_say_the_warmup_is_discarded(harness: ModuleType) -> None:
    text = render(harness, sample_runs(harness))

    assert "warm-up" in text.lower()
    assert "cold start" in text
    assert "discarded" in text


def test_main_notes_the_discarded_warmup_per_language(
    harness: ModuleType, tmp_path: Path
) -> None:
    code = harness.main(
        ["--dry-run", "--force", "--out-dir", str(tmp_path), "--languages", "en,de"]
    )

    assert code == 0
    (report,) = tmp_path.glob("eval-prompts-*.md")
    text = report.read_text(encoding="utf-8")
    assert "en: warm-up discarded" in text
    assert "de: warm-up discarded" in text


# --- scripted conversation -----------------------------------------------------------


async def test_run_language_scripts_two_turns(harness: ModuleType, scenarios: dict) -> None:
    args = harness.parse_args(["--dry-run"])
    scenario = scenarios["en"]

    run = await harness.run_language(args, scenario, harness.CannedLLM(scenario), 1, "CEFR")

    assert run.error is None
    assert run.scenario_id == "cafe-order"
    assert [item.turn for item in run.turns] == [1, 2]
    assert all(item.reply.strip() for item in run.turns)
    assert all(item.adherence_ok for item in run.turns)
    assert all(item.latency_ms is not None and item.latency_ms >= 0 for item in run.turns)
    assert all(item.evaluated and item.json_valid for item in run.turns)
    assert all(item.corrections == 1 for item in run.turns)


async def test_run_language_records_stream_error(harness: ModuleType, scenarios: dict) -> None:
    args = harness.parse_args(["--dry-run"])

    run = await harness.run_language(args, scenarios["ja"], ExplodingLLM(), 1, "JLPT")

    assert run.error is not None and "endpoint unreachable" in run.error
    assert len(run.turns) == 1
    assert run.turns[0].error is not None
    assert run.turns[0].adherence_ok is False
    assert run.turns[0].json_valid is False


async def test_run_language_records_evaluation_failure(
    harness: ModuleType, scenarios: dict
) -> None:
    args = harness.parse_args(["--dry-run"])
    llm = BadJsonLLM("好的，一杯拿铁，谢谢。")

    run = await harness.run_language(args, scenarios["zh"], llm, 1, "HSK")

    assert run.error is None
    assert len(run.turns) == 2
    assert all(item.adherence_ok for item in run.turns)
    assert all(item.evaluated for item in run.turns)
    assert all(item.json_valid is False for item in run.turns)
    assert all(item.eval_error for item in run.turns)
    assert all(item.corrections == 0 for item in run.turns)


# --- canned provider alignment (AST-029) ---------------------------------------------

# `--dry-run` answered from a per-language café script, so inside the `de` train role the
# canned barista offered Kuchen and the dry run measured the wrong dialogue.
CAFE_WORDS = ("latte", "oat milk", "to go", "receipt", "cup", "size", "café", "cafe")
KONBINI_WORDS = ("お弁当", "温め", "袋", "カード", "現金", "レジ")
RESTAURANT_WORDS = ("菜单", "两位", "不辣", "买单")


def test_canned_replies_are_keyed_by_scenario(harness: ModuleType) -> None:
    assert set(harness.CANNED_REPLIES) == set(harness.SCENARIO_BY_LANGUAGE.values())


def test_every_scenario_has_its_own_canned_replies(
    harness: ModuleType, scenarios: dict
) -> None:
    for scenario in scenarios.values():
        replies = harness.canned_replies(scenario.id)

        assert len(replies) == harness.SCRIPTED_TURNS_PER_RUN, scenario.id
        assert all(reply.strip() for reply in replies), scenario.id

    # One shared set is exactly what made `de` talk about cake inside the train role.
    assert len({harness.canned_replies(scenario.id) for scenario in scenarios.values()}) == len(
        harness.SCENARIO_BY_LANGUAGE
    )


def test_canned_llm_serves_the_replies_of_its_scenario(
    harness: ModuleType, scenarios: dict
) -> None:
    for scenario in scenarios.values():
        llm = harness.CannedLLM(scenario)

        assert llm.scenario_id == scenario.id
        assert llm.language == scenario.language
        assert llm.replies == harness.CANNED_REPLIES[scenario.id]


def test_canned_replies_reject_an_unknown_scenario(harness: ModuleType) -> None:
    with pytest.raises(LookupError) as error:
        harness.canned_replies("space-elevator")

    assert "no canned replies" in str(error.value)
    assert "bahn-ticket" in str(error.value)


def test_each_canned_set_uses_its_own_scenario_vocabulary(
    harness: ModuleType, scenarios: dict
) -> None:
    for scenario in scenarios.values():
        text = " ".join(harness.canned_replies(scenario.id))
        hits = [
            target
            for target in scenario.vocab_targets
            if target.lower() in text.lower()
        ]

        assert hits, f"{scenario.id}: canned replies share no term with {scenario.vocab_targets}"


@pytest.mark.parametrize(
    ("language", "words"),
    [("en", CAFE_WORDS), ("ja", KONBINI_WORDS), ("zh", RESTAURANT_WORDS)],
)
def test_canned_replies_speak_their_own_scenario(
    harness: ModuleType, scenarios: dict, language: str, words: tuple[str, ...]
) -> None:
    text = " ".join(harness.canned_replies(scenarios[language].id)).lower()

    assert [word for word in words if word in text] != [], f"{scenarios[language].id}: {text}"


def test_canned_german_replies_buy_a_train_ticket(
    harness: ModuleType, scenarios: dict
) -> None:
    text = " ".join(harness.canned_replies(scenarios["de"].id)).lower()

    assert [word for word in COFFEE_WORDS if word in text] == []
    assert [word for word in TRAIN_WORDS if word in text] != []


async def test_dry_run_canned_replies_keep_two_turns_and_one_correction(
    harness: ModuleType, scenarios: dict
) -> None:
    args = harness.parse_args(["--dry-run"])

    for language, scenario in scenarios.items():
        run = await harness.run_language(
            args,
            scenario,
            harness.CannedLLM(scenario),
            1,
            args.level_scales.get(language, "CEFR"),
        )

        assert run.error is None, language
        assert run.scenario_id == scenario.id, language
        assert [item.turn for item in run.turns] == [1, 2], language
        assert all(item.reply.strip() for item in run.turns), language
        assert all(item.adherence_ok for item in run.turns), language
        assert all(item.corrections == 1 for item in run.turns), language


# --- language-aware evaluation (AST-035) ---------------------------------------------

# The product keeps a case-only correction in German (AST-032), so the harness has to hand
# the scenario language to `normalize()`: without it the report drops every German
# capitalisation fix the app shows the learner, and the metric lies.
GERMAN_LEARNER_TURN = "ich spreche gut deutsch"
GERMAN_CASE_ONLY_PAYLOAD: dict[str, object] = {
    "corrections": [
        {
            "original": GERMAN_LEARNER_TURN,
            "corrected": "Ich spreche gut Deutsch",
            "explanation_pt": "Em alemão, início de frase e substantivos vão em maiúscula.",
            "type": "grammar",
            "severity": "medium",
            "confidence": 0.9,
        }
    ],
    "vocab": [],
    "scene_progress": {"goals_met": [], "status": "ongoing"},
    "language_leak": False,
}


def evaluated_turn(harness: ModuleType, scenario: object) -> object:
    """The per-turn record `run_language` builds before it calls `evaluate_turn`."""
    return harness.TurnResult(
        language=scenario.language,
        run=1,
        turn=1,
        user_text=GERMAN_LEARNER_TURN,
        reply="Gut! Möchten Sie einfach oder Hin- und Rückfahrt?",
    )


async def test_german_turn_keeps_the_case_only_correction(
    harness: ModuleType, scenarios: dict
) -> None:
    scenario = scenarios["de"]
    turn = evaluated_turn(harness, scenario)

    await harness.evaluate_turn(
        harness.parse_args(["--dry-run"]),
        scenario,
        EvaluationLLM(GERMAN_CASE_ONLY_PAYLOAD),
        turn,
    )

    assert turn.eval_error is None
    assert turn.json_valid is True
    assert turn.corrections == 1


async def test_english_scenario_drops_the_same_case_only_correction(
    harness: ModuleType, scenarios: dict
) -> None:
    """Control: identical payload, only the scenario language differs."""
    scenario = scenarios["en"]
    turn = evaluated_turn(harness, scenario)

    await harness.evaluate_turn(
        harness.parse_args(["--dry-run"]),
        scenario,
        EvaluationLLM(GERMAN_CASE_ONLY_PAYLOAD),
        turn,
    )

    assert turn.eval_error is None
    assert turn.json_valid is True
    assert turn.corrections == 0


# --- argparse and main ---------------------------------------------------------------


def test_parse_args_defaults(harness: ModuleType) -> None:
    args = harness.parse_args([])

    assert args.base_url == "http://127.0.0.1:11434/v1"
    assert args.languages == harness.DEFAULT_LANGUAGES
    assert args.runs == 1
    assert args.model
    assert args.out_dir_path.name or args.out_dir_path == REPO_ROOT / "docs" / "spikes"
    assert args.dry_run is False and args.force is False


def test_parse_args_accepts_language_subset(harness: ModuleType) -> None:
    args = harness.parse_args(["--languages", "ja, de,ja"])

    assert args.languages == ("ja", "de")


@pytest.mark.parametrize(
    "argv",
    [
        ["--languages", "fr"],
        ["--runs", "0"],
        ["--timeout", "0"],
        ["--base-url", "ftp://127.0.0.1"],
    ],
)
def test_parse_args_rejects_bad_input(harness: ModuleType, argv: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        harness.parse_args(argv)

    assert error.value.code == 2


def test_help_exits_zero(harness: ModuleType) -> None:
    with pytest.raises(SystemExit) as error:
        harness.parse_args(["--help"])

    assert error.value.code == 0


def test_main_dry_run_with_force_writes_into_temp_dir(
    harness: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry run must never build the real HTTP client")

    monkeypatch.setattr(harness, "OpenAICompatLLM", explode)

    code = harness.main(
        ["--dry-run", "--force", "--out-dir", str(tmp_path), "--languages", "en,ja,zh,de"]
    )

    assert code == 0
    reports = list(tmp_path.glob("eval-prompts-*.md"))
    assert len(reports) == 1
    text = reports[0].read_text(encoding="utf-8")
    assert "# Prompt evaluation —" in text
    for language in ("en", "ja", "zh", "de"):
        assert f"| {language} |" in text
    assert "## Totals" in text
    assert "| 8 |" in text  # 4 languages x 2 scripted turns


def test_main_dry_run_without_force_writes_nothing(
    harness: ModuleType, tmp_path: Path
) -> None:
    code = harness.main(["--dry-run", "--out-dir", str(tmp_path)])

    assert code == 0
    assert list(tmp_path.glob("eval-prompts-*.md")) == []


def test_main_dry_run_force_ignored_outside_temp_dir(
    harness: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(harness, "is_temp_dir", lambda path: False)

    code = harness.main(["--dry-run", "--force", "--out-dir", str(tmp_path)])

    assert code == 0
    assert list(tmp_path.glob("eval-prompts-*.md")) == []


def test_main_returns_1_when_every_turn_fails(
    harness: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(harness, "build_llm", lambda args, language: ExplodingLLM())

    code = harness.main(["--out-dir", str(tmp_path), "--force", "--languages", "en,ja"])

    assert code == 1
    reports = list(tmp_path.glob("eval-prompts-*.md"))
    assert len(reports) == 1
    text = reports[0].read_text(encoding="utf-8")
    assert "## Errors" in text
    assert "## Diagnosis" in text
    assert "endpoint unreachable" in text


# --- fakes ---------------------------------------------------------------------------


class ExplodingLLM:
    """Fails the very first streamed chunk, the way an unreachable endpoint does."""

    name = "exploding"

    async def chat_stream(self, messages: list[dict[str, str]], **overrides: object):
        raise RuntimeError("endpoint unreachable")
        yield ""  # never reached; keeps this an async generator

    async def chat_json(
        self, messages: list[dict[str, str]], schema: dict[str, object], **overrides: object
    ) -> dict[str, object]:
        raise RuntimeError("endpoint unreachable")


class EvaluationLLM:
    """Streams in-character text and answers `chat_json` with a fixed payload.

    The payload is what the model *says*: the harness decides what to keep of it, so a
    case-only pair must be dropped or kept based on the scenario language only.
    """

    name = "evaluation-payload"

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def chat_stream(self, messages: list[dict[str, str]], **overrides: object):
        yield "Gut! Möchten Sie einfach oder Hin- und Rückfahrt?"

    async def chat_json(
        self, messages: list[dict[str, str]], schema: dict[str, object], **overrides: object
    ) -> dict[str, object]:
        return self.payload


class BadJsonLLM:
    """Streams a well-formed reply but never returns valid JSON for the evaluation."""

    name = "bad-json"

    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def chat_stream(self, messages: list[dict[str, str]], **overrides: object):
        yield self.reply

    async def chat_json(
        self, messages: list[dict[str, str]], schema: dict[str, object], **overrides: object
    ) -> dict[str, object]:
        raise RuntimeError("model did not return valid JSON after retry")
