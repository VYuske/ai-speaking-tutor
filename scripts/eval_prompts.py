#!/usr/bin/env python3
"""Prompt/eval harness (AST-008b): one scripted conversation per language, quality report.

Run it with the backend environment, because it imports the real app modules:

    cd backend && uv run python ../scripts/eval_prompts.py --dry-run --force --out-dir /tmp/ast-eval

Per language the harness loads a real scenario from `ScenarioRepository`, replays the fixed
two-turn learner script written for that scenario through `build_roleplay_messages` +
`OpenAICompatLLM.chat_stream`, then asks for corrections with `build_evaluation_messages` +
`chat_json` and normalises the answer with `normalize(..., language=scenario.language)`, the
same policy the product applies (AST-035: German capitalisation survives the report). Metrics:
language adherence, empty replies, valid JSON evaluations, corrections per turn and p50 latency.

Before the measured turns, each language sends one short in-character warm-up call whose
reply and latency are thrown away, so a cold model load (Ollama pays ~4.3 s on the first
call) never enters the p50.

The report goes to `<out-dir>/eval-prompts-<YYYY-MM-DD>.md` and is never overwritten
without `--force`. `--dry-run` uses a canned provider (no network) whose replies are
written per scenario, like the learner script, and writes nothing except when `--force` is
also given **and** `--out-dir` is a temporary directory.

Adherence, the median helper and text sanitising are reused from `scripts/bench_llm.py`,
loaded by file path with `importlib`, so the two scripts cannot drift on the heuristics.
Standard library plus backend dependencies only; nothing here is built from user input.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import importlib.util
import logging
import re
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, TextIO

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
BACKEND_DIR = REPO_ROOT / "backend"
SCENARIOS_DIR = BACKEND_DIR / "scenarios"
CONFIG_PATH = BACKEND_DIR / "config" / "models.toml"

# The script lives beside the app, not inside it: make `import app.*` work when the
# caller forgot to run from `backend/`.
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agent.evaluator import EVALUATION_SCHEMA, normalize  # noqa: E402
from app.agent.prompts import build_evaluation_messages, build_roleplay_messages  # noqa: E402
from app.agent.scenario import Scenario, ScenarioRepository  # noqa: E402
from app.core.config import SUPPORTED_LANGUAGES, AppConfig, ConfigError, Settings  # noqa: E402
from app.providers.llm.openai_compat import OpenAICompatLLM  # noqa: E402

logger = logging.getLogger("eval_prompts")

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "qwen3.5:4b"
DEFAULT_OUT_DIR = "docs/spikes"
DEFAULT_LANGUAGES: tuple[str, ...] = ("en", "ja", "zh", "de")
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_LEVEL = 3
REPORT_PREFIX = "eval-prompts"
SCRIPTED_TURNS_PER_RUN = 2

# One scenario per language, all in the 1..4 level band so DEFAULT_LEVEL fits.
SCENARIO_BY_LANGUAGE: dict[str, str] = {
    "en": "cafe-order",
    "ja": "konbini-checkout",
    "zh": "restaurant-table",
    "de": "bahn-ticket",
}

# The learner's fixed script, keyed by scenario id: two turns that fit the role and reuse
# the scenario's own vocabulary. A script that fights the scenario (asking for a coffee
# inside `bahn-ticket`) makes the model refuse and turns the turn into metric noise.
SCRIPTED_TURNS: dict[str, tuple[str, ...]] = {
    "cafe-order": (
        "Hi, could I get a latte to go please",
        "Do you have oat milk, and can I have the receipt",
    ),
    "konbini-checkout": (
        "すみません、このお弁当を温めてください。",
        "袋はいりません。カードで払います。",
    ),
    "restaurant-table": (
        "两位，请给我菜单。",
        "我要一个不辣的菜，然后买单。",
    ),
    "bahn-ticket": (
        "Guten Morgen, ich brauche eine Fahrkarte nach Hamburg.",
        "Hin- und Rückfahrt bitte. Von welchem Gleis fährt der Zug?",
    ),
}

# One short in-character line per language, sent as the discarded warm-up. It only has to
# be plausible inside any role: the reply is never read.
WARMUP_LINES: dict[str, str] = {
    "en": "Hi!",
    "ja": "こんにちは。",
    "zh": "你好。",
    "de": "Guten Tag!",
}

# Canned provider used by --dry-run: same shape as the real one, no network, and keyed by
# scenario id like `SCRIPTED_TURNS`, so the dry run answers inside the same role the real
# run does instead of offering cake to a learner buying a train ticket.
CANNED_REPLIES: dict[str, tuple[str, ...]] = {
    "cafe-order": (
        "Of course! Would you like that small or large?",
        "Yes, we have oat milk for sixty pence. Your receipt is in the bag, anything else?",
    ),
    "konbini-checkout": (
        "お弁当ですね。お温めしますので、少々お待ちください。",
        "袋は必要ですか。お支払いは現金かカードが使えます。",
    ),
    "restaurant-table": (
        "两位这边请。这是菜单，慢慢看。",
        "好的，这个菜不辣。您要现在买单吗？",
    ),
    "bahn-ticket": (
        "Nach Hamburg? Möchten Sie einfach oder Hin- und Rückfahrt?",
        "Von Gleis sieben, und die Fahrkarte kostet neunzehn Euro.",
    ),
}

_CANNED_LEARNER = re.compile(r"Learner said:\s*(.+?)(?:\n|$)", re.S)
_CANNED_QUOTES = ("'", '"')
_CANNED_SUFFIX = {
    "en": " (more natural)",
    "ja": "（より自然）",
    "zh": "（更自然）",
    "de": " (natürlicher)",
}


def _unquote_learner_line(raw: str) -> str:
    """Unquote a line the prompt wrapped in matching quotes; keep raw text as-is (AST-034).

    Mirrors ``app.providers.fakes._unquote_learner_line``; the script and the app cannot
    import each other. ``literal_eval`` would turn a learner turn such as ``5`` or ``[1]``
    into a non-string and drop the quote.
    """
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in _CANNED_QUOTES:
        try:
            value = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return raw
        return value if isinstance(value, str) else raw
    return raw


def _canned_learner_quote(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        match = _CANNED_LEARNER.search(message.get("content", ""))
        if not match:
            continue
        value = _unquote_learner_line(match.group(1).strip())
        if value.strip():
            return value.strip()
    return ""


CANNED_CORRECTIONS: dict[str, tuple[str, str, str]] = {
    "en": (
        "could I get a flat white to go please",
        "Could I get a flat white to go, please?",
        "Maiúscula inicial e vírgula antes de 'please'; em inglês, pedido curto pede vírgula.",
    ),
    "ja": (
        "ホットコーヒーを一つください",
        "ホットコーヒーを一つお願いします",
        "'お願いします' soa mais natural que 'ください' ao pedir em uma loja.",
    ),
    "zh": (
        "我要一杯拿铁",
        "我要一杯拿铁，谢谢",
        "Acrescentar '谢谢' deixa o pedido mais educado em chinês.",
    ),
    "de": (
        "ich möchte bitte einen Kaffee",
        "Ich möchte bitte einen Kaffee.",
        "Início de frase e substantivos vão em maiúscula em alemão.",
    ),
}


def _load_sibling(filename: str) -> ModuleType:
    """Load a sibling script by path (they are not importable packages)."""
    path = SCRIPTS_DIR / filename
    name = f"eval_prompts_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - only if the file is gone
        raise ImportError(f"cannot load helper script {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BENCH = _load_sibling("bench_llm.py")


# --- pure helpers ---------------------------------------------------------------------


def check_adherence(language: str, text: str) -> tuple[bool, str]:
    """Heuristic script check, shared with `bench_llm.py`.

    `ja` needs kana, `zh` needs Han characters and no kana, `de` needs ä/ö/ü/ß or two
    German stopwords, `en` needs ≥90% ASCII and no CJK.
    """
    return BENCH.check_adherence(language, text)


def p50_latency(values: Iterable[float]) -> float | None:
    """p50 over the successful turns; `statistics.median` averages the middle pair."""
    return BENCH.median([float(value) for value in values])


def scripted_turns(scenario_id: str) -> tuple[str, ...]:
    """Learner lines written for the scenario, so the script never fights the role."""
    try:
        return SCRIPTED_TURNS[scenario_id]
    except KeyError:
        known = ", ".join(sorted(SCRIPTED_TURNS))
        raise LookupError(
            f"no scripted learner turns for scenario '{scenario_id}' (known: {known})"
        ) from None


def warmup_line(language: str) -> str:
    return WARMUP_LINES.get(language, WARMUP_LINES["en"])


def canned_replies(scenario_id: str) -> tuple[str, ...]:
    """Canned tutor lines written for the scenario, so `--dry-run` answers in role."""
    try:
        return CANNED_REPLIES[scenario_id]
    except KeyError:
        known = ", ".join(sorted(CANNED_REPLIES))
        raise LookupError(
            f"no canned replies for scenario '{scenario_id}' (known: {known})"
        ) from None


def is_warmup(messages: Sequence[dict[str, str]]) -> bool:
    """True when the last learner line is the short warm-up prompt."""
    last = next(
        (item["content"] for item in reversed(messages) if item["role"] == "user"), ""
    )
    return last in set(WARMUP_LINES.values())


def describe_failure(exc: BaseException) -> str:
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


async def collect_stream(stream: AsyncIterator[str]) -> str:
    """Drain one streamed reply, closing the stream even when streaming fails."""
    pieces: list[str] = []
    try:
        async for chunk in stream:
            if chunk:
                pieces.append(str(chunk))
    finally:
        closer = getattr(stream, "aclose", None)
        if closer is not None:
            try:
                await closer()
            except Exception:  # noqa: BLE001 - never mask the real streaming error
                logger.debug("failed to close the reply stream", exc_info=True)
    return "".join(pieces)


# --- results and metrics --------------------------------------------------------------


@dataclass(slots=True)
class TurnResult:
    language: str
    run: int
    turn: int
    user_text: str
    reply: str = ""
    latency_ms: float | None = None
    adherence_ok: bool = False
    adherence_reason: str = ""
    evaluated: bool = False
    json_valid: bool = False
    corrections: int = 0
    vocab: int = 0
    error: str | None = None
    eval_error: str | None = None


@dataclass(slots=True)
class RunResult:
    language: str
    run: int
    scenario_id: str = ""
    turns: list[TurnResult] = field(default_factory=list)
    error: str | None = None
    warmup_ms: float | None = None
    warmup_error: str | None = None


@dataclass(slots=True)
class LanguageMetrics:
    language: str
    scenario_id: str = ""
    runs: int = 0
    turns: int = 0
    adherence_passed: int = 0
    empty_replies: int = 0
    evals: int = 0
    json_valid: int = 0
    corrections: int = 0
    p50_latency_ms: float | None = None
    failed_runs: int = 0
    latencies_ms: tuple[float, ...] = ()


@dataclass(slots=True)
class Totals:
    turns: int = 0
    adherence_passed: int = 0
    empty_replies: int = 0
    evals: int = 0
    json_valid: int = 0
    corrections: int = 0
    p50_latency_ms: float | None = None
    languages_failed: int = 0


@dataclass(frozen=True, slots=True)
class ReportMeta:
    base_url: str
    model: str
    languages: tuple[str, ...]
    runs: int
    timeout_s: float
    dry_run: bool
    level: int
    json_mode: str
    config_label: str


def aggregate(runs: Sequence[RunResult], languages: Sequence[str]) -> list[LanguageMetrics]:
    """Fold the per-turn observations into one row of metrics per language."""
    metrics: list[LanguageMetrics] = []
    for language in languages:
        language_runs = [run for run in runs if run.language == language]
        turns = [turn for run in language_runs for turn in run.turns]
        latencies = tuple(
            turn.latency_ms
            for turn in turns
            if turn.latency_ms is not None and turn.error is None
        )
        metrics.append(
            LanguageMetrics(
                language=language,
                scenario_id=next(
                    (
                        run.scenario_id
                        for run in language_runs
                        if run.scenario_id
                    ),
                    SCENARIO_BY_LANGUAGE.get(language, ""),
                ),
                runs=len(language_runs),
                turns=len(turns),
                adherence_passed=sum(1 for turn in turns if turn.adherence_ok),
                empty_replies=sum(
                    1 for turn in turns if turn.error is None and not turn.reply.strip()
                ),
                evals=sum(1 for turn in turns if turn.evaluated),
                json_valid=sum(1 for turn in turns if turn.json_valid),
                corrections=sum(turn.corrections for turn in turns),
                p50_latency_ms=p50_latency(latencies),
                failed_runs=sum(1 for run in language_runs if run.error),
                latencies_ms=latencies,
            )
        )
    return metrics


def summarize(metrics: Sequence[LanguageMetrics]) -> Totals:
    """Pool every language into the totals block; p50 is taken over all turns."""
    latencies = [value for metric in metrics for value in metric.latencies_ms]
    return Totals(
        turns=sum(metric.turns for metric in metrics),
        adherence_passed=sum(metric.adherence_passed for metric in metrics),
        empty_replies=sum(metric.empty_replies for metric in metrics),
        evals=sum(metric.evals for metric in metrics),
        json_valid=sum(metric.json_valid for metric in metrics),
        corrections=sum(metric.corrections for metric in metrics),
        p50_latency_ms=p50_latency(latencies),
        languages_failed=sum(1 for metric in metrics if metric.failed_runs),
    )


# --- report --------------------------------------------------------------------------


def fmt_ms(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f}"


def fmt_ratio(part: int, whole: int) -> str:
    return f"{part}/{whole}"


def fmt_percent(part: int, whole: int) -> str:
    return "—" if whole == 0 else f"{part}/{whole} ({part / whole:.0%})"


def fmt_rate(corrections: int, turns: int) -> str:
    return "—" if turns == 0 else f"{corrections / turns:.2f}"


def _cell(text: str) -> str:
    """Escape a markdown table cell: pipes would break the row."""
    return " ".join(str(text).split()).replace("|", "\\|")


def render_report(
    *,
    meta: ReportMeta,
    runs: Sequence[RunResult],
    metrics: Sequence[LanguageMetrics],
    totals: Totals,
    notes: Sequence[str],
    report_path: Path,
    report_date: str,
) -> str:
    mode = "dry run (canned provider, no network)" if meta.dry_run else "real endpoint"
    lines: list[str] = [
        f"# Prompt evaluation — {report_date}",
        "",
        "Generated by `scripts/eval_prompts.py` (AST-008b). Each language replays a fixed "
        f"{SCRIPTED_TURNS_PER_RUN}-turn learner script written for its own scenario, through "
        "the real prompt builders and the real evaluation schema.",
        "",
        "## Environment",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Endpoint | `{meta.base_url}` |",
        f"| Mode | {mode} |",
        f"| Model | `{meta.model}` |",
        f"| Level | {meta.level}/6 |",
        f"| Level scale | per language (`CEFR`/`JLPT`/`HSK`) from {meta.config_label} |",
        f"| Languages | {', '.join(meta.languages)} |",
        f"| Runs per language | {meta.runs} |",
        f"| Scripted turns per run | {SCRIPTED_TURNS_PER_RUN} |",
        "| Warm-up | 1 discarded roleplay call per language, not measured |",
        f"| Per-turn timeout | {meta.timeout_s:g} s |",
        f"| JSON mode | `{meta.json_mode}` |",
        "| API key | redacted (read from config, never printed) |",
        f"| Report written to | `{report_path}` |",
        "",
        "## Per-language results",
        "",
        "| Language | Scenario | Runs | Turns | Adherence | Empty replies | JSON valid "
        "| Corrections | Corrections/turn | p50 latency (ms) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for metric in metrics:
        lines.append(
            f"| {metric.language} | {metric.scenario_id} | {metric.runs} | {metric.turns} "
            f"| {fmt_ratio(metric.adherence_passed, metric.turns)} | {metric.empty_replies} "
            f"| {fmt_ratio(metric.json_valid, metric.evals)} | {metric.corrections} "
            f"| {fmt_rate(metric.corrections, metric.turns)} "
            f"| {fmt_ms(metric.p50_latency_ms)} |"
        )

    lines.extend(
        [
            "",
            "## Totals",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Turns | {totals.turns} |",
            f"| Adherence | {fmt_percent(totals.adherence_passed, totals.turns)} |",
            f"| Empty replies | {totals.empty_replies} |",
            f"| JSON valid evaluations | {fmt_percent(totals.json_valid, totals.evals)} |",
            f"| Corrections | {totals.corrections} "
            f"({fmt_rate(totals.corrections, totals.turns)} per turn) |",
            f"| p50 latency (ms) | {fmt_ms(totals.p50_latency_ms)} |",
            f"| Languages with a failed run | {totals.languages_failed} |",
            "",
            "## Per-turn detail",
            "",
            "| Language | Run | Turn | Learner said | Tutor replied | Latency (ms) | Adherence "
            "| Corrections |",
            "|---|---:|---:|---|---|---:|---|---:|",
        ]
    )
    for run in runs:
        if not run.turns:
            label = _cell(run.error or "no turn recorded")
            lines.append(f"| {run.language} | {run.run} | — | — | — | — | failed: {label} | 0 |")
            continue
        for turn in run.turns:
            if turn.error is not None:
                lines.append(
                    f"| {turn.language} | {turn.run} | {turn.turn} "
                    f"| {_cell(BENCH.sanitize(turn.user_text, 60))} | failed: "
                    f"{_cell(turn.error)} | — | fail | 0 |"
                )
                continue
            verdict = "pass" if turn.adherence_ok else "fail"
            lines.append(
                f"| {turn.language} | {turn.run} | {turn.turn} "
                f"| {_cell(BENCH.sanitize(turn.user_text, 60))} "
                f"| {_cell(BENCH.sanitize(turn.reply, 90))} "
                f"| {fmt_ms(turn.latency_ms)} | {verdict} — {_cell(turn.adherence_reason)} "
                f"| {turn.corrections} |"
            )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Adherence is a script heuristic, not a language detector: `ja` needs kana, `zh` "
            "needs Han characters and no kana, `de` needs ä/ö/ü/ß or two German stopwords, `en` "
            "needs ≥90% ASCII and no CJK. It is shared with `scripts/bench_llm.py`.",
            "- p50 is `statistics.median` over the successful turns (for an even count it "
            "averages the two middle values), measured around the whole streamed reply.",
            "- Scripted turns are keyed by scenario id (`SCRIPTED_TURNS`), so the learner line "
            "always fits the role the model is playing; a script that fights the scenario "
            "produces a refusal, not language signal. The `--dry-run` canned provider is keyed "
            "the same way (`CANNED_REPLIES`), so it answers in the same role.",
            "- Warm-up: before the measured turns, each language sends one short in-character "
            "call whose reply and latency are discarded. The cold start of the model (~4.3 s "
            "on Ollama) therefore stays out of the p50, and `## Per-turn detail` lists the "
            "measured turns only.",
            "- `JSON valid` counts evaluations where `chat_json` returned a parseable object "
            "that `normalize()` accepted; `Corrections` sums `normalize()` output over the "
            "scripted turns.",
            "- Harness vs. product: no STT/TTS, empty learner memory, a fixed level, a fixed "
            "2-turn script and one evaluation call per turn.",
            "- The harness never prints the API key and builds no shell command.",
            "- Write policy: a real run writes `<out-dir>/eval-prompts-<date>.md` and refuses to "
            "overwrite without `--force`. `--dry-run` prints the report to stdout and writes "
            "nothing unless `--force` is given together with a temporary `--out-dir`.",
        ]
    )
    lines.extend(f"- {note}" for note in notes)
    lines.append("")

    failed = [run for run in runs if run.error]
    if failed:
        lines.extend(["## Errors", ""])
        for run in failed:
            lines.append(f"- `{run.language}` run {run.run}: {run.error}")
        lines.extend(["", "## Diagnosis", ""])
        if any(turn.error is None for run in runs for turn in run.turns):
            lines.extend(
                [
                    f"At least one run against `{meta.base_url}` failed. Check:",
                    "",
                    f"1. The endpoint answers and the model is pulled "
                    f"(`ollama list | grep {meta.model}`).",
                    "2. The failure text above: an HTTP 400 usually means the server rejected "
                    "`reasoning_effort` or `response_format`.",
                    f"3. The address/port is right: pass `--base-url` (current "
                    f"`{meta.base_url}`).",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    f"Every turn against `{meta.base_url}` failed. Check, in order:",
                    "",
                    f"1. Ollama is running on the host and the model is pulled "
                    f"(`ollama list | grep {meta.model}`).",
                    "2. AST-000: Ollama may be bound to `127.0.0.1` only, so containers reach "
                    "it as `host.docker.internal` and get ECONNREFUSED; restart with "
                    "`OLLAMA_HOST=0.0.0.0:11434`.",
                    f"3. The endpoint is elsewhere: pass `--base-url` (current "
                    f"`{meta.base_url}`).",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


# --- output plumbing -----------------------------------------------------------------


def resolve_out_dir(raw: str) -> Path:
    """Resolve `--out-dir` against the repo root, not the caller's cwd."""
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate
    return (REPO_ROOT / candidate).resolve()


def temp_roots() -> tuple[Path, ...]:
    roots = {Path("/tmp"), Path("/var/tmp"), Path("/private/tmp")}
    try:
        roots.add(Path(tempfile.gettempdir()))
    except (OSError, RuntimeError):  # pragma: no cover - only on a broken environment
        logger.debug("could not resolve the system temp dir", exc_info=True)
    return tuple(root.resolve() for root in roots)


def is_temp_dir(path: Path) -> bool:
    """True when `path` lives under a system temporary directory."""
    resolved = Path(path).expanduser().resolve()
    return any(resolved == root or root in resolved.parents for root in temp_roots())


def report_path(out_dir: Path, report_date: str) -> Path:
    return Path(out_dir) / f"{REPORT_PREFIX}-{report_date}.md"


def decide_write(*, dry_run: bool, force: bool, out_dir: Path, target: Path) -> tuple[bool, str]:
    """Decide whether the report may be persisted. Returns (write, reason when not)."""
    if dry_run:
        if not force:
            return False, (
                "dry run: nothing written (add --force together with a temporary --out-dir "
                "to save the report)"
            )
        if not is_temp_dir(out_dir):
            return False, (
                f"dry run: nothing written (--out-dir {out_dir} is not a temporary directory; "
                "even with --force, a dry run only writes under the system temp dir)"
            )
        return True, ""
    if Path(target).exists() and not force:
        return False, f"refusing to overwrite {target}: pass --force to replace it"
    return True, ""


def write_report(target: Path, text: str, *, force: bool) -> tuple[int, str]:
    if target.exists() and not force:
        return 2, f"refusing to overwrite {target} (pass --force to replace it)"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except OSError as exc:
        return 1, f"could not write report to {target}: {exc}"
    return 0, f"report: {target}"


def progress_stream(args: argparse.Namespace) -> TextIO:
    """`--dry-run` owns stdout for the markdown, so progress moves to stderr."""
    return sys.stderr if args.dry_run else sys.stdout


# --- provider ------------------------------------------------------------------------


class CannedLLM:
    """Deterministic stand-in for `OpenAICompatLLM`: same methods, no network.

    It is built from the `Scenario` it plays, exactly like `run_language`, so the canned
    replies come from the scenario's own set and a missing key fails loudly.
    """

    name = "canned"

    def __init__(self, scenario: Scenario) -> None:
        self.scenario_id = scenario.id
        self.language = scenario.language
        self.replies = canned_replies(scenario.id)
        self.calls = 0  # every chat_stream call, the discarded warm-up included
        self.scripted_calls = 0  # measured replies only, so the warm-up cannot shift them

    async def chat_stream(
        self, messages: list[dict[str, str]], **overrides: Any
    ) -> AsyncIterator[str]:
        self.calls += 1
        if is_warmup(messages):
            text = self.replies[0]
        else:
            self.scripted_calls += 1
            text = self.replies[(self.scripted_calls - 1) % len(self.replies)]
        for start in range(0, len(text), 12):
            await asyncio.sleep(0)
            yield text[start : start + 12]

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        **overrides: Any,
    ) -> dict[str, Any]:
        original, corrected, explanation = CANNED_CORRECTIONS.get(
            self.language, CANNED_CORRECTIONS["en"]
        )
        quote = _canned_learner_quote(messages)
        if quote:
            original = quote
            corrected = f"{quote}{_CANNED_SUFFIX.get(self.language, _CANNED_SUFFIX['en'])}"
        return {
            "corrections": [
                {
                    "original": original,
                    "corrected": corrected,
                    "explanation_pt": explanation,
                    "type": "naturalness",
                    "severity": "low",
                    "confidence": 0.9,
                }
            ],
            "vocab": [{"term": corrected.split()[0], "translation_pt": "ver nota de correção"}],
            "scene_progress": {"goals_met": [], "status": "ongoing"},
            "language_leak": False,
        }

    async def aclose(self) -> None:
        return None


def build_llm(args: argparse.Namespace, scenario: Scenario) -> Any:
    if args.dry_run:
        return CannedLLM(scenario)
    config = dict(args.llm_config)
    config.update(
        {"base_url": args.base_url, "model": args.model, "timeout_s": args.timeout}
    )
    return OpenAICompatLLM(config)


# --- scripted conversation -----------------------------------------------------------


async def evaluate_turn(
    args: argparse.Namespace, scenario: Scenario, llm: Any, turn: TurnResult
) -> None:
    messages = build_evaluation_messages(scenario, args.level, turn.user_text, turn.reply)
    turn.evaluated = True
    try:
        async with asyncio.timeout(args.timeout):
            raw = await llm.chat_json(
                messages, EVALUATION_SCHEMA, language=scenario.language
            )
    except Exception as exc:  # noqa: BLE001 - a failed pass is a metric, not a crash
        turn.eval_error = describe_failure(exc)
        return
    # AST-035: the scenario language is what `normalize()` needs to keep a case-only
    # correction in German; without it the report drops fixes the product shows.
    evaluation = normalize(raw, user_text=turn.user_text, language=scenario.language)
    turn.json_valid = True
    turn.corrections = len(evaluation.corrections)
    turn.vocab = len(evaluation.vocab)


async def warm_up(
    args: argparse.Namespace, scenario: Scenario, llm: Any, level_scale: str
) -> tuple[float | None, str | None]:
    """Send one short roleplay call and throw the reply away.

    It pays the model load before the measured turns, so a cold server cannot inflate
    turn 1. A failure here is not fatal: the measured turns report the same endpoint
    problem through their own errors.
    """
    history: list[dict[str, str]] = [
        {"role": "assistant", "content": scenario.opening_line},
        {"role": "user", "content": warmup_line(scenario.language)},
    ]
    messages = build_roleplay_messages(scenario, args.level, level_scale, history, memory="")
    started = time.perf_counter()
    try:
        async with asyncio.timeout(args.timeout):
            await collect_stream(llm.chat_stream(messages, language=scenario.language))
    except Exception as exc:  # noqa: BLE001 - the measured turns report the failure
        return None, describe_failure(exc)
    return (time.perf_counter() - started) * 1000.0, None


async def run_language(
    args: argparse.Namespace,
    scenario: Scenario,
    llm: Any,
    run_index: int,
    level_scale: str,
) -> RunResult:
    """Warm up once per language, then replay the scenario's fixed script."""
    result = RunResult(language=scenario.language, run=run_index, scenario_id=scenario.id)
    if run_index == 1:
        result.warmup_ms, result.warmup_error = await warm_up(
            args, scenario, llm, level_scale
        )
    history: list[dict[str, str]] = [
        {"role": "assistant", "content": scenario.opening_line}
    ]
    for index, user_text in enumerate(scripted_turns(scenario.id), start=1):
        history.append({"role": "user", "content": user_text})
        messages = build_roleplay_messages(
            scenario, args.level, level_scale, history, memory=""
        )
        turn = TurnResult(
            language=scenario.language, run=run_index, turn=index, user_text=user_text
        )
        started = time.perf_counter()
        try:
            async with asyncio.timeout(args.timeout):
                turn.reply = await collect_stream(
                    llm.chat_stream(messages, language=scenario.language)
                )
        except Exception as exc:  # noqa: BLE001 - reported as a failed turn
            turn.error = describe_failure(exc)
            result.error = turn.error
            result.turns.append(turn)
            break
        turn.latency_ms = (time.perf_counter() - started) * 1000.0
        turn.adherence_ok, turn.adherence_reason = check_adherence(
            scenario.language, turn.reply
        )
        history.append({"role": "assistant", "content": turn.reply})
        await evaluate_turn(args, scenario, llm, turn)
        result.turns.append(turn)
    return result


def warmup_note(language: str, run: RunResult) -> str:
    """One report note per language so a reader knows the warm-up was not measured."""
    if run.warmup_error is not None:
        return (
            f"{language}: warm-up call failed ({run.warmup_error}) and was discarded; "
            "the measured turns report their own errors"
        )
    return (
        f"{language}: warm-up discarded ({fmt_ms(run.warmup_ms)} ms, not measured) — "
        "the cold start stays out of the p50"
    )


async def run_all(
    args: argparse.Namespace, notes: list[str]
) -> list[RunResult]:
    repository = ScenarioRepository(SCENARIOS_DIR)
    repository.load()
    progress = progress_stream(args)
    results: list[RunResult] = []
    for language in args.languages:
        scenario = repository.get(SCENARIO_BY_LANGUAGE[language])
        level_scale = args.level_scales.get(language, "CEFR")
        for run_index in range(1, args.runs + 1):
            run = await run_language(
                args, scenario, build_llm(args, scenario), run_index, level_scale
            )
            results.append(run)
            turns_ok = sum(1 for turn in run.turns if turn.error is None)
            if run_index == 1:
                notes.append(warmup_note(language, run))
            print(
                f"{language} run {run_index}: {turns_ok}/{len(run.turns)} turns, "
                f"{sum(turn.corrections for turn in run.turns)} corrections"
                + (f", failed: {run.error}" if run.error else ""),
                file=progress,
                flush=True,
            )
            if run.error:
                note = (
                    f"{language}: stopped after run {run_index} failed ({run.error}); "
                    "later runs of this language were skipped"
                )
                if note not in notes:
                    notes.append(note)
                break
    return results


# --- config defaults -----------------------------------------------------------------


@dataclass(slots=True)
class ConfigDefaults:
    model: str
    timeout: float
    llm: dict[str, Any]
    level_scales: dict[str, str]
    label: str
    notes: list[str] = field(default_factory=list)


def load_config_defaults() -> ConfigDefaults:
    """Read `[llm.default]`/`[languages.*]` through `app.core.config`, with fallbacks."""
    notes: list[str] = []
    fallback_llm: dict[str, Any] = {
        "provider": "openai_compat",
        "base_url": DEFAULT_BASE_URL,
        "model": DEFAULT_MODEL,
        "api_key": "none",
        "timeout_s": DEFAULT_TIMEOUT_S,
        "json_mode": "json_object",
    }
    level_scales = {language: "CEFR" for language in DEFAULT_LANGUAGES}
    rel = str(CONFIG_PATH.relative_to(REPO_ROOT))
    settings = Settings(config_path=CONFIG_PATH)
    try:
        app_config = AppConfig.load(CONFIG_PATH)
    except (ConfigError, OSError) as exc:
        notes.append(f"`{rel}` unreadable ({type(exc).__name__}: {exc}); built-in defaults")
        return ConfigDefaults(
            DEFAULT_MODEL, DEFAULT_TIMEOUT_S, fallback_llm, level_scales,
            "built-in defaults", notes,
        )

    base_llm = dict(fallback_llm)
    for position, language in enumerate(DEFAULT_LANGUAGES):
        try:
            resolved = app_config.resolve(language, settings=settings)
        except ConfigError as exc:
            notes.append(f"{language}: {exc}; keeping the CEFR level scale")
            continue
        level_scales[language] = resolved.level_scale
        if position == 0:
            base_llm.update(resolved.llm)
    try:
        timeout = float(base_llm.get("timeout_s") or DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_S
        notes.append(f"`{rel}` `llm.timeout_s` is not a number; using {timeout:g} s")
    return ConfigDefaults(
        model=str(base_llm.get("model") or DEFAULT_MODEL),
        timeout=timeout,
        llm=base_llm,
        level_scales=level_scales,
        label=f"`{rel}`",
        notes=notes,
    )


# --- CLI -----------------------------------------------------------------------------


def parse_languages(raw: str, parser: argparse.ArgumentParser) -> tuple[str, ...]:
    seen: list[str] = []
    for piece in raw.split(","):
        language = piece.strip()
        if not language:
            continue
        if language not in SUPPORTED_LANGUAGES:
            parser.error(
                f"unsupported language '{language}'; expected a subset of "
                f"{', '.join(SUPPORTED_LANGUAGES)}"
            )
        if language not in SCENARIO_BY_LANGUAGE:
            parser.error(f"no scenario mapped for language '{language}'")
        if language not in seen:
            seen.append(language)
    if not seen:
        parser.error("--languages needs at least one language")
    return tuple(seen)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    config = load_config_defaults()
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=(
            "Model/timeout defaults come from "
            f"{CONFIG_PATH.relative_to(REPO_ROOT)} [llm.default]; the script is meant to run "
            "as `cd backend && uv run python ../scripts/eval_prompts.py`."
        ),
    )
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL, help="OpenAI-compatible base URL"
    )
    parser.add_argument("--model", default=config.model, help="model name served by the endpoint")
    parser.add_argument(
        "--languages",
        default=",".join(DEFAULT_LANGUAGES),
        help=f"comma-separated languages (default: {','.join(DEFAULT_LANGUAGES)})",
    )
    parser.add_argument("--runs", type=int, default=1, help="scripted runs per language")
    parser.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help=f"report directory, relative paths resolve to the repo root "
        f"(default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "use the canned provider (no network), print the report to stdout and write "
            "nothing unless --force is given with a temporary --out-dir"
        ),
    )
    parser.add_argument(
        "--force", action="store_true", help="overwrite today's report / allow the dry run write"
    )
    parser.add_argument(
        "--timeout", type=float, default=config.timeout, help="per-turn timeout in seconds"
    )
    args = parser.parse_args(argv)

    if args.runs < 1:
        parser.error("--runs must be >= 1")
    if args.timeout <= 0:
        parser.error("--timeout must be > 0")
    if not args.base_url.lower().startswith(("http://", "https://")):
        parser.error("--base-url must start with http:// or https://")

    args.languages = parse_languages(args.languages, parser)
    args.level = DEFAULT_LEVEL
    args.level_scales = config.level_scales
    args.llm_config = config.llm
    args.json_mode = str(config.llm.get("json_mode", "json_object"))
    args.config_label = config.label
    args.config_notes = config.notes
    args.out_dir_path = resolve_out_dir(args.out_dir)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    now = datetime.now()
    report_date = now.date().isoformat()
    started_at = now.isoformat(timespec="seconds")
    target = report_path(args.out_dir_path, report_date)
    notes: list[str] = list(args.config_notes) + [f"started at {started_at} (local)"]

    should_write, reason = decide_write(
        dry_run=args.dry_run, force=args.force, out_dir=args.out_dir_path, target=target
    )
    if not args.dry_run and not should_write:
        print(reason, file=sys.stderr)
        return 2
    if args.dry_run and not should_write:
        print(reason, file=sys.stderr)

    try:
        runs = asyncio.run(run_all(args, notes))
    except Exception as exc:  # noqa: BLE001 - the harness reports instead of tracing back
        print(f"evaluation aborted: {describe_failure(exc)}", file=sys.stderr)
        return 2

    metrics = aggregate(runs, args.languages)
    totals = summarize(metrics)
    meta = ReportMeta(
        base_url=args.base_url,
        model=args.model,
        languages=args.languages,
        runs=args.runs,
        timeout_s=args.timeout,
        dry_run=args.dry_run,
        level=args.level,
        json_mode=args.json_mode,
        config_label=args.config_label,
    )
    text = render_report(
        meta=meta,
        runs=runs,
        metrics=metrics,
        totals=totals,
        notes=notes,
        report_path=target,
        report_date=report_date,
    )

    if args.dry_run:
        sys.stdout.write(text)
        sys.stdout.flush()
        if should_write:
            code, message = write_report(target, text, force=True)
            if code != 0:
                print(message, file=sys.stderr)
                return code
            print(f"dry run: {message}", file=sys.stderr)
        else:
            print(f"dry run: nothing written to {target}", file=sys.stderr)
    else:
        code, message = write_report(target, text, force=args.force)
        if code != 0:
            print(message, file=sys.stderr)
            return code
        print(message)

    completed = {
        run.language for run in runs for turn in run.turns if turn.error is None
    }
    missing = [language for language in args.languages if language not in completed]
    if missing:
        print(
            f"ERROR: no turn completed for: {', '.join(missing)} (see {target})",
            file=sys.stderr,
        )
        return 1
    return 0


def entrypoint(argv: list[str] | None = None) -> int:
    """CLI boundary: no traceback ever reaches the terminal."""
    try:
        return main(argv)
    except KeyboardInterrupt:
        print("\ninterrupted (SIGINT): no report written", file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 1
    except Exception as exc:  # noqa: BLE001 - safety net for an unattended run
        print(f"unexpected failure: {describe_failure(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(entrypoint())
