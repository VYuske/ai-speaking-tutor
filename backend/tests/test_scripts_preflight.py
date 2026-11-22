"""Tests for the host-side `scripts/preflight_gpu.py`.

The script is stdlib-only, has no package and targets Python 3.14, while this venv runs
3.12, so it is loaded from its file path. Nothing here talks to nvidia-smi, docker, the
network or the GPU: every probe is a fabricated return value from `run_command`.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import app.core.vram as backend_vram

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
VRAM_PROFILES_TOML = REPO_ROOT / "backend" / "config" / "vram_profiles.toml"

CUSTOM_PROFILES_TOML = (
    "[compact]\ntotal_vram_mib = 4096\nfree_vram_mib = 3072\n"
    "[balanced]\ntotal_vram_mib = 12288\nfree_vram_mib = 10240\n"
)

# `nounits` output: bare numbers, "[N/A]" for values the driver refuses, and a GPU name
# with a comma that nvidia-smi does not quote.
FAKE_NVIDIA_SMI_CSV = (
    "NVIDIA GeForce RTX 3060, Laptop GPU, 6144, 4096, 535.104.05\n"
    "NVIDIA A100-SXM4-40GB, [N/A], [N/A], 535.104.05\n"
    "Quadro P400, 2048, 1900, 535.104.05\n"
)


def load_script(module_name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def preflight() -> ModuleType:
    return load_script("scripts_preflight_gpu_under_test", "preflight_gpu.py")


def device(
    name: str, total: int | None, free: int | None, driver: str | None = "535.104.05"
) -> dict[str, Any]:
    return {"name": name, "vram_total_mib": total, "vram_free_mib": free, "driver": driver}


# --- _first_number -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("6144", 6144),
        ("6144 MiB", 6144),
        ("24564 MiB", 24564),
        ("12.9", 12),
        ("[N/A]", None),
        ("[N/A] MiB", None),
        ("", None),
        ("not supported", None),
    ],
)
def test_first_number(preflight: ModuleType, text: str, expected: int | None) -> None:
    assert preflight._first_number(text) == expected


# --- nvidia-smi CSV parsing ----------------------------------------------------------


def test_detect_gpu_parses_comma_name_na_and_normal_row(
    preflight: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run_command(cmd: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
        calls.append(cmd)
        return 0, FAKE_NVIDIA_SMI_CSV, ""

    monkeypatch.setattr(preflight, "run_command", fake_run_command)

    gpu = preflight.detect_gpu()

    assert calls and calls[0][0] == "nvidia-smi"
    assert gpu.error is None
    assert gpu.present is True
    assert gpu.devices == [
        {
            "name": "NVIDIA GeForce RTX 3060, Laptop GPU",
            "vram_total_mib": 6144,
            "vram_free_mib": 4096,
            "driver": "535.104.05",
        },
        {
            "name": "NVIDIA A100-SXM4-40GB",
            "vram_total_mib": None,
            "vram_free_mib": None,
            "driver": "535.104.05",
        },
        {
            "name": "Quadro P400",
            "vram_total_mib": 2048,
            "vram_free_mib": 1900,
            "driver": "535.104.05",
        },
    ]


def test_detect_gpu_without_nvidia_smi_and_on_driver_failure(
    preflight: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        preflight, "run_command", lambda cmd, timeout=5.0: (127, "", f"{cmd[0]}: not found")
    )
    missing = preflight.detect_gpu()
    assert missing.present is False
    assert missing.error == "nvidia-smi not found"

    monkeypatch.setattr(
        preflight,
        "run_command",
        lambda cmd, timeout=5.0: (9, "", "NVIDIA-SMI has failed because it couldn't\nmore"),
    )
    failed = preflight.detect_gpu()
    assert failed.present is False
    assert failed.error == "NVIDIA-SMI has failed because it couldn't"

    monkeypatch.setattr(preflight, "run_command", lambda cmd, timeout=5.0: (0, "", ""))
    empty = preflight.detect_gpu()
    assert empty.present is False
    assert empty.error == "nvidia-smi reported no GPUs"


# --- evaluate_profiles ---------------------------------------------------------------


def test_evaluate_profiles_single_6gib_card_fits_compact_only(preflight: ModuleType) -> None:
    profiles = preflight.evaluate_profiles(
        preflight.GpuInfo(devices=[device("NVIDIA GeForce RTX 3060", 6144, 6000)])
    )

    assert profiles["recommended"] == "compact"
    assert profiles["compact"] is True
    assert profiles["balanced"] is False
    assert profiles["vram_known"] is True
    assert profiles["device_index"] == 0
    assert profiles["device_name"] == "NVIDIA GeForce RTX 3060"
    assert profiles["vram_total_mib"] == 6144
    assert "compact" in profiles["reason"]
    assert "not balanced" in profiles["reason"]


def test_evaluate_profiles_8gib_card_recommends_balanced(preflight: ModuleType) -> None:
    profiles = preflight.evaluate_profiles(
        preflight.GpuInfo(devices=[device("NVIDIA GeForce RTX 4060", 8192, 7900)])
    )

    assert profiles["recommended"] == "balanced"
    assert profiles["compact"] is True
    assert profiles["balanced"] is True
    assert profiles["headroom"] == []


def test_evaluate_profiles_below_compact_floor_recommends_skeleton(
    preflight: ModuleType,
) -> None:
    profiles = preflight.evaluate_profiles(
        preflight.GpuInfo(devices=[device("Quadro P400", 2048, 1900)])
    )

    assert profiles["recommended"] == "skeleton"
    assert profiles["compact"] is False
    assert profiles["balanced"] is False
    assert "below the compact floor" in profiles["reason"]


def test_evaluate_profiles_unknown_total_is_not_zero(preflight: ModuleType) -> None:
    gpu = preflight.GpuInfo(
        devices=[device("NVIDIA A100-SXM4-40GB", None, None), device("Tesla T4", None, None)]
    )

    profiles = preflight.evaluate_profiles(gpu)

    assert profiles["recommended"] == "skeleton"
    assert profiles["compact"] is False
    assert profiles["balanced"] is False
    assert profiles["vram_known"] is False
    assert profiles["device_index"] is None
    assert "total VRAM is unknown" in profiles["reason"]
    assert "[N/A]" in profiles["reason"]
    assert profiles["devices"][0]["compact"] is None
    assert profiles["devices"][0]["balanced"] is None


def test_evaluate_profiles_without_gpu_recommends_skeleton(preflight: ModuleType) -> None:
    profiles = preflight.evaluate_profiles(preflight.GpuInfo(error="nvidia-smi not found"))

    assert profiles["recommended"] == "skeleton"
    assert profiles["compact"] is False
    assert profiles["balanced"] is False
    assert profiles["reason"] == "no GPU detected"


@pytest.mark.parametrize("large_index", [0, 1])
def test_evaluate_profiles_follows_the_largest_card(
    preflight: ModuleType, large_index: int
) -> None:
    small = device("NVIDIA GeForce RTX 3050", 4096, 3800)
    large = device("NVIDIA GeForce RTX 4070", 8192, 7900)
    devices = [large, small] if large_index == 0 else [small, large]

    profiles = preflight.evaluate_profiles(preflight.GpuInfo(devices=devices))

    assert profiles["recommended"] == "balanced"
    assert profiles["device_index"] == large_index
    assert profiles["device_name"] == "NVIDIA GeForce RTX 4070"
    assert profiles["vram_total_mib"] == 8192
    assert len(profiles["devices"]) == 2
    assert profiles["devices"][large_index]["name"] == "NVIDIA GeForce RTX 4070"
    assert profiles["devices"][1 - large_index]["name"] == "NVIDIA GeForce RTX 3050"


def test_evaluate_profiles_low_free_vram_warns_for_both_profiles(
    preflight: ModuleType,
) -> None:
    profiles = preflight.evaluate_profiles(
        preflight.GpuInfo(devices=[device("NVIDIA GeForce RTX 4070", 8192, 512)])
    )

    headroom = profiles["headroom"]
    assert profiles["recommended"] == "balanced"
    assert len(headroom) == 2
    assert "compact" in headroom[0] and "balanced" in headroom[1]
    assert all("only 512 MiB VRAM is free" in warning for warning in headroom)


def test_evaluate_profiles_free_unknown_reports_no_headroom(preflight: ModuleType) -> None:
    profiles = preflight.evaluate_profiles(
        preflight.GpuInfo(devices=[device("NVIDIA GeForce RTX 4070", 8192, None)])
    )

    assert profiles["recommended"] == "balanced"
    assert profiles["headroom"] == []


# --- single source of the thresholds: backend/config/vram_profiles.toml (AST-028) -----


def test_script_requirements_match_the_backend_and_the_toml(preflight: ModuleType) -> None:
    parsed = tomllib.loads(VRAM_PROFILES_TOML.read_text(encoding="utf-8"))
    assert parsed == preflight.PROFILE_REQUIREMENTS == backend_vram.PROFILE_REQUIREMENTS


def test_both_loaders_reflect_changed_values_in_a_temp_copy(
    preflight: ModuleType, tmp_path: Path
) -> None:
    custom = tmp_path / "vram_profiles.toml"
    custom.write_text(CUSTOM_PROFILES_TOML, encoding="utf-8")

    from_script = preflight.load_profile_requirements(custom)
    from_backend = backend_vram.load_profile_requirements(custom)

    assert from_script == from_backend == tomllib.loads(CUSTOM_PROFILES_TOML)
    assert from_script["compact"]["total_vram_mib"] == 4096
    assert from_script["balanced"]["free_vram_mib"] == 10240


def test_evaluate_profiles_and_render_text_follow_the_loaded_requirements(
    preflight: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both ``compact``/``balanced`` stay in the report; only the MiB numbers move."""
    raised_floors = {
        "compact": {"total_vram_mib": 8192, "free_vram_mib": 6144},
        "balanced": {"total_vram_mib": 16384, "free_vram_mib": 12288},
    }
    monkeypatch.setattr(preflight, "PROFILE_REQUIREMENTS", raised_floors)

    profiles = preflight.evaluate_profiles(
        preflight.GpuInfo(devices=[device("NVIDIA GeForce RTX 3060", 6144, 6000)])
    )

    assert profiles["recommended"] == "skeleton"
    assert profiles["compact"] is False
    assert profiles["balanced"] is False
    assert profiles["requirements"] == raised_floors
    assert "8192 MiB" in profiles["reason"]

    report = preflight.HostReport(
        generated_at="2026-11-18T10:00:00",
        hostname="test-host",
        os_name="Linux 6.8.0",
        python_version="3.14.0",
        cpu_threads=8,
        ram_gib=32.0,
        gpu=preflight.GpuInfo(devices=[device("NVIDIA GeForce RTX 3060", 6144, 6000)]),
        docker=preflight.DockerInfo(),
        profiles=profiles,
        warnings=[],
    )
    text = preflight.render_text(report)
    assert "compact   needs >= 8192 MiB total (~6144 MiB free at load time)" in text
    assert "balanced  needs >= 16384 MiB total (~12288 MiB free at load time)" in text


def test_load_profile_requirements_rejects_missing_and_invalid_files(
    preflight: ModuleType, tmp_path: Path
) -> None:
    with pytest.raises(preflight.ProfileConfigError, match="vram_profiles.toml"):
        preflight.load_profile_requirements(tmp_path / "vram_profiles.toml")

    broken = tmp_path / "vram_profiles.toml"
    broken.write_text("[compact\ntotal_vram_mib = 6144\n", encoding="utf-8")
    with pytest.raises(preflight.ProfileConfigError, match="vram_profiles.toml"):
        preflight.load_profile_requirements(broken)


def test_script_exits_non_zero_with_a_clear_error_when_the_config_is_missing(
    tmp_path: Path,
) -> None:
    """A copy of the script outside the repo resolves no config file: it must refuse."""
    isolated_scripts = tmp_path / "scripts"
    isolated_scripts.mkdir()
    isolated = isolated_scripts / "preflight_gpu.py"
    isolated.write_bytes((SCRIPTS_DIR / "preflight_gpu.py").read_bytes())

    proc = subprocess.run(
        [sys.executable, str(isolated)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert proc.returncode != 0
    assert "vram_profiles.toml" in proc.stderr
    assert "Traceback" not in proc.stderr


# --- end-to-end without a GPU --------------------------------------------------------


def test_main_json_without_gpu_or_docker(
    preflight: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run_command(cmd: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
        return 127, "", f"{cmd[0]}: command not found"

    monkeypatch.setattr(preflight, "run_command", fake_run_command)

    exit_code = preflight.main(["--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["profiles"]["recommended"] == "skeleton"
    assert payload["gpu"]["present"] is False
    assert payload["gpu"]["error"] == "nvidia-smi not found"
    assert payload["docker"]["installed"] is False
    warnings = " ".join(payload["warnings"])
    assert "no GPU detected" in warnings
    assert "docker unavailable" in warnings


def test_main_text_mode_without_gpu(
    preflight: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        preflight, "run_command", lambda cmd, timeout=5.0: (127, "", "command not found")
    )

    exit_code = preflight.main([])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "GPU: no GPU detected (nvidia-smi not found)" in captured.out
    assert "Recommendation: skeleton" in captured.out


def test_render_text_marks_unknown_vram_as_unknown(preflight: ModuleType) -> None:
    gpu = preflight.GpuInfo(
        devices=[device("NVIDIA A100-SXM4-40GB", None, None)],
    )
    report = preflight.HostReport(
        generated_at="2026-11-18T10:00:00",
        hostname="test-host",
        os_name="Linux 6.8.0",
        python_version="3.14.0",
        cpu_threads=8,
        ram_gib=32.0,
        gpu=gpu,
        docker=preflight.DockerInfo(),
        profiles=preflight.evaluate_profiles(gpu),
        warnings=[],
    )

    text = preflight.render_text(report)

    assert "total unknown [N/A]" in text
    assert "compact unknown" in text and "balanced unknown" in text
    assert "Recommendation: skeleton" in text
