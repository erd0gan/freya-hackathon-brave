"""Provider-neutral chat types (OpenAI-compatible wire shape)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from builder.llm.roles import Role


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class ToolDef(BaseModel):
    """A function tool exposed to the model (JSON Schema parameters)."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class LLMRequest(BaseModel):
    role: Role
    messages: list[Message]
    model: str | None = None
    temperature: float | None = None
    max_tokens: int = 2000
    tools: list[ToolDef] = Field(default_factory=list)
    json_mode: bool = False
    seed: int | None = None
    # Distinguishes otherwise-identical requests that must not share a cache
    # entry (e.g. repeated trials of a stochastic simulation).
    cache_salt: str = ""


class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str = ""
    cached: bool = False
    latency_s: float = 0.0
