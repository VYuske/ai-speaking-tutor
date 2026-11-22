#!/usr/bin/env python3
"""Host capability preflight: GPU, CPU, RAM, Docker and NVIDIA runtime (AST-001).

Runs on the host (no Docker, stdlib only). Always exits 0 when detection works,
even without a GPU; a missing capability is a report line, not a failure.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

GIB = 1024**3

REPO_ROOT = Path(__file__).resolve().parents[1]
# Single source of truth for the VRAM floors (MiB), shared with backend/app/core/vram.py.
# `backend/config/models.toml` maps providers per profile but carries no MiB numbers.
VRAM_PROFILES_PATH = REPO_ROOT / "backend" / "config" / "vram_profiles.toml"

REQUIRED_PROFILE_KEYS = ("total_vram_mib", "free_vram_mib")

TOOLKIT_BINARIES = ("nvidia-container-toolkit", "nvidia-ctk", "nvidia-container-runtime")

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


class ProfileConfigError(RuntimeError):
    """`backend/config/vram_profiles.toml` is missing or malformed."""


def load_profile_requirements(path: Path = VRAM_PROFILES_PATH) -> dict[str, dict[str, int]]:
    """Read the per-profile MiB thresholds from `path`.

    Same file and same validation as `backend/app/core/vram.py`; the shape mirrors the
    TOML so a changed value shows up in both readers. A missing or malformed file is a
    configuration error, raised instead of guessed.
    """
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileConfigError(f"cannot read VRAM profiles from {path}: {exc}") from exc

    if not raw:
        raise ProfileConfigError(f"{path} declares no VRAM profile")

    requirements: dict[str, dict[str, int]] = {}
    for profile, values in raw.items():
        if not isinstance(values, dict):
            raise ProfileConfigError(f"{path}: profile '{profile}' must be a TOML table")
        profile_requirements: dict[str, int] = {}
        for key in REQUIRED_PROFILE_KEYS:
            value = values.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ProfileConfigError(
                    f"{path}: profile '{profile}' needs a positive integer '{key}'"
                )
            profile_requirements[key] = value
        requirements[profile] = profile_requirements
    return requirements


try:
    PROFILE_REQUIREMENTS: dict[str, dict[str, int]] = load_profile_requirements()
except ProfileConfigError as exc:
    print(f"preflight_gpu: {exc}", file=sys.stderr)
    raise SystemExit(1) from None


def run_command(cmd: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    """Run a command without a shell. Return (returncode, stdout, stderr); 127 when missing."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: command not found"
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, "", f"{cmd[0]}: {exc}"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _first_number(text: str) -> int | None:
    match = _NUMBER.search(text)
    return int(float(match.group(0))) if match else None


@dataclass
class GpuInfo:
    devices: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def present(self) -> bool:
        return bool(self.devices)


@dataclass
class DockerInfo:
    installed: bool = False
    version: str | None = None
    nvidia_runtime: bool = False
    runtime_names: list[str] = field(default_factory=list)
    toolkit_binaries: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class HostReport:
    generated_at: str
    hostname: str
    os_name: str
    python_version: str
    cpu_threads: int | None
    ram_gib: float | None
    gpu: GpuInfo
    docker: DockerInfo
    profiles: dict[str, Any]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "hostname": self.hostname,
            "os": self.os_name,
            "python": self.python_version,
            "cpu_threads": self.cpu_threads,
            "ram_gib": self.ram_gib,
            "gpu": {
                "present": self.gpu.present,
                "devices": self.gpu.devices,
                "error": self.gpu.error,
            },
            "docker": {
                "installed": self.docker.installed,
                "version": self.docker.version,
                "nvidia_runtime": self.docker.nvidia_runtime,
                "runtimes": self.docker.runtime_names,
                "toolkit_binaries": self.docker.toolkit_binaries,
                "error": self.docker.error,
            },
            "profiles": self.profiles,
            "warnings": self.warnings,
        }


def detect_gpu() -> GpuInfo:
    """Parse the first nvidia-smi query; tolerate a host without NVIDIA drivers."""
    code, out, err = run_command(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.free,driver_version",
            "--format=csv,noheader,nounits",
        ]
    )
    if code == 127:
        return GpuInfo(error="nvidia-smi not found")
    if code != 0:
        message = (err or out).strip().splitlines()
        return GpuInfo(error=message[0] if message else f"nvidia-smi exited {code}")

    devices: list[dict[str, Any]] = []
    # `nounits` yields bare numbers and "[N/A]" for values the driver refuses to report;
    # those stay None so they are never confused with 0 MiB. GPU names can contain commas,
    # so the row is parsed with the csv module and any extra leading field (a name the
    # driver did not quote) is folded back into the name.
    for row in csv.reader(io.StringIO(out)):
        parts = [part.strip() for part in row]
        if len(parts) < 4:
            continue
        name = ", ".join(parts[:-3]).strip()
        if not name:
            continue
        devices.append(
            {
                "name": name,
                "vram_total_mib": _first_number(parts[-3]),
                "vram_free_mib": _first_number(parts[-2]),
                "driver": parts[-1] or None,
            }
        )
    if not devices:
        return GpuInfo(error="nvidia-smi reported no GPUs")
    return GpuInfo(devices=devices)


def detect_docker() -> DockerInfo:
    info = DockerInfo()
    code, out, err = run_command(["docker", "--version"])
    if code != 0:
        info.error = (err or out).strip() or "docker --version failed"
        return info
    info.installed = True
    version_match = re.search(r"Docker version ([^,\s]+)", out)
    info.version = version_match.group(1) if version_match else out.strip() or None

    code, out, err = run_command(["docker", "info", "--format", "{{json .Runtimes}}"])
    if code == 0 and out.strip():
        try:
            runtimes = json.loads(out)
        except json.JSONDecodeError:
            warn = f"could not parse docker runtimes: {out.strip()[:80]}"
            info.error = f"{info.error}; {warn}" if info.error else warn
        else:
            if isinstance(runtimes, dict):
                info.runtime_names = sorted(runtimes)
                info.nvidia_runtime = any("nvidia" in name for name in info.runtime_names)
    else:
        message = (err or out).strip().splitlines()
        problem = message[0] if message else "docker info failed"
        info.error = f"{info.error}; {problem}" if info.error else problem

    info.toolkit_binaries = [name for name in TOOLKIT_BINARIES if shutil.which(name)]
    return info


def total_ram_gib() -> float | None:
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / GIB, 1)
    except (AttributeError, ValueError, OSError):
        return None


def _fits(total_mib: int | None, need: float) -> bool | None:
    """True/False for a known total; None when nvidia-smi reported no number ([N/A])."""
    return None if total_mib is None else total_mib >= need


def evaluate_profiles(gpu: GpuInfo) -> dict[str, Any]:
    """Decide which configured profiles fit the detected VRAM, judging every device.

    Every detected device is evaluated and the recommendation follows the card with the
    largest total VRAM: a second GPU is not assumed to host part of the same model.
    Per-profile total/free floors come from `backend/config/vram_profiles.toml`; free VRAM
    is only reported as headroom (for every profile) because a loaded model makes free
    memory look tiny. A `[N/A]` total is *unknown*, never "0 MiB".
    """
    base: dict[str, Any] = {
        "headroom": [],
        "requirements": PROFILE_REQUIREMENTS,
        "vram_known": False,
        "device_index": None,
        "device_name": None,
        "vram_total_mib": None,
        "devices": [],
    }
    if not gpu.present:
        return {
            **base,
            "recommended": "skeleton",
            "compact": False,
            "balanced": False,
            "reason": "no GPU detected",
        }

    per_device: list[dict[str, Any]] = []
    for index, device in enumerate(gpu.devices):
        total = device.get("vram_total_mib")
        per_device.append(
            {
                "index": index,
                "name": device.get("name"),
                "vram_total_mib": total,
                "vram_free_mib": device.get("vram_free_mib"),
                **{
                    name: _fits(total, need["total_vram_mib"])
                    for name, need in PROFILE_REQUIREMENTS.items()
                },
            }
        )

    known = [entry for entry in per_device if entry["vram_total_mib"] is not None]
    if not known:
        return {
            **base,
            "recommended": "skeleton",
            "compact": False,
            "balanced": False,
            "reason": (
                "total VRAM is unknown (nvidia-smi reported [N/A] for every device), so "
                "compact and balanced cannot be sized; stay on skeleton (fake providers) "
                "and re-run this preflight once the driver reports memory"
            ),
            "devices": per_device,
        }

    primary = max(known, key=lambda entry: (entry["vram_total_mib"], -entry["index"]))
    total = primary["vram_total_mib"]
    free = primary["vram_free_mib"]
    where = f"GPU {primary['index']} ({primary['name']})"
    fits = {
        name: total >= need["total_vram_mib"] for name, need in PROFILE_REQUIREMENTS.items()
    }
    if fits["balanced"]:
        recommended = "balanced"
        reason = f"{where} has {total} MiB total VRAM, which covers balanced (audio on GPU)"
    elif fits["compact"]:
        recommended = "compact"
        reason = (
            f"{where} has {total} MiB total VRAM: covers compact (LLM on GPU, audio on CPU) "
            f"but not balanced ({int(PROFILE_REQUIREMENTS['balanced']['total_vram_mib'])} MiB)"
        )
    else:
        recommended = "skeleton"
        reason = (
            f"{where} has {total} MiB total VRAM, below the compact floor "
            f"({int(PROFILE_REQUIREMENTS['compact']['total_vram_mib'])} MiB); "
            "run the LLM on CPU or with a smaller quantization"
        )

    headroom: list[str] = []
    if free is not None:
        for name, need in PROFILE_REQUIREMENTS.items():
            if free < need["free_vram_mib"]:
                headroom.append(
                    f"only {free} MiB VRAM is free on {where} right now; {name} assumes "
                    f"{int(need['free_vram_mib'])} MiB free at load time — another process "
                    "may be holding the GPU"
                )
    return {
        "recommended": recommended,
        "compact": fits["compact"],
        "balanced": fits["balanced"],
        "reason": reason,
        "headroom": headroom,
        "requirements": PROFILE_REQUIREMENTS,
        "vram_known": True,
        "device_index": primary["index"],
        "device_name": primary["name"],
        "vram_total_mib": total,
        "devices": per_device,
    }


def collect() -> HostReport:
    gpu = detect_gpu()
    docker = detect_docker()
    profiles = evaluate_profiles(gpu)
    warnings: list[str] = []

    if not gpu.present:
        warnings.append(f"no GPU detected ({gpu.error or 'unknown reason'})")
    unknown_vram = [
        f"GPU {index} ({device['name']})"
        for index, device in enumerate(gpu.devices)
        if device.get("vram_total_mib") is None
    ]
    if unknown_vram:
        warnings.append(
            "total VRAM unknown (nvidia-smi reported [N/A]) for "
            f"{', '.join(unknown_vram)}; not used to size profiles"
        )
    if not docker.installed:
        warnings.append(f"docker unavailable ({docker.error or 'unknown reason'})")
    elif docker.error:
        warnings.append(f"docker probe incomplete ({docker.error})")
    if docker.installed and not docker.nvidia_runtime:
        warnings.append(
            "NVIDIA Container Toolkit not detected: containers cannot reach the GPU, "
            "so Ollama must run on the host"
        )
    warnings.extend(profiles.get("headroom") or [])

    return HostReport(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        hostname=platform.node() or "unknown",
        os_name=f"{platform.system()} {platform.release()}",
        python_version=platform.python_version(),
        cpu_threads=os.cpu_count(),
        ram_gib=total_ram_gib(),
        gpu=gpu,
        docker=docker,
        profiles=profiles,
        warnings=warnings,
    )


def _fit_verdict(fits: bool | None) -> str:
    if fits is None:
        return "unknown (VRAM not reported)"
    return "FITS" if fits else "does not fit"


def _device_fit_line(entry: dict[str, Any]) -> str:
    total = entry.get("vram_total_mib")
    label = f"{total} MiB total" if total is not None else "total unknown [N/A]"
    verdicts: list[str] = []
    for name in PROFILE_REQUIREMENTS:
        fits = entry.get(name)
        if fits is None:
            verdicts.append(f"{name} unknown")
        else:
            verdicts.append(f"{name} {'fits' if fits else 'no fit'}")
    return f"{label} -> " + ", ".join(verdicts)


def render_text(report: HostReport) -> str:
    lines: list[str] = ["AST-001 preflight — host capability report", "=" * 44]
    if report.gpu.present:
        for index, device in enumerate(report.gpu.devices):
            total = device.get("vram_total_mib")
            free = device.get("vram_free_mib")
            total_text = f"{total} MiB total" if total is not None else "total unknown [N/A]"
            free_text = f"{free} MiB free" if free is not None else "free unknown [N/A]"
            usage = f", {free * 100 // total}% free" if total and free is not None else ""
            lines.append(f"GPU {index}: {device['name']}")
            lines.append(f"  VRAM: {free_text} / {total_text}{usage}")
            lines.append(f"  Driver: {device.get('driver') or 'unknown'}")
    else:
        lines.append(f"GPU: no GPU detected ({report.gpu.error or 'unknown reason'})")

    lines.append(f"CPU: {report.cpu_threads or 'unknown'} threads")
    ram = f"{report.ram_gib} GiB total" if report.ram_gib is not None else "unknown"
    lines.append(f"RAM: {ram}")

    if report.docker.installed:
        lines.append(f"Docker: {report.docker.version or 'present, version unknown'}")
        state = "present" if report.docker.nvidia_runtime else "absent"
        names = ", ".join(report.docker.runtime_names) or "none"
        lines.append(f"NVIDIA runtime: {state} (runtimes: {names})")
        toolkit = ", ".join(report.docker.toolkit_binaries) or "none found in PATH"
        lines.append(f"NVIDIA Container Toolkit: {toolkit}")
    else:
        lines.append(f"Docker: not available ({report.docker.error or 'unknown reason'})")

    lines.append("")
    lines.append("Profile fit (by total VRAM; thresholds are read from the shared config")
    lines.append("backend/config/vram_profiles.toml, also loaded by app/core/vram.py):")
    vram_unknown = report.gpu.present and not report.profiles.get("vram_known")
    for name, need in PROFILE_REQUIREMENTS.items():
        if vram_unknown:
            verdict = "unknown (total VRAM not reported)"
        else:
            verdict = _fit_verdict(report.profiles.get(name))
        lines.append(
            f"  {name:<9} needs >= {int(need['total_vram_mib'])} MiB total "
            f"(~{int(need['free_vram_mib'])} MiB free at load time) -> {verdict}"
        )
    lines.append("  skeleton  always available (no models)")
    devices = report.profiles.get("devices") or []
    if devices:
        lines.append("  Per-device:")
        for entry in devices:
            lines.append(f"    GPU {entry['index']} {entry['name']}: {_device_fit_line(entry)}")
    if len(devices) > 1:
        lines.append(
            "  note: the recommendation follows the largest card; a second GPU is not"
        )
        lines.append("  assumed to host part of the same model")
    lines.append("")
    lines.append(f"Recommendation: {report.profiles['recommended']}")
    lines.append(f"  {report.profiles['reason']}")
    if report.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  - {warning}" for warning in report.warnings)
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json", action="store_true", help="print machine-readable JSON instead of text"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = collect()
    except Exception as exc:  # unexpected: only place a crash is reported, never swallowed
        print(f"preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
