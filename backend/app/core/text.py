"""Text cleanup for the speech path.

The LLM occasionally answers with markdown (`**orange**`, links, bullets, code
spans). The UI transcript keeps it verbatim, but the TTS must speak the words,
not the markup: Kokoro reads `**` as noise and stretches the sentence, which is
what took `en` to 4.1 s of first audio on AST-039.
"""

from __future__ import annotations

import re

_LINK = re.compile(r"!?\[([^\[\]]*)\]\([^()]*\)")
_EMPHASIS = re.compile(
    r"\*\*(?P<bold>.+?)\*\*"
    r"|\*(?P<italic>.+?)\*"
    r"|__(?P<strong>.+?)__"
    r"|`{1,3}(?P<code>[^`]+?)`{1,3}"
)
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.MULTILINE)
_BULLET = re.compile(r"^[ \t]*(?:[-*+•]|\d{1,2}[.)])[ \t]+", re.MULTILINE)
_LEFTOVER = re.compile(r"[*`]")
_WHITESPACE = re.compile(r"\s+")


def sanitize_for_tts(text: str) -> str:
    """Drop markdown markers, keep the words, the punctuation and the CJK.

    Punctuation is preserved on purpose: the chunker and the TTS both use it for
    prosody (`、` and `，` are clause boundaries, `。` and `？` sentence ends).
    """
    if not text:
        return ""
    cleaned = _LINK.sub(r"\1", text)
    cleaned = _EMPHASIS.sub(_keep_content, cleaned)
    cleaned = _HEADING.sub("", cleaned)
    cleaned = _BULLET.sub("", cleaned)
    cleaned = _LEFTOVER.sub("", cleaned)
    return _WHITESPACE.sub(" ", cleaned).strip()


def _keep_content(match: re.Match[str]) -> str:
    return match.group(match.lastgroup or 0)
