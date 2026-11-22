"""Tests for the host-side `scripts/bench_llm.py`.

The script targets Python 3.14 and has no package, so it is loaded from its file path.
No real endpoint is used: the streaming tests talk to a local 127.0.0.1 stub server and
every other test is pure.
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import importlib.util
import io
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def load_script(module_name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bench() -> ModuleType:
    return load_script("scripts_bench_llm_under_test", "bench_llm.py")


# --- token estimate and adherence ----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("Good morning, welcome!", "en", 3.0),
        ("Guten Tag, willkommen!", "de", 3.0),
        ("", "en", 0.0),
        ("こんにちは", "ja", 5 / 1.7),
        ("今天有一杯咖啡", "zh", 7 / 1.7),
    ],
)
def test_estimate_tokens(bench: ModuleType, text: str, language: str, expected: float) -> None:
    assert bench.estimate_tokens(text, language) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("language", "text", "expected_ok", "reason_fragment"),
    [
        (
            "en",
            "Good morning! Would you like a flat white today?",
            True,
            "ASCII",
        ),
        ("en", "こんにちは、コーヒーはいかがですか", False, "contains CJK/kana"),
        ("ja", "こんにちは、いらっしゃいませ。今日のコーヒーはいかがですか", True, "hiragana"),
        ("ja", "Good morning, welcome to the café", False, "no hiragana/katakana"),
        ("zh", "早上好，欢迎光临，今天有一杯咖啡", True, "CJK ideographs"),
        ("zh", "こんにちは、コーヒーはいかがですか", False, "looks Japanese"),
        ("de", "Guten Tag! Möchten Sie einen Kaffee?", True, "ä/ö/ü/ß"),
        ("de", "Good morning, would you like a coffee?", False, "no German"),
        ("de", "Ja bitte, der Kaffee ist gut und ich nehme den hier", True, "stopwords"),
    ],
)
def test_check_adherence(
    bench: ModuleType,
    language: str,
    text: str,
    expected_ok: bool,
    reason_fragment: str,
) -> None:
    ok, reason = bench.check_adherence(language, text)

    assert ok is expected_ok
    assert reason_fragment in reason


def test_check_adherence_rejects_empty_text(bench: ModuleType) -> None:
    for language in bench.LANGUAGES:
        assert bench.check_adherence(language, "   \n") == (False, "empty response")


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/v1/chat/completions"),
        ("http://127.0.0.1:11434/v1/", "http://127.0.0.1:11434/v1/chat/completions"),
        (
            "http://127.0.0.1:11434/v1/chat/completions",
            "http://127.0.0.1:11434/v1/chat/completions",
        ),
        ("https://example.invalid", "https://example.invalid/chat/completions"),
    ],
)
def test_query_endpoint(bench: ModuleType, base_url: str, expected: str) -> None:
    assert bench.query_endpoint(base_url) == expected


def test_median_and_format_helpers(bench: ModuleType) -> None:
    assert bench.median([]) is None
    assert bench.median([7.0]) == 7.0
    assert bench.median([1.0, 3.0]) == 2.0
    assert bench.median([9.0, 1.0, 5.0]) == 5.0

    assert bench.fmt(None) == "—"
    assert bench.fmt(250.4) == "250"
    assert bench.fmt(250.44, 1) == "250.4"
    assert bench.span([]) == "—"
    assert bench.span([3.0, 9.6]) == "3–10"
    assert bench.sanitize(" a\n b  c ") == "a b c"
    assert bench.sanitize("abcdef", 3) == "abc…"


def test_tokens_per_second_uses_generation_window(bench: ModuleType) -> None:
    measured = bench.RunResult(
        language="en", run=1, ttfb_ms=100.0, total_ms=1100.0, chars=40, tokens=10.0
    )
    assert measured.measured is True
    assert measured.tokens_per_second == pytest.approx(10.0)

    failed = bench.RunResult(language="en", run=1, error="boom", total_ms=1100.0)
    assert failed.ok is False
    assert failed.tokens_per_second is None

    warmup = bench.RunResult(language="en", run=0, ttfb_ms=500.0, total_ms=500.0, tokens=1.0)
    assert warmup.measured is False
    assert warmup.tokens_per_second is None


# --- write_report --------------------------------------------------------------------


def test_write_report_refuses_to_overwrite_without_force(bench: ModuleType, tmp_path: Path) -> None:
    report = tmp_path / "bench-llm-2026-11-18.md"
    report.write_text("previous\n", encoding="utf-8")

    code, message = bench.write_report(report, "new\n", force=False)

    assert code == 2
    assert "refusing to overwrite" in message
    assert "--force" in message
    assert report.read_text(encoding="utf-8") == "previous\n"
    assert list(tmp_path.glob("bench-llm-2026-11-18-*.md")) == []


def test_write_report_with_force_keeps_timestamped_backup(
    bench: ModuleType, tmp_path: Path
) -> None:
    report = tmp_path / "bench-llm-2026-11-18.md"
    report.write_text("previous\n", encoding="utf-8")
    stamp = datetime(2026, 11, 18, 7, 30, 15).timestamp()
    # mtime drives the backup name, so pin it instead of trusting the wall clock.
    os.utime(report, (stamp, stamp))
    expected_backup = f"bench-llm-2026-11-18-{datetime.fromtimestamp(stamp).strftime('%H%M%S')}.md"

    code, message = bench.write_report(report, "new\n", force=True)

    assert code == 0
    assert report.read_text(encoding="utf-8") == "new\n"
    assert expected_backup in message
    assert (tmp_path / expected_backup).read_text(encoding="utf-8") == "previous\n"


def test_write_report_creates_missing_parent_directories(
    bench: ModuleType, tmp_path: Path
) -> None:
    report = tmp_path / "spikes" / "nested" / "bench-llm-2026-11-18.md"

    code, message = bench.write_report(report, "new\n", force=False)

    assert code == 0
    assert str(report) in message
    assert report.read_text(encoding="utf-8") == "new\n"


def test_write_report_reports_oserror_as_exit_1(bench: ModuleType, tmp_path: Path) -> None:
    blocker = tmp_path / "blocker.md"
    blocker.write_text("not a directory\n", encoding="utf-8")

    code, message = bench.write_report(blocker / "report.md", "new\n", force=False)

    assert code == 1
    assert "could not write report" in message


# --- render_report -------------------------------------------------------------------


def make_args(**overrides: Any) -> argparse.Namespace:
    values: dict[str, Any] = {
        "base_url": "http://127.0.0.1:9/v1",
        "model": "qwen3.5:4b",
        "runs": 2,
        "timeout": 5.0,
        "deadline": 10.0,
        "max_tokens": 120,
        "reasoning_effort": "none",
        "languages": ("en", "ja"),
        "config_label": "test defaults",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


REPORT_PATH = Path("docs/spikes/bench-llm-2026-11-18.md")


def render(bench: ModuleType, results: list[Any], notes: list[str] | None = None, **kwargs: Any):
    return bench.render_report(
        make_args(**kwargs),
        results,
        notes or [],
        "2026-11-18T10:00:00",
        "2026-11-18",
        REPORT_PATH,
    )


def test_render_report_with_zero_results(bench: ModuleType) -> None:
    text = render(bench, [])

    assert text.startswith("# LLM benchmark — 2026-11-18")
    assert "| en | 0/0 |" in text
    assert "no successful run" in text
    assert "_no sample (request failed)_" in text
    assert "## Errors" not in text
    assert "## Diagnosis" not in text


def test_render_report_with_one_failed_result(bench: ModuleType) -> None:
    failed = bench.RunResult(
        language="en",
        run=1,
        error="connection failed: [Errno 111] Connection refused",
    )

    text = render(bench, [failed])

    assert "## Errors" in text
    assert "`en` run 1: connection failed: [Errno 111] Connection refused" in text
    assert "## Diagnosis" in text
    assert "Every request to `http://127.0.0.1:9/v1` failed" in text
    assert "OLLAMA_HOST=0.0.0.0:11434" in text
    assert "| en | 1 | — | — | — | — | failed:" in text


def test_render_report_with_one_successful_run(bench: ModuleType) -> None:
    result = bench.RunResult(
        language="en",
        run=1,
        ttfb_ms=250.0,
        total_ms=1250.0,
        chars=42,
        tokens=20.0,
        token_source="usage",
        text="Good morning! Today's special is a flat white.",
    )

    text = render(bench, [result], notes=["all good"])

    assert "| en | 1/1 | 250 |" in text
    assert "| en | 1 | 250 | 1250 | 42 | 20.0 (usage) | pass —" in text
    assert "## Notes" in text
    assert "- all good" in text
    assert "## Errors" not in text
    assert "## Diagnosis" not in text


# --- _read_stream against a local stub server (no external network) -------------------


class _StubServer(ThreadingHTTPServer):
    daemon_threads = True
    script: Callable[[BaseHTTPRequestHandler], None]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        server: _StubServer = self.server  # type: ignore[assignment]
        try:
            server.script(self)
        except (BrokenPipeError, ConnectionResetError):
            # The client hanging up mid-stream is the point of the deadline test.
            return None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        return None


@contextlib.contextmanager
def stub_endpoint(script: Callable[[BaseHTTPRequestHandler], None]) -> Iterator[str]:
    httpd = _StubServer(("127.0.0.1", 0), _Handler)
    httpd.script = script
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


def sse_script(handler: BaseHTTPRequestHandler) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.end_headers()
    events = [
        ": keepalive\n\n",
        'data: {"choices":[{"delta":{"reasoning":"hmm"}}]}\n\n',
        'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
        'data: {"choices":[{"delta":{"content":" world"}}]}\n\n',
        'data: {"choices":[],"usage":{"completion_tokens":7,'
        '"prompt_tokens_details":{"cached_tokens":32}}}\n\n',
        "data: [DONE]\n\n",
    ]
    for event in events:
        handler.wfile.write(event.encode())
        handler.wfile.flush()


def test_read_stream_happy_path(bench: ModuleType) -> None:
    with stub_endpoint(sse_script) as base_url:
        request = bench.build_request(base_url, "stub", "test-key", "hi", 16, None)
        sample = bench._read_stream(request, timeout=5.0, deadline_s=10.0)

    assert sample.text == "Hello world"
    assert sample.ttfb_ms is not None and sample.ttfb_ms >= 0
    assert sample.total_ms >= sample.ttfb_ms
    assert sample.thinking_chars == len("hmm")
    assert sample.usage_completion_tokens == 7
    assert sample.cached_prompt_tokens == 32


def bad_status_script(handler: BaseHTTPRequestHandler) -> None:
    handler.wfile.write(b"NOT-HTTP\r\n\r\n")
    handler.wfile.flush()


def test_read_stream_bad_status_line_is_contained_as_bench_error(bench: ModuleType) -> None:
    notes: list[str] = []
    with stub_endpoint(bad_status_script) as base_url:
        args = make_args(base_url=base_url, api_key="test-key", max_tokens=16)
        with pytest.raises(bench.BenchError) as error:
            bench._stream_with_fallback(args, "hi", notes)

    assert isinstance(error.value.__cause__, http.client.BadStatusLine)
    assert "BadStatusLine" in str(error.value)
    assert error.value.connection is False
    assert error.value.stop_language is False


def dribble_script(handler: BaseHTTPRequestHandler) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.end_headers()
    # A chunk every 50 ms: no single read stalls (the socket timeout is min(timeout,
    # deadline)), but the stream as a whole outlives the deadline, so the wall-clock check
    # is what trips.
    for _ in range(20):
        handler.wfile.write(b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n')
        handler.wfile.flush()
        time.sleep(0.05)


def test_read_stream_enforces_wall_clock_deadline(bench: ModuleType) -> None:
    with stub_endpoint(dribble_script) as base_url:
        request = bench.build_request(base_url, "stub", "test-key", "hi", 16, None)
        started = time.perf_counter()
        with pytest.raises(bench.BenchError) as error:
            bench._read_stream(request, timeout=5.0, deadline_s=0.3)
        elapsed = time.perf_counter() - started

    assert error.value.deadline is True
    assert "wall-clock deadline" in str(error.value)
    assert error.value.stop_language is True
    assert elapsed < 1.0


def test_describe_error_covers_http_url_and_timeout(bench: ModuleType) -> None:
    http_error = bench.urllib.error.HTTPError(
        "http://127.0.0.1:9/v1",
        400,
        "Bad Request",
        http.client.HTTPMessage(),
        io.BytesIO(b"upstream exploded"),
    )
    assert bench.describe_error(http_error) == "HTTP 400 Bad Request: upstream exploded"
    assert bench.describe_error(TimeoutError("slow")) == "request timed out"
    assert bench.describe_error(ValueError("boom")) == "ValueError: boom"
