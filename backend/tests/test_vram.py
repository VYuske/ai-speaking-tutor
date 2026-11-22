from __future__ import annotations

import subprocess
import tomllib
import types
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.providers.registry as registry
from app.core.config import ResolvedConfig, Settings
from app.core.vram import (
    PROFILE_REQUIREMENTS,
    VramProfileError,
    detect_vram,
    load_profile_requirements,
    profile_warning,
)
from app.main import create_app
from app.providers.base import Providers
from app.providers.fakes import FakeLLM, FakeSTT, FakeTTS
from app.providers.registry import ProviderCache

ROOT = Path(__file__).resolve().parents[1]
VRAM_PROFILES_TOML = ROOT / "config" / "vram_profiles.toml"

NVIDIA_SMI_ARGS = [
    "nvidia-smi",
    "--query-gpu=memory.total,memory.free",
    "--format=csv,noheader,nounits",
]


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=NVIDIA_SMI_ARGS, returncode=returncode, stdout=stdout)


def _patch_run(monkeypatch: pytest.MonkeyPatch, run: Any) -> list[tuple[Any, Any]]:
    """Replace subprocess inside app.core.vram only, recording every call."""
    calls: list[tuple[Any, Any]] = []

    def _record(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return run(*args, **kwargs)

    monkeypatch.setattr(
        "app.core.vram.subprocess",
        types.SimpleNamespace(run=_record, TimeoutExpired=subprocess.TimeoutExpired),
    )
    return calls


def test_detect_vram_parses_first_device(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_run(monkeypatch, lambda *a, **k: _completed("8192, 6144\n16384, 15000\n"))

    assert detect_vram() == (8192, 6144)

    assert len(calls) == 1
    args, kwargs = calls[0]
    command = args[0]
    assert command == NVIDIA_SMI_ARGS
    assert isinstance(command, list)
    assert kwargs.get("shell") is not True
    assert kwargs["timeout"] <= 2


def test_detect_vram_returns_none_when_binary_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("nvidia-smi")

    _patch_run(monkeypatch, boom)
    assert detect_vram() is None


def test_detect_vram_returns_none_on_non_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, lambda *a, **k: _completed("", returncode=9))
    assert detect_vram() is None


def test_detect_vram_returns_none_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd=NVIDIA_SMI_ARGS, timeout=2)

    _patch_run(monkeypatch, slow)
    assert detect_vram() is None


def test_detect_vram_returns_none_on_unparsable_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, lambda *a, **k: _completed("[N/A], [N/A]\n"))
    assert detect_vram() is None


def test_profile_warning_balanced_does_not_fit_six_gib() -> None:
    warning = profile_warning("balanced", 6144)
    assert warning is not None
    assert "balanced" in warning
    assert "8192" in warning


def test_profile_warning_compact_fits_six_gib() -> None:
    assert profile_warning("compact", 6144) is None


def test_profile_warning_balanced_fits_eight_gib() -> None:
    assert profile_warning("balanced", 8192) is None


def test_profile_warning_unknown_vram_is_silent() -> None:
    assert profile_warning("balanced", None) is None


def test_profile_warning_ignores_profiles_without_floor() -> None:
    assert profile_warning("fake", 1024) is None


def test_profile_warning_compact_warns_below_its_total() -> None:
    warning = profile_warning("compact", 6143)
    assert warning is not None
    assert "compact" in warning
    assert "6144" in warning


# --- thresholds come from backend/config/vram_profiles.toml (AST-028) -----------------


def test_profile_requirements_equal_the_config_file() -> None:
    parsed = tomllib.loads(VRAM_PROFILES_TOML.read_text(encoding="utf-8"))
    assert parsed == PROFILE_REQUIREMENTS


def test_profile_requirements_values_are_unchanged() -> None:
    assert PROFILE_REQUIREMENTS == {
        "compact": {"total_vram_mib": 6144, "free_vram_mib": 4608},
        "balanced": {"total_vram_mib": 8192, "free_vram_mib": 6144},
    }


def test_profile_warning_follows_the_loaded_thresholds() -> None:
    """Every warning boundary is derived from the file, not from a local copy."""
    assert PROFILE_REQUIREMENTS, "the config file must declare at least one profile"
    for name, need in PROFILE_REQUIREMENTS.items():
        total = need["total_vram_mib"]
        assert profile_warning(name, total) is None
        below = profile_warning(name, total - 1)
        assert below is not None
        assert str(total) in below


def test_load_profile_requirements_reads_an_arbitrary_file(tmp_path: Path) -> None:
    custom = tmp_path / "vram_profiles.toml"
    custom.write_text(
        "[compact]\ntotal_vram_mib = 4096\nfree_vram_mib = 3072\n",
        encoding="utf-8",
    )
    assert load_profile_requirements(custom) == {
        "compact": {"total_vram_mib": 4096, "free_vram_mib": 3072}
    }


def test_load_profile_requirements_rejects_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "vram_profiles.toml"
    with pytest.raises(VramProfileError, match="vram_profiles.toml"):
        load_profile_requirements(missing)


def test_load_profile_requirements_rejects_invalid_toml(tmp_path: Path) -> None:
    broken = tmp_path / "vram_profiles.toml"
    broken.write_text("[compact\ntotal_vram_mib = 6144\n", encoding="utf-8")
    with pytest.raises(VramProfileError, match="vram_profiles.toml"):
        load_profile_requirements(broken)


@pytest.mark.parametrize(
    "body",
    [
        "",
        "[compact]\ntotal_vram_mib = 6144\n",
        "[compact]\ntotal_vram_mib = 6144\nfree_vram_mib = 4608\n[balanced]\n",
        '[compact]\ntotal_vram_mib = "6144"\nfree_vram_mib = 4608\n',
    ],
)
def test_load_profile_requirements_rejects_incomplete_profiles(
    tmp_path: Path, body: str
) -> None:
    incomplete = tmp_path / "vram_profiles.toml"
    incomplete.write_text(body, encoding="utf-8")
    with pytest.raises(VramProfileError):
        load_profile_requirements(incomplete)


def _resolved(profile: str = "balanced") -> ResolvedConfig:
    return ResolvedConfig(
        language="en",
        profile=profile,
        llm={"provider": "fake"},
        stt={"provider": "faster_whisper", "model": "small"},
        tts={"provider": "kokoro", "voice": "af_heart"},
    )


async def test_provider_cache_degrades_to_fake_on_local_oom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[ResolvedConfig] = []
    fakes = Providers(llm=FakeLLM(), stt=FakeSTT(), tts=FakeTTS(), warnings=[])

    def fake_build(cfg: ResolvedConfig) -> Providers:
        calls.append(cfg)
        if len(calls) == 1:
            raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
        return fakes

    monkeypatch.setattr(registry, "build_providers", fake_build)

    cache = ProviderCache()
    cfg = _resolved()

    providers = await cache.get(cfg)

    assert providers is fakes
    assert len(calls) == 2
    assert calls[1].stt == {"provider": "fake"}
    assert calls[1].tts == {"provider": "fake"}
    assert calls[1].llm == cfg.llm
    assert len(providers.warnings) == 1
    warning = providers.warnings[0]
    assert "out of memory" in warning.lower()
    assert "fake" in warning.lower()

    assert await cache.get(cfg) is fakes
    assert len(calls) == 2


async def test_provider_cache_does_not_retry_other_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[ResolvedConfig] = []

    def fake_build(cfg: ResolvedConfig) -> Providers:
        calls.append(cfg)
        raise RuntimeError("model file is missing")

    monkeypatch.setattr(registry, "build_providers", fake_build)

    cache = ProviderCache()
    with pytest.raises(RuntimeError, match="model file is missing"):
        await cache.get(_resolved())

    assert len(calls) == 1


CONFIG_WITH_BALANCED = """
default_profile = "fake"

[llm.fake]
provider = "fake"

[profiles.fake]
llm = "fake"
stt = { provider = "fake" }
tts = { provider = "fake" }

[profiles.balanced]
llm = "fake"
stt = { provider = "fake" }
tts = { provider = "fake" }

[languages.en]
level_scale = "CEFR"
"""


def test_session_ready_reports_vram_and_warns_on_small_gpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "models.toml"
    config_path.write_text(CONFIG_WITH_BALANCED, encoding="utf-8")
    settings = Settings(
        config_path=config_path,
        scenarios_dir=ROOT / "scenarios",
        data_dir=tmp_path,
        profile="fake",
    )
    monkeypatch.setattr("app.api.ws.detect_vram", lambda: (6144, 2048))

    with (
        TestClient(create_app(settings)) as client,
        client.websocket_connect("/ws") as ws,
    ):
        ws.send_json(
            {
                "type": "session.start",
                "language": "en",
                "level": 2,
                "scenario_id": "cafe-order",
                "profile": "balanced",
            }
        )
        events: list[dict[str, Any]] = []
        for _ in range(200):
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "state" and event.get("state") == "idle":
                break

    ready = next(event for event in events if event["type"] == "session.ready")
    assert ready["profile"] == "balanced"
    assert ready["vram"] == {"total_mib": 6144, "free_mib": 2048}

    warnings = [event for event in events if event["type"] == "warning"]
    assert any("balanced" in event.get("message", "") for event in warnings)
