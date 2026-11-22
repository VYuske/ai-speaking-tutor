"""Deterministic reading aids: furigana/romaji for `ja`, tonal pinyin for `zh`.

No LLM and no network: `pykakasi` and `pypinyin` are local dictionary lookups, so
the aid is free and reproducible. `en`/`de` have nothing to romanize and return
``None``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import pykakasi
from pypinyin import Style, lazy_pinyin


@dataclass(frozen=True)
class Segment:
    """One displayable unit of the original text plus how to read it."""

    text: str
    reading: str
    romaji: str | None = None


@dataclass(frozen=True)
class Reading:
    kind: Literal["furigana", "pinyin"]
    text: str
    kana: str | None
    segments: tuple[Segment, ...]


# Only the Japanese script reaches pykakasi: its state machine drops emoji (the
# private-use branch) and emits full-width punctuation twice, so latin,
# punctuation and emoji are passed through as their own segments.
_JA_RUN = re.compile(
    r"[\u3005\u3041-\u3096\u30a1-\u30fa\u30fc-\u30fe\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+"
)
# pypinyin romanizes Han characters and returns everything else verbatim.
_ZH_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")

# pykakasi's dictionary stores 「今日は」 as the greeting こんにちは, which misreads the
# everyday topic construction 「今日は〜」. Readings are word-based, so the topic
# particle is cut off a preceding kanji compound before conversion; single-kanji
# words (「実は」→じつは) keep their dictionary entry.
_TOPIC_PARTICLE = re.compile(r"(?<=[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]{2})(?=は)")


@lru_cache(maxsize=1)
def _converter() -> pykakasi.Kakasi:
    """One converter per process; the dictionary is ~10 MB, so load it once."""
    return pykakasi.Kakasi()


@lru_cache(maxsize=512)
def reading_for(text: str, language: str) -> Reading | None:
    """Return the reading aid for ``text``, or ``None`` when there is nothing to show."""
    if not text or not text.strip():
        return None
    language = language.strip().lower()
    if language == "ja":
        return _reading_ja(text)
    if language == "zh":
        return _reading_zh(text)
    return None


def _reading_ja(text: str) -> Reading:
    entries: list[tuple[Segment, bool]] = []
    for chunk, is_japanese in _split_runs(text, _JA_RUN):
        if not is_japanese:
            entries.append((Segment(text=chunk, reading=chunk, romaji=chunk), False))
            continue
        for piece in filter(None, _TOPIC_PARTICLE.split(chunk)):
            for token in _converter().convert(piece):
                entries.append((_ja_segment(token), True))
    segments = tuple(segment for segment, _ in entries)
    return Reading(
        kind="furigana",
        text=_join_spaced(entries),
        kana="".join(segment.reading for segment in segments),
        segments=segments,
    )


def _reading_zh(text: str) -> Reading:
    entries: list[tuple[Segment, bool]] = []
    for chunk, is_han in _split_runs(text, _ZH_RUN):
        if not is_han:
            entries.append((Segment(text=chunk, reading=chunk), False))
            continue
        syllables = lazy_pinyin(chunk, style=Style.TONE)
        if len(syllables) != len(chunk):
            syllables = [lazy_pinyin(char, style=Style.TONE)[0] for char in chunk]
        for char, syllable in zip(chunk, syllables, strict=True):
            entries.append((Segment(text=char, reading=syllable or char), True))
    return Reading(
        kind="pinyin",
        text=_join_spaced(entries),
        kana=None,
        segments=tuple(segment for segment, _ in entries),
    )


def _ja_segment(token: dict[str, str]) -> Segment:
    original = token.get("orig", "")
    return Segment(
        text=original,
        reading=token.get("hira") or original,
        romaji=token.get("hepburn") or original,
    )


def _join_spaced(entries: list[tuple[Segment, bool]]) -> str:
    """Space-separate the readings of adjacent words, keep punctuation attached."""
    out = ""
    previous_was_word = False
    for segment, is_word in entries:
        if is_word and previous_was_word:
            out += " "
        out += segment.romaji or segment.reading
        previous_was_word = is_word
    return out


def _split_runs(text: str, pattern: re.Pattern[str]) -> Iterator[tuple[str, bool]]:
    """Alternate ``(chunk, matched)`` pieces so the caller can romanize only matches."""
    position = 0
    for match in pattern.finditer(text):
        if match.start() > position:
            yield text[position : match.start()], False
        yield match.group(0), True
        position = match.end()
    if position < len(text):
        yield text[position:], False
