#!/usr/bin/env python3
"""AST-004 smoke test: real STT/TTS audio through the WebSocket contract.

Runs one push-to-talk turn per language against a live API (normally inside the
`api` container, with the image built using `--extra stt --extra tts`):

1. open `ws://127.0.0.1:8000/ws` and send `session.start` (level 2, one scenario);
2. require `session.ready.models.stt == "faster_whisper"`, `models.tts == "kokoro"`
   for en/ja/zh and `models.tts == "piper"` for de — a fallback to the fakes fails
   the run;
3. synthesize a short spoken WAV with `espeak-ng`, resample it to 16 kHz PCM16
   and send it as binary frames between `audio.start` and `audio.end`;
4. wait for `stt.final`, `tts.audio` (reply), `feedback.card` and `turn.end`;
5. print a per-language latency table. `tts1_ms` splits `audio_ms` at the LLM's
   first token (`audio_ms - ttok_ms`): it is the wait for the first speakable
   chunk plus its synthesis, and `tts1_s` is the audio length of that chunk, so a
   long first chunk can be told apart from a slow one. Since AST-041 the first
   chunk of a reply is a clause or a capped CJK fragment, not the whole sentence.

Every language needs real TTS: Kokoro for en/ja/zh, Piper for de (Kokoro has no
official German voice).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_URL = "ws://127.0.0.1:8000/ws"
_CHUNK_BYTES = 3_200  # 100 ms of 16 kHz PCM16


def _bootstrap_app() -> None:
    """Import `app.*` both from /app/scripts (container) and scripts/ (repo)."""
    here = Path(__file__).resolve()
    for candidate in (here.parents[1] / "backend", here.parents[1]):
        if (candidate / "app" / "core" / "audio.py").is_file():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return
    raise SystemExit("cannot locate the 'app' package next to scripts/audio_smoke.py")


_bootstrap_app()

from app.core.audio import (  # noqa: E402
    TARGET_SAMPLE_RATE,
    pcm16_duration_s,
    read_wav_pcm16,
    resample_pcm16,
)

try:
    import websockets
except ImportError as exc:  # pragma: no cover - the API image ships uvicorn[standard]
    raise SystemExit(f"'websockets' is required to run this smoke test: {exc}") from exc


class SmokeError(RuntimeError):
    """A turn or a session did not behave as the contract requires."""


@dataclass(frozen=True)
class Case:
    language: str
    scenario: str
    espeak_voice: str
    sentence: str
    tts_provider: str  # required real TTS: AST-004 (kokoro) / AST-005 (piper)


CASES: tuple[Case, ...] = (
    Case("en", "cafe-order", "en-us", "I would like a large coffee, please.", "kokoro"),
    Case("ja", "konbini-checkout", "ja", "これはコーヒーです。", "kokoro"),
    Case("zh", "restaurant-table", "cmn", "我要一杯咖啡。", "kokoro"),
    Case("de", "bahn-ticket", "de", "Ich möchte bitte eine Fahrkarte nach Berlin.", "piper"),
)


@dataclass
class TurnResult:
    language: str
    profile: str = ""
    stt_model: str = ""
    tts_model: str = ""
    stt_ms: int | None = None
    first_token_ms: int | None = None
    first_audio_ms: int | None = None
    first_tts_ms: int | None = None
    first_tts_s: float | None = None
    first_tts_text: str = ""
    total_ms: int | None = None
    transcript: str = ""
    tts_chunks: int = 0
    feedback: bool = False
    audio_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def synth_speech_wav(voice: str, sentence: str, path: Path) -> None:
    """Render `sentence` with espeak-ng. No network, no model weights."""
    binary = shutil.which("espeak-ng")
    if binary is None:
        raise SmokeError(
            "espeak-ng not found; the api image must install it (see docker/api.Dockerfile)"
        )
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [binary, "-v", voice, "-w", str(path), sentence],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise SmokeError(
            f"espeak-ng -v {voice} failed ({completed.returncode}): "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    if not path.is_file() or path.stat().st_size == 0:
        raise SmokeError(f"espeak-ng produced no WAV for voice '{voice}'")


def wav_to_pcm16k(path: Path) -> bytes:
    pcm, rate = read_wav_pcm16(path.read_bytes())
    return resample_pcm16(pcm, rate, TARGET_SAMPLE_RATE)


async def await_ready(ws: Any, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SmokeError(
                f"session.ready did not arrive in {timeout_s:.0f}s "
                "(first run downloads the Whisper/Kokoro weights)"
            )
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        event = json.loads(raw)
        kind = event.get("type")
        if kind == "session.ready":
            return event
        if kind == "error":
            raise SmokeError(
                f"session.start rejected: {event.get('code')} — {event.get('message')}"
            )


async def collect_turn(ws: Any, turn_id: int, timeout_s: float) -> TurnResult:
    result = TurnResult(language="")
    deadline = time.monotonic() + timeout_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SmokeError(f"turn {turn_id} did not finish in {timeout_s:.0f}s")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        event = json.loads(raw)
        kind = event.get("type")
        if kind == "stt.final" and event.get("turn_id") == turn_id:
            result.transcript = str(event.get("text", ""))
            result.stt_ms = event.get("stt_ms")
            if event.get("source") != "audio":
                result.failures.append("stt.final did not come from the audio path")
        elif kind == "tts.audio" and event.get("turn_id") == turn_id:
            if event.get("source") == "reply":
                if result.tts_chunks == 0:
                    duration = event.get("duration_s")
                    result.first_tts_s = float(duration) if duration is not None else None
                    result.first_tts_text = str(event.get("text", ""))
                result.tts_chunks += 1
        elif kind == "feedback.card" and event.get("turn_id") == turn_id:
            result.feedback = True
        elif kind == "warning":
            result.warnings.append(f"{event.get('code')}: {event.get('message')}")
        elif kind == "error":
            code = event.get("code")
            message = event.get("message")
            if event.get("recoverable"):
                result.warnings.append(f"{code}: {message}")
                continue
            raise SmokeError(f"turn {turn_id} failed: {code} — {message}")
        elif kind == "turn.end" and event.get("turn_id") == turn_id:
            timings = event.get("timings") or {}
            result.stt_ms = timings.get("stt_ms", result.stt_ms)
            result.first_token_ms = timings.get("first_token_ms")
            result.first_audio_ms = timings.get("first_audio_ms")
            result.total_ms = timings.get("total_ms")
            if result.first_audio_ms is not None and result.first_token_ms is not None:
                result.first_tts_ms = result.first_audio_ms - result.first_token_ms
            return result


def check(case: Case, ready: dict[str, Any], turn: TurnResult) -> None:
    models = ready.get("models") or {}
    turn.profile = str(ready.get("profile", ""))
    turn.stt_model = str(models.get("stt", ""))
    turn.tts_model = str(models.get("tts", ""))
    turn.warnings = list(ready.get("warnings") or []) + turn.warnings

    if turn.stt_model != "faster_whisper":
        turn.failures.append(f"stt provider is '{turn.stt_model}', expected 'faster_whisper'")
    if turn.tts_model != case.tts_provider:
        turn.failures.append(
            f"tts provider is '{turn.tts_model}', expected '{case.tts_provider}'"
        )
    if turn.tts_chunks < 1:
        turn.failures.append("no tts.audio chunk arrived for the reply")
    if not turn.transcript.strip():
        turn.failures.append("empty STT transcript")
    if not turn.feedback:
        turn.failures.append("no feedback.card for the turn")


async def run_case(case: Case, url: str, ready_timeout: float, turn_timeout: float) -> TurnResult:
    with tempfile.TemporaryDirectory(prefix="audio-smoke-") as tmp:
        wav_path = Path(tmp) / f"{case.language}.wav"
        synth_speech_wav(case.espeak_voice, case.sentence, wav_path)
        pcm = wav_to_pcm16k(wav_path)

    async with websockets.connect(url, open_timeout=30, max_size=None) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "session.start",
                    "language": case.language,
                    "level": 2,
                    "scenario_id": case.scenario,
                }
            )
        )
        ready = await await_ready(ws, ready_timeout)
        await ws.send(json.dumps({"type": "audio.start"}))
        for offset in range(0, len(pcm), _CHUNK_BYTES):
            await ws.send(pcm[offset : offset + _CHUNK_BYTES])
        await ws.send(json.dumps({"type": "audio.end"}))
        turn = await collect_turn(ws, 1, turn_timeout)
        turn.language = case.language
        turn.audio_s = pcm16_duration_s(pcm, TARGET_SAMPLE_RATE)
        check(case, ready, turn)
        await ws.send(json.dumps({"type": "session.end"}))
    return turn


def _cell(value: int | None) -> str:
    return "-" if value is None else str(value)


def _seconds_cell(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def print_table(results: list[TurnResult]) -> None:
    header = (
        f"{'lang':<5}{'stt':<16}{'tts':<8}"
        f"{'stt_ms':>7}{'ttok_ms':>9}{'tts1_ms':>9}{'tts1_s':>8}"
        f"{'audio_ms':>10}{'total_ms':>9}"
    )
    print(header)
    print("-" * len(header))
    for item in results:
        print(
            f"{item.language:<5}{item.stt_model:<16}{item.tts_model:<8}"
            f"{_cell(item.stt_ms):>7}{_cell(item.first_token_ms):>9}"
            f"{_cell(item.first_tts_ms):>9}{_seconds_cell(item.first_tts_s):>8}"
            f"{_cell(item.first_audio_ms):>10}{_cell(item.total_ms):>9}"
        )
    print()
    for item in results:
        print(f"[{item.language}] {item.transcript!r} ({item.tts_chunks} tts chunk(s), "
              f"{item.audio_s:.2f}s of input audio)")
        if item.first_tts_text:
            print(
                f"[{item.language}] first reply chunk "
                f"({_seconds_cell(item.first_tts_s)}s of audio): {item.first_tts_text!r}"
            )
        for warning in item.warnings:
            print(f"[{item.language}] warning: {warning}")
        for failure in item.failures:
            print(f"[{item.language}] FAIL: {failure}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AST-004 real STT/TTS audio smoke test")
    parser.add_argument(
        "--url", default=_DEFAULT_URL, help=f"API WebSocket (default {_DEFAULT_URL})"
    )
    parser.add_argument(
        "--languages",
        default=",".join(case.language for case in CASES),
        help="comma-separated subset of en,ja,zh,de",
    )
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=900.0,
        help="seconds to wait for session.ready (first run downloads model weights)",
    )
    parser.add_argument(
        "--turn-timeout", type=float, default=180.0, help="seconds to wait for turn.end"
    )
    return parser.parse_args(argv)


def select_cases(requested: str) -> list[Case]:
    by_language = {case.language: case for case in CASES}
    selected: list[Case] = []
    for raw in requested.split(","):
        language = raw.strip().lower()
        if not language:
            continue
        if language not in by_language:
            raise SystemExit(
                f"unknown language '{raw.strip()}'; expected one of {', '.join(by_language)}"
            )
        selected.append(by_language[language])
    if not selected:
        raise SystemExit("no language selected")
    return selected


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cases = select_cases(args.languages)
    results: list[TurnResult] = []
    errors: list[str] = []

    print(f"audio smoke → {args.url} ({', '.join(c.language for c in cases)})")
    for case in cases:
        started = time.monotonic()
        try:
            result = asyncio.run(
                run_case(case, args.url, args.ready_timeout, args.turn_timeout)
            )
        except (SmokeError, OSError, TimeoutError) as exc:
            errors.append(f"[{case.language}] {exc}")
            print(f"[{case.language}] ERROR: {exc}")
            continue
        results.append(result)
        elapsed = time.monotonic() - started
        status = "ok" if result.ok else "FAIL"
        print(f"[{case.language}] {status} in {elapsed:.1f}s")

    if results:
        print()
        print_table(results)

    failed = [item for item in results if not item.ok]
    if errors or failed:
        print()
        print(
            f"FAILED: {len(failed)} language(s) with contract violations, "
            f"{len(errors)} error(s)"
        )
        return 1
    print()
    print("OK: real STT and real TTS (kokoro en/ja/zh, piper de) for every language")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
