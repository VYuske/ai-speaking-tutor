from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.errors import LLMError

logger = logging.getLogger(__name__)


class OpenAICompatLLM:
    """Thin async client for any OpenAI-compatible chat endpoint.

    Works with Ollama, llama.cpp server, LM Studio and vLLM. Keeping this
    deliberately small is what makes the model swappable via config.
    """

    name = "openai_compat"

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.base_url = str(cfg["base_url"]).rstrip("/")
        self.model = str(cfg["model"])
        self.api_key = str(cfg.get("api_key", "none"))
        self.temperature = float(cfg.get("temperature", 0.8))
        self.eval_temperature = float(cfg.get("eval_temperature", 0.0))
        self.max_tokens = int(cfg.get("max_tokens", 256))
        self.timeout_s = float(cfg.get("timeout_s", 60))
        self.json_mode = str(cfg.get("json_mode", "json_object"))
        effort = cfg.get("reasoning_effort", "none")
        self.reasoning_effort = str(effort) if effort else None

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    @property
    def _reasoning_payload(self) -> dict[str, Any]:
        return {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}

    async def chat_stream(
        self, messages: list[dict[str, str]], **overrides: Any
    ) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": overrides.get("temperature", self.temperature),
            "max_tokens": overrides.get("max_tokens", self.max_tokens),
            **self._reasoning_payload,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await self._post_stream(client, payload)
                try:
                    if _drop_rejected_field(payload, response, "reasoning_effort"):
                        await response.aclose()
                        response = await self._post_stream(client, payload)
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", "replace")
                        raise LLMError(f"LLM HTTP {response.status_code}: {body[:400]}")
                    async for content in _sse_content(response):
                        yield content
                finally:
                    await response.aclose()
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

    async def _post_stream(
        self, client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> httpx.Response:
        request = client.build_request(
            "POST",
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=self._headers,
        )
        return await client.send(request, stream=True)

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        **overrides: Any,
    ) -> dict[str, Any]:
        if self.json_mode == "ollama_native":
            content = await self._ollama_json(messages, schema, overrides)
        else:
            content = await self._openai_json(messages, schema, overrides)

        parsed = _parse_json_object(content)
        if parsed is not None:
            return parsed

        repair = [
            *messages,
            {"role": "assistant", "content": content[:2000]},
            {
                "role": "user",
                "content": "Your previous answer was not valid JSON. Return ONLY a JSON object.",
            },
        ]
        retry = (
            await self._ollama_json(repair, schema, overrides)
            if self.json_mode == "ollama_native"
            else await self._openai_json(repair, schema, overrides)
        )
        parsed = _parse_json_object(retry)
        if parsed is None:
            raise LLMError("model did not return valid JSON after retry")
        return parsed

    async def _ollama_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        overrides: dict[str, Any],
    ) -> str:
        base = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": schema,
            "options": {"temperature": overrides.get("temperature", self.eval_temperature)},
        }
        if self.reasoning_effort:
            payload["think"] = False
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.post(
                    f"{base}/api/chat", json=payload, headers=self._headers
                )
                if _drop_rejected_field(payload, response, "think"):
                    response = await client.post(
                        f"{base}/api/chat", json=payload, headers=self._headers
                    )
                if response.status_code >= 400:
                    raise LLMError(f"LLM HTTP {response.status_code}: {response.text[:400]}")
                body = response.json()
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        return str((body.get("message") or {}).get("content", ""))

    async def _openai_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        overrides: dict[str, Any],
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": overrides.get("temperature", self.eval_temperature),
            "response_format": {"type": "json_object"},
            **self._reasoning_payload,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=self._headers
                )
                if _drop_rejected_field(payload, response, "reasoning_effort"):
                    response = await client.post(
                        f"{self.base_url}/chat/completions", json=payload, headers=self._headers
                    )
                if response.status_code >= 400:
                    raise LLMError(f"LLM HTTP {response.status_code}: {response.text[:400]}")
                body = response.json()
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        choices = body.get("choices") or []
        if not choices:
            raise LLMError("LLM returned no choices")
        return str((choices[0].get("message") or {}).get("content", ""))

    async def aclose(self) -> None:
        return None


def _drop_rejected_field(
    payload: dict[str, Any], response: httpx.Response, field: str
) -> bool:
    """Remove `field` from `payload` so the caller retries once without it."""
    if response.status_code != 400 or field not in payload:
        return False
    logger.warning("LLM rejected %s with HTTP 400; retrying without it", field)
    payload.pop(field)
    return True


async def _sse_content(response: httpx.Response) -> AsyncIterator[str]:
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        if not data:
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = event.get("choices") or []
        if not choices:
            continue
        content = (choices[0].get("delta") or {}).get("content")
        if content:
            yield str(content)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
