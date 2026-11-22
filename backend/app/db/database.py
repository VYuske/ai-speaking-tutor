from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlmodel import Field, SQLModel, create_engine, select
from sqlmodel import Session as SQLSession

logger = logging.getLogger(__name__)

MAX_MEMORY_CHARS = 600
MAX_RECENT_CORRECTIONS = 10
MAX_PROFILE_VOCAB = 20
MEMORY_ERROR_TYPES = 4
MEMORY_CORRECTIONS = 5
MEMORY_VOCAB = 10


class SessionRow(SQLModel, table=True):
    __tablename__ = "sessions"

    id: str = Field(primary_key=True)
    language: str = Field(index=True)
    level: int = 3
    scenario_id: str
    profile: str
    status: str = "active"
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None


class TurnRow(SQLModel, table=True):
    __tablename__ = "turns"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    idx: int
    user_text: str
    assistant_text: str
    stt_confidence: float | None = None
    models_json: str = "{}"
    timings_json: str = "{}"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CorrectionRow(SQLModel, table=True):
    __tablename__ = "corrections"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    turn_id: int
    type: str
    original: str
    corrected: str
    explanation_pt: str
    severity: str
    confidence: float = 0.0


class LearnerProfileRow(SQLModel, table=True):
    __tablename__ = "learner_profiles"

    language: str = Field(primary_key=True)
    level_estimate: int = 3
    profile_json: str = "{}"
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{path}", connect_args={"check_same_thread": False}
        )

    def init(self) -> None:
        SQLModel.metadata.create_all(self.engine)

    def create_session(
        self, session_id: str, language: str, level: int, scenario_id: str, profile: str
    ) -> None:
        with SQLSession(self.engine) as db:
            db.add(
                SessionRow(
                    id=session_id,
                    language=language,
                    level=level,
                    scenario_id=scenario_id,
                    profile=profile,
                )
            )
            db.commit()

    def end_session(self, session_id: str) -> None:
        with SQLSession(self.engine) as db:
            row = db.get(SessionRow, session_id)
            if row is None:
                return
            row.status = "finished"
            row.ended_at = datetime.now(UTC)
            db.add(row)
            db.commit()

    def add_turn(
        self,
        session_id: str,
        idx: int,
        user_text: str,
        assistant_text: str,
        stt_confidence: float | None,
        models: dict[str, Any],
        timings: dict[str, Any],
    ) -> int:
        with SQLSession(self.engine) as db:
            row = TurnRow(
                session_id=session_id,
                idx=idx,
                user_text=user_text,
                assistant_text=assistant_text,
                stt_confidence=stt_confidence,
                models_json=json.dumps(models, ensure_ascii=False),
                timings_json=json.dumps(timings),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return int(row.id or 0)

    def add_corrections(self, session_id: str, turn_id: int, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        with SQLSession(self.engine) as db:
            for item in items:
                db.add(
                    CorrectionRow(
                        session_id=session_id,
                        turn_id=turn_id,
                        type=str(item.get("type", "grammar")),
                        original=str(item.get("original", "")),
                        corrected=str(item.get("corrected", "")),
                        explanation_pt=str(item.get("explanation_pt", "")),
                        severity=str(item.get("severity", "low")),
                        confidence=float(item.get("confidence", 0.0)),
                    )
                )
            db.commit()

    def get_learner_memory(self, language: str) -> str:
        """Compact English summary of the learner's history in one language.

        Returns an empty string when the language has no profile yet or when the
        stored JSON cannot be parsed. Never raises: memory is optional.
        """
        try:
            with SQLSession(self.engine) as db:
                row = db.get(LearnerProfileRow, language)
                if row is None:
                    return ""
                raw = row.profile_json
        except Exception:  # noqa: BLE001 - a bad profile must not break a session
            logger.warning("could not read learner profile for %s", language, exc_info=True)
            return ""

        profile = _parse_profile(raw, language)
        if not profile:
            return ""
        return _format_memory(profile)

    def finalize_session(
        self, session_id: str, language: str, level: int, vocab: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Merge one finished session into the persistent profile of `language`.

        `recent_corrections` keeps the newest entries last (oldest dropped first)
        and is deduped by original+corrected; `vocab` keeps plain terms, newest
        last. Returns the merged profile dict.
        """
        with SQLSession(self.engine) as db:
            corrections = db.exec(
                select(CorrectionRow).where(CorrectionRow.session_id == session_id)
            ).all()
            row = db.get(LearnerProfileRow, language)
            profile = _parse_profile(row.profile_json, language) if row is not None else {}

            profile["sessions"] = _as_count(profile.get("sessions")) + 1
            profile["level"] = max(1, min(6, int(level)))

            counts: dict[str, int] = {}
            stored_counts = profile.get("error_counts")
            if isinstance(stored_counts, dict):
                for key, value in stored_counts.items():
                    counts[str(key)] = _as_count(value)
            for item in corrections:
                counts[item.type] = counts.get(item.type, 0) + 1
            profile["error_counts"] = dict(
                sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
            )

            recent = _load_corrections(profile.get("recent_corrections"))
            for item in corrections:
                entry = {"original": item.original, "corrected": item.corrected}
                recent = [old for old in recent if old != entry]
                recent.append(entry)
            profile["recent_corrections"] = recent[-MAX_RECENT_CORRECTIONS:]

            terms = _load_terms(profile.get("vocab"))
            for item in vocab:
                term = str(item.get("term", "")).strip()
                if not term:
                    continue
                lowered = term.lower()
                terms = [old for old in terms if old.lower() != lowered]
                terms.append(term)
            profile["vocab"] = terms[-MAX_PROFILE_VOCAB:]

            now = datetime.now(UTC)
            profile["updated_at"] = now.isoformat()
            payload = json.dumps(profile, ensure_ascii=False, sort_keys=True)

            if row is None:
                row = LearnerProfileRow(
                    language=language,
                    level_estimate=profile["level"],
                    profile_json=payload,
                    updated_at=now,
                )
            else:
                row.level_estimate = profile["level"]
                row.profile_json = payload
                row.updated_at = now
            db.add(row)
            db.commit()

        logger.info("learner profile %s finalized (sessions=%s)", language, profile["sessions"])
        return profile

    def review(self, session_id: str) -> dict[str, Any] | None:
        with SQLSession(self.engine) as db:
            session = db.get(SessionRow, session_id)
            if session is None:
                return None
            turns = db.exec(
                select(TurnRow).where(TurnRow.session_id == session_id).order_by(TurnRow.idx)
            ).all()
            corrections = db.exec(
                select(CorrectionRow).where(CorrectionRow.session_id == session_id)
            ).all()

        by_turn: dict[int, list[dict[str, Any]]] = {}
        for item in corrections:
            by_turn.setdefault(item.turn_id, []).append(
                {
                    "type": item.type,
                    "original": item.original,
                    "corrected": item.corrected,
                    "explanation_pt": item.explanation_pt,
                    "severity": item.severity,
                    "confidence": item.confidence,
                }
            )

        return {
            "session": {
                "id": session.id,
                "language": session.language,
                "level": session.level,
                "scenario_id": session.scenario_id,
                "profile": session.profile,
                "status": session.status,
                "started_at": session.started_at.isoformat(),
                "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            },
            "turns": [
                {
                    "idx": turn.idx,
                    "user_text": turn.user_text,
                    "assistant_text": turn.assistant_text,
                    "timings": _parse_timings(turn.timings_json),
                    "corrections": by_turn.get(turn.id, []),
                }
                for turn in turns
            ],
            "stats": {
                "turns": len(turns),
                "corrections": len(corrections),
                "by_type": _count_by_type(corrections),
            },
        }


def _count_by_type(items: list[CorrectionRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.type] = counts.get(item.type, 0) + 1
    return counts


def _parse_timings(raw: Any) -> dict[str, Any]:
    """Best-effort `timings_json` parse; a corrupt row must not break the review."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("turn holds invalid timings JSON; reporting empty timings")
        return {}
    return data if isinstance(data, dict) else {}


def _parse_profile(raw: str, language: str) -> dict[str, Any]:
    """Best-effort JSON parse; an unreadable profile degrades to an empty dict."""
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        logger.warning("learner profile %s holds invalid JSON; starting from scratch", language)
        return {}
    if not isinstance(data, dict):
        logger.warning("learner profile %s is not a JSON object; starting from scratch", language)
        return {}
    return data


def _as_count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _load_corrections(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, str]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        original = str(entry.get("original", "")).strip()
        corrected = str(entry.get("corrected", "")).strip()
        if original and corrected:
            items.append({"original": original, "corrected": corrected})
    return items


def _load_terms(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(entry).strip() for entry in value if str(entry).strip()]


def _format_memory(profile: dict[str, Any]) -> str:
    """One deterministic English paragraph, capped at MAX_MEMORY_CHARS."""
    sessions = _as_count(profile.get("sessions"))
    level = _as_count(profile.get("level"))

    ranked: list[tuple[str, int]] = []
    stored_counts = profile.get("error_counts")
    if isinstance(stored_counts, dict):
        ranked = sorted(
            (
                (str(key), _as_count(value))
                for key, value in stored_counts.items()
                if _as_count(value) > 0
            ),
            key=lambda pair: (-pair[1], pair[0]),
        )[:MEMORY_ERROR_TYPES]

    corrections = _load_corrections(profile.get("recent_corrections"))
    terms = _load_terms(profile.get("vocab"))

    def render(correction_limit: int, vocab_limit: int) -> str:
        parts: list[str] = []
        if sessions:
            parts.append(f"Previous sessions: {sessions}.")
        if level:
            parts.append(f"Level: {level}.")
        if ranked:
            listed = ", ".join(f"{kind} ({count})" for kind, count in ranked)
            parts.append(f"Recurring errors: {listed}.")
        recent = corrections[-correction_limit:] if correction_limit else []
        if recent:
            quoted = "; ".join(f'"{item["original"]}" → "{item["corrected"]}"' for item in recent)
            parts.append(f"Recent corrections: {quoted}.")
        known = terms[:vocab_limit]
        if known:
            parts.append("Known vocabulary: " + ", ".join(known) + ".")
        return " ".join(parts)

    correction_limit = MEMORY_CORRECTIONS
    vocab_limit = MEMORY_VOCAB
    while True:
        text = render(correction_limit, vocab_limit)
        if len(text) <= MAX_MEMORY_CHARS:
            return text
        if vocab_limit:
            vocab_limit -= 1
        elif correction_limit:
            correction_limit -= 1
        else:
            return text[:MAX_MEMORY_CHARS].rstrip()
