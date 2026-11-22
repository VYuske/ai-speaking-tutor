from __future__ import annotations

import numpy as np
import pytest

from app.core.audio import (
    float32_to_pcm16,
    pcm16_duration_s,
    pcm16_to_float32,
    read_wav_pcm16,
    resample_float32,
    wav_bytes,
)


def test_pcm_roundtrip() -> None:
    samples = np.array([0.0, 0.5, -0.5, 1.0], dtype=np.float32)
    recovered = pcm16_to_float32(float32_to_pcm16(samples))
    assert np.allclose(recovered, samples, atol=1 / 32768)


def test_resample_changes_length_and_keeps_duration() -> None:
    src = np.sin(np.linspace(0, 100, 48_000)).astype(np.float32)
    out = resample_float32(src, 48_000, 16_000)
    assert out.size == pytest.approx(16_000, abs=2)
    assert pcm16_duration_s(float32_to_pcm16(out), 16_000) == pytest.approx(1.0, abs=0.01)


def test_resample_noop_on_same_rate() -> None:
    src = np.ones(100, dtype=np.float32)
    assert resample_float32(src, 16_000, 16_000) is src


def test_wav_header_roundtrip() -> None:
    pcm = float32_to_pcm16(np.zeros(1600, dtype=np.float32))
    data = wav_bytes(pcm, 16_000)
    recovered, rate = read_wav_pcm16(data)
    assert rate == 16_000
    assert recovered == pcm
