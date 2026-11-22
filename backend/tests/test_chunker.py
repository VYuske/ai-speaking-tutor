from __future__ import annotations

import pytest

from app.orchestrator.chunker import SentenceChunker


def feed_all(parts: list[str]) -> list[str]:
    chunker = SentenceChunker()
    sentences: list[str] = []
    for part in parts:
        sentences.extend(chunker.feed(part))
    tail = chunker.flush()
    if tail:
        sentences.append(tail)
    return sentences


def test_splits_latin_sentences() -> None:
    assert feed_all(["Hello there. How are you? "]) == ["Hello there.", "How are you?"]


def test_streaming_across_deltas() -> None:
    assert feed_all(["Hi, I wou", "ld like a ", "coffee. Than", "ks!"]) == [
        "Hi, I would like a coffee.",
        "Thanks!",
    ]


def test_decimal_is_not_a_boundary() -> None:
    assert feed_all(["Pi is 3.14 exactly."]) == ["Pi is 3.14 exactly."]


def test_abbreviation_is_not_a_boundary() -> None:
    assert feed_all(["Mr. Smith is here. Welcome!"]) == [
        "Mr. Smith is here.",
        "Welcome!",
    ]


def test_cjk_terminators() -> None:
    assert feed_all(["こんにちは。元気ですか？", "はい！"]) == [
        "こんにちは。",
        "元気ですか？",
        "はい！",
    ]


def test_flush_returns_remaining_text() -> None:
    chunker = SentenceChunker()
    assert chunker.feed("no terminator yet") == []
    assert chunker.flush() == "no terminator yet"
    assert chunker.flush() is None


def test_very_long_segment_is_cut_on_space() -> None:
    chunker = SentenceChunker(max_chars=40)
    text = "word " * 20
    out = chunker.feed(text)
    assert out and all(len(sentence) <= 40 for sentence in out)


@pytest.mark.parametrize("text", ["", "   "])
def test_blank_input(text: str) -> None:
    assert feed_all([text]) == []


# ---------------------------------------------------------------- AST-041


def test_first_ja_chunk_splits_at_the_clause_marker() -> None:
    assert feed_all(["おっしゃいましたが、コーヒーを温めてください。"])[0] == "おっしゃいましたが、"


def test_first_zh_chunk_splits_at_the_clause_marker() -> None:
    assert feed_all(["请问您需要点什么饮料，我们有咖啡。"])[0] == "请问您需要点什么饮料，"


def test_first_clause_minimum_is_configurable() -> None:
    chunker = SentenceChunker(min_clause_chars=3)
    assert chunker.feed("はい、コーヒーを温めてください。")[0] == "はい、"


def test_short_first_clause_is_not_split() -> None:
    sentences = feed_all(["嗯、コーヒーを温めてください。"])
    assert "嗯、" not in sentences


def test_zh_clause_wins_over_a_mid_word_cut() -> None:
    assert feed_all(["没问题，您想喝什么口味的咖啡？"])[0] == "没问题，"


def test_first_chunk_never_crosses_a_sentence_terminator() -> None:
    out = feed_all(["Of course! Would you like that hot or iced, and more?"])
    assert out[0] == "Of course!"


def test_number_is_not_a_clause_boundary() -> None:
    chunker = SentenceChunker(min_clause_chars=3)
    assert chunker.feed("The total is 1,234 dollars, right?")[0] == (
        "The total is 1,234 dollars,"
    )


def test_latin_first_sentence_still_waits_for_the_terminator() -> None:
    assert feed_all(["Hello there. How are you? "]) == ["Hello there.", "How are you?"]


def test_cjk_first_chunk_respects_the_character_cap() -> None:
    chunker = SentenceChunker(first_chunk_chars=6)
    out = chunker.feed("コーヒーを温めてください。")
    assert out and len(out[0]) <= 6
    assert out[0] == "コーヒーを"


def test_ja_window_cut_does_not_leave_a_bare_kanji_stem() -> None:
    chunker = SentenceChunker(first_chunk_chars=6)
    first = chunker.feed("コーヒーは温めます。")[0]
    assert first == "コーヒーは"


def test_first_chunk_is_not_cut_inside_a_kana_word() -> None:
    # One hiragana run longer than the cap: wait for the terminator instead of
    # splitting the word (and never drop characters).
    sentences = feed_all(["ありがとうございます。"])
    assert sentences == ["ありがとうございます。"]


def test_incremental_ja_cut_waits_for_the_lookahead_character() -> None:
    # The buffer is exactly at the cap when the kanji arrives: without the next
    # character there is no way to know whether it starts a verb (`温|めて`).
    assert feed_all(list("このコーヒーを温めてください。")) == [
        "このコーヒーを",
        "温めてください。",
    ]


def test_incremental_ja_kana_run_is_not_split() -> None:
    assert feed_all(list("ありがとうございます。")) == ["ありがとうございます。"]


def test_cjk_character_cut_loses_no_text() -> None:
    text = "コーヒーを温めてください。"
    assert "".join(feed_all([text])) == text


def test_cjk_max_chars_is_cut_by_character() -> None:
    chunker = SentenceChunker(max_chars=20)
    out = chunker.feed("あ" * 50)
    assert out and all(len(sentence) <= 20 for sentence in out)
