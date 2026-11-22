from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace

from app.core.config import ResolvedConfig
from app.core.errors import ProviderUnavailable
from app.providers.base import Providers
from app.providers.fakes import FakeLLM, FakeSTT, FakeTTS
from app.providers.llm.openai_compat import OpenAICompatLLM
from app.providers.tts.kokoro import KokoroTTS

logger = logging.getLogger(__name__)

# Markers of a local GPU model load that did not fit in memory. Only these degrade
# the audio providers; any other failure is a real error and must surface.
_LOCAL_OOM_MARKERS: tuple[str, ...] = ("out of memory", "cuda error", "oom")


def build_llm(cfg: dict[str, object]) -> FakeLLM | OpenAICompatLLM:
    provider = cfg.get("provider")
    if provider == "fake":
        return FakeLLM(cfg)
    if provider == "openai_compat":
        return OpenAICompatLLM(cfg)
    raise ProviderUnavailable(f"unknown llm provider '{provider}'")


def build_stt(cfg: dict[str, object], warnings: list[str]) -> object:
    provider = cfg.get("provider")
    if provider == "fake":
        return FakeSTT()
    if provider == "faster_whisper":
        from app.providers.stt.faster_whisper import FasterWhisperSTT

        return _with_fallback(
            "stt", provider, lambda: FasterWhisperSTT(cfg), FakeSTT(), warnings
        )
    if provider in {"qwen3_asr", "parakeet"}:
        warnings.append(f"stt provider '{provider}' is not wired yet; using fake STT")
        return FakeSTT()
    raise ProviderUnavailable(f"unknown stt provider '{provider}'")


def build_tts(cfg: dict[str, object], warnings: list[str]) -> object:
    provider = cfg.get("provider")
    if provider == "fake":
        return FakeTTS(cfg)
    if provider == "kokoro":
        return _with_fallback("tts", provider, lambda: KokoroTTS(cfg), FakeTTS(), warnings)
    if provider == "piper":
        from app.providers.tts.piper import PiperTTS

        return _with_fallback("tts", provider, lambda: PiperTTS(cfg), FakeTTS(), warnings)
    if provider in {"qwen3_tts", "chatterbox"}:
        warnings.append(f"tts provider '{provider}' is not wired yet; using fake TTS")
        return FakeTTS()
    raise ProviderUnavailable(f"unknown tts provider '{provider}'")


def build_providers(cfg: ResolvedConfig) -> Providers:
    warnings: list[str] = []
    llm = build_llm(cfg.llm)
    stt = build_stt(cfg.stt, warnings)
    tts = build_tts(cfg.tts, warnings)
    return Providers(llm=llm, stt=stt, tts=tts, warnings=warnings)  # type: ignore[arg-type]


def _with_fallback(
    kind: str, name: str, factory: object, fallback: object, warnings: list[str]
) -> object:
    try:
        return factory()  # type: ignore[operator]
    except (Exception, SystemExit) as exc:
        # `SystemExit` comes from spaCy's auto-download path inside a Kokoro build;
        # a constructor failure must degrade the provider, never kill the session.
        warnings.append(f"{kind} provider '{name}' unavailable ({exc}); using fake {kind}")
        return fallback


def _looks_like_local_oom(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _LOCAL_OOM_MARKERS)


def _degraded(cfg: ResolvedConfig) -> ResolvedConfig:
    """Same session, same llm, but audio providers that cannot exhaust VRAM."""
    return replace(cfg, stt={"provider": "fake"}, tts={"provider": "fake"})


class ProviderCache:
    """One instance per (profile, provider config).

    Loading a local model takes seconds and VRAM, so sessions that resolve to
    the same configuration must share the instance. `language` is not part of the
    key: every provider receives the language per call, so a profile is built once
    for every language. The lock also prevents two sessions from loading models at
    the same time.
    """

    def __init__(self) -> None:
        self._items: dict[str, Providers] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(cfg: ResolvedConfig) -> str:
        return json.dumps(
            {
                "profile": cfg.profile,
                "llm": cfg.llm,
                "stt": cfg.stt,
                "tts": cfg.tts,
            },
            sort_keys=True,
            default=str,
        )

    async def get(self, cfg: ResolvedConfig) -> Providers:
        key = self._key(cfg)
        async with self._lock:
            if key not in self._items:
                self._items[key] = await self._build(cfg)
            return self._items[key]

    @staticmethod
    async def _build(cfg: ResolvedConfig) -> Providers:
        try:
            return await asyncio.to_thread(build_providers, cfg)
        except (Exception, SystemExit) as exc:
            degraded_reason: str | None = None
            if isinstance(exc, SystemExit):
                degraded_reason = "provider bootstrap aborted (SystemExit)"
            elif _looks_like_local_oom(exc):
                degraded_reason = "local GPU out of memory"
            if degraded_reason is None:
                raise
            logger.warning(
                "%s building '%s' providers (%s); retrying once with fake stt/tts",
                degraded_reason,
                cfg.profile,
                exc,
            )
            degraded = await asyncio.to_thread(build_providers, _degraded(cfg))
            degraded.warnings.append(
                f"{degraded_reason} while loading '{cfg.profile}' providers ({exc}); "
                "stt and tts degraded to fake, the llm is unchanged"
            )
            return degraded

    def clear(self) -> None:
        self._items.clear()
