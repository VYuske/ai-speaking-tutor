from __future__ import annotations

CJK_TERMINATORS = "。！？；"
LATIN_TERMINATORS = ".!?…"
CLAUSE_MARKERS = "、，,；;"
ABBREVIATIONS = {
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "st",
    "vs",
    "etc",
    "jr",
    "sr",
    "no",
    "e.g",
    "i.e",
}

# A clause only becomes the first chunk on its own when it carries at least this
# many characters (marker included). Shorter lead-ins ("Oh,", "嗯、") are not
# worth a synthesis call of their own.
MIN_CLAUSE_CHARS = 10
# ... but a long first sentence still delays the audio start, and Kokoro's cost is
# proportional to the length of the audio. CJK has no spaces to fall back on, so
# the first chunk of a CJK reply is capped at this many characters (~2 s of ja/zh
# audio) instead of waiting for the sentence terminator.
FIRST_CHUNK_CHARS = 8
# Inside that cap a clause is still preferred over a raw character cut (it sounds
# like a pause instead of a stutter), as long as it is a real clause: 2 characters
# of content plus the marker. A one-character filler ("嗯、", "啊，") never cuts.
MIN_WINDOW_CLAUSE_CHARS = 3


class SentenceChunker:
    """Splits streamed LLM text into speakable sentences.

    The TTS provider is called per sentence, so a bad split is audible.
    Handles Latin punctuation, decimals, common abbreviations and CJK
    terminators (which need no following whitespace).

    The first chunk of a turn is cut earlier, at a clause boundary (`、，,；;`)
    or at a CJK character cap, so the audio starts before the full sentence;
    after that the sentence behaviour is unchanged.
    """

    def __init__(
        self,
        min_chars: int = 2,
        max_chars: int = 400,
        min_clause_chars: int = MIN_CLAUSE_CHARS,
        first_chunk_chars: int = FIRST_CHUNK_CHARS,
    ) -> None:
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.min_clause_chars = min_clause_chars
        self.first_chunk_chars = first_chunk_chars
        self._buffer = ""
        self._first = True

    def feed(self, text: str) -> list[str]:
        if not text:
            return []
        self._buffer += text
        return self._drain()

    def flush(self) -> str | None:
        remaining = self._buffer.strip()
        self._buffer = ""
        return remaining if len(remaining) >= self.min_chars else None

    def _drain(self) -> list[str]:
        sentences: list[str] = []
        while True:
            cut = self._next_cut()
            if cut is None or cut <= 0:
                break
            piece = self._buffer[:cut].strip()
            self._buffer = self._buffer[cut:].lstrip()
            if len(piece) >= self.min_chars:
                sentences.append(piece)
                self._first = False

        if len(self._buffer) > self.max_chars:
            cut = self._buffer.rfind(" ", 0, self.max_chars)
            if cut <= 0:
                # No space to cut on: ja/zh have none, so cut by character.
                cut = self.max_chars
            sentences.append(self._buffer[:cut].strip())
            self._buffer = self._buffer[cut:].lstrip()
            self._first = False
        return sentences

    def _next_cut(self) -> int | None:
        if self._first:
            clause = self._find_clause(self._buffer, self.min_clause_chars)
            if clause is not None:
                return clause

        boundary = self._find_boundary(self._buffer)
        if self._first and _has_cjk(self._buffer):
            if boundary is not None and boundary <= self.first_chunk_chars:
                return boundary
            if len(self._buffer) >= self.first_chunk_chars:
                window = self._window_cut()
                if window:
                    return window
        return boundary

    def _window_cut(self) -> int | None:
        """Character cut for the capped first chunk, on the last clause if any.

        Returns 0 when the cut would land inside a word; the caller then waits for
        the next delta instead of splitting the audio mid-word.
        """
        cut = self.first_chunk_chars
        while (
            0 < cut < len(self._buffer)
            and self._buffer[cut - 1].isdigit()
            and self._buffer[cut].isdigit()
        ):
            cut += 1
        for index in range(cut - 1, 0, -1):
            if index + 1 < MIN_WINDOW_CLAUSE_CHARS:
                break
            if self._buffer[index] not in CLAUSE_MARKERS or _between_digits(self._buffer, index):
                continue
            return index + 1
        if cut >= len(self._buffer) and _has_kana(self._buffer):
            # No lookahead yet: the stream may still append the rest of the word
            # (`温|めて`, `ござい|ます`). One more delta is cheaper than a stutter.
            return 0
        return self._word_cut(cut)

    def _word_cut(self, cut: int) -> int:
        """Back off a character cut that would land inside a word.

        Japanese writes a verb as a kanji stem plus okurigana kana (`温|めます`)
        and runs katakana words like `コーヒー` together; cutting there is audible.
        Chinese has no kana, so a kanji run stays cuttable.
        """
        while 1 < cut < len(self._buffer):
            if _is_kana(self._buffer[cut]) and _is_kana(self._buffer[cut - 1]):
                while cut > 1 and _is_kana(self._buffer[cut - 1]):
                    cut -= 1
            elif _is_kana(self._buffer[cut]) and _is_kanji(self._buffer[cut - 1]):
                while cut > 1 and _is_kanji(self._buffer[cut - 1]):
                    cut -= 1
            else:
                break
        return cut if cut >= MIN_WINDOW_CLAUSE_CHARS else 0

    def _find_clause(self, text: str, min_chars: int) -> int | None:
        """First clause boundary of the first sentence worth cutting on.

        Stops at the sentence terminator: `Of course! Would you like ... iced,`
        must still start with `Of course!`, never with a clause of sentence two.
        """
        for index, char in enumerate(text):
            if char in CJK_TERMINATORS or char == "\n":
                return None
            if char in LATIN_TERMINATORS and not (
                char == "." and self._is_protected_dot(text, index)
            ):
                return None
            if char not in CLAUSE_MARKERS:
                continue
            if _between_digits(text, index):
                continue
            if index + 1 >= min_chars:
                return index + 1
        return None

    def _find_boundary(self, text: str) -> int | None:
        for index, char in enumerate(text):
            if char in CJK_TERMINATORS or char == "\n":
                return index + 1
            if char not in LATIN_TERMINATORS:
                continue
            if char == "." and self._is_protected_dot(text, index):
                continue
            following = text[index + 1] if index + 1 < len(text) else ""
            if following and not following.isspace() and following not in LATIN_TERMINATORS:
                continue
            return index + 1
        return None

    def _is_protected_dot(self, text: str, index: int) -> bool:
        if (
            0 < index < len(text) - 1
            and text[index - 1].isdigit()
            and text[index + 1].isdigit()
        ):
            return True
        if index == 0:
            return False
        start = index - 1
        while start >= 0 and text[start].isalpha():
            start -= 1
        token = text[start + 1 : index].lower()
        return token in ABBREVIATIONS


def _between_digits(text: str, index: int) -> bool:
    """`1,000` is a number, not a clause boundary."""
    return 0 < index < len(text) - 1 and text[index - 1].isdigit() and text[index + 1].isdigit()


def _has_cjk(text: str) -> bool:
    return any(_is_cjk_char(char) for char in text)


def _is_cjk_char(char: str) -> bool:
    code = ord(char)
    return (
        0x3000 <= code <= 0x30FF  # CJK punctuation, hiragana, katakana
        or 0x3400 <= code <= 0x4DBF  # CJK extension A
        or 0x4E00 <= code <= 0x9FFF  # CJK unified ideographs
        or 0xF900 <= code <= 0xFAFF  # CJK compatibility ideographs
        or 0xFF00 <= code <= 0xFFEF  # fullwidth forms (，！？)
    )


def _has_kana(text: str) -> bool:
    return any(_is_kana(char) for char in text)


def _is_kanji(char: str) -> bool:
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
    )


def _is_kana(char: str) -> bool:
    code = ord(char)
    return 0x3040 <= code <= 0x30FF or 0xFF66 <= code <= 0xFF9F
