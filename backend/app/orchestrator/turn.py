from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import time
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, Literal, cast

from app.agent.evaluator import Evaluator
from app.agent.prompts import (
    build_explain_messages,
    build_free_conversation_messages,
    build_roleplay_messages,
)
from app.agent.scenario import FREE_SCENARIO_ID, Scenario
from app.agent.translate import translate_pt
from app.core.config import ResolvedConfig
from app.core.errors import TutorError
from app.core.romanize import reading_for
from app.core.text import sanitize_for_tts
from app.db.database import Database
from app.orchestrator.chunker import SentenceChunker
from app.orchestrator.events import EventSink
from app.orchestrator.state import InvalidTransition, TurnState, advance
from app.providers.base import Providers

logger = logging.getLogger(__name__)

WARMUP_WAIT_S = 30.0
WARMUP_CANCEL_S = 5.0
WARMUP_CLOSE_S = 5.0
AUX_WAIT_S = 10.0
AUX_CANCEL_S = 5.0

TranslationMode = Literal["auto", "manual"]
TRANSLATION_MODES: tuple[str, ...] = ("auto", "manual")


@dataclass
class AidsConfig:
    """Learning aids of one session: deterministic reading + LLM translation."""

    translation: TranslationMode = "auto"
    reading: bool = True

    @classmethod
    def from_payload(cls, raw: object) -> AidsConfig:
        """Build from `session.start.aids`, falling back to the defaults."""
        aids = cls()
        if not isinstance(raw, dict):
            return aids
        mode = raw.get("translation")
        if isinstance(mode, str) and mode in TRANSLATION_MODES:
            aids.translation = cast(TranslationMode, mode)
        reading = raw.get("reading")
        if isinstance(reading, bool):
            aids.reading = reading
        return aids

    def to_public(self) -> dict[str, Any]:
        return {"translation": self.translation, "reading": self.reading}

    def set_translation(self, mode: object) -> bool:
        """Apply a new mode; ``False`` when the value is not a known mode."""
        if not isinstance(mode, str) or mode not in TRANSLATION_MODES:
            return False
        self.translation = cast(TranslationMode, mode)
        return True


class TurnRunner:
    def __init__(
        self,
        *,
        config: ResolvedConfig,
        scenario: Scenario,
        level: int,
        providers: Providers,
        sink: EventSink,
        db: Database,
        session_id: str,
        max_audio_seconds: float = 30.0,
        aids: AidsConfig | None = None,
    ) -> None:
        self.config = config
        self.scenario = scenario
        self.level = level
        self.providers = providers
        self.sink = sink
        self.db = db
        self.session_id = session_id
        self.max_audio_seconds = max_audio_seconds
        self.aids = aids or AidsConfig()
        self.history: list[dict[str, str]] = []
        self.state = TurnState.IDLE
        self.evaluator = Evaluator(providers.llm)
        self.cold_start_ms: int | None = None
        self._warmup_task: asyncio.Task[None] | None = None
        self.memory = self.db.get_learner_memory(self.config.language)
        self.session_vocab: list[dict[str, str]] = []
        self._finalized = False
        self.turn_texts: dict[int, str] = {}
        self.turn_pairs: dict[int, tuple[str, str]] = {}
        self._aux_tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------ warm-up
    def start_warmup(self) -> asyncio.Task[None]:
        """Load the model in the background; returns immediately."""
        if self._warmup_task is None:
            self._warmup_task = asyncio.create_task(
                self._warmup(), name=f"warmup-{self.session_id}"
            )
        return self._warmup_task

    async def _warmup(self) -> None:
        started = time.perf_counter()
        stream: Any = None
        try:
            stream = self.providers.llm.chat_stream(
                [{"role": "user", "content": "ping"}],
                max_tokens=1,
                temperature=0.0,
                language=self.config.language,
            )
            async for _chunk in stream:
                break
        except Exception as exc:  # noqa: BLE001 - warm-up is best-effort
            logger.warning("llm warm-up failed: %s", exc)
            await self.sink.emit("warmup.failed", message=str(exc) or type(exc).__name__)
            return
        finally:
            await _aclose(stream)

        self.cold_start_ms = int((time.perf_counter() - started) * 1000)
        logger.info("llm warm-up done in %s ms", self.cold_start_ms)
        await self.sink.emit("warmup.done", cold_start_ms=self.cold_start_ms)

    async def cancel_warmup(self) -> None:
        """Stop a pending warm-up. Never emits `warmup.done`; safe to call twice."""
        task = self._warmup_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            done, _pending = await asyncio.wait({task}, timeout=WARMUP_CANCEL_S)
        except asyncio.CancelledError:
            logger.info("warm-up cancellation interrupted for %s", self.session_id)
            return
        if task in done:
            with contextlib.suppress(asyncio.CancelledError):
                task.result()
        else:
            logger.warning(
                "warm-up ignored cancellation for %ss (%s)",
                WARMUP_CANCEL_S,
                self.session_id,
            )
        if self._warmup_task is task:
            self._warmup_task = None

    async def _wait_for_warmup(self) -> None:
        """Keep one generation on the GPU at a time, but never hang the turn."""
        task = self._warmup_task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=WARMUP_WAIT_S)
        except TimeoutError:
            logger.warning(
                "warm-up still running after %ss; starting the turn anyway", WARMUP_WAIT_S
            )

    # ------------------------------------------------------------------ events
    async def _set_state(self, target: TurnState) -> None:
        try:
            self.state = advance(self.state, target)
        except InvalidTransition:
            logger.debug("forcing state %s -> %s", self.state, target)
            self.state = target
        await self.sink.emit("state", state=self.state.value)

    async def set_listening(self) -> None:
        await self._set_state(TurnState.LISTENING)

    async def set_idle(self) -> None:
        await self._set_state(TurnState.IDLE)

    def _voice(self) -> str | None:
        voices = self.config.tts.get("voice_by_lang") or {}
        if isinstance(voices, dict):
            return voices.get(self.config.language)
        return None

    async def _emit_tts(self, turn_id: int, seq: int, text: str, source: str) -> bool:
        """Synthesize one chunk. `text` keeps the model's markup; TTS does not.

        The transcript (history, llm.delta, review) keeps whatever the model wrote;
        only the spoken text and the `tts.audio` payload are sanitized (AST-041).
        """
        spoken = sanitize_for_tts(text)
        if not spoken:
            logger.debug("turn %s chunk %s has nothing speakable", turn_id, seq)
            return False
        try:
            chunk = await self.providers.tts.synthesize(
                spoken, self.config.language, self._voice()
            )
        except Exception as exc:  # noqa: BLE001 - audio is best-effort
            logger.warning("tts failed: %s", exc)
            await self.sink.emit(
                "warning",
                code="tts_failed",
                message="Text is available but audio synthesis failed for this sentence.",
            )
            return False
        await self.sink.emit(
            "tts.audio",
            turn_id=turn_id,
            seq=seq,
            text=spoken,
            source=source,
            wav_b64=base64.b64encode(chunk.wav_bytes).decode("ascii"),
            sample_rate=chunk.sample_rate,
            duration_s=round(chunk.duration_s, 3),
        )
        return True

    # -------------------------------------------------------------------- aids
    def _reading_payload(self, text: str) -> dict[str, Any] | None:
        """Romanized reading of `text`, or ``None`` when the aid is off or useless."""
        if not self.aids.reading:
            return None
        try:
            reading = reading_for(text, self.config.language)
        except Exception as exc:  # noqa: BLE001 - a learning aid never breaks a turn
            logger.warning("reading failed (%s): %s", self.config.language, exc)
            return None
        if reading is None:
            return None
        return {
            "kind": reading.kind,
            "text": reading.text,
            "kana": reading.kana,
            "segments": [
                {"text": segment.text, "reading": segment.reading, "romaji": segment.romaji}
                for segment in reading.segments
            ],
        }

    async def _emit_reading(self, turn_id: int, text: str) -> None:
        """Emit the reading aid now; it is a local dictionary lookup, not a model call.

        Every tutor reply is registered in `turn_texts` so a later `translate.request`
        can find it, even when the reading aid itself is disabled.
        """
        if not text.strip():
            return
        self.turn_texts[turn_id] = text
        reading = self._reading_payload(text)
        if reading is not None:
            await self.sink.emit("assistant.aux", turn_id=turn_id, reading=reading)

    def _start_auto_translation(self, turn_id: int, text: str) -> None:
        """Spawn the background translation; `translate_pt` may emit after `turn.end`."""
        if not text.strip() or self.aids.translation != "auto":
            return
        self._spawn(self._translate_auto(turn_id, text), f"translate-{turn_id}")

    def _spawn(self, coro: Coroutine[Any, Any, None], name: str) -> None:
        task = asyncio.create_task(coro, name=f"{name}-{self.session_id}")
        self._aux_tasks.add(task)
        task.add_done_callback(self._aux_tasks.discard)

    async def _translation(self, text: str) -> str | None:
        """`translate_pt` already swallows model errors; never let one escape a task."""
        try:
            return await translate_pt(self.providers.llm, text, self.config.language)
        except Exception as exc:  # noqa: BLE001 - a learning aid never breaks a turn
            logger.warning("translation failed (%s): %s", self.config.language, exc)
            return None

    async def _translate_auto(self, turn_id: int, text: str) -> None:
        translation = await self._translation(text)
        if translation is None:
            # Tell the client so it can offer the retry button instead of waiting.
            await self.sink.emit("assistant.aux", turn_id=turn_id, translation_failed=True)
            return
        await self.sink.emit("assistant.aux", turn_id=turn_id, translation_pt=translation)

    async def _translate_request(self, turn_id: int, text: str) -> None:
        translation = await self._translation(text)
        if translation is None:
            await self.sink.emit(
                "warning",
                code="translation_failed",
                message="Could not translate this turn. Try again in a moment.",
            )
            return
        await self.sink.emit(
            "translation.result", turn_id=turn_id, translation_pt=translation
        )

    async def request_translation(self, turn_id: int) -> None:
        """Answer an on-demand `translate.request` for a turn the tutor already spoke."""
        text = self.turn_texts.get(turn_id)
        if not text:
            await self.sink.emit(
                "error",
                code="nothing_to_translate",
                message=f"Turn {turn_id} has no tutor text to translate.",
                recoverable=True,
            )
            return
        self._spawn(self._translate_request(turn_id, text), f"translate-request-{turn_id}")

    def update_aids(self, translation: object) -> bool:
        """Switch the translation mode for the following turns; ``False`` if invalid."""
        return self.aids.set_translation(translation)

    async def wait_aux(self, timeout: float = AUX_WAIT_S) -> None:
        """Await the in-flight aid tasks; used on shutdown and by the tests."""
        pending = [task for task in self._aux_tasks if not task.done()]
        if pending:
            await asyncio.wait(pending, timeout=timeout)

    async def cancel_aux(self) -> None:
        """Drop pending aid tasks so nothing is emitted after `review`.

        Bounded like the warm-up: a task that ignores cancellation must not delay the
        end of the session.
        """
        pending = [task for task in self._aux_tasks if not task.done()]
        for task in pending:
            task.cancel()
        if not pending:
            return
        _done, still_running = await asyncio.wait(pending, timeout=AUX_CANCEL_S)
        if still_running:
            logger.warning(
                "aid task ignored cancellation for %ss (%s)", AUX_CANCEL_S, self.session_id
            )

    # ----------------------------------------------------------------- opening
    async def speak_opening(self, turn_id: int = 0) -> None:
        """Speak the scenario opening line; a scenario without one stays silent."""
        text = self.scenario.opening_line
        if not text.strip():
            logger.debug("scenario %s has no opening line", self.scenario.id)
            return
        self.history.append({"role": "assistant", "content": text})
        await self.sink.emit(
            "assistant.message", turn_id=turn_id, text=text, opening=True
        )
        await self._emit_reading(turn_id, text)
        await self._set_state(TurnState.SPEAKING)
        await self._emit_tts(turn_id, 0, text, source="opening")
        await self._set_state(TurnState.IDLE)
        self._start_auto_translation(turn_id, text)

    # -------------------------------------------------------------------- turn
    def _conversation_messages(self) -> list[dict[str, str]]:
        """Free mode follows the learner; a real scenario plays the roleplay prompt."""
        builder = (
            build_free_conversation_messages
            if self.scenario.id == FREE_SCENARIO_ID
            else build_roleplay_messages
        )
        return builder(
            self.scenario, self.level, self.config.level_scale, self.history, memory=self.memory
        )

    async def run_turn(
        self,
        turn_id: int,
        *,
        text: str | None = None,
        pcm16: bytes | None = None,
        sample_rate: int = 16_000,
    ) -> None:
        started = time.perf_counter()
        stt_ms = 0
        confidence: float | None = None

        if pcm16 is not None:
            await self._set_state(TurnState.TRANSCRIBING)
            stt_started = time.perf_counter()
            try:
                transcript = await self.providers.stt.transcribe(
                    pcm16, sample_rate, self.config.language
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("stt failed")
                await self._set_state(TurnState.IDLE)
                await self.sink.emit(
                    "error", code="stt_failed", message=str(exc), recoverable=True
                )
                return
            stt_ms = int((time.perf_counter() - stt_started) * 1000)
            user_text = transcript.text.strip()
            confidence = transcript.confidence
            await self.sink.emit(
                "stt.final",
                turn_id=turn_id,
                text=user_text,
                language=transcript.language or self.config.language,
                confidence=confidence,
                source="audio",
                stt_ms=stt_ms,
            )
        else:
            user_text = (text or "").strip()
            await self.sink.emit(
                "stt.final",
                turn_id=turn_id,
                text=user_text,
                language=self.config.language,
                confidence=None,
                source="text",
                stt_ms=0,
            )

        if not user_text:
            await self._set_state(TurnState.IDLE)
            await self.sink.emit(
                "warning",
                code="empty_transcript",
                message="I could not hear anything. Try again a bit closer to the microphone.",
            )
            return

        self.history.append({"role": "user", "content": user_text})
        messages = self._conversation_messages()
        await self._wait_for_warmup()
        await self._set_state(TurnState.THINKING)

        parts: list[str] = []
        first_token_ms: int | None = None
        first_audio_ms: int | None = None
        sentences: asyncio.Queue[str | None] = asyncio.Queue()
        semaphore = asyncio.Semaphore(2)

        async def produce() -> None:
            nonlocal first_token_ms
            chunker = SentenceChunker()
            try:
                async for delta in self.providers.llm.chat_stream(
                    messages, language=self.config.language
                ):
                    if first_token_ms is None:
                        first_token_ms = int((time.perf_counter() - started) * 1000)
                    parts.append(delta)
                    await self.sink.emit("llm.delta", turn_id=turn_id, text=delta)
                    for sentence in chunker.feed(delta):
                        await sentences.put(sentence)
            except Exception as exc:  # noqa: BLE001
                logger.exception("llm stream failed")
                await self.sink.emit(
                    "error", code="llm_failed", message=str(exc), recoverable=True
                )
            finally:
                tail = chunker.flush()
                if tail:
                    await sentences.put(tail)
                await sentences.put(None)

        async def synthesize() -> None:
            nonlocal first_audio_ms
            seq = 0
            while True:
                sentence = await sentences.get()
                if sentence is None:
                    break
                async with semaphore:
                    ok = await self._emit_tts(turn_id, seq, sentence, source="reply")
                if not ok:
                    continue
                if first_audio_ms is None:
                    first_audio_ms = int((time.perf_counter() - started) * 1000)
                    await self._set_state(TurnState.SPEAKING)
                seq += 1

        producer = asyncio.create_task(produce(), name=f"llm-{turn_id}")
        speaker = asyncio.create_task(synthesize(), name=f"tts-{turn_id}")
        await producer

        try:
            assistant_text = "".join(parts).strip()
            if assistant_text:
                await self._emit_reading(turn_id, assistant_text)
            evaluation = await self.evaluator.evaluate(
                self.scenario, self.level, user_text, assistant_text
            )
            self.session_vocab.extend(evaluation.vocab)

            await speaker

            # Only after the reply audio: the extra model call would compete with
            # the remaining TTS chunks. It still never delays `turn.end`.
            if assistant_text:
                self._start_auto_translation(turn_id, assistant_text)

            total_ms = int((time.perf_counter() - started) * 1000)
            timings = {
                "stt_ms": stt_ms,
                "first_token_ms": first_token_ms,
                "first_audio_ms": first_audio_ms,
                "total_ms": total_ms,
                "cold_start_ms": self.cold_start_ms,
            }

            if assistant_text:
                self.turn_pairs[turn_id] = (user_text, assistant_text)
                self.history.append({"role": "assistant", "content": assistant_text})
                persisted = self._persist_turn(
                    turn_id, user_text, assistant_text, confidence, timings, evaluation
                )
                if not persisted:
                    await self.sink.emit(
                        "warning",
                        code="persist_failed",
                        message="This turn could not be saved and is missing from the review.",
                    )
                await self._set_state(TurnState.SPEAKING)
                await self._set_state(TurnState.FEEDBACK)
                await self.sink.emit("feedback.card", turn_id=turn_id, **evaluation.to_public())
            else:
                self._discard_user_message(user_text)
                await self.sink.emit(
                    "error",
                    code="empty_reply",
                    message="The model returned an empty reply. Is Ollama running?",
                    recoverable=True,
                )

            await self._set_state(TurnState.IDLE)
            await self.sink.emit("turn.end", turn_id=turn_id, timings=timings)
        finally:
            # A failure between `await producer` and `await speaker` (evaluator,
            # reading aid, persistence) would otherwise leave the TTS task running.
            if not speaker.done():
                speaker.cancel()
                await asyncio.gather(speaker, return_exceptions=True)

    def _discard_user_message(self, user_text: str) -> None:
        """Drop the learner line of a turn that produced no reply.

        Otherwise the next prompt carries a dangling user message and the model
        answers a question the tutor never answered.
        """
        if self.history and self.history[-1] == {"role": "user", "content": user_text}:
            self.history.pop()

    def _persist_turn(
        self,
        turn_id: int,
        user_text: str,
        assistant_text: str,
        confidence: float | None,
        timings: dict[str, Any],
        evaluation: Any,
    ) -> bool:
        """Store the turn; ``False`` means the review will miss this row."""
        try:
            row_id = self.db.add_turn(
                self.session_id,
                turn_id,
                user_text,
                assistant_text,
                confidence,
                {
                    "llm": self.config.llm.get("model"),
                    "stt": self.providers.stt.name,
                    "tts": self.providers.tts.name,
                    "profile": self.config.profile,
                },
                timings,
            )
            self.db.add_corrections(
                self.session_id, row_id, [item.to_public() for item in evaluation.corrections]
            )
        except Exception:  # noqa: BLE001 - persistence must not kill the turn
            logger.exception("failed to persist turn %s", turn_id)
            return False
        return True

    # ----------------------------------------------------------------- finalize
    def finalize(self) -> None:
        """Merge this session into the learner profile. Safe to call twice."""
        if self._finalized:
            return
        self._finalized = True
        try:
            self.db.finalize_session(
                self.session_id, self.config.language, self.level, self.session_vocab
            )
        except Exception:  # noqa: BLE001 - memory is best-effort, never fail a session
            logger.exception("failed to finalize learner profile for %s", self.session_id)

    # ----------------------------------------------------------------- explain
    async def explain(self, turn_id: int, focus: str = "grammar") -> None:
        pair = self.turn_pairs.get(turn_id)
        if pair is None:
            await self.sink.emit(
                "error",
                code="nothing_to_explain",
                message=f"Turn {turn_id} has no tutor reply to explain.",
                recoverable=True,
            )
            return
        user_text, assistant_text = pair

        try:
            messages = build_explain_messages(
                user_text, assistant_text, self.config.language, focus
            )
        except TutorError as exc:
            logger.warning("invalid explain request: %s", exc)
            await self.sink.emit(
                "error",
                code="invalid_explain_request",
                message=str(exc),
                recoverable=True,
            )
            return
        try:
            data = await self.providers.llm.chat_json(
                messages,
                {
                    "type": "object",
                    "properties": {
                        "explanation_pt": {"type": "string"},
                        "example_target": {"type": "string"},
                        "example_pt": {"type": "string"},
                    },
                    "required": ["explanation_pt", "example_target", "example_pt"],
                },
                language=self.config.language,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("explain failed: %s", exc)
            await self.sink.emit(
                "error", code="explain_failed", message=str(exc), recoverable=True
            )
            return

        explanation = str(data.get("explanation_pt", "")).strip()
        example = str(data.get("example_target", "")).strip()
        example_pt = str(data.get("example_pt", "")).strip()

        wav_b64 = None
        if example:
            try:
                chunk = await self.providers.tts.synthesize(
                    example, self.config.language, self._voice()
                )
                wav_b64 = base64.b64encode(chunk.wav_bytes).decode("ascii")
            except Exception as exc:  # noqa: BLE001
                logger.warning("explain tts failed: %s", exc)

        await self.sink.emit(
            "explain.result",
            turn_id=turn_id,
            explanation_pt=explanation,
            example_target=example,
            example_pt=example_pt,
            wav_b64=wav_b64,
        )


async def _aclose(stream: Any) -> None:
    """Close a partially consumed stream so the HTTP connection is released.

    Bounded: a stream that ignores `aclose()` must not delay the cancellation of
    the warm-up task that is closing it.
    """
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    try:
        await asyncio.wait_for(aclose(), timeout=WARMUP_CLOSE_S)
    except TimeoutError:
        logger.debug("warm-up stream did not close within %ss", WARMUP_CLOSE_S)
    except Exception as exc:  # noqa: BLE001 - closing a warm-up stream is best-effort
        logger.debug("failed to close warm-up stream: %s", exc)

