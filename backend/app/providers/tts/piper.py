from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

from app.core.audio import pcm16_duration_s, wav_bytes
from app.core.errors import ProviderUnavailable
from app.providers.base import AudioChunk

logger = logging.getLogger(__name__)

MODEL_DIR = Path("/app/voices")
DEFAULT_VOICES = {"de": "de_DE-thorsten-medium"}
_DOWNLOAD_HINT = "python -m piper.download_voices --download-dir {dir} {voice}"


class PiperTTS:
    """Piper (VITS on onnxruntime). CPU-only, useful where Kokoro has no voice.

    The voice files are baked into the image by `docker/api.Dockerfile`; the ONNX
    session is only created on the first sentence so a session that never speaks
    German does not pay for it.
    """

    name = "piper"

    def __init__(self, cfg: dict[str, Any]) -> None:
        try:
            from piper import PiperVoice  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ProviderUnavailable(
                "piper-tts is not installed; build the api image with "
                "AST_INSTALL_EXTRAS='--extra tts'"
            ) from exc

        self.model_dir = Path(str(cfg.get("model_dir") or MODEL_DIR))
        self.voice_by_lang: dict[str, str] = {
            **DEFAULT_VOICES,
            **(cfg.get("voice_by_lang") or {}),
        }
        self.default_voice = str(cfg.get("voice") or "") or None
        self._voices: dict[str, Any] = {}
        self._lock = threading.Lock()
        # Fail here (not at the first sentence) so the registry can fall back to fake.
        self._verify_present(self._declared_voices())

    def _declared_voices(self) -> list[str]:
        """The voice(s) this instance promises to speak.

        `[languages.<code>]` sets `voice`; a language-agnostic config only has the
        per-language map. When both exist, `voice` wins: a merged profile may still
        carry another provider's `voice_by_lang` ids.
        """
        if self.default_voice:
            return [self.default_voice]
        return sorted(set(self.voice_by_lang.values()))

    def _voice_name(self, language: str, voice: str | None) -> str:
        name = voice or self.voice_by_lang.get(language) or self.default_voice
        if not name:
            raise ProviderUnavailable(
                f"piper has no voice configured for '{language}'; "
                "set tts.voice in config/models.toml"
            )
        return name

    def _verify_present(self, names: list[str]) -> None:
        missing = [
            f"{name}{suffix}"
            for name in names
            for suffix in (".onnx", ".onnx.json")
            if not (self.model_dir / f"{name}{suffix}").is_file()
        ]
        if missing:
            raise ProviderUnavailable(
                f"piper voice files are missing in {self.model_dir}: "
                f"{', '.join(missing)}; rebuild the api image with "
                "AST_INSTALL_EXTRAS='--extra stt --extra tts' (docker/api.Dockerfile "
                "downloads the voices) or run "
                f"'{_DOWNLOAD_HINT.format(dir=self.model_dir, voice=names[0])}'"
            )

    def _voice(self, name: str) -> Any:
        if name in self._voices:
            return self._voices[name]
        with self._lock:
            if name in self._voices:
                return self._voices[name]
            self._verify_present([name])
            from piper import PiperVoice

            voice = PiperVoice.load(
                str(self.model_dir / f"{name}.onnx"),
                config_path=str(self.model_dir / f"{name}.onnx.json"),
                use_cuda=False,
            )
            self._voices[name] = voice
            logger.info("piper voice '%s' loaded from %s", name, self.model_dir)
            return voice

    async def synthesize(
        self, text: str, language: str, voice: str | None = None
    ) -> AudioChunk:
        return await asyncio.to_thread(self._synthesize, text, language, voice)

    def _synthesize(self, text: str, language: str, voice: str | None) -> AudioChunk:
        selected = self._voice_name(language, voice)
        piper_voice = self._voice(selected)
        pcm = bytearray()
        sample_rate = 0
        for chunk in piper_voice.synthesize(text):
            pcm.extend(chunk.audio_int16_bytes)
            sample_rate = int(chunk.sample_rate or sample_rate)
        if not pcm:
            raise ProviderUnavailable(
                f"piper voice '{selected}' produced no audio for '{language}'"
            )
        if sample_rate <= 0:
            raise ProviderUnavailable(
                f"piper voice '{selected}' did not report a sample rate"
            )
        samples = bytes(pcm)
        return AudioChunk(
            wav_bytes=wav_bytes(samples, sample_rate),
            sample_rate=sample_rate,
            duration_s=pcm16_duration_s(samples, sample_rate),
        )
