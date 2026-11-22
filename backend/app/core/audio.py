from __future__ import annotations

import io
import wave

import numpy as np

TARGET_SAMPLE_RATE = 16_000


def pcm16_to_float32(data: bytes) -> np.ndarray:
    if not data:
        return np.zeros(0, dtype=np.float32)
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    return samples / 32768.0


def float32_to_pcm16(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16).tobytes()


def resample_float32(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate or samples.size == 0:
        return samples
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("sample rates must be positive")
    duration = samples.size / float(src_rate)
    target_size = max(1, int(round(duration * dst_rate)))
    src_idx = np.linspace(0.0, samples.size - 1, num=target_size, dtype=np.float64)
    lower = np.floor(src_idx).astype(np.int64)
    upper = np.minimum(lower + 1, samples.size - 1)
    weight = (src_idx - lower).astype(np.float32)
    return (samples[lower] * (1.0 - weight) + samples[upper] * weight).astype(np.float32)


def resample_pcm16(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    return float32_to_pcm16(resample_float32(pcm16_to_float32(data), src_rate, dst_rate))


def pcm16_duration_s(data: bytes, sample_rate: int) -> float:
    if sample_rate <= 0:
        return 0.0
    return (len(data) / 2) / float(sample_rate)


def wav_bytes(pcm16: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm16)
    return buffer.getvalue()


def read_wav_pcm16(data: bytes) -> tuple[bytes, int]:
    with wave.open(io.BytesIO(data), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise ValueError("expected mono 16-bit WAV")
        return wav.readframes(wav.getnframes()), wav.getframerate()
