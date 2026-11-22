from __future__ import annotations


class TutorError(Exception):
    """Base error for recoverable tutor failures."""


class ConfigError(TutorError):
    pass


class LLMError(TutorError):
    pass


class ProviderUnavailable(TutorError):
    pass


class InvalidAudio(TutorError):
    pass
