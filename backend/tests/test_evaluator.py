from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.agent.evaluator import Evaluator, normalize
from app.agent.scenario import Scenario

SCENARIO = Scenario(
    id="test",
    language="en",
    title="Test",
    title_pt="Teste",
    level_min=1,
    level_max=6,
    persona="barista",
    setting="cafe",
    goal="order",
    opening_line="Hi!",
    success_criteria=("order a drink",),
    vocab_targets=(),
    guardrails=(),
)


class RaisingLLM:
    name = "raising"

    async def chat_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("boom")


class FixedLLM:
    name = "fixed"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def chat_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.payload


async def test_evaluation_failure_returns_empty() -> None:
    evaluation = await Evaluator(RaisingLLM()).evaluate(SCENARIO, 3, "hi", "hello")
    assert evaluation.corrections == []
    assert evaluation.vocab == []
    assert evaluation.language_leak is False


async def test_evaluator_normalizes_payload() -> None:
    llm = FixedLLM(
        {
            "corrections": [
                {
                    "original": "I go",
                    "corrected": "I went",
                    "explanation_pt": "Passado.",
                    "type": "grammar",
                    "severity": "high",
                    "confidence": 0.9,
                },
                {
                    "original": "x",
                    "corrected": "y",
                    "explanation_pt": "z",
                    "type": "grammar",
                    "severity": "low",
                    "confidence": 0.2,
                },
            ],
            "vocab": [{"term": "latte", "translation_pt": "latte"}],
            "scene_progress": {"goals_met": ["pediu"], "status": "ongoing"},
            "language_leak": True,
        }
    )
    evaluation = await Evaluator(llm).evaluate(SCENARIO, 3, "I go cafe", "Sure!")
    assert len(evaluation.corrections) == 1
    assert evaluation.corrections[0].corrected == "I went"
    assert evaluation.language_leak is True
    assert evaluation.goals_met == ["pediu"]


def test_normalize_caps_and_defaults() -> None:
    raw = {
        "corrections": [
            {
                "original": f"o{i}",
                "corrected": f"c{i}",
                "explanation_pt": "e",
                "type": "weird",
                "severity": "extreme",
                "confidence": 0.95,
            }
            for i in range(10)
        ],
        "vocab": [{"term": f"t{i}", "translation_pt": "x"} for i in range(10)],
        "scene_progress": {"goals_met": [], "status": "weird"},
        "language_leak": "yes",
    }
    evaluation = normalize(raw, max_corrections=3, max_vocab=2)
    assert len(evaluation.corrections) == 3
    assert evaluation.corrections[0].type == "grammar"
    assert evaluation.corrections[0].severity == "low"
    assert len(evaluation.vocab) == 2
    assert evaluation.status == "ongoing"
    assert evaluation.language_leak is True


def test_normalize_ignores_malformed_entries() -> None:
    evaluation = normalize(
        {
            "corrections": ["nope", {"original": "", "corrected": "x"}, None],
            "vocab": [{"term": "a"}],
            "scene_progress": "bad",
            "language_leak": None,
        }
    )
    assert evaluation.corrections == []
    assert evaluation.vocab == []


@pytest.mark.parametrize("confidence", [0.59, 0.6, 1.0])
def test_confidence_threshold(confidence: float) -> None:
    evaluation = normalize(
        {
            "corrections": [
                {
                    "original": "a",
                    "corrected": "b",
                    "explanation_pt": "c",
                    "type": "grammar",
                    "severity": "low",
                    "confidence": confidence,
                }
            ],
            "vocab": [],
            "scene_progress": {"goals_met": [], "status": "ongoing"},
            "language_leak": False,
        }
    )
    assert bool(evaluation.corrections) is (confidence >= 0.6)


# --- AST-027: precision filter on the learner's own text ------------------------------


class RecordingLLM:
    """Returns a fixed payload and keeps every call, so forwarding is assertable."""

    name = "recording"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        **overrides: Any,
    ) -> dict[str, Any]:
        self.calls.append({"messages": messages, "schema": schema, "overrides": overrides})
        return self.payload


def correction(
    original: str,
    corrected: str,
    confidence: float = 0.9,
    kind: str = "grammar",
) -> dict[str, Any]:
    return {
        "original": original,
        "corrected": corrected,
        "explanation_pt": "Explicação.",
        "type": kind,
        "severity": "low",
        "confidence": confidence,
    }


def payload(*corrections: dict[str, Any]) -> dict[str, Any]:
    return {
        "corrections": list(corrections),
        "vocab": [],
        "scene_progress": {"goals_met": [], "status": "ongoing"},
        "language_leak": False,
    }


LEARNER_TEXT = "I go to the station yesterday and buy two ticket."


def test_normalize_drops_invented_original() -> None:
    evaluation = normalize(
        payload(
            correction("I go to the station", "I went to the station"),
            correction("I bought two tickets", "I bought two tickets, please"),
        ),
        user_text=LEARNER_TEXT,
    )
    assert [item.original for item in evaluation.corrections] == ["I go to the station"]


def test_normalize_keeps_legitimate_substring_across_case_and_spacing() -> None:
    evaluation = normalize(
        payload(
            correction("i   GO to the Station", "I went to the station"),
            correction("TWO   TICKET", "two tickets"),
        ),
        user_text=LEARNER_TEXT.replace("station and", "station  and"),
    )
    assert [item.original for item in evaluation.corrections] == [
        "i   GO to the Station",
        "TWO   TICKET",
    ]


def test_normalize_matches_cjk_across_spacing() -> None:
    evaluation = normalize(
        payload(
            correction("袋はいりません", "袋はいりませんでした"),
            correction("温めてください", "温めていただけますか"),
        ),
        user_text="すみません、この お弁当を温めてください。袋は いりません。",
    )
    assert [item.original for item in evaluation.corrections] == [
        "袋はいりません",
        "温めてください",
    ]


def test_normalize_drops_corrections_equal_to_original() -> None:
    evaluation = normalize(
        payload(
            correction("two ticket", "Two  TICKET"),
            correction("two ticket", "two tickets"),
        ),
        user_text=LEARNER_TEXT,
    )
    assert [item.corrected for item in evaluation.corrections] == ["two tickets"]


def test_normalize_cap_applies_after_the_filter() -> None:
    invented = [correction(f"invented {index}", f"fix {index}") for index in range(5)]
    legitimate = [
        correction("I go", "I went"),
        correction("two ticket", "two tickets"),
        correction("yesterday and", "yesterday, and"),
    ]
    evaluation = normalize(
        payload(*invented, *legitimate),
        max_corrections=2,
        user_text=LEARNER_TEXT,
    )
    assert [item.corrected for item in evaluation.corrections] == ["I went", "two tickets"]


def test_normalize_without_user_text_keeps_everything() -> None:
    evaluation = normalize(
        payload(correction("not in the learner text", "anything")), max_corrections=3
    )
    assert [item.corrected for item in evaluation.corrections] == ["anything"]
    # The four positional parameters stay backward compatible.
    positional = normalize(payload(correction("a", "b")), 0.6, 3, 5)
    assert [item.corrected for item in positional.corrections] == ["b"]


def test_correct_learner_sentence_with_only_invented_corrections_is_empty() -> None:
    evaluation = normalize(
        payload(
            correction("I want a latte", "I would like a latte"),
            correction("can I get a latte", "could I get a latte"),
            correction("a flat white", "a flat white, please."),
        ),
        user_text="I would like a latte, please.",
    )
    assert evaluation.corrections == []


async def test_evaluate_forwards_user_text_to_the_filter() -> None:
    llm = RecordingLLM(
        payload(
            correction("I go to the station", "I went to the station"),
            correction("I buy a ticket", "I bought a ticket"),
        )
    )
    evaluation = await Evaluator(llm).evaluate(
        SCENARIO, 3, "I go to the station yesterday.", "Two tickets, then."
    )
    assert [item.corrected for item in evaluation.corrections] == ["I went to the station"]
    assert "I go to the station yesterday." in llm.calls[0]["messages"][1]["content"]


async def test_evaluate_drops_a_correction_that_only_matches_the_tutor_reply() -> None:
    llm = RecordingLLM(payload(correction("Where are you heading", "Where are you heading to")))
    evaluation = await Evaluator(llm).evaluate(
        SCENARIO, 3, "A latte, please.", "Where are you heading?"
    )
    assert evaluation.corrections == []


# --- AST-030: dedupe and punctuation/case-only corrections ----------------------------


def test_normalize_dedupes_identical_corrections() -> None:
    pair = ("我要一杯拿铁", "我想要一杯拿铁")
    evaluation = normalize(
        payload(*[correction(*pair, kind=kind) for kind in ("grammar", "vocab", "register")]),
        user_text="我要一杯拿铁，谢谢。",
    )
    assert len(evaluation.corrections) == 1
    assert evaluation.corrections[0].corrected == pair[1]


def test_normalize_dedupes_across_case_and_spacing() -> None:
    evaluation = normalize(
        payload(
            correction("I go", "I went"),
            correction("i   GO", "i went"),
        ),
        user_text=LEARNER_TEXT,
    )
    assert [item.corrected for item in evaluation.corrections] == ["I went"]


def test_normalize_drops_punctuation_only_correction() -> None:
    evaluation = normalize(
        payload(correction("Hi, could I get a latte", "Hi, could I get a latte?"))
    )
    assert evaluation.corrections == []


def test_normalize_drops_case_only_correction() -> None:
    evaluation = normalize(
        payload(correction("hi, could i get a latte", "Hi, could I get a Latte"))
    )
    assert evaluation.corrections == []


def test_normalize_keeps_a_real_word_change() -> None:
    evaluation = normalize(payload(correction("I go", "I went")), user_text=LEARNER_TEXT)
    assert [item.corrected for item in evaluation.corrections] == ["I went"]


def test_normalize_keeps_a_cjk_particle_change() -> None:
    evaluation = normalize(
        payload(correction("我要一杯拿铁", "我想要一杯拿铁")),
        user_text="我要一杯拿铁，谢谢。",
    )
    assert [item.corrected for item in evaluation.corrections] == ["我想要一杯拿铁"]


def test_normalize_cap_applies_after_dedupe_and_punctuation_filter() -> None:
    duplicate = [correction("I go", "I went") for _ in range(3)]
    punctuation_only = correction("two ticket.", "two ticket?")
    legitimate = [
        correction("and buy", "and bought"),
        correction("two ticket", "two tickets"),
    ]
    evaluation = normalize(
        payload(punctuation_only, *duplicate, *legitimate),
        max_corrections=2,
        user_text="I go yesterday and buy two ticket.",
    )
    assert [item.corrected for item in evaluation.corrections] == ["I went", "and bought"]


# --- AST-033: subsumption of a fragment and the full sentence -------------------------


ZH_FRAGMENTS = (
    correction("我要一个不辣的菜", "我要一份不辣的菜", kind="vocab"),
    correction("然后买单", "然后结账", kind="register"),
)
ZH_FULL = correction("我要一个不辣的菜，然后买单。", "我要一份不辣的菜，然后结账。")
ZH_LEARNER_TEXT = "我要一个不辣的菜，然后买单。"
OMS_FRAGMENT = correction("two ticket", "two tickets")
OMS_FULL = correction("yesterday and buy two ticket", "yesterday and bought two tickets")
OMS_LEARNER_TEXT = "I go to the station yesterday and buy two ticket."


def test_normalize_drops_a_fragment_covered_by_the_full_sentence() -> None:
    evaluation = normalize(payload(ZH_FULL, *ZH_FRAGMENTS), user_text=ZH_LEARNER_TEXT)
    assert [item.corrected for item in evaluation.corrections] == [ZH_FULL["corrected"]]


def test_normalize_replaces_a_kept_fragment_with_a_later_full_sentence() -> None:
    evaluation = normalize(payload(*ZH_FRAGMENTS, ZH_FULL), user_text=ZH_LEARNER_TEXT)
    assert [item.corrected for item in evaluation.corrections] == [ZH_FULL["corrected"]]


def test_normalize_replaces_a_kept_fragment_with_a_later_full_sentence_in_english() -> None:
    evaluation = normalize(payload(OMS_FRAGMENT, OMS_FULL), user_text=OMS_LEARNER_TEXT)
    assert [item.corrected for item in evaluation.corrections] == [OMS_FULL["corrected"]]


def test_normalize_keeps_two_different_fixes_of_the_same_fragment() -> None:
    evaluation = normalize(
        payload(
            correction("然后买单", "然后结账"),
            correction("然后买单", "然后算账"),
        ),
        user_text=ZH_LEARNER_TEXT,
    )
    assert [item.corrected for item in evaluation.corrections] == ["然后结账", "然后算账"]


def test_normalize_keeps_overlapping_originals_when_the_fix_differs() -> None:
    evaluation = normalize(
        payload(
            correction("然后买单", "然后结账"),
            correction("我要一个不辣的菜，然后买单。", "我要一份不辣的菜，现在算账。"),
        ),
        user_text=ZH_LEARNER_TEXT,
    )
    assert [item.corrected for item in evaluation.corrections] == [
        "然后结账",
        "我要一份不辣的菜，现在算账。",
    ]


def test_normalize_keeps_disjoint_corrections() -> None:
    evaluation = normalize(payload(*ZH_FRAGMENTS), user_text=ZH_LEARNER_TEXT)
    assert [item.corrected for item in evaluation.corrections] == ["我要一份不辣的菜", "然后结账"]


def test_normalize_cap_applies_after_subsumption() -> None:
    correction_of_the_learner = correction("I go", "I went")
    evaluation = normalize(
        payload(OMS_FRAGMENT, OMS_FULL, correction_of_the_learner),
        max_corrections=2,
        user_text=OMS_LEARNER_TEXT,
    )
    assert [item.corrected for item in evaluation.corrections] == [
        OMS_FULL["corrected"],
        "I went",
    ]


# --- AST-032: German capitalization policy --------------------------------------------

DE_LEARNER_TEXT = "ich möchte einen kaffee"
DE_CASE_ONLY = correction(DE_LEARNER_TEXT, "Ich möchte einen Kaffee.")
GERMAN_SCENARIO = replace(SCENARIO, id="test-de", language="de")


def test_normalize_keeps_a_case_only_correction_in_german() -> None:
    evaluation = normalize(payload(DE_CASE_ONLY), language="de", user_text=DE_LEARNER_TEXT)
    assert [item.corrected for item in evaluation.corrections] == ["Ich möchte einen Kaffee."]


def test_normalize_drops_a_case_only_correction_in_english() -> None:
    evaluation = normalize(payload(DE_CASE_ONLY), language="en", user_text=DE_LEARNER_TEXT)
    assert evaluation.corrections == []


def test_normalize_drops_a_case_only_correction_by_default() -> None:
    evaluation = normalize(payload(DE_CASE_ONLY))
    assert evaluation.corrections == []


def test_normalize_still_drops_a_punctuation_only_correction_in_german() -> None:
    evaluation = normalize(
        payload(correction("Guten Tag", "Guten Tag!")), language="de", user_text="Guten Tag"
    )
    assert evaluation.corrections == []


def test_normalize_keeps_a_real_word_change_in_german() -> None:
    evaluation = normalize(
        payload(correction(DE_LEARNER_TEXT, "ich hätte gern einen Kaffee.")),
        language="de",
        user_text=DE_LEARNER_TEXT,
    )
    assert [item.corrected for item in evaluation.corrections] == ["ich hätte gern einen Kaffee."]


# --- AST-032b: the German policy must hold on the production path (with user_text) ----


DE_PRODUCTION_TEXT = "ich spreche gut deutsch"
DE_PRODUCTION_CASE_ONLY = correction(DE_PRODUCTION_TEXT, "Ich spreche gut Deutsch")


def test_normalize_keeps_a_case_only_correction_in_german_with_user_text() -> None:
    evaluation = normalize(
        payload(DE_PRODUCTION_CASE_ONLY), language="de", user_text=DE_PRODUCTION_TEXT
    )
    assert [item.corrected for item in evaluation.corrections] == ["Ich spreche gut Deutsch"]


def test_normalize_drops_a_case_only_correction_in_english_with_user_text() -> None:
    evaluation = normalize(
        payload(DE_PRODUCTION_CASE_ONLY), language="en", user_text=DE_PRODUCTION_TEXT
    )
    assert evaluation.corrections == []


def test_normalize_drops_a_case_only_correction_by_default_with_user_text() -> None:
    evaluation = normalize(payload(DE_PRODUCTION_CASE_ONLY), user_text=DE_PRODUCTION_TEXT)
    assert evaluation.corrections == []


def test_normalize_still_drops_a_punctuation_only_correction_in_german_with_user_text() -> None:
    evaluation = normalize(
        payload(correction("Guten Tag", "Guten Tag!")), language="de", user_text="Guten Tag"
    )
    assert evaluation.corrections == []


def test_normalize_keeps_a_real_word_change_in_german_with_user_text() -> None:
    evaluation = normalize(
        payload(correction(DE_PRODUCTION_TEXT, "ich spreche sehr gut Deutsch")),
        language="de",
        user_text=DE_PRODUCTION_TEXT,
    )
    assert [item.corrected for item in evaluation.corrections] == [
        "ich spreche sehr gut Deutsch"
    ]


def test_normalize_drops_an_invented_original_in_german_with_user_text() -> None:
    evaluation = normalize(
        payload(correction("ich lerne jeden tag deutsch", "Ich lerne jeden Tag Deutsch")),
        language="de",
        user_text=DE_PRODUCTION_TEXT,
    )
    assert evaluation.corrections == []


async def test_evaluate_forwards_the_scenario_language_to_normalize() -> None:
    llm = RecordingLLM(payload(DE_CASE_ONLY))
    evaluation = await Evaluator(llm).evaluate(
        GERMAN_SCENARIO, 3, DE_LEARNER_TEXT, "Guten Tag! Was darf es sein?"
    )
    assert [item.corrected for item in evaluation.corrections] == ["Ich möchte einen Kaffee."]


async def test_evaluate_drops_the_same_case_only_correction_for_an_english_scenario() -> None:
    llm = RecordingLLM(payload(DE_CASE_ONLY))
    evaluation = await Evaluator(llm).evaluate(
        SCENARIO, 3, DE_LEARNER_TEXT, "Sure, coming right up!"
    )
    assert evaluation.corrections == []
