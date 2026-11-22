from __future__ import annotations

import copy
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "ja", "zh", "de")
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "ja": "Japanese",
    "zh": "Mandarin Chinese",
    "de": "German",
}


class ConfigError(RuntimeError):
    """Raised when the model configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    config_path: Path = Path("config/models.toml")
    scenarios_dir: Path = Path("scenarios")
    data_dir: Path = Path("data")
    profile: str | None = None
    ollama_base_url: str | None = None
    llm_model: str | None = None
    log_level: str = "INFO"
    max_audio_seconds: float = 30.0
    cors_origins: tuple[str, ...] = (
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        origins = env.get("AST_CORS_ORIGINS")
        return cls(
            config_path=Path(env.get("AST_CONFIG", "config/models.toml")),
            scenarios_dir=Path(env.get("AST_SCENARIOS", "scenarios")),
            data_dir=Path(env.get("AST_DATA_DIR", "data")),
            profile=env.get("AST_PROFILE") or None,
            ollama_base_url=env.get("AST_OLLAMA_BASE_URL") or None,
            llm_model=env.get("AST_LLM_MODEL") or None,
            log_level=env.get("AST_LOG_LEVEL", "INFO"),
            max_audio_seconds=float(env.get("AST_MAX_AUDIO_SECONDS", "30")),
            cors_origins=(
                tuple(o.strip() for o in origins.split(",") if o.strip())
                if origins
                else cls.cors_origins
            ),
        )


@dataclass(frozen=True)
class ResolvedConfig:
    language: str
    profile: str
    llm: dict[str, Any]
    stt: dict[str, Any]
    tts: dict[str, Any]
    level_scale: str = "CEFR"


@dataclass
class AppConfig:
    default_profile: str
    llm: dict[str, dict[str, Any]] = field(default_factory=dict)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    languages: dict[str, dict[str, Any]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> AppConfig:
        try:
            with path.open("rb") as fh:
                raw = tomllib.load(fh)
        except FileNotFoundError as exc:
            raise ConfigError(f"config file not found: {path}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

        default_profile = raw.get("default_profile")
        if not isinstance(default_profile, str):
            raise ConfigError("config must define a string 'default_profile'")

        profiles = raw.get("profiles", {})
        if not isinstance(profiles, dict) or not profiles:
            raise ConfigError("config must define at least one [profiles.<name>]")

        return cls(
            default_profile=default_profile,
            llm=raw.get("llm", {}),
            profiles=profiles,
            languages=raw.get("languages", {}),
            raw=raw,
        )

    def profile_names(self) -> list[str]:
        return sorted(self.profiles)

    def resolve(
        self,
        language: str,
        profile: str | None = None,
        settings: Settings | None = None,
    ) -> ResolvedConfig:
        if language not in SUPPORTED_LANGUAGES:
            allowed = ", ".join(SUPPORTED_LANGUAGES)
            raise ConfigError(f"unsupported language '{language}'; expected one of {allowed}")

        lang_cfg = self.languages.get(language, {})
        requested = (
            profile
            or (settings.profile if settings else None)
            or lang_cfg.get("profile")
            or self.default_profile
        )
        if requested not in self.profiles:
            raise ConfigError(
                f"unknown profile '{requested}'; available: {', '.join(self.profile_names())}"
            )

        base = copy.deepcopy(self.profiles[requested])
        overrides = {k: v for k, v in lang_cfg.items() if k not in {"profile", "level_scale"}}
        merged = _deep_merge(base, overrides)

        llm = merged.get("llm", "default")
        if isinstance(llm, str):
            if llm not in self.llm:
                raise ConfigError(f"profile '{requested}' references unknown llm '{llm}'")
            llm_cfg = copy.deepcopy(self.llm[llm])
        elif isinstance(llm, dict):
            llm_cfg = copy.deepcopy(llm)
        else:
            raise ConfigError(f"profile '{requested}' has an invalid 'llm' value")

        if settings and settings.ollama_base_url:
            llm_cfg["base_url"] = settings.ollama_base_url
        if settings and settings.llm_model:
            llm_cfg["model"] = settings.llm_model

        stt = merged.get("stt", {})
        tts = merged.get("tts", {})
        if not isinstance(stt, dict) or not isinstance(tts, dict):
            raise ConfigError(f"profile '{requested}' must define stt and tts objects")

        _validate_provider("llm", llm_cfg)
        _validate_provider("stt", stt)
        _validate_provider("tts", tts)

        return ResolvedConfig(
            language=language,
            profile=requested,
            llm=llm_cfg,
            stt=stt,
            tts=tts,
            level_scale=lang_cfg.get("level_scale", "CEFR"),
        )


def _validate_provider(kind: str, cfg: dict[str, Any]) -> None:
    if not cfg.get("provider"):
        raise ConfigError(f"{kind} config must define 'provider'")
    if kind == "llm" and cfg["provider"] == "openai_compat":
        for key in ("base_url", "model"):
            if not cfg.get(key):
                raise ConfigError(f"llm provider 'openai_compat' requires '{key}'")
    requires_model = {"faster_whisper", "qwen3_asr", "parakeet", "qwen3_tts", "chatterbox"}
    if kind in {"stt", "tts"} and cfg["provider"] in requires_model and not cfg.get("model"):
        raise ConfigError(f"{kind} provider '{cfg['provider']}' requires 'model'")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(copy.deepcopy(base[key]), value)
        else:
            base[key] = copy.deepcopy(value)
    return base
