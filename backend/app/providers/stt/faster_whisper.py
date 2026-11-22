from __future__ import annotations

import asyncio
import math
from typing import Any

from app.core.audio import TARGET_SAMPLE_RATE, pcm16_to_float32, resample_float32
from app.core.errors import ProviderUnavailable
from app.providers.base import Transcript


def logprob_to_confidence(avg_logprob: float | None) -> float | None:
    """Map faster-whisper's `avg_logprob` (-1..0) to a probability in 0..1.

    The model reports the mean log-probability of the decoded tokens, not a
    confidence: -1 means "no idea" and 0 means "certain". The client renders
    `confidence` as a percentage, so the value is exponentiated and clamped.
    """
    if avg_logprob is None:
        return None
    return min(1.0, max(0.0, math.exp(min(0.0, float(avg_logprob)))))


class FasterWhisperSTT:
    """faster-whisper on CPU or CUDA. Weights load once per provider instance."""

    name = "faster_whisper"

    def __init__(self, cfg: dict[str, Any]) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ProviderUnavailable(
                "faster-whisper is not installed; build the api image with "
                "AST_INSTALL_EXTRAS='--extra stt'"
            ) from exc

        self.model_name = str(cfg.get("model", "large-v3-turbo"))
        self.device = str(cfg.get("device", "cpu"))
        self.compute_type = str(cfg.get("compute_type", "int8"))
        self.language_hint = cfg.get("language_hint")
        try:
            self._model = WhisperModel(
                self.model_name, device=self.device, compute_type=self.compute_type
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the client
            raise ProviderUnavailable(f"failed to load faster-whisper: {exc}") from exc

    async def transcribe(
        self, pcm16: bytes, sample_rate: int, language: str | None = None
    ) -> Transcript:
        return await asyncio.to_thread(self._transcribe, pcm16, sample_rate, language)

    def _transcribe(self, pcm16: bytes, sample_rate: int, language: str | None) -> Transcript:
        audio = pcm16_to_float32(pcm16)
        if sample_rate != TARGET_SAMPLE_RATE:
            audio = resample_float32(audio, sample_rate, TARGET_SAMPLE_RATE)
        segments, info = self._model.transcribe(
            audio,
            language=language or self.language_hint,
            vad_filter=True,
            beam_size=1,
            condition_on_previous_text=False,
        )
        collected = list(segments)
        text = "".join(segment.text for segment in collected).strip()
        avg_logprob = (
            sum(segment.avg_logprob for segment in collected) / len(collected)
            if collected
            else None
        )
        return Transcript(
            text=text,
            language=getattr(info, "language", language),
            confidence=logprob_to_confidence(avg_logprob),
            duration_s=len(audio) / float(TARGET_SAMPLE_RATE),
        )
