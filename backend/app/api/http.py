from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from app.core.config import SUPPORTED_LANGUAGES, AppConfig, ConfigError, Settings

router = APIRouter(prefix="/api")


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    state = request.app.state
    payload: dict[str, Any] = {
        "status": "ok",
        "languages": list(SUPPORTED_LANGUAGES),
        "profiles": state.config.profile_names(),
        "scenarios": {
            language: len(state.scenarios.list(language)) for language in SUPPORTED_LANGUAGES
        },
    }
    try:
        resolved = state.config.resolve("en", None, state.settings)
    except ConfigError as exc:
        payload["status"] = "degraded"
        payload["error"] = str(exc)
        return payload

    base_url = str(resolved.llm.get("base_url", ""))
    payload["llm"] = {
        "model": resolved.llm.get("model"),
        "endpoint": base_url,
        "reachable": await _ping_llm(base_url),
    }
    return payload


@router.get("/config")
async def config(request: Request) -> dict[str, Any]:
    state = request.app.state
    languages: dict[str, Any] = {}
    for language in SUPPORTED_LANGUAGES:
        try:
            resolved = state.config.resolve(language, None, state.settings)
        except ConfigError as exc:
            languages[language] = {"error": str(exc)}
            continue
        languages[language] = {
            "profile": resolved.profile,
            "level_scale": resolved.level_scale,
            "llm": {
                "model": resolved.llm.get("model"),
                "endpoint": resolved.llm.get("base_url"),
            },
            "stt": {
                "provider": resolved.stt.get("provider"),
                "device": resolved.stt.get("device"),
            },
            "tts": {
                "provider": resolved.tts.get("provider"),
                "device": resolved.tts.get("device"),
            },
        }
    return {
        "default_profile": _active_profile(state.config, state.settings),
        "profiles": state.config.profile_names(),
        "languages": languages,
    }


def _active_profile(config: AppConfig, settings: Settings) -> str:
    """Profile a new session gets when it does not pass an explicit choice.

    The UI echoes this value back in `session.start`, where it overrides the
    per-language defaults in `AppConfig.resolve`. Advertising the file default
    unconditionally downgraded every browser session to `skeleton` (fake audio)
    whenever `AST_PROFILE` said otherwise.

    An unknown `AST_PROFILE` is ignored on purpose: `resolve` rejects it, so the
    file default is what a session ends up with and is what gets advertised.
    """
    if settings.profile and settings.profile in config.profiles:
        return settings.profile
    return config.default_profile


@router.get("/scenarios")
async def scenarios(
    request: Request, language: str = Query(default="en")
) -> dict[str, Any]:
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"unsupported language '{language}'")
    items = request.app.state.scenarios.list(language)
    return {"language": language, "items": [item.to_public() for item in items]}


@router.get("/sessions/{session_id}")
async def session_review(request: Request, session_id: str) -> dict[str, Any]:
    review = request.app.state.db.review(session_id)
    if review is None:
        raise HTTPException(status_code=404, detail="session not found")
    return review


async def _ping_llm(base_url: str) -> str:
    if not base_url:
        return "unconfigured"
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            response = await client.get(f"{base_url.rstrip('/')}/models")
    except httpx.HTTPError:
        return "unreachable"
    return "ok" if response.status_code < 500 else f"http_{response.status_code}"
