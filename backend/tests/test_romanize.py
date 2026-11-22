"""Contract for the deterministic reading aid (AST-045).

No network, no models: `pykakasi` and `pypinyin` are pure local tables, so these
tests are cheap and reproducible.
"""

from __future__ import annotations

import pytest

from app.core.romanize import Reading, Segment, reading_for

# ------------------------------------------------------------------- japanese


def test_ja_kanji_yields_hiragana_and_hepburn() -> None:
    reading = reading_for("天気", "ja")

    assert reading is not None
    assert reading.kind == "furigana"
    assert reading.kana == "てんき"
    assert reading.text == "tenki"
    assert reading.segments
    assert "".join(segment.text for segment in reading.segments) == "天気"


def test_ja_hiragana() -> None:
    reading = reading_for("ねこ", "ja")

    assert reading is not None
    assert reading.kana == "ねこ"
    assert reading.text == "neko"


def test_ja_katakana_becomes_hiragana_and_romaji() -> None:
    reading = reading_for("カタカナ", "ja")

    assert reading is not None
    assert reading.kana == "かたかな"
    assert reading.text == "katakana"


def test_ja_sentence_keeps_kana_and_romaji() -> None:
    reading = reading_for("今日はいい天気ですね", "ja")

    assert reading is not None
    assert reading.kind == "furigana"
    assert reading.kana is not None
    assert "きょう" in reading.kana
    # Long vowels are spelled differently per converter (`kyou`/`kyō`): assert only
    # the stable part instead of pinning a spelling.
    assert reading.text
    assert "tenki" in reading.text
    assert reading.segments


def test_ja_mixed_text_does_not_crash() -> None:
    reading = reading_for("Hello 天気", "ja")

    assert reading is not None
    assert "tenki" in reading.text


def test_ja_punctuation_and_emoji_are_preserved() -> None:
    reading = reading_for("こんにちは！🙂", "ja")

    assert reading is not None
    assert "！" in reading.text
    assert "🙂" in reading.text


# --------------------------------------------------------------------- chinese


def test_zh_hello_has_tone_marks() -> None:
    reading = reading_for("你好", "zh")

    assert reading is not None
    assert reading.kind == "pinyin"
    assert reading.kana is None
    assert "nǐ" in reading.text
    assert "hǎo" in reading.text
    assert [segment.reading for segment in reading.segments] == ["nǐ", "hǎo"]


def test_zh_sentence_has_tone_marks() -> None:
    reading = reading_for("今天天气很好", "zh")

    assert reading is not None
    assert reading.kind == "pinyin"
    assert "jīn" in reading.text
    assert "hǎo" in reading.text
    assert [segment.text for segment in reading.segments] == list("今天天气很好")


def test_zh_punctuation_is_preserved() -> None:
    reading = reading_for("你好，世界", "zh")

    assert reading is not None
    assert "，" in reading.text
    assert "nǐ" in reading.text
    assert "hǎo" in reading.text


# ------------------------------------------------------------- no-op languages


@pytest.mark.parametrize("language", ["en", "de"])
def test_latin_languages_have_no_reading(language: str) -> None:
    assert reading_for("Good morning", language) is None


@pytest.mark.parametrize("text", ["", " ", "\n\t "])
def test_blank_text_has_no_reading(text: str) -> None:
    assert reading_for(text, "ja") is None
    assert reading_for(text, "zh") is None


# ------------------------------------------------------------------ determinism


def test_two_calls_return_equal_readings() -> None:
    first = reading_for("天気", "ja")
    second = reading_for("天気", "ja")

    assert first == second
    # The public API is cached, so repeated calls must not rebuild the result.
    assert first is second


def test_segments_are_immutable() -> None:
    reading = reading_for("ねこ", "ja")

    assert reading is not None
    assert isinstance(reading, Reading)
    assert all(isinstance(segment, Segment) for segment in reading.segments)
    with pytest.raises(AttributeError):
        reading.text = "other"  # type: ignore[misc]
