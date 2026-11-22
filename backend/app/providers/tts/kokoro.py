from __future__ import annotations

import asyncio
import threading
from typing import Any

import numpy as np

from app.core.audio import wav_bytes
from app.core.errors import ProviderUnavailable
from app.providers.base import AudioChunk

LANG_CODES = {"en": "a", "ja": "j", "zh": "z"}
DEFAULT_VOICES = {"en": "af_heart", "ja": "jf_alpha", "zh": "zf_xiaobei"}
DEFAULT_CPU_THREADS = 6


def cpu_threads_from_config(cfg: dict[str, Any]) -> int:
    """Intra-op threads for the Kokoro pipelines (AST-043).

    torch opens one thread per core by default (10 here), which oversubscribes the
    CPU next to faster-whisper and the host LLM. 6 measured -32% (ja) and -36%
    (zh) of synthesis time per sentence on this machine.
    """
    raw = cfg.get("cpu_threads", DEFAULT_CPU_THREADS)
    try:
        threads = int(raw)
    except (TypeError, ValueError) as exc:
        raise ProviderUnavailable(
            f"tts.cpu_threads must be a positive integer, got {raw!r}"
        ) from exc
    if threads < 1:
        raise ProviderUnavailable(f"tts.cpu_threads must be a positive integer, got {threads}")
    return threads


def apply_torch_threads(threads: int) -> None:
    """Global in the process: torch has a single intra-op thread pool."""
    try:
        import torch
    except ImportError:  # pragma: no cover - torch ships with the `tts` extra
        return
    torch.set_num_threads(threads)


class KokoroTTS:
    """Kokoro-82M. Excellent CPU latency; no official German voice."""

    name = "kokoro"

    def __init__(self, cfg: dict[str, Any]) -> None:
        try:
            from kokoro import KPipeline  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ProviderUnavailable(
                "kokoro is not installed; build the api image with "
                "AST_INSTALL_EXTRAS='--extra tts'"
            ) from exc

        self.device = str(cfg.get("device", "cpu"))
        self.cpu_threads = cpu_threads_from_config(cfg)
        apply_torch_threads(self.cpu_threads)
        self.voice_by_lang: dict[str, str] = {
            **DEFAULT_VOICES,
            **(cfg.get("voice_by_lang") or {}),
        }
        self._pipelines: dict[str, Any] = {}

    def _pipeline(self, language: str) -> Any:
        from kokoro import KPipeline

        code = LANG_CODES.get(language)
        if code is None:
            raise ProviderUnavailable(
                f"kokoro has no voice configured for '{language}'; "
                "use qwen3_tts or piper for German"
            )
        if code not in self._pipelines:
            self._pipelines[code] = (
                _japanese_pipeline(self.device)
                if code == "j"
                else KPipeline(lang_code=code, device=self.device)
            )
        return self._pipelines[code]

    async def synthesize(
        self, text: str, language: str, voice: str | None = None
    ) -> AudioChunk:
        return await asyncio.to_thread(self._synthesize, text, language, voice)

    def _synthesize(self, text: str, language: str, voice: str | None) -> AudioChunk:
        pipeline = self._pipeline(language)
        selected = voice or self.voice_by_lang.get(language)
        chunks: list[np.ndarray] = []
        for _graphemes, _phonemes, audio in pipeline(text, voice=selected, speed=1.0):
            chunks.append(np.asarray(audio, dtype=np.float32))
        if not chunks:
            raise ProviderUnavailable("kokoro produced no audio")
        samples = np.concatenate(chunks)
        pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
        sample_rate = 24_000
        return AudioChunk(
            wav_bytes=wav_bytes(pcm, sample_rate),
            sample_rate=sample_rate,
            duration_s=samples.size / float(sample_rate),
        )


_JA_PATCH_LOCK = threading.Lock()


def _japanese_pipeline(device: str) -> Any:
    """`KPipeline(lang_code="j")` without misaki's cutlet/unidic data.

    Kokoro hardcodes `misaki.ja.JAG2P()`, whose default `version="cutlet"` needs the
    ~500 MB unidic dictionary, downloaded from a third-party bucket on first use. The
    `pyopenjtalk` variant ships inside the `misaki[ja]` wheel, so the class is swapped
    only while the pipeline constructor runs.
    """
    from kokoro import KPipeline
    from misaki import ja

    original = ja.JAG2P

    def _pyopenjtalk_jag2p(*args: Any, **kwargs: Any) -> Any:
        return original(version="pyopenjtalk")

    with _JA_PATCH_LOCK:
        ja.JAG2P = _pyopenjtalk_jag2p  # type: ignore[assignment]
        try:
            return KPipeline(lang_code="j", device=device)
        finally:
            ja.JAG2P = original  # type: ignore[assignment]
