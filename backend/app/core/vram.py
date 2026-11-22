"""VRAM awareness for session start (AST-010).

`nvidia-smi` is optional: inside the container it is usually absent, and a missing
binary must stay silent instead of failing a session. The MiB thresholds live in
`backend/config/vram_profiles.toml`, the single source also read by
`scripts/preflight_gpu.py`; here they only drive a warning and never change the
profile automatically.
"""

from __future__ import annotations

import csv
import io
import logging
import subprocess
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
VRAM_PROFILES_PATH = BACKEND_ROOT / "config" / "vram_profiles.toml"

REQUIRED_PROFILE_KEYS = ("total_vram_mib", "free_vram_mib")


class VramProfileError(RuntimeError):
    """`config/vram_profiles.toml` is missing or malformed: a build error, not a fallback."""


def load_profile_requirements(path: Path = VRAM_PROFILES_PATH) -> dict[str, dict[str, int]]:
    """Read the per-profile MiB thresholds from `path`.

    The same file is loaded by `scripts/preflight_gpu.py`, so the numbers are never
    duplicated in code. A missing or malformed file raises instead of guessing.
    """
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise VramProfileError(f"cannot read VRAM profiles from {path}: {exc}") from exc

    if not raw:
        raise VramProfileError(f"{path} declares no VRAM profile")

    requirements: dict[str, dict[str, int]] = {}
    for profile, values in raw.items():
        if not isinstance(values, dict):
            raise VramProfileError(f"{path}: profile '{profile}' must be a TOML table")
        profile_requirements: dict[str, int] = {}
        for key in REQUIRED_PROFILE_KEYS:
            value = values.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise VramProfileError(
                    f"{path}: profile '{profile}' needs a positive integer '{key}'"
                )
            profile_requirements[key] = value
        requirements[profile] = profile_requirements
    return requirements


# Total VRAM each profile assumes (MiB), read from config/vram_profiles.toml.
PROFILE_REQUIREMENTS: dict[str, dict[str, int]] = load_profile_requirements()

NVIDIA_SMI_TIMEOUT_S = 2.0
NVIDIA_SMI_CMD: list[str] = [
    "nvidia-smi",
    "--query-gpu=memory.total,memory.free",
    "--format=csv,noheader,nounits",
]


def detect_vram() -> tuple[int, int] | None:
    """Return the first device's (total_mib, free_mib), or None when unknown.

    Any failure (missing binary, non-zero exit, timeout, unparsable output) is
    normal and returns None without logging noise.
    """
    try:
        proc = subprocess.run(
            NVIDIA_SMI_CMD,
            capture_output=True,
            text=True,
            timeout=NVIDIA_SMI_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("nvidia-smi unavailable: %s", exc)
        return None

    if proc.returncode != 0:
        logger.debug("nvidia-smi exited %s", proc.returncode)
        return None

    for row in csv.reader(io.StringIO(proc.stdout or "")):
        parts = [part.strip() for part in row]
        if not any(parts):
            continue
        if len(parts) < 2:
            return None
        try:
            total_mib, free_mib = int(parts[0]), int(parts[1])
        except ValueError:
            return None
        return total_mib, free_mib
    return None


def profile_warning(profile: str, total_mib: int | None) -> str | None:
    """Warn when `profile` needs more total VRAM than the machine has.

    Pure: None when the profile fits, when it has no floor (skeleton/fake) or when
    VRAM is unknown. The profile itself is never rewritten here.
    """
    if total_mib is None:
        return None
    requirement = PROFILE_REQUIREMENTS.get(profile)
    if requirement is None:
        return None
    floor = requirement["total_vram_mib"]
    if total_mib >= floor:
        return None
    return (
        f"profile '{profile}' assumes at least {floor} MiB of GPU memory but this machine "
        f"reports {total_mib} MiB total; model loading may run out of memory (stt/tts fall "
        "back to fake on a local OOM, the llm stays as configured)"
    )
