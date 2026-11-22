"""Contract tests for OpenAICompatLLM against a local stub server (no network)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from app.core.errors import LLMError
from app.providers.llm.openai_compat import OpenAICompatLLM, _parse_json_object

MESSAGES: list[dict[str, str]] = [{"role": "user", "content": "hi"}]
SCHEMA: dict[str, Any] = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


@dataclass(frozen=True)
class RecordedRequest:
    path: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class Reply:
    status: int
    body: bytes
    content_type: str = "application/json"

    def headers(self) -> dict[str, str]:
        return {"Content-Type": self.content_type, "Content-Length": str(len(self.body))}


Responder = Callable[[RecordedRequest], Reply]


def sse_reply(*deltas: str) -> Reply:
    events = [": keepalive\n\n"]
    events += [
        f"data: {json.dumps({'choices': [{'delta': {'content': d}}]})}\n\n" for d in deltas
    ]
    events += ['data: {"choices":[{"delta":{"content":""}}]}\n\n', "data: [DONE]\n\n"]
    return Reply(200, "".join(events).encode(), "text/event-stream")


def openai_reply(content: str) -> Reply:
    return Reply(200, json.dumps({"choices": [{"message": {"content": content}}]}).encode())


def ollama_reply(content: str) -> Reply:
    return Reply(200, json.dumps({"message": {"content": content}}).encode())


def error_reply(status: int, text: str = "boom") -> Reply:
    return Reply(status, text.encode(), "text/plain")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    stub: StubHTTPServer


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        record = RecordedRequest(path=self.path, payload=payload)

        server: _Server = self.server  # type: ignore[assignment]
        stub = server.stub
        with stub.lock:
            stub.records.append(record)
            responder = stub.queue.pop(0) if stub.queue else None
        reply = responder(record) if responder else stub.default(record)

        self.send_response(reply.status)
        for key, value in reply.headers().items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(reply.body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        return None


class StubHTTPServer:
    """Configurable OpenAI/Ollama compatible stub listening on 127.0.0.1."""

    def __init__(self) -> None:
        self.records: list[RecordedRequest] = []
        self.lock = threading.Lock()
        self.queue: list[Responder] = []
        self.sse_deltas: list[str] = ["He", "llo"]
        self.openai_content: str = '{"ok": true}'
        self.ollama_content: str = '{"ok": true}'
        self._httpd = _Server(("127.0.0.1", 0), _Handler)
        self._httpd.stub = self
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def enqueue(self, *responders: Responder) -> None:
        self.queue.extend(responders)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._thread.join(timeout=5)
        self._httpd.server_close()

    def default(self, record: RecordedRequest) -> Reply:
        if record.path.endswith("/api/chat"):
            return ollama_reply(self.ollama_content)
        if record.payload.get("stream"):
            return sse_reply(*self.sse_deltas)
        return openai_reply(self.openai_content)


@pytest.fixture
def stub() -> Iterator[StubHTTPServer]:
    server = StubHTTPServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


def make_llm(server: StubHTTPServer, **overrides: Any) -> OpenAICompatLLM:
    cfg: dict[str, Any] = {
        "base_url": server.base_url,
        "model": "stub",
        "api_key": "test-key",
        "timeout_s": 5,
        **overrides,
    }
    return OpenAICompatLLM(cfg)


async def test_chat_stream_yields_deltas_in_order(stub: StubHTTPServer) -> None:
    stub.sse_deltas = ["Hello", ", ", "world"]
    llm = make_llm(stub)

    chunks = [chunk async for chunk in llm.chat_stream(MESSAGES)]

    assert chunks == ["Hello", ", ", "world"]
    assert len(stub.records) == 1
    assert stub.records[0].path == "/v1/chat/completions"
    assert stub.records[0].payload["stream"] is True


async def test_http_500_raises_llm_error_with_status(stub: StubHTTPServer) -> None:
    stub.enqueue(lambda _record: error_reply(500, "upstream exploded"))
    llm = make_llm(stub)

    with pytest.raises(LLMError) as streaming_error:
        async for _ in llm.chat_stream(MESSAGES):
            pass
    assert "500" in str(streaming_error.value)

    stub.enqueue(lambda _record: error_reply(503, "unavailable"))
    with pytest.raises(LLMError) as json_error:
        await llm.chat_json(MESSAGES, SCHEMA)
    assert "503" in str(json_error.value)
    assert len(stub.records) == 2


async def test_chat_json_json_object_returns_parsed_dict(stub: StubHTTPServer) -> None:
    stub.openai_content = '{"ok": true, "items": [1, 2]}'
    llm = make_llm(stub, json_mode="json_object")

    parsed = await llm.chat_json(MESSAGES, SCHEMA)

    assert parsed == {"ok": True, "items": [1, 2]}
    assert len(stub.records) == 1
    request = stub.records[0]
    assert request.path == "/v1/chat/completions"
    assert request.payload["response_format"] == {"type": "json_object"}


async def test_chat_json_retries_once_after_invalid_json(stub: StubHTTPServer) -> None:
    stub.enqueue(
        lambda _record: openai_reply("Sure! I cannot do JSON today."),
        lambda _record: openai_reply('{"status": "recovered"}'),
    )
    llm = make_llm(stub, json_mode="json_object")

    parsed = await llm.chat_json(MESSAGES, SCHEMA)

    assert parsed == {"status": "recovered"}
    assert len(stub.records) == 2
    repair_messages = stub.records[1].payload["messages"]
    assert repair_messages[-1]["role"] == "user"
    assert "JSON" in repair_messages[-1]["content"]


async def test_chat_json_ollama_native_posts_to_api_chat(stub: StubHTTPServer) -> None:
    stub.ollama_content = '{"ok": true}'
    llm = make_llm(stub, json_mode="ollama_native")

    parsed = await llm.chat_json(MESSAGES, SCHEMA)

    assert parsed == {"ok": True}
    assert len(stub.records) == 1
    request = stub.records[0]
    assert request.path == "/api/chat"
    assert request.payload["format"] == SCHEMA
    assert request.payload["stream"] is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1, "b": [2]}\n```', {"a": 1, "b": [2]}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('prefix {"a": 1} suffix', {"a": 1}),
        ("no json here", None),
        ("", None),
        ('["a", "b"]', None),
        ('{"a": ', None),
    ],
)
def test_parse_json_object(text: str, expected: dict[str, Any] | None) -> None:
    assert _parse_json_object(text) == expected


async def test_default_config_sends_reasoning_effort_none(stub: StubHTTPServer) -> None:
    llm = make_llm(stub)

    _ = [chunk async for chunk in llm.chat_stream(MESSAGES)]
    parsed = await llm.chat_json(MESSAGES, SCHEMA)

    assert parsed == {"ok": True}
    assert len(stub.records) == 2
    assert [record.payload["reasoning_effort"] for record in stub.records] == ["none", "none"]


async def test_empty_reasoning_effort_is_omitted_from_payloads(stub: StubHTTPServer) -> None:
    llm = make_llm(stub, reasoning_effort="")

    _ = [chunk async for chunk in llm.chat_stream(MESSAGES)]
    parsed = await llm.chat_json(MESSAGES, SCHEMA)

    assert parsed == {"ok": True}
    assert len(stub.records) == 2
    assert all("reasoning_effort" not in record.payload for record in stub.records)


async def test_ollama_native_sends_think_false(stub: StubHTTPServer) -> None:
    llm = make_llm(stub, json_mode="ollama_native")

    await llm.chat_json(MESSAGES, SCHEMA)

    request = stub.records[0]
    assert request.path == "/api/chat"
    assert request.payload["think"] is False
    assert "reasoning_effort" not in request.payload


async def test_stream_retries_once_without_reasoning_effort_on_400(stub: StubHTTPServer) -> None:
    stub.sse_deltas = ["He", "llo"]
    stub.enqueue(lambda _record: error_reply(400, "unknown field 'reasoning_effort'"))
    llm = make_llm(stub)

    chunks = [chunk async for chunk in llm.chat_stream(MESSAGES)]

    assert chunks == ["He", "llo"]
    assert len(stub.records) == 2
    assert stub.records[0].payload["reasoning_effort"] == "none"
    assert "reasoning_effort" not in stub.records[1].payload


async def test_json_retries_once_without_reasoning_effort_on_400(stub: StubHTTPServer) -> None:
    stub.enqueue(lambda _record: error_reply(400, "unknown field 'reasoning_effort'"))
    llm = make_llm(stub, json_mode="json_object")

    parsed = await llm.chat_json(MESSAGES, SCHEMA)

    assert parsed == {"ok": True}
    assert len(stub.records) == 2
    assert stub.records[0].payload["reasoning_effort"] == "none"
    assert "reasoning_effort" not in stub.records[1].payload


async def test_stream_does_not_retry_on_non_400_error(stub: StubHTTPServer) -> None:
    stub.enqueue(lambda _record: error_reply(422, "bad request"))
    llm = make_llm(stub)

    with pytest.raises(LLMError) as error:
        _ = [chunk async for chunk in llm.chat_stream(MESSAGES)]

    assert "422" in str(error.value)
    assert len(stub.records) == 1


async def test_stream_stops_at_done_marker(stub: StubHTTPServer) -> None:
    tail = json.dumps({"choices": [{"delta": {"content": "GHOST"}}]})

    def responder(_record: RecordedRequest) -> Reply:
        events = [
            f"data: {json.dumps({'choices': [{'delta': {'content': 'Hi'}}]})}\n\n",
            "data: [DONE]\n\n",
            f"data: {tail}\n\n",
        ]
        return Reply(200, "".join(events).encode(), "text/event-stream")

    stub.enqueue(responder)
    llm = make_llm(stub)

    chunks = [chunk async for chunk in llm.chat_stream(MESSAGES)]

    assert chunks == ["Hi"]


async def test_all_traffic_stays_on_local_stub(stub: StubHTTPServer) -> None:
    llm = make_llm(stub)
    assert llm.base_url == f"http://127.0.0.1:{stub.port}/v1"

    _ = [chunk async for chunk in llm.chat_stream(MESSAGES)]
    parsed = await llm.chat_json(MESSAGES, SCHEMA)

    assert parsed == {"ok": True}
    assert len(stub.records) == 2
    assert {record.path for record in stub.records} == {"/v1/chat/completions"}
