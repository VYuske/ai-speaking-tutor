from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agent.scenario import ScenarioError, free_scenario
from app.core.config import SUPPORTED_LANGUAGES, ConfigError
from app.core.vram import detect_vram, profile_warning
from app.orchestrator.events import EventSink
from app.orchestrator.turn import AidsConfig, TurnRunner

logger = logging.getLogger(__name__)
router = APIRouter()

MIN_AUDIO_BYTES = 16_000 * 2 * 2 // 10  # 0.2s of 16kHz PCM16
SESSION_MODES: tuple[str, ...] = ("scenario", "free")


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    state = websocket.app.state
    await websocket.accept()

    sink = EventSink(websocket.send_json)
    await sink.start()

    runner: TurnRunner | None = None
    buffer = bytearray()
    recording = False
    busy = False
    turn_id = 1
    max_bytes = int(state.settings.max_audio_seconds * 16_000 * 2)

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            chunk = message.get("bytes")
            if chunk is not None:
                if recording and not busy:
                    buffer.extend(chunk)
                    if len(buffer) > max_bytes:
                        del buffer[: len(buffer) - max_bytes]
                continue

            raw = message.get("text")
            if not raw:
                continue
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                await sink.emit(
                    "error", code="bad_json", message="Invalid JSON frame.", recoverable=True
                )
                continue
            if not isinstance(frame, dict):
                # A scalar/array frame would blow up on `.get` and kill the loop.
                await sink.emit(
                    "error",
                    code="bad_json",
                    message="Invalid JSON frame: expected an object.",
                    recoverable=True,
                )
                continue
            payload: dict[str, Any] = frame

            event = payload.get("type")

            if event == "ping":
                await sink.emit("pong")
                continue

            if event == "session.start":
                if runner is not None:
                    await sink.emit(
                        "error",
                        code="already_started",
                        message="A session is already running.",
                        recoverable=True,
                    )
                    continue
                runner = await _start_session(websocket, sink, payload)
                turn_id = 1
                if runner is not None and runner.scenario.opening_line.strip():
                    await runner.speak_opening(0)
                continue

            if runner is None:
                await sink.emit(
                    "error",
                    code="no_session",
                    message="Send session.start first.",
                    recoverable=True,
                )
                continue

            if event == "audio.start":
                recording = True
                buffer.clear()
                await runner.set_listening()
                await sink.emit("state", state="listening")
                continue

            if event == "audio.end":
                recording = False
                if busy:
                    await sink.emit(
                        "warning", code="busy", message="Still finishing the previous turn."
                    )
                    continue
                if len(buffer) < MIN_AUDIO_BYTES:
                    buffer.clear()
                    await runner.set_idle()
                    await sink.emit(
                        "warning",
                        code="audio_too_short",
                        message="Hold the button a little longer and speak.",
                    )
                    continue
                pcm = bytes(buffer)
                buffer.clear()
                busy = True
                try:
                    await runner.run_turn(turn_id, pcm16=pcm, sample_rate=16_000)
                    turn_id += 1
                finally:
                    busy = False
                continue

            if event == "text.input":
                if busy:
                    await sink.emit(
                        "warning", code="busy", message="Still finishing the previous turn."
                    )
                    continue
                busy = True
                try:
                    await runner.run_turn(turn_id, text=str(payload.get("text", "")))
                    turn_id += 1
                finally:
                    busy = False
                continue

            if event == "explain.request":
                await runner.explain(
                    _turn_ref(payload, turn_id),
                    str(payload.get("focus", "grammar")),
                )
                continue

            if event == "translate.request":
                await runner.request_translation(_turn_ref(payload, turn_id))
                continue

            if event == "aids.update":
                await _update_aids(runner, payload, sink)
                continue

            if event == "session.end":
                await runner.cancel_warmup()
                await runner.cancel_aux()
                runner.finalize()
                state.db.end_session(runner.session_id)
                review = state.db.review(runner.session_id)
                await sink.emit("review", review=review)
                runner = None
                continue

            await sink.emit(
                "error",
                code="unknown_event",
                message=f"Unknown event '{event}'.",
                recoverable=True,
            )

    except WebSocketDisconnect:
        logger.info("client disconnected")
    except Exception:  # noqa: BLE001 - never leak a stack trace to the socket
        logger.exception("websocket crashed")
    finally:
        # Covers every exit without `session.end` (disconnect, crash, plain exit).
        # `runner` is None once `session.end` finalized the profile, and
        # `TurnRunner.finalize()` is idempotent, so nothing is counted twice.
        if runner is not None:
            try:
                await runner.cancel_warmup()
            except Exception:  # noqa: BLE001 - the exit path must not mask the original error
                logger.exception("failed to cancel warm-up for %s", runner.session_id)
            try:
                await runner.cancel_aux()
            except Exception:  # noqa: BLE001 - the exit path must not mask the original error
                logger.exception("failed to cancel aids for %s", runner.session_id)
            try:
                runner.finalize()
            except Exception:  # noqa: BLE001 - the exit path must not mask the original error
                logger.exception("failed to finalize learner profile for %s", runner.session_id)
            try:
                # A closed tab never sends `session.end`; without this the session
                # stays 'active' in the review. Idempotent: it only rewrites the row.
                state.db.end_session(runner.session_id)
            except Exception:  # noqa: BLE001 - the exit path must not mask the original error
                logger.exception("failed to mark session %s as finished", runner.session_id)
        await sink.close()


async def _start_session(
    websocket: WebSocket, sink: EventSink, payload: dict[str, Any]
) -> TurnRunner | None:
    state = websocket.app.state
    language = str(payload.get("language", "en"))
    if language not in SUPPORTED_LANGUAGES:
        await sink.emit(
            "error",
            code="invalid_language",
            message=f"Unsupported language '{language}'.",
            recoverable=False,
        )
        return None

    mode = payload.get("mode", "scenario")
    if not isinstance(mode, str) or mode not in SESSION_MODES:
        allowed = ", ".join(f"'{item}'" for item in SESSION_MODES)
        await sink.emit(
            "error",
            code="invalid_mode",
            message=f"Unknown mode {mode!r}; expected one of: {allowed}.",
            recoverable=False,
        )
        return None

    try:
        level = int(payload.get("level", 3))
    except (TypeError, ValueError):
        level = 3
    level = max(1, min(6, level))

    scenario_id = str(payload.get("scenario_id", ""))
    try:
        resolved = state.config.resolve(
            language, payload.get("profile"), state.settings
        )
        # Free mode has no scenario file: `scenario_id` is ignored on purpose.
        scenario = free_scenario(language) if mode == "free" else state.scenarios.get(scenario_id)
    except (ConfigError, ScenarioError) as exc:
        await sink.emit(
            "error", code="config_error", message=str(exc), recoverable=False
        )
        return None

    if scenario.language != language:
        await sink.emit(
            "error",
            code="scenario_mismatch",
            message=f"Scenario '{scenario.id}' is for '{scenario.language}'.",
            recoverable=False,
        )
        return None

    # Probing the GPU shells out to nvidia-smi, so it runs off the event loop and a
    # missing binary only means "unknown VRAM" — never a failed session.
    vram = await asyncio.to_thread(detect_vram)
    vram_total = vram[0] if vram is not None else None
    vram_free = vram[1] if vram is not None else None

    try:
        providers = await state.cache.get(resolved)
    except Exception as exc:  # noqa: BLE001
        logger.exception("failed to build providers")
        await sink.emit(
            "error", code="provider_init_failed", message=str(exc), recoverable=False
        )
        return None

    session_id = uuid.uuid4().hex
    state.db.create_session(session_id, language, level, scenario.id, resolved.profile)
    aids = AidsConfig.from_payload(payload.get("aids"))

    runner = TurnRunner(
        config=resolved,
        scenario=scenario,
        level=level,
        providers=providers,
        sink=sink,
        db=state.db,
        session_id=session_id,
        max_audio_seconds=state.settings.max_audio_seconds,
        aids=aids,
    )

    warning = profile_warning(resolved.profile, vram_total)
    if warning is not None:
        await sink.emit("warning", code="vram_low", message=warning)

    await sink.emit(
        "session.ready",
        session_id=session_id,
        language=language,
        level=level,
        mode=mode,
        opening=bool(scenario.opening_line.strip()),
        profile=resolved.profile,
        level_scale=resolved.level_scale,
        scenario=scenario.to_public(),
        aids=aids.to_public(),
        vram={"total_mib": vram_total, "free_mib": vram_free},
        models={
            "llm": resolved.llm.get("model") or resolved.llm.get("provider"),
            "stt": providers.stt.name,
            "tts": providers.tts.name,
        },
        warnings=providers.warnings,
        warmup="pending",
    )
    runner.start_warmup()
    return runner


def _turn_ref(payload: dict[str, Any], turn_id: int) -> int:
    """`turn_id` of the request, defaulting to the turn that just finished."""
    try:
        return int(payload.get("turn_id", turn_id - 1))
    except (TypeError, ValueError):
        return turn_id - 1


async def _update_aids(runner: TurnRunner, payload: dict[str, Any], sink: EventSink) -> None:
    """Switch Auto/Desligada for the following turns; a missing key is a no-op."""
    mode = payload.get("translation")
    if mode is None:
        return
    if not runner.update_aids(mode):
        await sink.emit(
            "warning",
            code="invalid_aids",
            message=f"Unknown translation mode {mode!r}; expected 'auto' or 'manual'.",
        )

