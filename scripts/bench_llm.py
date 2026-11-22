#!/usr/bin/env python3
"""Benchmark an OpenAI-compatible chat endpoint: TTFB, throughput, language adherence.

Streams a café-barista prompt in every configured language and writes a markdown report.
Standard library only; no command is built from user input.

Measured surface: `POST {base-url}/chat/completions` with `"stream": true`, i.e. the
OpenAI-compatible `/v1` surface. The backend's `json_mode = "ollama_native"` path
(`POST /api/chat`) is NOT covered by this benchmark.
"""

from __future__ import annotations

import argparse
import http.client
import json
import platform
import re
import socket
import statistics
import sys
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "backend" / "config" / "models.toml"

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_OUT_DIR = "docs/spikes"
DEFAULT_DEADLINE_S = 90.0

FALLBACK_MODEL = "qwen3.5:4b"
FALLBACK_TIMEOUT_S = 60.0
FALLBACK_API_KEY = "ollama"
FALLBACK_MAX_TOKENS = 120

# The four languages the app ships with; `[languages.*]` in models.toml must match.
LANGUAGES: tuple[str, ...] = ("en", "ja", "zh", "de")

PROMPTS: dict[str, str] = {
    "en": (
        "You are a café barista. Answer in English, 1-2 sentences: "
        "greet the customer and offer today's coffee."
    ),
    "ja": (
        "You are a café barista. Answer in Japanese (日本語), 1-2 sentences: "
        "greet the customer and offer today's coffee."
    ),
    "zh": (
        "You are a café barista. Answer in Simplified Chinese (简体中文), 1-2 sentences: "
        "greet the customer and offer today's coffee."
    ),
    "de": (
        "You are a café barista. Answer in German (Deutsch), 1-2 sentences: "
        "greet the customer and offer today's coffee."
    ),
}

# Appended one per run so consecutive runs are not byte-identical.
CAFE_ITEMS: tuple[str, ...] = (
    "a flat white",
    "an espresso",
    "a cortado",
    "a cappuccino",
    "a cold brew",
    "a macchiato",
)

KANA = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
GERMAN_SPECIFIC = ("ä", "ö", "ü", "ß")
GERMAN_STOPWORDS = frozenset(
    {
        "der", "die", "das", "den", "dem", "und", "ich", "nicht", "ein", "eine", "einen",
        "ist", "sind", "mit", "für", "sie", "wir", "haben", "habe", "möchte", "möchten",
        "guten", "tag", "morgen", "danke", "bitte", "ihnen", "ihr", "heute", "können",
        "hier", "unser", "unsere", "kaffee", "gern", "sehr", "auch", "noch", "wie",
    }
)
JA_ZH_CHARS_PER_TOKEN = 1.7


class BenchError(Exception):
    """A single benchmark request failed; the message is user-facing."""

    def __init__(
        self, message: str, connection: bool = False, deadline: bool = False
    ) -> None:
        super().__init__(message)
        self.connection = connection
        self.deadline = deadline

    @property
    def stop_language(self) -> bool:
        """True when retrying further runs (of this language) is pointless."""
        return self.connection or self.deadline


@dataclass
class ConfigDefaults:
    model: str
    timeout: float
    api_key: str
    max_tokens: int
    languages: tuple[str, ...]
    notes: list[str]
    label: str


@dataclass
class StreamSample:
    ttfb_ms: float | None
    total_ms: float
    text: str
    thinking_chars: int
    usage_completion_tokens: int | None = None
    cached_prompt_tokens: int | None = None


@dataclass
class RunResult:
    language: str
    run: int  # 0 = discarded warm-up, 1..n = measured runs
    ttfb_ms: float | None = None
    total_ms: float | None = None
    chars: int = 0
    tokens: float = 0.0
    token_source: str = "estimate"
    thinking_chars: int = 0
    cached_prompt_tokens: int | None = None
    text: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def measured(self) -> bool:
        return self.run > 0

    @property
    def tokens_per_second(self) -> float | None:
        if not self.ok or self.total_ms is None or self.ttfb_ms is None:
            return None
        window_ms = self.total_ms - self.ttfb_ms
        if window_ms <= 0:
            return None
        return self.tokens / (window_ms / 1000.0)


def config_rel_path() -> str:
    try:
        return str(CONFIG_PATH.relative_to(REPO_ROOT))
    except ValueError:
        return str(CONFIG_PATH)


def _languages_from_config(data: dict[str, Any], rel: str, notes: list[str]) -> tuple[str, ...]:
    configured = [str(key) for key in (data.get("languages") or {})]
    if not configured:
        notes.append(
            f"`{rel}` declares no `[languages.*]`; benchmarking the known set "
            f"{', '.join(LANGUAGES)}"
        )
        return LANGUAGES
    unknown = [language for language in configured if language not in PROMPTS]
    if unknown:
        notes.append(
            f"ignored `[languages.*]` entries without a prompt: {', '.join(unknown)}"
        )
    languages = tuple(language for language in configured if language in PROMPTS)
    if not languages:
        notes.append(f"no usable language in `{rel}`; falling back to {', '.join(LANGUAGES)}")
        return LANGUAGES
    if set(configured) != set(LANGUAGES):
        notes.append(
            f"`{rel}` languages {configured} differ from the known set {list(LANGUAGES)}"
        )
    return languages


def load_config() -> ConfigDefaults:
    """Read `[llm.default]` and `[languages.*]` from backend/config/models.toml."""
    notes: list[str] = []
    rel = config_rel_path()
    try:
        with CONFIG_PATH.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        notes.append(
            f"`{rel}` unreadable ({type(exc).__name__}: {exc}); using built-in defaults"
        )
        return ConfigDefaults(
            FALLBACK_MODEL,
            FALLBACK_TIMEOUT_S,
            FALLBACK_API_KEY,
            FALLBACK_MAX_TOKENS,
            LANGUAGES,
            notes,
            "built-in defaults",
        )

    llm = (data.get("llm") or {}).get("default") or {}
    if not llm:
        notes.append(f"`{rel}` has no `[llm.default]`; using built-in defaults")
    try:
        timeout = float(llm.get("timeout_s") or FALLBACK_TIMEOUT_S)
    except (TypeError, ValueError):
        timeout = FALLBACK_TIMEOUT_S
        notes.append(f"`{rel}` `llm.default.timeout_s` is not a number; using {timeout:g} s")
    try:
        max_tokens = int(llm.get("max_tokens") or FALLBACK_MAX_TOKENS)
    except (TypeError, ValueError):
        max_tokens = FALLBACK_MAX_TOKENS
        notes.append(f"`{rel}` `llm.default.max_tokens` is not an integer; using {max_tokens}")
    return ConfigDefaults(
        model=str(llm.get("model") or FALLBACK_MODEL),
        timeout=timeout,
        api_key=str(llm.get("api_key") or FALLBACK_API_KEY),
        max_tokens=max_tokens,
        languages=_languages_from_config(data, rel, notes),
        notes=notes,
        label=f"`{rel}` `[llm.default]`",
    )


def estimate_tokens(text: str, language: str) -> float:
    """Approximate token count: whitespace words for latin text, characters/1.7 for ja/zh."""
    if language in ("ja", "zh"):
        return len(text) / JA_ZH_CHARS_PER_TOKEN
    return float(len(text.split()))


def check_adherence(language: str, text: str) -> tuple[bool, str]:
    """Heuristic script/stopword check that the answer is in the requested language."""
    if not text.strip():
        return False, "empty response"
    has_kana = bool(KANA.search(text))
    has_cjk = bool(CJK.search(text))
    ascii_ratio = sum(1 for char in text if ord(char) < 128) / len(text)

    if language == "ja":
        if has_kana:
            return True, "contains hiragana/katakana"
        return False, "no hiragana/katakana found"
    if language == "zh":
        if has_kana:
            return False, "contains kana (looks Japanese)"
        if has_cjk:
            return True, "CJK ideographs, no kana"
        return False, "no CJK ideographs found"
    if language == "de":
        if any(char in text.lower() for char in GERMAN_SPECIFIC):
            return True, "contains ä/ö/ü/ß"
        words = set(re.findall(r"[a-zßäöü]+", text.lower()))
        hits = sorted(words & GERMAN_STOPWORDS)
        if len(hits) >= 2:
            return True, f"German stopwords: {', '.join(hits[:3])}"
        return False, "no German-specific characters or stopwords"
    if has_kana or has_cjk:
        return False, "contains CJK/kana"
    if ascii_ratio >= 0.9:
        return True, f"{ascii_ratio:.0%} ASCII, no CJK"
    return False, f"only {ascii_ratio:.0%} ASCII"


def build_prompt(language: str, run_index: int) -> str:
    """Base prompt plus a per-run café item so consecutive runs are not byte-identical."""
    item = CAFE_ITEMS[run_index % len(CAFE_ITEMS)]
    return f"{PROMPTS[language]} Today's special: {item}."


def build_request(
    base_url: str, model: str, api_key: str, prompt: str, max_tokens: int, effort: str | None
) -> urllib.request.Request:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": max_tokens,
    }
    if effort:
        payload["reasoning_effort"] = effort
    return urllib.request.Request(
        query_endpoint(base_url),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )


def query_endpoint(base_url: str) -> str:
    """Build the /chat/completions URL, tolerating a base URL that already has the path."""
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/chat/completions"):
        return trimmed
    return f"{trimmed}/chat/completions"


def _read_stream(
    request: urllib.request.Request, timeout: float, deadline_s: float
) -> StreamSample:
    """Stream the SSE body, bounded by both the socket timeout and a wall-clock deadline."""
    start = time.perf_counter()
    ttfb_ms: float | None = None
    pieces: list[str] = []
    thinking_chars = 0
    raw: list[str] = []
    usage_tokens: int | None = None
    cached_tokens: int | None = None

    with urllib.request.urlopen(request, timeout=min(timeout, deadline_s)) as response:
        for line in response:
            if (time.perf_counter() - start) > deadline_s:
                raise BenchError(
                    f"exceeded the {deadline_s:g} s wall-clock deadline", deadline=True
                )
            decoded = line.decode("utf-8", "replace").strip()
            if not decoded:
                continue
            if not decoded.startswith("data:"):
                raw.append(decoded)
                continue
            payload = decoded[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            usage = chunk.get("usage")
            if isinstance(usage, dict):
                completion_tokens = usage.get("completion_tokens")
                if isinstance(completion_tokens, int):
                    usage_tokens = completion_tokens
                prompt_details = usage.get("prompt_tokens_details")
                if isinstance(prompt_details, dict):
                    cached = prompt_details.get("cached_tokens")
                    if isinstance(cached, int):
                        cached_tokens = cached
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            if delta.get("reasoning"):
                thinking_chars += len(delta["reasoning"])
            piece = delta.get("content")
            if piece:
                if ttfb_ms is None:
                    ttfb_ms = (time.perf_counter() - start) * 1000.0
                pieces.append(piece)

    if not pieces and raw:
        fallback, fallback_tokens, fallback_cached = _parse_plain_body("".join(raw))
        if fallback:
            pieces.append(fallback)
            if ttfb_ms is None:
                ttfb_ms = (time.perf_counter() - start) * 1000.0
        usage_tokens = fallback_tokens if fallback_tokens is not None else usage_tokens
        cached_tokens = fallback_cached if fallback_cached is not None else cached_tokens

    total_ms = (time.perf_counter() - start) * 1000.0
    if total_ms > deadline_s * 1000.0:
        raise BenchError(f"exceeded the {deadline_s:g} s wall-clock deadline", deadline=True)
    return StreamSample(
        ttfb_ms=ttfb_ms,
        total_ms=total_ms,
        text="".join(pieces),
        thinking_chars=thinking_chars,
        usage_completion_tokens=usage_tokens,
        cached_prompt_tokens=cached_tokens,
    )


def _parse_plain_body(body: str) -> tuple[str, int | None, int | None]:
    """Handle servers that ignore stream=true and answer with a single JSON body."""
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return "", None, None
    if not isinstance(parsed, dict):
        return "", None, None
    choices = parsed.get("choices") or []
    text = (choices[0].get("message") or {}).get("content") or "" if choices else ""
    usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {}
    tokens = usage.get("completion_tokens")
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    return (
        text,
        tokens if isinstance(tokens, int) else None,
        cached if isinstance(cached, int) else None,
    )


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace").strip()[:200]
        except OSError:
            detail = ""
        suffix = f": {detail}" if detail else ""
        return f"HTTP {exc.code} {exc.reason}{suffix}"
    if isinstance(exc, urllib.error.URLError):
        return f"connection failed: {exc.reason}"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "request timed out"
    return f"{type(exc).__name__}: {exc}"


# http.client.HTTPException (BadStatusLine, IncompleteRead, ...) is not an OSError and
# used to escape as a traceback.
HANDLED_ERRORS = (
    urllib.error.URLError,
    http.client.HTTPException,
    OSError,
    TimeoutError,
)


def is_connection_error(exc: BaseException) -> bool:
    """True when the failure means 'endpoint not reachable' rather than 'bad request'."""
    if isinstance(exc, urllib.error.HTTPError):
        return False
    return isinstance(exc, (socket.timeout, TimeoutError, urllib.error.URLError, OSError))


def _attempt(
    args: argparse.Namespace, prompt: str, effort: str | None
) -> StreamSample:
    request = build_request(
        args.base_url, args.model, args.api_key, prompt, args.max_tokens, effort
    )
    return _read_stream(request, args.timeout, args.deadline)


def _stream_with_fallback(
    args: argparse.Namespace, prompt: str, notes: list[str]
) -> StreamSample:
    """Stream one completion, retrying without reasoning_effort if the server rejects it."""
    try:
        return _attempt(args, prompt, args.reasoning_effort)
    except urllib.error.HTTPError as exc:
        if exc.code != 400 or not args.reasoning_effort:
            raise BenchError(describe_error(exc)) from exc
        note = (
            f"server rejected reasoning_effort={args.reasoning_effort!r} with HTTP 400; "
            "retried without the field, so latency includes thinking tokens"
        )
        if note not in notes:
            notes.append(note)
        try:
            return _attempt(args, prompt, None)
        except HANDLED_ERRORS as retry_exc:
            raise BenchError(
                describe_error(retry_exc), is_connection_error(retry_exc)
            ) from retry_exc
    except HANDLED_ERRORS as exc:
        raise BenchError(describe_error(exc), is_connection_error(exc)) from exc


def run_once(
    args: argparse.Namespace, language: str, run_index: int, notes: list[str]
) -> RunResult:
    sample = _stream_with_fallback(args, build_prompt(language, run_index), notes)
    text = sample.text
    if not text.strip() and sample.thinking_chars:
        note = (
            f"{language}: response contained {sample.thinking_chars} chars of thinking and no "
            "content; raise --max-tokens or disable thinking via --reasoning-effort none"
        )
        if note not in notes:
            notes.append(note)

    if sample.usage_completion_tokens is not None:
        tokens = float(sample.usage_completion_tokens)
        source = "usage"
    else:
        tokens = estimate_tokens(text, language)
        source = "estimate"
        if text.strip():
            note = (
                f"{language}: stream carried no `usage.completion_tokens`; tokens/s falls back "
                "to the character/word estimate"
            )
            if note not in notes:
                notes.append(note)

    return RunResult(
        language=language,
        run=run_index,
        ttfb_ms=sample.ttfb_ms,
        total_ms=sample.total_ms,
        chars=len(text),
        tokens=tokens,
        token_source=source,
        thinking_chars=sample.thinking_chars,
        cached_prompt_tokens=sample.cached_prompt_tokens,
        text=text,
    )


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def fmt(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def span(values: list[float]) -> str:
    if not values:
        return "—"
    return f"{min(values):.0f}–{max(values):.0f}"


def sanitize(text: str, limit: int = 200) -> str:
    flat = " ".join(text.split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def render_report(
    args: argparse.Namespace,
    results: list[RunResult],
    notes: list[str],
    started_at: str,
    report_date: str,
    report_path: Path,
) -> str:
    failures = [run for run in results if not run.ok]
    measured = [run for run in results if run.measured]
    usage_runs = sum(1 for run in measured if run.ok and run.token_source == "usage")
    ok_measured = [run for run in measured if run.ok]

    lines: list[str] = [
        f"# LLM benchmark — {report_date}",
        "",
        "Generated by `scripts/bench_llm.py` (AST-001, hardened in AST-013). "
        "Host-side run, no Docker involved.",
        "",
        "## Environment",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Endpoint | `{args.base_url}` → `{query_endpoint(args.base_url)}` |",
        "| Endpoint surface | OpenAI-compatible `/v1` streaming (`\"stream\": true`, SSE); "
        "`json_mode = \"ollama_native\"` (`POST /api/chat`) is **not** measured |",
        f"| Model | `{args.model}` |",
        f"| Defaults source | {args.config_label} (CLI flags override) |",
        f"| Languages | {', '.join(args.languages)} |",
        f"| Measured runs per language | {args.runs} (plus 1 discarded warm-up run) |",
        f"| Max tokens | {args.max_tokens} |",
        f"| Socket timeout | {args.timeout:g} s |",
        f"| Wall-clock deadline per run | {args.deadline:g} s |",
        f"| Reasoning effort | `{args.reasoning_effort or 'server default'}` |",
        f"| Token accounting | server `usage.completion_tokens` "
        f"({usage_runs}/{len(ok_measured)} runs); estimate for the rest |",
        f"| Started (local) | {started_at} |",
        f"| Report written to | `{report_path}` |",
        f"| Host | {platform.system()} {platform.machine()}, "
        f"Python {platform.python_version()} |",
        "",
        "## Results",
        "",
        "Cold = measured run 1, warm = measured runs 2..n. `n` counts successful measured "
        "runs over attempted measured runs.",
        "",
        "| Language | n | cold TTFB (ms) | warm TTFB p50 (ms) | TTFB min–max (ms) "
        "| total p50 (ms) | total min–max (ms) | p50 tokens/s (warm) | Adherence |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]

    for language in args.languages:
        language_runs = [run for run in measured if run.language == language]
        ok_runs = [run for run in language_runs if run.ok]
        ttfb_values = [run.ttfb_ms for run in ok_runs if run.ttfb_ms is not None]
        total_values = [run.total_ms for run in ok_runs if run.total_ms is not None]
        cold_run = next((run for run in ok_runs if run.run == 1), None)
        warm_runs = [run for run in ok_runs if run.run >= 2]
        warm_ttfb = [run.ttfb_ms for run in warm_runs if run.ttfb_ms is not None]
        tps_pool = warm_runs or ok_runs
        tps = median(
            [run.tokens_per_second for run in tps_pool if run.tokens_per_second is not None]
        )
        n_cell = f"{len(ok_runs)}/{len(language_runs)}"
        if ok_runs:
            verdicts = [check_adherence(language, run.text) for run in ok_runs]
            passed = sum(1 for ok, _ in verdicts if ok)
            failing = [why for ok, why in verdicts if not ok]
            reason = failing[0] if failing else verdicts[0][1]
            status = "pass" if passed == len(ok_runs) else "fail"
            adherence = f"{status} ({passed}/{len(ok_runs)}) — {reason}"
        else:
            adherence = "fail (0/0) — no successful run"
        lines.append(
            f"| {language} | {n_cell} | {fmt(cold_run.ttfb_ms if cold_run else None)} "
            f"| {fmt(median(warm_ttfb))} | {span(ttfb_values)} "
            f"| {fmt(median(total_values))} | {span(total_values)} "
            f"| {fmt(tps, 1)} | {adherence} |"
        )

    cold_reports = next(
        (run.cached_prompt_tokens for run in ok_measured if run.run == 1), None
    )
    warm_cached = [
        run.cached_prompt_tokens
        for run in ok_measured
        if run.run >= 2 and run.cached_prompt_tokens is not None
    ]

    lines.extend(
        [
            "",
            "## Measurement notes",
            "",
            "- TTFB is the time from the HTTP request to the first SSE chunk with non-empty "
            "`delta.content`; total is the time until the stream closes.",
            "- Cold vs warm: every request repeats the same instruction prefix with a different "
            "café item, so the first run of a language is **cold** and later runs are **warm**. "
            "The server's prompt-prefix cache (Ollama reports it as "
            "`prompt_tokens_details.cached_tokens`) can skip re-prefilling the shared prefix, "
            "which is what makes warm TTFB lower than cold TTFB. One warm-up run per language "
            "runs first and is discarded: it pays the model load into VRAM (seconds on a "
            "CPU-first host) and the first prefix fill, so cold run 1 does not carry the "
            "model-load cost. If the server reports no cache reuse below, cold/warm "
            "differences here are run-to-run noise, not a caching effect.",
        ]
    )
    if cold_reports is not None or warm_cached:
        cached_max = max(warm_cached) if warm_cached else 0
        cold_value = cold_reports or 0
        observed = cold_value > 0 or cached_max > 0
        lines.append(
            "- Server-reported prefix cache (`prompt_tokens_details.cached_tokens`): cold run 1 "
            f"= {cold_value} tokens, warm runs = up to {cached_max}."
            + (
                ""
                if observed
                else " The server reported no reuse at all, so cold/warm differences here are "
                "run-to-run noise rather than a caching effect."
            )
        )
    lines.extend(
        [
            "- Cold TTFB is a single sample (run 1) — treat it as indicative, not a median.",
            "- tokens/s = tokens divided by the generation window (`total_ms - ttfb_ms`). "
            f"{usage_runs}/{len(ok_measured)} successful measured runs used the server's "
            "`usage.completion_tokens` from the final `stream_options.include_usage` chunk"
            + (
                "; every measured run here is a server token count."
                if usage_runs == len(ok_measured)
                else "; the remaining runs use the estimate below and are labelled "
                "`estimate` in the per-run table."
            ),
            f"- Token estimate: whitespace-split words for `en`/`de`; for `ja`/`zh` "
            f"characters/{JA_ZH_CHARS_PER_TOKEN} (CJK tokenizers emit roughly 1.7 characters "
            "per token — an approximation, not a tokenizer count).",
            f"- Each run is bounded by a {args.timeout:g} s socket timeout **and** a "
            f"{args.deadline:g} s wall-clock deadline, so a server dribbling bytes cannot hang "
            "the benchmark; the socket timeout still bounds a single stalled read.",
            "- Adherence is a heuristic: `ja` needs hiragana/katakana, `zh` needs CJK without "
            "kana, `de` needs ä/ö/ü/ß or two German stopwords, `en` needs ≥90% ASCII without "
            "CJK.",
        ]
    )
    if args.runs < 2:
        lines.append(
            "- `--runs 1`: no warm run exists, so warm TTFB and warm tokens/s are `—`."
        )
    if args.deadline < args.timeout:
        lines.append(
            f"- `--deadline` ({args.deadline:g} s) is tighter than `--timeout` "
            f"({args.timeout:g} s); the deadline wins."
        )

    lines.extend(
        [
            "",
            "## Per-run detail",
            "",
            "`warm-up` rows are discarded and excluded from every statistic above.",
            "",
            "| Language | Run | TTFB (ms) | Total (ms) | Chars | tokens/s (source) "
            "| Adherence |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for run in results:
        label = "warm-up" if run.run == 0 else str(run.run)
        if not run.ok:
            lines.append(
                f"| {run.language} | {label} | — | — | — | — "
                f"| failed: {sanitize(run.error or '', 120).replace('|', chr(92) + '|')} |"
            )
            continue
        verdict, why = check_adherence(run.language, run.text)
        lines.append(
            f"| {run.language} | {label} | {fmt(run.ttfb_ms)} | {fmt(run.total_ms)} "
            f"| {run.chars} | {fmt(run.tokens_per_second, 1)} ({run.token_source}) "
            f"| {'pass' if verdict else 'fail'} — {why} |"
        )

    lines.extend(["", "## Samples (first 200 chars)", ""])
    for language in args.languages:
        sample = next(
            (run for run in results if run.language == language and run.ok and run.text.strip()),
            None,
        )
        if sample is None:
            lines.append(f"### {language}")
            lines.append("")
            lines.append("_no sample (request failed)_")
            lines.append("")
            continue
        lines.append(f"### {language}")
        lines.append("")
        lines.append(f"`{sanitize(sample.text).replace('|', chr(92) + '|')}`")
        lines.append("")

    if notes:
        lines.extend(["## Notes", ""])
        lines.extend(f"- {note}" for note in notes)
        lines.append("")

    if failures:
        lines.extend(["## Errors", ""])
        for run in failures:
            label = "warm-up" if run.run == 0 else f"run {run.run}"
            lines.append(f"- `{run.language}` {label}: {run.error}")
        lines.append("")

    if results and not any(run.ok for run in results):
        lines.extend(
            [
                "## Diagnosis",
                "",
                f"Every request to `{args.base_url}` failed. Check, in order:",
                "",
                "1. Ollama is running on the host and the model is pulled "
                f"(`ollama list | grep {args.model}`).",
                "2. AST-000: Ollama may be bound to `127.0.0.1` only. Containers reach it as "
                "`host.docker.internal` and get ECONNREFUSED. Restart with "
                "`OLLAMA_HOST=0.0.0.0:11434` (or `172.17.0.1:11434`) and retry.",
                f"3. Pass the reachable address with `--base-url` (currently `{args.base_url}`).",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def resolve_out_dir(raw: str) -> Path:
    """Resolve `--out-dir` against the repo root (parent of scripts/), not the cwd."""
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate
    return (REPO_ROOT / candidate).resolve()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    config = load_config()
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=(
            "Defaults for --model/--timeout/--api-key/--max-tokens come from "
            f"{config_rel_path()} [llm.default]; the language list comes from [languages.*]."
        ),
    )
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL, help="OpenAI-compatible base URL"
    )
    parser.add_argument(
        "--model", default=config.model, help="model name served by the endpoint"
    )
    parser.add_argument("--runs", type=int, default=3, help="measured runs per language")
    parser.add_argument(
        "--timeout", type=float, default=config.timeout, help="per-read socket timeout in s"
    )
    parser.add_argument(
        "--deadline",
        type=float,
        default=DEFAULT_DEADLINE_S,
        help=f"wall-clock deadline per run in s (default: {DEFAULT_DEADLINE_S:g})",
    )
    parser.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help=f"report directory, relative paths resolve to the repo root "
        f"(default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "overwrite today's report, keeping the previous file as "
            "bench-llm-<date>-<HHMMSS>.md"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the benchmark, print the markdown to stdout, write nothing",
    )
    parser.add_argument("--api-key", default=config.api_key, help="bearer token for the endpoint")
    parser.add_argument("--max-tokens", type=int, default=config.max_tokens, help="max_tokens")
    parser.add_argument(
        "--reasoning-effort",
        default="none",
        help=(
            "value sent as reasoning_effort; 'none' disables thinking tokens on thinking "
            "models (default: none). Empty string omits the field."
        ),
    )
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be >= 1")
    if args.max_tokens < 1:
        parser.error("--max-tokens must be >= 1")
    if args.timeout <= 0:
        parser.error("--timeout must be > 0")
    if args.deadline <= 0:
        parser.error("--deadline must be > 0")
    if not args.base_url.lower().startswith(("http://", "https://")):
        parser.error("--base-url must start with http:// or https://")
    args.reasoning_effort = args.reasoning_effort.strip() or None
    args.languages = config.languages
    args.config_label = config.label
    args.config_notes = config.notes
    args.out_dir_path = resolve_out_dir(args.out_dir)
    return args


def progress_stream(args: argparse.Namespace) -> TextIO:
    """--dry-run owns stdout for the markdown, so progress moves to stderr."""
    return sys.stderr if args.dry_run else sys.stdout


def run_benchmark(args: argparse.Namespace, notes: list[str]) -> tuple[list[RunResult], str | None]:
    """Run the warm-up plus measured runs. Returns (results, aborted_language)."""
    results: list[RunResult] = []
    aborted: str | None = None
    progress = progress_stream(args)

    for language in args.languages:
        for run_index in [0, *range(1, args.runs + 1)]:  # 0 = discarded warm-up
            label = "warm-up (discarded)" if run_index == 0 else f"run {run_index}"
            try:
                result = run_once(args, language, run_index, notes)
            except BenchError as exc:
                print(f"{language} {label}: FAILED — {exc}", file=sys.stderr)
                results.append(RunResult(language=language, run=run_index, error=str(exc)))
                if exc.connection:
                    aborted = language
                    break
                if exc.deadline:
                    note = (
                        f"{language}: stopped after {label} hit the {args.deadline:g} s "
                        "deadline; remaining runs of this language were skipped"
                    )
                    if note not in notes:
                        notes.append(note)
                    break
                continue
            results.append(result)
            tokens_per_second = result.tokens_per_second
            tps_text = f"{tokens_per_second:.1f} tok/s" if tokens_per_second else "n/a"
            print(
                f"{language} {label}: ttfb {fmt(result.ttfb_ms)} ms, "
                f"total {fmt(result.total_ms)} ms, {result.chars} chars, {tps_text} "
                f"({result.token_source})",
                file=progress,
                flush=True,
            )
        if aborted:
            notes.append(
                f"aborted during {aborted}: the endpoint looks unreachable, "
                "later languages were skipped"
            )
            break
    return results, aborted


def write_report(path: Path, text: str, force: bool) -> tuple[int, str]:
    """Write the report, rotating an existing file to bench-llm-<date>-<HHMMSS>.md."""
    backup_name = ""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if not force:
                return 2, (
                    f"refusing to overwrite {path} (pass --force to replace it, "
                    "which keeps the previous file as a timestamped copy)"
                )
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            backup = path.with_name(f"{path.stem}-{mtime.strftime('%H%M%S')}.md")
            counter = 1
            while backup.exists():
                backup = path.with_name(f"{path.stem}-{mtime.strftime('%H%M%S')}-{counter}.md")
                counter += 1
            path.replace(backup)
            backup_name = backup.name
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        return 1, f"could not write report to {path}: {exc}"
    message = f"report: {path}"
    if backup_name:
        message += f" (previous report kept as {backup_name})"
    return 0, message


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started_at = datetime.now().isoformat(timespec="seconds")
    report_date = datetime.now().date().isoformat()
    report_path = args.out_dir_path / f"bench-llm-{report_date}.md"

    notes: list[str] = list(args.config_notes)
    if args.deadline < args.timeout:
        notes.append(
            f"--deadline ({args.deadline:g} s) is tighter than --timeout "
            f"({args.timeout:g} s); the deadline bounds each run"
        )

    if not args.dry_run and report_path.exists() and not args.force:
        print(
            f"refusing to overwrite {report_path}:\n"
            "  pass --force to replace it (the previous file is kept as "
            f"{report_path.stem}-<HHMMSS>.md) or pick another --out-dir.",
            file=sys.stderr,
        )
        return 2

    results, aborted = run_benchmark(args, notes)
    report_text = render_report(
        args, results, notes, started_at, report_date, report_path
    )

    if args.dry_run:
        sys.stdout.write(report_text)
        sys.stdout.flush()
        print(f"\ndry run: nothing written to {report_path}", file=sys.stderr)
    else:
        code, message = write_report(report_path, report_text, args.force)
        if code != 0:
            print(message, file=sys.stderr)
            return code
        print(f"\n{message}")

    measured = [run for run in results if run.measured]
    ok_runs = [run for run in measured if run.ok]
    print(
        f"\n{len(ok_runs)}/{len(measured)} measured runs succeeded "
        f"({len(results) - len(measured)} warm-up runs discarded)",
        file=progress_stream(args),
    )

    empty_languages = [
        language
        for language in args.languages
        if not any(run.ok for run in measured if run.language == language)
    ]
    if not ok_runs:
        if aborted:
            print(
                f"ERROR: no request reached {args.base_url} ({aborted} failed to connect).\n"
                "  Possible causes:\n"
                "    1. Ollama is not running on the host (start it, then re-run).\n"
                "    2. AST-000 is still open: Ollama listens on 127.0.0.1 only, so containers "
                "get ECONNREFUSED via host.docker.internal.\n"
                "       Fix: restart Ollama with OLLAMA_HOST=0.0.0.0:11434 "
                "(or 172.17.0.1:11434) and re-run.\n"
                f"    3. The endpoint is elsewhere: pass --base-url (current {args.base_url}).\n"
                f"  Details are in the report: {report_path}",
                file=sys.stderr,
            )
        else:
            first_error = next(run.error for run in results if not run.ok)
            print(
                f"ERROR: {args.base_url} rejected every request: {first_error}\n"
                "  Checks:\n"
                f"    1. The model name exists: `ollama list | grep {args.model}`.\n"
                "    2. The server accepts `\"stream\": true` with "
                "`stream_options.include_usage` on /chat/completions.\n"
                f"  Details are in the report: {report_path}",
                file=sys.stderr,
            )
        return 1
    if empty_languages:
        print(
            f"ERROR: zero successful measured runs for: {', '.join(empty_languages)} "
            f"(see {report_path})",
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
    except Exception as exc:  # safety net for an unattended benchmark
        print(f"unexpected failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(entrypoint())
