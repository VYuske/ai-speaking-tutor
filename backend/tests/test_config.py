from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import AppConfig, ConfigError, Settings

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def make_config() -> AppConfig:
    return AppConfig.load(FIXTURES / "models.toml")


def test_resolve_uses_profile_and_language_overrides() -> None:
    config = make_config()
    resolved = config.resolve("de")
    assert resolved.profile == "fake"
    assert resolved.tts["voice"] == "de-voice"
    assert resolved.level_scale == "CEFR"


def test_explicit_profile_wins() -> None:
    config = make_config()
    resolved = config.resolve("en", "alternate")
    assert resolved.profile == "alternate"
    assert resolved.stt["model"] == "alternate-stt"


def test_env_settings_override_llm_endpoint() -> None:
    config = AppConfig(
        default_profile="fake",
        llm={"fake": {"provider": "fake"}},
        profiles={
            "fake": {
                "llm": "fake",
                "stt": {"provider": "fake"},
                "tts": {"provider": "fake"},
            }
        },
        raw={},
    )
    settings = Settings(
        config_path=FIXTURES / "models.toml",
        ollama_base_url="http://example:1234/v1",
        llm_model="custom:4b",
    )
    resolved = config.resolve("en", settings=settings)
    assert resolved.llm["base_url"] == "http://example:1234/v1"
    assert resolved.llm["model"] == "custom:4b"


def test_unknown_profile_raises() -> None:
    with pytest.raises(ConfigError):
        make_config().resolve("en", "nope")


def test_unknown_language_raises() -> None:
    with pytest.raises(ConfigError):
        make_config().resolve("fr")


def test_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        AppConfig.load(tmp_path / "missing.toml")
