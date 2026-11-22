from __future__ import annotations

import logging
from typing import Any

from app.agent.prompts import LANGUAGE_LABEL
from app.providers.base import LLMProvider

logger = logging.getLogger(__name__)

TRANSLATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"translation_pt": {"type": "string"}},
    "required": ["translation_pt"],
}

# A session repeats its opening line and common replies, so a small in-process cache
# turns those back into a single model call. Bounded to keep memory flat.
CACHE_SIZE = 256

_cache: dict[tuple[str, str], str] = {}


def clear_cache() -> None:
    """Drop every cached translation; used by tests and on a session reset."""
    _cache.clear()


def _remember(key: tuple[str, str], translation: str) -> None:
    if key not in _cache and len(_cache) >= CACHE_SIZE:
        _cache.pop(next(iter(_cache)))
    _cache[key] = translation


def build_translation_messages(text: str, language: str) -> list[dict[str, str]]:
    target = LANGUAGE_LABEL.get(language, language)
    system = (
        "You translate a language tutor's utterance for a Brazilian learner. "
        "Translate it into natural, concise Brazilian Portuguese. "
        "Rules: no explanations, no commentary, no notes, no markdown, no quotes; "
        "keep proper names, places and product names as written; keep the same tone and "
        "length; never add information that is not in the text. "
        "Return only the JSON field 'translation_pt'."
    )
    user = f"""Tutor's utterance ({language}, {target}):
{text}

Translate it into Brazilian Portuguese."""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _read_translation(raw: object) -> str | None:
    """Return the trimmed ``translation_pt`` of a chat_json payload, or ``None``."""
    if not isinstance(raw, dict):
        return None
    value = raw.get("translation_pt")
    if not isinstance(value, str):
        return None
    return value.strip() or None


async def translate_pt(llm: LLMProvider, text: str, language: str) -> str | None:
    """Translate the tutor's utterance to PT-BR, best-effort: never raise."""
    cleaned = text.strip()
    if not cleaned:
        return None
    key = (language, cleaned)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    messages = build_translation_messages(cleaned, language)
    try:
        raw = await llm.chat_json(
            messages, TRANSLATION_SCHEMA, temperature=0.0, language=language
        )
    except Exception as exc:  # noqa: BLE001 - translation is best-effort
        logger.warning("translation failed (%s): %s", language, exc)
        return None
    translation = _read_translation(raw)
    if translation is None:
        logger.warning("translation payload without a usable translation_pt (%s)", language)
        return None
    _remember(key, translation)
    return translation
