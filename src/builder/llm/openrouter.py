"""OpenRouter provider (OpenAI-compatible chat completions over httpx)."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from builder.llm.errors import LLMError
from builder.llm.types import LLMRequest, LLMResponse, Message, ToolCall, Usage

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def to_wire_message(m: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in m.tool_calls
        ]
    if m.tool_call_id:
        out["tool_call_id"] = m.tool_call_id
    if m.name:
        out["name"] = m.name
    return out


def build_payload(model: str, request: LLMRequest) -> dict[str, Any]:
    # Empty assistant turns (no text, no tool calls) are rejected by some providers.
    msgs = [
        m
        for m in request.messages
        if not (m.role == "assistant" and not (m.content or "").strip() and not m.tool_calls)
    ]
    payload: dict[str, Any] = {
        "model": model,
        "messages": [to_wire_message(m) for m in msgs],
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "usage": {"include": True},
    }
    if request.seed is not None:
        payload["seed"] = request.seed
    if request.tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in request.tools
        ]
    if request.json_mode and not request.tools and supports_json_mode(model):
        payload["response_format"] = {"type": "json_object"}
    return payload


# Providers that reject OpenAI-style `response_format`; our JSON extraction copes without it.
NO_JSON_MODE_PREFIXES = ("anthropic/",)


def supports_json_mode(model: str) -> bool:
    return not model.startswith(NO_JSON_MODE_PREFIXES)


def parse_response(data: dict[str, Any]) -> LLMResponse:
    if "error" in data:
        raise LLMError(f"provider error: {data['error']}")
    choice = data["choices"][0]["message"]
    calls: list[ToolCall] = []
    for tc in choice.get("tool_calls") or []:
        raw = tc["function"].get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except json.JSONDecodeError:
            args = {"__unparsed__": raw}
        calls.append(ToolCall(id=tc.get("id", ""), name=tc["function"]["name"], arguments=args))
    u = data.get("usage") or {}
    return LLMResponse(
        content=choice.get("content"),
        tool_calls=calls,
        model=data.get("model", ""),
        usage=Usage(
            prompt_tokens=int(u.get("prompt_tokens", 0)),
            completion_tokens=int(u.get("completion_tokens", 0)),
            cost_usd=float(u.get("cost", 0.0) or 0.0),
        ),
    )


class OpenRouterProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_s: float = 120.0,
        max_retries: int = 4,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.max_retries = max_retries
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_s,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Title": "agent-builder",
            },
        )

    def complete(self, model: str, request: LLMRequest) -> LLMResponse:
        payload = build_payload(model, request)
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            try:
                r = self._http.post("/chat/completions", json=payload)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise LLMError(f"transport error: {exc}") from exc
            else:
                if r.status_code == 200:
                    return parse_response(r.json())
                if r.status_code not in RETRY_STATUS or attempt == self.max_retries:
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:500]}")
            time.sleep(delay)
            delay *= 2
        raise LLMError("unreachable")
